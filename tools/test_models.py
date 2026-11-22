"""Verifie les modeles d'IA de l'equipe : python tools/test_models.py

Ce script verifie que les modeles (1) sont intacts, (2) se chargent, (3) ont les bonnes entrees, (4) repondent de facon COHERENTE
a des cas simples. Les cas d'entree ci-dessous sont des cas de test (pas des donnees reelles).
Il NE mesure PAS la precision : pour cela il faut les resultats de l'equipe sur un jeu de test reel (MAE/MAPE, rappel...).
"""
import math
import os
import sys
import warnings

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")
OK, KO, WARN = "[OK]  ", "[ECHEC]", "[ATTENTION]"
fails = 0


def check(cond, ok, ko):
    global fails
    print((OK + ok) if cond else (KO + " " + ko))
    if not cond:
        fails += 1
    return cond


print("== 0. Bibliotheques ==")
try:
    import numpy as np
    import sklearn
    import xgboost
    import joblib
    print(OK + f"numpy {np.__version__}, scikit-learn {sklearn.__version__}, xgboost {xgboost.__version__}")
except ImportError as e:
    print(KO, e, "\n   -> pip install -r requirements.txt")
    sys.exit(1)
from kizuna import ai

print("\n== 1. Integrite des fichiers (empreintes SHA-256) ==")
import json
man = json.load(open(os.path.join(ai.ROOT, "MANIFEST.json"), encoding="utf-8"))["files"]
for k, fn in ai.FILES.items():
    p = os.path.join(ai.ROOT, fn)
    if not os.path.exists(p):
        print(WARN, fn, "absent"); continue
    check(man.get(fn) == ai.sha256(p), f"{fn} conforme a MANIFEST.json", f"{fn} modifie depuis la verification (python manage.py trust-models si c'est voulu)")

print("\n== 2. Chargement par l'application ==")
st = ai.status()
check(st["available"], "modeles charges", f"indisponible : {st['error']}")
if st["error"]:
    print(WARN, st["error"])
for w in st["warnings"]:
    print(WARN, w)

