"""Modeles d'IA de l'equipe, branches sur les mesures REELLES des capteurs.

  - Isolation Forest (models/isolation_forest_model.pkl) : detecte les heures de consommation anormales.
    Entrees (model_features.pkl) : power_mean, power_max, power_std (en kW, sur l'heure), hour, day_of_week, is_weekend.
  - XGBoost (models/kizuna_watt_xgb.json, converti du pickle de l'equipe) : prevoit la consommation horaire (kW) du lendemain.
    28 variables : consommation 24/48/72/168/336/504 h plus tot, moyennes glissantes, meteo decalee, calendrier.

Securite : les fichiers sont verifies par empreinte SHA-256 (models/MANIFEST.json) avant chargement ; le pickle des variables
est lu par un lecteur qui interdit toute importation. Les bibliotheques (numpy, scikit-learn, xgboost) sont facultatives :
sans elles, l'application fonctionne et signale simplement que l'IA est indisponible.

HYPOTHESES a confirmer par l'equipe (voir ml/INTEGRATION_MODELES.md) : la cible du XGBoost est la puissance moyenne horaire en kW ;
les variables retardees (lag_k, moyennes) sont calculees sur cette meme serie ; la meteo vient d'Open-Meteo (km/h pour le vent).
La prevision est donc affichee comme EXPERIMENTALE, avec l'erreur reellement observee sur les 24 dernieres heures.
"""
import hashlib
import io
import json
import math
import os
import pickle
import threading
import time
import urllib.parse
import urllib.request
import warnings
from datetime import datetime, timedelta, timezone

from . import db, lighting

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models")
FILES = {"iso": "isolation_forest_model.pkl", "feats": "model_features.pkl", "xgb": "kizuna_watt_xgb.json", "demand": "model3_reel.pkl"}
TZ = timezone(timedelta(hours=1))
MIN_SAMPLES = 10            # mesures minimum dans une heure pour l'analyser
NEED_DAYS = 28              # historique conseille pour la prevision (lags jusqu'a 504 h + moyenne sur 4 semaines)
_lock = threading.Lock()
_S = dict(tried=False, error=None, iso=None, names=None, xgb=None, warnings=[])
_cache = {}                 # (uid, genre) -> (heure, valeur)
_weather = {}               # gouvernorat -> (heure, donnees)


# ---------- chargement securise ----------
class _NoGlobals(pickle.Unpickler):
    def find_class(self, module, name):
        raise pickle.UnpicklingError(f"importation interdite : {module}.{name}")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def trust_models():
    """Enregistre les empreintes des fichiers actuels (a lancer apres avoir verifie les modeles) : python manage.py trust-models"""
    man = {k: sha256(os.path.join(ROOT, f)) for k, f in FILES.items() if os.path.exists(os.path.join(ROOT, f))}
    with open(os.path.join(ROOT, "MANIFEST.json"), "w", encoding="utf-8") as f:
        json.dump({"files": {FILES[k]: v for k, v in man.items()}}, f, indent=2)
    return len(man)


