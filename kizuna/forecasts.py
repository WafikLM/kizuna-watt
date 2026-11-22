"""Previsions meteo et climatiques generees par les modeles d'IA de l'equipe.

L'application ne produit AUCUNE prevision elle-meme : elle recoit les sorties des modeles (par l'API ou par import CSV)
et les affiche. Tant qu'aucun modele n'a envoye de resultats, les ecrans restent vides.
"""
import csv
import io
import re
import secrets
import time

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  key_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  last_push INTEGER
);
CREATE TABLE IF NOT EXISTS forecasts (
  model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
  governorate TEXT NOT NULL,
  granularity TEXT NOT NULL,
  period TEXT NOT NULL,
  scenario TEXT NOT NULL DEFAULT '',
  temp_mean_c REAL NOT NULL, temp_min_c REAL, temp_max_c REAL,
  precip_mm REAL, humidity_pct REAL, confidence REAL,
  version TEXT, created_at INTEGER NOT NULL,
  PRIMARY KEY (model_id, governorate, granularity, period, scenario)
);
CREATE INDEX IF NOT EXISTS idx_forecasts ON forecasts(governorate, granularity, period);
CREATE TABLE IF NOT EXISTS national_energy (
  period TEXT PRIMARY KEY, energy REAL NOT NULL, temp_max_c REAL, imported_at INTEGER NOT NULL
);
"""
NATIONAL = "Tunisie"
MAX_ITEMS = 5000
CSV_MODEL = "import-csv"
PERIOD_RE = {"day": r"\d{4}-\d{2}-\d{2}", "month": r"\d{4}-\d{2}", "year": r"\d{4}"}


def init(c=None):
    own = c is None
    c = c or db.conn()
    c.executescript(SCHEMA)
    c.commit()
    if own:
        c.close()


# ---------- modeles (cles d'API) ----------
def create_model(name):
    mid = "MD-" + secrets.token_hex(3).upper()
    key = secrets.token_urlsafe(24)
    c = db.conn()
    try:
        if c.execute("SELECT COUNT(*) FROM models").fetchone()[0] >= 30:
            return None, None
        c.execute("INSERT INTO models(id,name,key_hash,created_at) VALUES (?,?,?,?)", (mid, (name or "Modele").strip()[:60], db._th(key), int(time.time())))
        c.commit()
        return mid, key
    finally:
        c.close()


def model_auth(mid, key):
    c = db.conn()
    r = c.execute("SELECT id, key_hash FROM models WHERE id=?", (mid or "",)).fetchone()
    c.close()
    return r["id"] if r and db.hmac.compare_digest(r["key_hash"], db._th(key or "")) else None


def list_models():
    c = db.conn()
    rows = c.execute("""SELECT m.id, m.name, m.created_at, m.last_push,
                        (SELECT COUNT(*) FROM forecasts f WHERE f.model_id = m.id) AS n,
                        (SELECT f.version FROM forecasts f WHERE f.model_id = m.id ORDER BY f.created_at DESC LIMIT 1) AS version
                        FROM models m ORDER BY m.created_at""").fetchall()
    c.close()
    return [dict(r) for r in rows]


def delete_model(mid):
    c = db.conn()
    c.execute("DELETE FROM models WHERE id=?", (mid,))
    c.commit()
    c.close()


def _csv_model_id():
    """Modele « import CSV » (cree a la demande) pour les fichiers importes depuis l'interface STEG."""
    c = db.conn()
    r = c.execute("SELECT id FROM models WHERE name=?", (CSV_MODEL,)).fetchone()
    if r:
        c.close()
        return r["id"]
    mid = "MD-CSV"
    c.execute("INSERT OR IGNORE INTO models(id,name,key_hash,created_at) VALUES (?,?,?,?)", (mid, CSV_MODEL, db._th(secrets.token_hex(16)), int(time.time())))
    c.commit()
    c.close()
    return mid


# ---------- validation ----------
def _num(v, lo, hi, field, required=False):
    if v is None or v == "":
        if required:
            raise ValueError(f"{field} manquant")
        return None
    x = float(v)
    if not lo <= x <= hi:
        raise ValueError(f"{field} hors limites ({lo} a {hi})")
    return x