print("\n== 3. Isolation Forest (heures anormales) ==")
iso = ai._S["iso"]
names = ai._S["names"]
print("   variables d'entree :", names)
if iso is not None:
    print("   type :", type(iso).__name__, "| arbres :", getattr(iso, "n_estimators", "?"), "| contamination :", getattr(iso, "contamination", "?"),
          "| n_features_in_ :", getattr(iso, "n_features_in_", "?"))
    check(getattr(iso, "n_features_in_", len(names)) == len(names), "nombre de variables = model_features.pkl", "nombre de variables different de model_features.pkl")

    def row(mean_kw, max_kw, std_kw, hour, dow):
        d = dict(power_mean=mean_kw, power_max=max_kw, power_std=std_kw, hour=hour, day_of_week=dow, is_weekend=int(dow >= 5))
        return [d[n] for n in names]
    cases = [("soiree habituelle (0,6 kW, 19 h)", row(0.6, 1.0, 0.2, 19, 2), False),
             ("nuit calme (0,15 kW, 3 h)", row(0.15, 0.25, 0.05, 3, 2), False),
             ("midi week-end (0,5 kW)", row(0.5, 0.9, 0.2, 12, 6), False),
             ("pic extreme (9 kW, 3 h du matin)", row(9.0, 12.0, 2.0, 3, 2), True),
             ("charge de 5 kW a 4 h du matin (a titre indicatif)", row(5.0, 6.0, 0.3, 4, 1), None)]
    X = np.array([c[1] for c in cases], dtype=float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pred, dec = iso.predict(X), iso.decision_function(X)
    for (label, _, expect_anom), p, d in zip(cases, pred, dec):
        flagged = p == -1
        if expect_anom is None:
            print(f"   [INFO]   {label} -> {'anormal' if flagged else 'normal'} (score {ai.anomaly_score(d)}/100) : le seuil du modele depend de ses donnees d'entrainement")
            continue
        check(flagged == expect_anom, f"{label} -> {'anormal' if flagged else 'normal'} (score {ai.anomaly_score(d)}/100)",
              f"{label} -> {'anormal' if flagged else 'normal'} mais on attendait {'anormal' if expect_anom else 'normal'}")
    # monotonie : a heure fixe, plus la puissance monte, moins c'est « normal »
    sweep = np.array([row(k, k * 1.4, k * 0.3, 14, 2) for k in (0.3, 1, 2, 4, 8)], dtype=float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = iso.decision_function(sweep)
    print("   score de normalite quand la puissance monte (0,3 / 1 / 2 / 4 / 8 kW) :", [round(float(x), 3) for x in ds])
    check(ds[-1] < ds[0], "une puissance tres elevee est jugee moins normale qu'une faible", "pas de lien logique entre puissance et anormalite")
    print("   NB : ce que le modele considere « habituel » depend de ses donnees d'entrainement (foyer ? agrege ?) : a confirmer avec l'equipe.")

print("\n== 4. XGBoost (prevision horaire du lendemain) ==")
bst = ai._S["xgb"]
if bst is not None:
    fn = bst.feature_names
    print(f"   {len(fn)} variables :", ", ".join(fn))
    nan = float("nan")

    def predict(**over):
        base = {n: nan for n in fn}
        base.update(lag_24=0.6, lag_48=0.6, lag_72=0.6, lag_168=0.6, lag_336=0.6, lag_504=0.6, mean_3d=0.6, mean_7d=0.6, std_7d=0.2, same_hour_mean_4w=0.6, same_hour_std_4w=0.1,
                    temp_c_lag24=25, humidity_lag24=60, feels_like_lag24=25, precipitation_mm_lag24=0, wind_speed_lag24=10,
                    temp_c_lag168=25, humidity_lag168=60, feels_like_lag168=25, precipitation_mm_lag168=0, wind_speed_lag168=10,
                    hour_sin=math.sin(2 * math.pi * 19 / 24), hour_cos=math.cos(2 * math.pi * 19 / 24), dow_sin=0.0, dow_cos=1.0, month_sin=0.5, month_cos=-0.87, is_weekend=0)
        base.update(over)
        X = np.array([[base[n] for n in fn]], dtype=float)
        return float(bst.predict(xgboost.DMatrix(X, feature_names=fn))[0])
    p0 = predict()
    check(math.isfinite(p0), f"prediction de reference : {p0:.3f} (unite du modele, kW si la cible est kW : hypothese)", "prediction non finie")
    lo, hi = predict(lag_24=0.2, lag_168=0.2, mean_3d=0.2, mean_7d=0.2, same_hour_mean_4w=0.2, lag_48=0.2, lag_72=0.2), predict(lag_24=3, lag_168=3, mean_3d=3, mean_7d=3, same_hour_mean_4w=3, lag_48=3, lag_72=3)
    print(f"   historique faible (0,2) -> {lo:.3f} | historique fort (3) -> {hi:.3f}")
    check(hi > lo, "la prevision suit le niveau de consommation passee", "la prevision ne suit pas la consommation passee : verifier les variables")
    t_cold, t_hot = predict(temp_c_lag24=12, feels_like_lag24=11), predict(temp_c_lag24=42, feels_like_lag24=46)
    print(f"   temperature 12 C -> {t_cold:.3f} | 42 C -> {t_hot:.3f}  (en ete, la climatisation devrait faire monter la consommation)")
    if not (t_hot > t_cold):
        print(WARN, "la prevision ne monte pas avec la chaleur : a discuter avec l'equipe (effet meteo faible ou autre sens)")
    print(f"   16 h -> {predict(hour_sin=math.sin(2*math.pi*16/24), hour_cos=math.cos(2*math.pi*16/24)):.3f} | 3 h -> {predict(hour_sin=math.sin(2*math.pi*3/24), hour_cos=math.cos(2*math.pi*3/24), lag_24=0.15):.3f}")
    allfinite = all(math.isfinite(predict(lag_24=v)) for v in (0, 0.5, 2, 6))
    check(allfinite, "reponses finies sur plusieurs entrees", "reponse non finie sur certaines entrees")
    miss = predict(**{n: nan for n in fn if n.startswith("lag_5") or n.startswith("lag_3")})
    check(math.isfinite(miss), f"accepte des variables manquantes (NaN) : {miss:.3f}", "echoue avec des variables manquantes")

print("\n== 5. Modele 3 (demande nationale mensuelle) ==")
if ai.load_demand():
    pack = ai._D["pack"]
    print("   contenu :", sorted(pack.keys()))
    print("   variables :", pack["features"], "| seuil CDD :", pack["cdd_threshold"])
    mdl = pack["model"]
    last = mdl.steps[-1][1] if hasattr(mdl, "steps") else mdl
    print("   type :", type(mdl).__name__, "->", type(last).__name__, "| etapes :", [n for n, _ in getattr(mdl, "steps", [])], "| coef :", [round(float(x), 4) for x in getattr(last, "coef_", [])], "| intercept :", round(float(getattr(last, "intercept_", 0)), 4))
    feats = pack["features"]
    def pm(mom3, dcdd):
        d = dict(mom3=mom3, dcdd=dcdd)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return float(mdl.predict(np.array([[d.get(f, 0.0) for f in feats]], dtype=float))[0])
    a, b = pm(0.03, 0), pm(0.03, 3)
    print(f"   croissance annuelle prevue : dcdd=0 -> {a:+.3%} | dcdd=+3 C -> {b:+.3%}")
    check(math.isfinite(a) and math.isfinite(b), "reponses finies", "reponse non finie")
    if not b > a:
        print(WARN, "un mois plus chaud ne fait pas monter la demande : a confirmer avec l'equipe")
    else:
        print(OK + "un mois plus chaud fait monter la demande (coherent)")
    check(abs(pm(0.0, 0.0)) < 0.5, "la variation annuelle sans tendance ni chaleur reste plausible (< 50 %)", "variation annuelle aberrante")
else:
    print(KO, ai._D["error"]); fails += 1

print("\n== Resume ==")
print("Tout est coherent." if not fails else f"{fails} verification(s) en echec : voir ci-dessus.")
print("Rappel : ceci prouve que les modeles chargent et reagissent de facon logique, pas qu'ils sont PRECIS.")
sys.exit(1 if fails else 0)