def load():
    """Charge les modeles une seule fois. Retourne True si au moins un modele est utilisable."""
    with _lock:
        if _S["tried"]:
            return bool(_S["iso"] or _S["xgb"])
        _S["tried"] = True
        try:
            import numpy  # noqa: F401
        except ImportError:
            _S["error"] = "bibliotheques manquantes : pip install -r requirements.txt"
            return False
        try:
            man = json.load(open(os.path.join(ROOT, "MANIFEST.json"), encoding="utf-8"))["files"]
        except Exception:
            _S["error"] = "models/MANIFEST.json absent : verifiez les modeles puis lancez python manage.py trust-models"
            return False
        for key, fn in FILES.items():
            p = os.path.join(ROOT, fn)
            if os.path.exists(p) and man.get(fn) != sha256(p):
                _S["error"] = f"{fn} a ete modifie depuis la derniere verification : refus de le charger"
                return False
        try:
            with open(os.path.join(ROOT, FILES["feats"]), "rb") as f:
                _S["names"] = list(_NoGlobals(f).load())
        except Exception as e:
            _S["error"] = f"model_features.pkl illisible : {e}"
            return False
        errs = []
        try:
            import joblib
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                _S["iso"] = joblib.load(os.path.join(ROOT, FILES["iso"]))
                if any("version" in str(x.message) for x in w):
                    import re
                    import sklearn
                    m = re.search(r"from version (\S+)", " ".join(str(x.message) for x in w))
                    _S["warnings"].append(f"Le detecteur d'anomalies a ete entraine avec scikit-learn {m.group(1) if m else '?'} ; cette installation utilise la version "
                                          f"{sklearn.__version__}. Les resultats peuvent differer legerement : a faire verifier par l'equipe.")
        except Exception as e:
            errs.append(f"Isolation Forest : {e}")
        try:
            import xgboost as xgb
            b = xgb.Booster()
            b.load_model(os.path.join(ROOT, FILES["xgb"]))
            _S["xgb"] = b
        except Exception as e:
            errs.append(f"XGBoost : {e}")
        _S["error"] = " ; ".join(errs) or None
        return bool(_S["iso"] or _S["xgb"])


def status():
    ok = load()
    return dict(available=ok, error=_S["error"], isolation_forest=_S["iso"] is not None, xgboost=_S["xgb"] is not None,
                features=_S["names"], warnings=_S["warnings"][:2])


# ---------- Isolation Forest : heures anormales ----------
def _hour_features(h_ts, v):
    """Variables du modele pour une heure : puissances en kW, heure locale, jour de semaine (lundi = 0), week-end."""
    dt = datetime.fromtimestamp(h_ts, TZ)
    d = dict(power_mean=v["mean_w"] / 1000, power_max=v["max_w"] / 1000, power_std=v["std_w"] / 1000, hour=dt.hour, day_of_week=dt.weekday(), is_weekend=int(dt.weekday() >= 5))
    return [d[n] for n in _S["names"]], dt


def anomaly_score(decision):
    """Indice 0-100 (50 = limite du modele) a partir de decision_function (positif = normal)."""
    return round(100 * min(1.0, max(0.0, 0.5 - 2.5 * decision)))


def analyze_anomalies(uid, hours=24):
    if not load() or _S["iso"] is None:
        return None
    import numpy as np
    ser = db.hourly_series(uid, hours + 2)
    keys = [h for h in sorted(ser) if ser[h]["n"] >= MIN_SAMPLES][-hours:]
    if not keys:
        return dict(items=[], latest=None, hours_analyzed=0)
    rows = [_hour_features(h, ser[h])[0] for h in keys]
    X = np.array(rows, dtype=float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pred, dec = _S["iso"].predict(X), _S["iso"].decision_function(X)
    items = [dict(hour=datetime.fromtimestamp(h, TZ).strftime("%d/%m %Hh"), ts=h, mean_w=round(ser[h]["mean_w"]), max_w=round(ser[h]["max_w"]),
                  anomaly=bool(p == -1), score=anomaly_score(d)) for h, p, d in zip(keys, pred, dec)]
    return dict(items=items, latest=items[-1], hours_analyzed=len(items), flagged=sum(1 for i in items if i["anomaly"]))


def latest_anomaly(uid):
    """Derniere heure analysee (mise en cache 60 s) : sert a la vue STEG. None si l'IA est indisponible ou sans donnees."""
    k = (uid, "anom")
    hit = _cache.get(k)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    try:
        r = analyze_anomalies(uid, 3)
        val = r["latest"] if r else None
    except Exception:
        val = None
    _cache[k] = (time.time(), val)
    return val


# ---------- meteo (Open-Meteo, sur le chef-lieu du gouvernorat : aucune donnee personnelle envoyee) ----------
def weather_for(governorate):
    hit = _weather.get(governorate)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    la, lo = lighting.GOV_CENTERS[governorate]
    url = ("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(dict(
        latitude=la, longitude=lo, hourly="temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,wind_speed_10m",
        past_days=14, forecast_days=2, timezone="Africa/Tunis")))
    try:
        data = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "KizunaWatt/1.0"}), timeout=15).read())["hourly"]
    except Exception:
        return None
    out = {}
    for i, t in enumerate(data["time"]):
        ts = int(datetime.strptime(t, "%Y-%m-%dT%H:%M").replace(tzinfo=TZ).timestamp())
        out[ts] = dict(temp_c=data["temperature_2m"][i], humidity=data["relative_humidity_2m"][i], feels_like=data["apparent_temperature"][i],
                       precipitation_mm=data["precipitation"][i], wind_speed=data["wind_speed_10m"][i])
    _weather[governorate] = (time.time(), out)
    return out