def validate_item(it):
    """Retourne l'element nettoye, ou leve ValueError avec un message clair."""
    gov = str(it.get("governorate", "")).strip()
    if gov != NATIONAL and gov not in db.GOVERNORATES:
        raise ValueError(f"governorate inconnu : {gov!r}")
    gran = str(it.get("granularity", "")).strip().lower()
    if gran not in PERIOD_RE:
        raise ValueError("granularity doit etre day, month ou year")
    period = str(it.get("period", "")).strip()
    if not re.fullmatch(PERIOD_RE[gran], period):
        raise ValueError(f"period invalide pour {gran} : {period!r}")
    mean = _num(it.get("temp_mean_c"), -30, 60, "temp_mean_c", True)
    tmin = _num(it.get("temp_min_c"), -40, 70, "temp_min_c")
    tmax = _num(it.get("temp_max_c"), -40, 70, "temp_max_c")
    if tmin is not None and tmax is not None and tmin > tmax:
        raise ValueError("temp_min_c superieur a temp_max_c")
    if (tmin is not None and tmin > mean) or (tmax is not None and tmax < mean):
        raise ValueError("temp_mean_c doit etre entre temp_min_c et temp_max_c")
    return dict(governorate=gov, granularity=gran, period=period, scenario=str(it.get("scenario") or "").strip()[:30],
                temp_mean_c=mean, temp_min_c=tmin, temp_max_c=tmax,
                precip_mm=_num(it.get("precip_mm"), 0, 3000, "precip_mm"),
                humidity_pct=_num(it.get("humidity_pct"), 0, 100, "humidity_pct"),
                confidence=_num(it.get("confidence"), 0, 1, "confidence"))


def upsert(model_id, items, version=""):
    """Valide TOUT avant d'ecrire : si une ligne est invalide, rien n'est enregistre. Retourne (nombre, erreur)."""
    if not isinstance(items, list) or not items:
        return 0, "items doit etre une liste non vide"
    if len(items) > MAX_ITEMS:
        return 0, f"maximum {MAX_ITEMS} lignes par envoi"
    clean = []
    for i, it in enumerate(items):
        try:
            clean.append(validate_item(it))
        except (ValueError, TypeError, AttributeError) as e:
            return 0, f"ligne {i + 1} : {e}"
    now = int(time.time())
    c = db.conn()
    for x in clean:
        c.execute("""INSERT INTO forecasts(model_id,governorate,granularity,period,scenario,temp_mean_c,temp_min_c,temp_max_c,precip_mm,humidity_pct,confidence,version,created_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(model_id,governorate,granularity,period,scenario) DO UPDATE SET
                       temp_mean_c=excluded.temp_mean_c, temp_min_c=excluded.temp_min_c, temp_max_c=excluded.temp_max_c,
                       precip_mm=excluded.precip_mm, humidity_pct=excluded.humidity_pct, confidence=excluded.confidence,
                       version=excluded.version, created_at=excluded.created_at""",
                  (model_id, x["governorate"], x["granularity"], x["period"], x["scenario"], x["temp_mean_c"], x["temp_min_c"], x["temp_max_c"],
                   x["precip_mm"], x["humidity_pct"], x["confidence"], str(version or "")[:30], now))
    c.execute("UPDATE models SET last_push=? WHERE id=?", (now, model_id))
    c.commit()
    c.close()
    return len(clean), None


def import_csv(text):
    """CSV : governorate,period,granularity,temp_mean_c[,temp_min_c,temp_max_c,precip_mm,humidity_pct,confidence,scenario]."""
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    if rows and "temp_mean_c" not in rows[0]:
        return 0, "colonne temp_mean_c absente (voir ml/API_PREVISIONS.md)"
    return upsert(_csv_model_id(), rows, "csv")


# ---------- lecture ----------
def get_forecasts(governorate, granularity=None):
    q = """SELECT f.model_id, m.name AS model_name, f.governorate, f.granularity, f.period, f.scenario, f.temp_mean_c, f.temp_min_c,
                  f.temp_max_c, f.precip_mm, f.humidity_pct, f.confidence, f.version, f.created_at
           FROM forecasts f JOIN models m ON m.id = f.model_id WHERE f.governorate=?"""
    args = [governorate]
    if granularity:
        q += " AND f.granularity=?"
        args.append(granularity)
    c = db.conn()
    rows = c.execute(q + " ORDER BY f.model_id, f.scenario, f.period", args).fetchall()
    c.close()
    groups = {}
    for r in rows:
        k = (r["model_id"], r["scenario"], r["granularity"])
        g = groups.setdefault(k, dict(model_id=r["model_id"], model=r["model_name"], scenario=r["scenario"], granularity=r["granularity"],
                                      version=r["version"], updated=r["created_at"], items=[]))
        g["updated"] = max(g["updated"], r["created_at"])
        g["items"].append({k2: r[k2] for k2 in ("period", "temp_mean_c", "temp_min_c", "temp_max_c", "precip_mm", "humidity_pct", "confidence")})
    return list(groups.values())