# ---------- XGBoost : prevision horaire ----------
NAN = float("nan")


def _stat(vals, minimum):
    v = [x for x in vals if x is not None]
    if len(v) < minimum:
        return NAN, NAN
    m = sum(v) / len(v)
    return m, math.sqrt(sum((x - m) ** 2 for x in v) / len(v))


def build_features(H, W, T):
    """Variables du modele pour l'heure T (debut d'heure, epoch). H : {heure: kW}, W : meteo par heure. Retourne un dict (NaN si inconnu)."""
    g = lambda ts: H.get(ts)
    f = {}
    for k in (24, 48, 72, 168, 336, 504):
        f[f"lag_{k}"] = H.get(T - k * 3600, NAN)
    f["mean_3d"], _ = _stat([g(T - 3600 * (24 + i)) for i in range(72)], 36)
    f["mean_7d"], f["std_7d"] = _stat([g(T - 3600 * (24 + i)) for i in range(168)], 84)
    f["same_hour_mean_4w"], f["same_hour_std_4w"] = _stat([g(T - 3600 * 168 * j) for j in range(1, 5)], 3)
    for lag in (24, 168):
        w = (W or {}).get(T - lag * 3600) or {}
        for name in ("temp_c", "humidity", "feels_like", "precipitation_mm", "wind_speed"):
            f[f"{name}_lag{lag}"] = w.get(name, NAN)
    dt = datetime.fromtimestamp(T, TZ)
    f.update(hour_sin=math.sin(2 * math.pi * dt.hour / 24), hour_cos=math.cos(2 * math.pi * dt.hour / 24),
             dow_sin=math.sin(2 * math.pi * dt.weekday() / 7), dow_cos=math.cos(2 * math.pi * dt.weekday() / 7),
             month_sin=math.sin(2 * math.pi * dt.month / 12), month_cos=math.cos(2 * math.pi * dt.month / 12), is_weekend=int(dt.weekday() >= 5))
    return f


def _predict(hours, H, W):
    import numpy as np
    import xgboost as xgb
    names = _S["xgb"].feature_names
    X = np.array([[build_features(H, W, T).get(n, NAN) for n in names] for T in hours], dtype=float)
    return _S["xgb"].predict(xgb.DMatrix(X, feature_names=names)), X