# ---------- energie electrique nationale mensuelle (historique fourni par l'exploitant) ----------
def import_energy_csv(text):
    """CSV : period (AAAA-MM), energy (ou energy_gwh) et, facultatif, temp_max_c (temperature maximale moyenne du mois, °C).
    Tout est valide avant d'ecrire. Retourne (nombre de mois, erreur)."""
    rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows:
        return 0, "fichier vide"
    keys = {k.strip().lower(): k for k in rows[0]}
    ecol = keys.get("energy") or keys.get("energy_gwh") or keys.get("energie")
    if "period" not in keys or not ecol:
        return 0, "colonnes requises : period (AAAA-MM) et energy (ou energy_gwh)"
    if len(rows) > 2000:
        return 0, "maximum 2000 mois"
    clean = {}
    for i, r in enumerate(rows, 1):
        try:
            period = str(r[keys["period"]]).strip()
            if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period):
                raise ValueError("period au format AAAA-MM")
            e = float(str(r[ecol]).replace(",", "."))
            if not 0 < e < 1e9:
                raise ValueError("energy doit etre positive")
            t = r.get(keys.get("temp_max_c", ""), "")
            tv = None if t in (None, "") else float(str(t).replace(",", "."))
            if tv is not None and not -10 <= tv <= 60:
                raise ValueError("temp_max_c entre -10 et 60")
        except (ValueError, TypeError, KeyError) as ex:
            return 0, f"ligne {i + 1} : {ex}"
        clean[period] = (e, tv)
    now = int(time.time())
    c = db.conn()
    for p, (e, tv) in clean.items():
        c.execute("INSERT INTO national_energy VALUES (?,?,?,?) ON CONFLICT(period) DO UPDATE SET energy=excluded.energy, temp_max_c=excluded.temp_max_c, imported_at=excluded.imported_at", (p, e, tv, now))
    c.commit()
    c.close()
    return len(clean), None


def list_energy():
    c = db.conn()
    rows = [dict(r) for r in c.execute("SELECT period, energy, temp_max_c FROM national_energy ORDER BY period")]
    c.close()
    return rows


def clear_energy():
    c = db.conn()
    c.execute("DELETE FROM national_energy")
    c.commit()
    c.close()


# ---------- meteo reelle (Open-Meteo) : secours quand aucun modele de l'equipe n'a envoye de previsions ----------
_OM = {}


def open_meteo_group(governorate):
    """Previsions meteo REELLES sur 16 jours pour le chef-lieu du gouvernorat (Open-Meteo, sans cle, aucune donnee personnelle envoyee).
    Retourne un groupe au meme format que get_forecasts(), ou None si le gouvernorat est inconnu / le service injoignable."""
    import json
    import urllib.parse
    import urllib.request
    from . import lighting
    if governorate not in lighting.GOV_CENTERS:
        return None
    hit = _OM.get(governorate)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    la, lo = lighting.GOV_CENTERS[governorate]
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(dict(
        latitude=la, longitude=lo, timezone="Africa/Tunis", forecast_days=16,
        daily="temperature_2m_max,temperature_2m_min,temperature_2m_mean,precipitation_sum,relative_humidity_2m_mean"))
    try:
        d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "KizunaWatt/1.0"}), timeout=15).read())["daily"]
    except Exception:
        return hit[1] if hit else None
    items = [dict(period=d["time"][i], temp_mean_c=d["temperature_2m_mean"][i], temp_min_c=d["temperature_2m_min"][i], temp_max_c=d["temperature_2m_max"][i],
                  precip_mm=d["precipitation_sum"][i], humidity_pct=d["relative_humidity_2m_mean"][i], confidence=None) for i in range(len(d["time"])) if d["temperature_2m_mean"][i] is not None]      # les derniers jours peuvent etre incomplets : on les ignore
    g = dict(model_id=0, model="Open-Meteo : prevision meteo reelle", scenario="", granularity="day", version="", updated=int(time.time()), source="open-meteo", items=items)
    _OM[governorate] = (time.time(), g)
    return g