def forecast(uid, governorate):
    """Prevision des 24 prochaines heures + erreur observee sur les 24 dernieres. Retourne toujours un dict avec `status`."""
    if not load() or _S["xgb"] is None:
        return dict(status="unavailable", message=_S["error"] or "modele XGBoost indisponible")
    ser = db.hourly_series(uid, 24 * 35)
    H = {h: v["mean_w"] / 1000 for h, v in ser.items() if v["n"] >= MIN_SAMPLES}
    span = (max(H) - min(H)) / 86400 if len(H) > 1 else 0
    now_h = int(time.time()) // 3600 * 3600
    # il faut au moins une valeur a T-24 h et T-168 h pour chaque heure a prevoir
    ready = sum(1 for k in range(1, 25) if (now_h + k * 3600 - 24 * 3600) in H and (now_h + k * 3600 - 168 * 3600) in H)
    if ready < 12:
        return dict(status="insufficient", have_days=round(span, 1), need_days=NEED_DAYS,
                    message=f"Historique insuffisant : {span:.1f} jour(s) de mesures, au moins 8 jours (idealement {NEED_DAYS}) sont necessaires.")
    W = weather_for(governorate)
    if not W:
        return dict(status="no_weather", message="Meteo indisponible (connexion internet requise).", have_days=round(span, 1), need_days=NEED_DAYS)
    hours = [now_h + k * 3600 for k in range(1, 25)]
    pred, X = _predict(hours, H, W)
    out = [dict(ts=T, hour=datetime.fromtimestamp(T, TZ).strftime("%d/%m %Hh"), kw=round(max(0.0, float(p)), 3)) for T, p in zip(hours, pred)]
    # erreur observee : on prevoit les heures deja mesurees et on compare
    past = [T for T in sorted(H) if T <= now_h - 3600 and T >= now_h - 24 * 3600]
    err = None
    if len(past) >= 6:
        p2, _ = _predict(past, H, W)
        diffs = [abs(max(0.0, float(p)) - H[T]) for T, p in zip(past, p2)]
        mean_actual = sum(H[T] for T in past) / len(past)
        err = dict(hours=len(past), mae_w=round(1000 * sum(diffs) / len(diffs)), mean_actual_w=round(1000 * mean_actual),
                   mape_pct=round(100 * sum(diffs) / max(1e-9, sum(H[T] for T in past)), 1))
    nan_share = float(np_isnan_share(X))
    return dict(status="ok", items=out, backtest=err, have_days=round(span, 1), need_days=NEED_DAYS, missing_inputs_pct=round(100 * nan_share), experimental=True)


def np_isnan_share(X):
    import numpy as np
    return np.isnan(X).mean()


def analysis(uid, governorate):
    """Tout ce que l'espace client affiche (mise en cache 60 s)."""
    k = (uid, "full")
    hit = _cache.get(k)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    st = status()
    val = dict(status=st)
    if st["available"]:
        try:
            val["anomaly"] = analyze_anomalies(uid)
        except Exception as e:
            val["anomaly"] = dict(error=str(e)[:120])
        try:
            val["forecast"] = forecast(uid, governorate)
        except Exception as e:
            val["forecast"] = dict(status="error", message=str(e)[:160])
    _cache[k] = (time.time(), val)
    return val


# ---------- Modele 3 : demande d'electricite nationale mensuelle (Ridge, variation sur 12 mois) ----------
_D = dict(tried=False, pack=None, error=None)
DEMAND_HYPOTHESES = [
    "E_t prevu = E_(t-12) x exp(Ridge(mom3, dcdd)) (formule indiquee dans le fichier du modele).",
    "mom3 = moyenne de la variation annuelle log(E_s / E_(s-12)) sur les 3 mois s = t-14, t-13, t-12 (hypothese : meme moyenne que la cible dans les donnees d'entrainement).",
    "dcdd = CDD(t) - CDD(t-12), avec CDD(T) = max(0, T - seuil), seuil lu dans le fichier (31 °C) et T = temperature MAXIMALE moyenne du mois (hypothese).",
    "La temperature du mois prevu vient des previsions climatiques des modeles (granularite mois, « Tunisie », colonne temp_max_c).",
]


def load_demand():
    with _lock:
        if _D["tried"]:
            return _D["pack"] is not None
        _D["tried"] = True
        try:
            import numpy  # noqa: F401
            import joblib
        except ImportError:
            _D["error"] = "bibliotheques manquantes : pip install -r requirements.txt"
            return False
        try:
            man = json.load(open(os.path.join(ROOT, "MANIFEST.json"), encoding="utf-8"))["files"]
            p = os.path.join(ROOT, FILES["demand"])
            if man.get(FILES["demand"]) != sha256(p):
                _D["error"] = "model3_reel.pkl modifie ou non verifie : refus de le charger (python manage.py trust-models)"
                return False
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                pack = joblib.load(p)
            if not {"model", "features", "cdd_threshold"} <= set(pack):
                raise ValueError("contenu inattendu")
            _D["pack"] = pack
        except Exception as e:
            _D["error"] = f"model3_reel.pkl illisible : {e}"
            return False
        return True


def _month_add(p, n):
    idx = int(p[:4]) * 12 + int(p[5:7]) - 1 + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def demand_forecast():
    """Prevision de l'energie des 12 mois suivants, par scenario climatique, + verification sur l'historique."""
    from . import forecasts
    import numpy as np
    if not load_demand():
        return dict(status="unavailable", message=_D["error"] or "modele indisponible")
    pack = _D["pack"]
    thr = float(pack["cdd_threshold"])
    rows = forecasts.list_energy()
    E = {r["period"]: r["energy"] for r in rows}
    T = {r["period"]: r["temp_max_c"] for r in rows if r["temp_max_c"] is not None}
    if len(E) < 27:
        return dict(status="insufficient", months=len(E), need_months=27,
                    message=f"Historique insuffisant : {len(E)} mois importes, au moins 27 sont necessaires (E de t-26 a t-12 pour calculer la tendance).")
    last = max(E)
    cdd = lambda x: max(0.0, x - thr)

    def mom3(origin):
        v = []
        for s in (_month_add(origin, -2), _month_add(origin, -1), origin):
            a, b = E.get(s), E.get(_month_add(s, -12))
            if a and b:
                v.append(math.log(a / b))
        return sum(v) / 3 if len(v) == 3 else None

    def predict(t, tt):
        """tt : temperature maximale du mois t (ou None -> dcdd = 0). Retourne l'energie prevue ou None."""
        base, m = E.get(_month_add(t, -12)), mom3(_month_add(t, -12))
        if base is None or m is None:
            return None
        prev_t = T.get(_month_add(t, -12))
        if tt is None:
            dc = 0.0
        elif prev_t is None:
            return None
        else:
            dc = cdd(tt) - cdd(prev_t)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            y = float(pack["model"].predict(np.array([[m, dc]]))[0])
        return base * math.exp(y), dc

    targets = [_month_add(last, k) for k in range(1, 13)]
    scenarios = []
    pts = [(t, predict(t, None)) for t in targets]
    scenarios.append(dict(name="Sans prevision climatique (meme climat que l'an dernier)", climate=False,
                          points=[dict(period=t, energy=round(p[0], 2), dcdd=round(p[1], 2)) for t, p in pts if p]))
    for g in forecasts.get_forecasts(forecasts.NATIONAL, "month"):
        tmap = {i["period"]: i["temp_max_c"] for i in g["items"] if i["temp_max_c"] is not None}
        out = []
        for t in targets:
            if t in tmap:
                p = predict(t, tmap[t])
                if p:
                    out.append(dict(period=t, energy=round(p[0], 2), dcdd=round(p[1], 2), temp_max_c=tmap[t]))
        if out:
            scenarios.append(dict(name=f"{g['model']}" + (f" · scenario {g['scenario']}" if g["scenario"] else ""), climate=True, points=out))
    # verification : on « prevoit » les mois deja connus (en partie vus a l'entrainement : resultat optimiste)
    bt = []
    for t in sorted(E):
        tt = T.get(t)
        p = predict(t, tt) if tt is not None else None
        if p:
            bt.append((t, E[t], p[0]))
    err = None
    if len(bt) >= 6:
        ape = [abs(p - a) / a for _, a, p in bt]
        err = dict(months=len(bt), mape_pct=round(100 * sum(ape) / len(ape), 1), last=[dict(period=t, actual=round(a, 1), predicted=round(p, 1)) for t, a, p in bt[-12:]])
    return dict(status="ok", unit_note="meme unite que votre fichier d'historique", threshold_c=thr, months=len(E), last_period=last,
                history=[dict(period=p, energy=E[p]) for p in sorted(E)][-72:], scenarios=scenarios, backtest=err, hypotheses=DEMAND_HYPOTHESES, experimental=True)
