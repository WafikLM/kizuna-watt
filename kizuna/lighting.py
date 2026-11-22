"""Eclairage public automatise : luminaires, consigne de puissance, detection du gaspillage.

AUCUNE donnee fictive : tout ce qui est affiche vient des controleurs de luminaires (rapports reels) ou de la politique
configuree par l'exploitant. Les predictions de frequentation viennent des modeles d'IA de l'equipe (comme les previsions meteo).

Principe :
  - chaque luminaire a un controleur qui envoie son etat (niveau, puissance, luminosite, presence) et RECOIT la consigne ;
  - la consigne = regles (soleil, horaires, luminosite, presence) + prediction de frequentation d'un modele d'IA ;
  - le gaspillage = energie consommee au-dela de la consigne + allumage en plein jour + luminaire qui ne suit pas la consigne.
"""
import json
import math
import re
import secrets
import time
from datetime import date, datetime, timedelta, timezone

from . import db, forecasts

TZ = timezone(timedelta(hours=1))            # Tunisie : UTC+1 toute l'annee
OFFLINE_SECONDS = 600                         # controleur muet depuis 10 min
KEEP_SECONDS = 7 * 24 * 3600
LAMP_TYPES = ["LED", "Sodium haute pression", "Mercure", "Halogenure metallique", "Fluocompacte"]
MAX_LAMPS = 20000

# Chefs-lieux des 24 gouvernorats (coordonnees approximatives) : sert a rattacher un point au gouvernorat le plus proche
GOV_CENTERS = {
    "Tunis": (36.8065, 10.1815), "Ariana": (36.8625, 10.1956), "Ben Arous": (36.7531, 10.2189), "La Manouba": (36.8089, 10.0863),
    "Nabeul": (36.4513, 10.7357), "Zaghouan": (36.4029, 10.1429), "Bizerte": (37.2744, 9.8739), "Béja": (36.7256, 9.1817),
    "Jendouba": (36.5011, 8.7802), "Le Kef": (36.1742, 8.7049), "Siliana": (36.0850, 9.3708), "Sousse": (35.8256, 10.6369),
    "Monastir": (35.7643, 10.8113), "Mahdia": (35.5047, 11.0622), "Sfax": (34.7406, 10.7603), "Kairouan": (35.6781, 10.0963),
    "Kasserine": (35.1676, 8.8365), "Sidi Bouzid": (35.0382, 9.4849), "Gabès": (33.8815, 10.0982), "Médenine": (33.3549, 10.5055),
    "Tataouine": (32.9297, 10.4518), "Gafsa": (34.4250, 8.7842), "Tozeur": (33.9197, 8.1335), "Kébili": (33.7044, 8.9690)}


def nearest_governorate(lat, lng):
    """Gouvernorat dont le chef-lieu est le plus proche (approximation : pas de limites administratives exactes)."""
    return min(GOV_CENTERS, key=lambda g: (GOV_CENTERS[g][0] - lat) ** 2 + ((GOV_CENTERS[g][1] - lng) * math.cos(math.radians(lat))) ** 2)

SCHEMA = """
CREATE TABLE IF NOT EXISTS lamps (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, zone TEXT, governorate TEXT NOT NULL, delegation TEXT,
  lat REAL NOT NULL, lng REAL NOT NULL, rated_w INTEGER NOT NULL, lamp_type TEXT NOT NULL,
  key_hash TEXT NOT NULL, created_at INTEGER NOT NULL,
  last_seen INTEGER, last_level REAL, last_power_w REAL, last_lux REAL, last_motion INTEGER
);
CREATE TABLE IF NOT EXISTS lamp_readings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, lamp_id TEXT NOT NULL REFERENCES lamps(id) ON DELETE CASCADE,
  ts INTEGER NOT NULL, level_pct REAL, power_w REAL, lux REAL, motion INTEGER, target_pct REAL
);
CREATE INDEX IF NOT EXISTS idx_lamp_readings ON lamp_readings(lamp_id, ts);
CREATE TABLE IF NOT EXISTS lamp_daily (
  lamp_id TEXT NOT NULL REFERENCES lamps(id) ON DELETE CASCADE, day TEXT NOT NULL,
  kwh REAL NOT NULL DEFAULT 0, baseline_kwh REAL NOT NULL DEFAULT 0, waste_kwh REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (lamp_id, day)
);
CREATE TABLE IF NOT EXISTS streets (
  id TEXT PRIMARY KEY, name TEXT, name_ar TEXT, ref TEXT, kind TEXT NOT NULL, kind_label TEXT, length_m INTEGER,
  gov TEXT, lat REAL, lng REAL, geometry TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_streets_name ON streets(name);
CREATE TABLE IF NOT EXISTS lighting_policy (id INTEGER PRIMARY KEY CHECK (id = 1), json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lamp_predictions (
  model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE, target TEXT NOT NULL, hour TEXT NOT NULL,
  presence REAL NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY (model_id, target, hour)
);
"""

DEFAULT_POLICY = dict(
    dusk_offset_min=0,          # allumage = coucher du soleil + decalage (minutes)
    dawn_offset_min=0,          # extinction = lever du soleil + decalage
    lux_cutoff=40,              # au-dessus de cette luminosite mesuree (lux), le luminaire s'eteint
    min_level=20,               # niveau plancher quand la frequentation prevue est nulle (%)
    presence_level=100,         # niveau force quand un mouvement est detecte (%)
    presence_hold_min=3,        # duree de maintien apres un mouvement (minutes)
    use_ai=True,                # moduler le niveau avec la frequentation prevue par les modeles d'IA
    blocks=[                    # niveaux par plage horaire, appliques seulement la nuit
        {"start": "00:00", "end": "05:00", "level": 50}, {"start": "05:00", "end": "12:00", "level": 70},
        {"start": "12:00", "end": "22:30", "level": 100}, {"start": "22:30", "end": "24:00", "level": 50}])


def init(c):
    c.executescript(SCHEMA)
    if "street_id" not in {r[1] for r in c.execute("PRAGMA table_info(lamps)")}:      # migration des bases existantes
        c.execute("ALTER TABLE lamps ADD COLUMN street_id TEXT")
    c.commit()


# ---------- soleil (algorithme NOAA, precision ~ 1 minute) ----------
def sun_times(lat, lng, d):
    """Retourne (lever, coucher) en minutes depuis minuit, heure locale (UTC+1)."""
    n = d.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1)
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    dec = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
           + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    la = math.radians(lat)
    cosha = math.cos(math.radians(90.833)) / (math.cos(la) * math.cos(dec)) - math.tan(la) * math.tan(dec)
    cosha = max(-1.0, min(1.0, cosha))
    ha = math.degrees(math.acos(cosha))
    off = 60                                              # UTC+1
    rise = 720 - 4 * (lng + ha) - eqt + off
    sset = 720 - 4 * (lng - ha) - eqt + off
    return rise % 1440, sset % 1440


def fmt_min(m):
    return f"{int(m // 60):02d}:{int(m % 60):02d}"


def now_local(ts=None):
    return datetime.fromtimestamp(ts if ts is not None else time.time(), TZ)


def is_night(lat, lng, dt, policy):
    rise, sset = sun_times(lat, lng, dt.date())
    on = sset + policy["dusk_offset_min"]
    off = rise + policy["dawn_offset_min"]
    m = dt.hour * 60 + dt.minute + dt.second / 60
    return m >= on or m < off


# ---------- politique ----------
def get_policy():
    c = db.conn()
    r = c.execute("SELECT json FROM lighting_policy WHERE id=1").fetchone()
    c.close()
    p = dict(DEFAULT_POLICY)
    if r:
        p.update(json.loads(r["json"]))
    return p


def validate_policy(d):
    def num(k, lo, hi):
        v = float(d[k])
        if not lo <= v <= hi:
            raise ValueError(f"{k} entre {lo} et {hi}")
        return v
    try:
        p = dict(dusk_offset_min=int(num("dusk_offset_min", -120, 120)), dawn_offset_min=int(num("dawn_offset_min", -120, 120)),
                 lux_cutoff=num("lux_cutoff", 1, 20000), min_level=int(num("min_level", 0, 100)), presence_level=int(num("presence_level", 0, 100)),
                 presence_hold_min=int(num("presence_hold_min", 0, 60)), use_ai=bool(d.get("use_ai")))
        blocks = d.get("blocks")
        if not isinstance(blocks, list) or not 1 <= len(blocks) <= 12:
            raise ValueError("1 a 12 plages horaires")
        out = []
        for b in blocks:
            s, e, lv = str(b["start"]), str(b["end"]), float(b["level"])
            if not (re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s) and re.fullmatch(r"(([01]\d|2[0-3]):[0-5]\d|24:00)", e)):
                raise ValueError("heures au format HH:MM")
            if not 0 <= lv <= 100:
                raise ValueError("niveau entre 0 et 100")
            out.append({"start": s, "end": e, "level": int(lv)})
        p["blocks"] = out
        return p, None
    except (KeyError, ValueError, TypeError) as e:
        return None, str(e)


def set_policy(p):
    c = db.conn()
    c.execute("INSERT INTO lighting_policy(id,json) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET json=excluded.json", (json.dumps(p),))
    c.commit()
    c.close()


def _hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def block_level(policy, minute):
    for b in policy["blocks"]:
        if _hm(b["start"]) <= minute < _hm(b["end"]):
            return b["level"]
    return policy["min_level"]


# ---------- predictions de frequentation (modeles d'IA) ----------
def hour_key(dt):
    return dt.strftime("%Y-%m-%dT%H")


def prediction_for(lamp, dt):
    """Frequentation prevue (0..1) pour cette heure : cible = luminaire, puis zone, puis gouvernorat. None si aucun modele n'a repondu."""
    targets = [lamp["id"]] + ([f"zone:{lamp['zone']}"] if lamp.get("zone") else []) + [f"gov:{lamp['governorate']}"]
    c = db.conn()
    best = None
    for t in targets:
        r = c.execute("SELECT presence FROM lamp_predictions WHERE target=? AND hour=? ORDER BY created_at DESC LIMIT 1", (t, hour_key(dt))).fetchone()
        if r:
            best = r["presence"]
            break
    c.close()
    return best


def upsert_predictions(model_id, items):
    if not isinstance(items, list) or not items:
        return 0, "items doit etre une liste non vide"
    if len(items) > 20000:
        return 0, "maximum 20000 lignes par envoi"
    c = db.conn()
    ids = {r["id"] for r in c.execute("SELECT id FROM lamps")}
    clean = []
    for i, it in enumerate(items):
        try:
            t, h, p = str(it["target"]).strip(), str(it["hour"]).strip(), float(it["presence"])
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}", h):
                raise ValueError("hour au format AAAA-MM-JJTHH (heure locale)")
            if not 0 <= p <= 1:
                raise ValueError("presence entre 0 et 1")
            if t.startswith("gov:"):
                if t[4:] not in db.GOVERNORATES:
                    raise ValueError("gouvernorat inconnu")
            elif t.startswith("zone:"):
                if not 1 <= len(t) - 5 <= 40:
                    raise ValueError("nom de zone de 1 a 40 caracteres")
            elif t not in ids:
                raise ValueError(f"cible inconnue : {t!r} (luminaire, zone:NOM ou gov:GOUVERNORAT)")
            clean.append((model_id, t, h, p))
        except (KeyError, ValueError, TypeError) as e:
            c.close()
            return 0, f"ligne {i + 1} : {e}"
    now = int(time.time())
    for m, t, h, p in clean:
        c.execute("INSERT INTO lamp_predictions VALUES (?,?,?,?,?) ON CONFLICT(model_id,target,hour) DO UPDATE SET presence=excluded.presence, created_at=excluded.created_at", (m, t, h, p, now))
    c.execute("UPDATE models SET last_push=? WHERE id=?", (now, model_id))
    c.commit()
    c.close()
    return len(clean), None


def prediction_stats():
    c = db.conn()
    rows = c.execute("""SELECT m.name, COUNT(*) n, MIN(p.hour) h0, MAX(p.hour) h1, MAX(p.created_at) upd
                        FROM lamp_predictions p JOIN models m ON m.id = p.model_id GROUP BY p.model_id""").fetchall()
    c.close()
    return [dict(r) for r in rows]


# ---------- consigne ----------
def target_level(lamp, dt, policy, last=None, use_motion=True):
    """Retourne (niveau %, raison). `last` = dernier etat connu du luminaire (lux, mouvement)."""
    last = last or {}
    now = dt.timestamp()
    if not is_night(lamp["lat"], lamp["lng"], dt, policy):
        return 0, "jour (soleil leve)"
    if last.get("lux") is not None and last.get("seen") and now - last["seen"] < 900 and last["lux"] >= policy["lux_cutoff"]:
        return 0, f"luminosite mesuree {last['lux']:.0f} lux (seuil {policy['lux_cutoff']:.0f})"
    minute = dt.hour * 60 + dt.minute
    base = block_level(policy, minute)
    reason = f"plage horaire ({base} %)"
    if policy["use_ai"]:
        pred = prediction_for(lamp, dt)
        if pred is not None:
            lvl = round(policy["min_level"] + (base - policy["min_level"]) * pred) if base > policy["min_level"] else base
            reason = f"plage horaire {base} % x frequentation prevue {pred:.2f} (modele IA)"
            base = lvl
    if use_motion and last.get("motion_ts") and now - last["motion_ts"] <= policy["presence_hold_min"] * 60:
        if policy["presence_level"] > base:
            return policy["presence_level"], "mouvement detecte"
    return max(0, min(100, base)), reason


# ---------- luminaires ----------
def create_lamp(d):
    try:
        name = str(d.get("name", "")).strip()
        gov = d.get("governorate")
        lat, lng, rw = float(d.get("lat")), float(d.get("lng")), int(d.get("rated_w"))
        lt = d.get("lamp_type")
        assert 2 <= len(name) <= 60 and gov in db.GOVERNORATES and lt in LAMP_TYPES
        assert db.LAT_RANGE[0] <= lat <= db.LAT_RANGE[1] and db.LNG_RANGE[0] <= lng <= db.LNG_RANGE[1] and 5 <= rw <= 1000
    except Exception:
        return None, None, "Nom (2-60 car.), gouvernorat, position en Tunisie, puissance (5-1000 W) et type de lampe requis."
    c = db.conn()
    if c.execute("SELECT COUNT(*) FROM lamps").fetchone()[0] >= MAX_LAMPS:
        c.close()
        return None, None, "Nombre maximum de luminaires atteint."
    lid, key = "LP-" + secrets.token_hex(3).upper(), secrets.token_urlsafe(24)
    c.execute("INSERT INTO lamps(id,name,zone,governorate,delegation,lat,lng,rated_w,lamp_type,key_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (lid, name, str(d.get("zone") or "").strip()[:40], gov, str(d.get("delegation") or "").strip()[:60], lat, lng, rw, lt, db._th(key), int(time.time())))
    c.commit()
    c.close()
    return lid, key, None


def delete_lamp(lid):
    c = db.conn()
    c.execute("DELETE FROM lamps WHERE id=?", (lid,))
    c.commit()
    c.close()


def lamp_auth(lid, key):
    c = db.conn()
    r = c.execute("SELECT * FROM lamps WHERE id=?", (lid or "",)).fetchone()
    c.close()
    return dict(r) if r and db.hmac.compare_digest(r["key_hash"], db._th(key or "")) else None


def _last(lamp):
    return dict(lux=lamp.get("last_lux"), seen=lamp.get("last_seen"), motion_ts=lamp.get("last_motion"))


def report(lamp, d):
    """Rapport d'un controleur. Retourne (reponse, erreur). La reponse contient la CONSIGNE a appliquer."""
    try:
        level = float(d.get("level_pct"))
        power = float(d.get("power_w"))
        lux = None if d.get("lux") in (None, "") else float(d["lux"])
        motion = int(d.get("motion") or 0)
        assert 0 <= level <= 100 and 0 <= power <= lamp["rated_w"] * 3 + 50 and (lux is None or 0 <= lux <= 200000) and 0 <= motion <= 100000
    except Exception:
        return None, "level_pct (0-100), power_w (>=0), lux (facultatif), motion (facultatif, entier) requis"
    pol = get_policy()
    now = int(time.time())
    dt = now_local(now)
    dt_s = min(900, max(0, now - lamp["last_seen"])) if lamp.get("last_seen") else 0
    # energie reelle, energie de reference (plein regime toute la nuit), energie de la consigne precedente
    prev_target = None
    c = db.conn()
    r = c.execute("SELECT target_pct FROM lamp_readings WHERE lamp_id=? ORDER BY ts DESC LIMIT 1", (lamp["id"],)).fetchone()
    if r:
        prev_target = r["target_pct"]
    night = is_night(lamp["lat"], lamp["lng"], dt, pol)
    kwh = power * dt_s / 3_600_000
    base = lamp["rated_w"] * dt_s / 3_600_000 if night else 0.0
    exp = lamp["rated_w"] * ((prev_target if prev_target is not None else level) / 100) * dt_s / 3_600_000
    waste = max(0.0, kwh - exp)                         # energie consommee au-dela de la consigne precedente
    if not night and power > 0.1 * lamp["rated_w"]:
        waste = kwh                                     # allume en plein jour : toute l'energie est du gaspillage
    motion_ts = now if motion > 0 else lamp.get("last_motion")
    last = dict(lux=lux, seen=now, motion_ts=motion_ts)
    target, reason = target_level(lamp, dt, pol, last)
    day = dt.strftime("%Y-%m-%d")
    c.execute("INSERT INTO lamp_readings(lamp_id,ts,level_pct,power_w,lux,motion,target_pct) VALUES (?,?,?,?,?,?,?)", (lamp["id"], now, level, power, lux, motion, target))
    c.execute("UPDATE lamps SET last_seen=?, last_level=?, last_power_w=?, last_lux=?, last_motion=? WHERE id=?", (now, level, power, lux, motion_ts, lamp["id"]))
    c.execute("""INSERT INTO lamp_daily(lamp_id,day,kwh,baseline_kwh,waste_kwh) VALUES (?,?,?,?,?)
                 ON CONFLICT(lamp_id,day) DO UPDATE SET kwh=kwh+excluded.kwh, baseline_kwh=baseline_kwh+excluded.baseline_kwh, waste_kwh=waste_kwh+excluded.waste_kwh""",
              (lamp["id"], day, kwh, base, waste))
    c.execute("DELETE FROM lamp_readings WHERE lamp_id=? AND ts<?", (lamp["id"], now - KEEP_SECONDS))
    c.commit()
    c.close()
    rise, sset = sun_times(lamp["lat"], lamp["lng"], dt.date())
    return dict(ok=True, target_level=target, reason=reason, night=night, sunrise=fmt_min(rise), sunset=fmt_min(sset), server_time=now), None


def diagnose(lamp, now=None):
    """Alertes a partir du DERNIER rapport reel et de la politique. Liste de {level, kind, text}."""
    now = now or int(time.time())
    out = []
    if not lamp.get("last_seen"):
        return [dict(level="info", kind="never", text="Aucun rapport recu de ce controleur.")]
    pol = get_policy()
    dt = now_local(now)
    night = is_night(lamp["lat"], lamp["lng"], dt, pol)
    age = now - lamp["last_seen"]
    lvl, pw, rw = lamp.get("last_level") or 0, lamp.get("last_power_w") or 0, lamp["rated_w"]
    if age > OFFLINE_SECONDS:
        out.append(dict(level="warn", kind="offline", text=f"Controleur muet depuis {age // 60} min."))
        return out
    target, _ = target_level(lamp, dt, pol, _last(lamp))
    if not night and (lvl > 0 or pw > 0.1 * rw):
        out.append(dict(level="critical", kind="daylight", text=f"Allume en plein jour ({pw:.0f} W) : gaspillage."))
    if lvl >= 50 and pw < 0.2 * rw * lvl / 100:
        out.append(dict(level="critical", kind="fault", text=f"Puissance trop faible ({pw:.0f} W pour {lvl:.0f} %) : lampe ou alimentation defectueuse ?"))
    if lvl > 0 and pw > 1.4 * rw * lvl / 100 + 5:
        out.append(dict(level="warn", kind="overpower", text=f"Consommation anormale ({pw:.0f} W pour {lvl:.0f} %)."))
    if night and lvl - target > 25:
        out.append(dict(level="warn", kind="over_target", text=f"Niveau {lvl:.0f} % au-dessus de la consigne ({target} %) : gaspillage."))
    return out


def lamp_state(lamp, now=None):
    now = now or int(time.time())
    if not lamp.get("last_seen"):
        return "never"
    if now - lamp["last_seen"] > OFFLINE_SECONDS:
        return "offline"
    if any(a["kind"] in ("fault", "daylight") for a in diagnose(lamp, now)):
        return "alert"
    lv = lamp.get("last_level") or 0
    return "off" if lv < 1 else "full" if lv >= 90 else "dim"


def list_lamps():
    c = db.conn()
    rows = [dict(r) for r in c.execute("SELECT * FROM lamps ORDER BY created_at DESC")]
    c.close()
    return rows


def overview():
    now = int(time.time())
    pol = get_policy()
    dt = now_local(now)
    lamps = list_lamps()
    day = dt.strftime("%Y-%m-%d")
    c = db.conn()
    agg = {r["lamp_id"]: r for r in c.execute("SELECT lamp_id,kwh,baseline_kwh,waste_kwh FROM lamp_daily WHERE day=?", (day,))}
    c.close()
    out, tot = [], dict(kwh=0.0, base=0.0, waste=0.0)
    for l in lamps:
        a = agg.get(l["id"])
        st = lamp_state(l, now)
        target, reason = target_level(l, dt, pol, _last(l))
        al = diagnose(l, now)
        if a:
            tot["kwh"] += a["kwh"]
            tot["base"] += a["baseline_kwh"]
            tot["waste"] += a["waste_kwh"]
        out.append(dict(id=l["id"], name=l["name"], zone=l["zone"], governorate=l["governorate"], delegation=l["delegation"], lat=l["lat"], lng=l["lng"],
                        rated_w=l["rated_w"], lamp_type=l["lamp_type"], last_seen=l["last_seen"], level=l["last_level"], power_w=l["last_power_w"],
                        lux=l["last_lux"], state=st, target=target, reason=reason, alerts=al,
                        kwh_today=round(a["kwh"], 3) if a else 0, saved_kwh_today=round(max(0, a["baseline_kwh"] - a["kwh"]), 3) if a else 0,
                        waste_kwh_today=round(a["waste_kwh"], 3) if a else 0))
    online = [x for x in out if x["state"] in ("off", "dim", "full", "alert")]
    return dict(now=now, policy=pol, lamps=out, kpi=dict(
        total=len(out), online=len(online), lit=sum(1 for x in online if (x["level"] or 0) >= 1),
        kwh=round(tot["kwh"], 3), baseline_kwh=round(tot["base"], 3), saved_kwh=round(max(0.0, tot["base"] - tot["kwh"]), 3),
        saved_pct=round(100 * (tot["base"] - tot["kwh"]) / tot["base"], 1) if tot["base"] > 0.0005 else None,
        waste_kwh=round(tot["waste"], 3), alerts=sum(1 for x in out if any(a["level"] in ("critical", "warn") for a in x["alerts"]))),
        predictions=prediction_stats())


def detail(lid):
    c = db.conn()
    r = c.execute("SELECT * FROM lamps WHERE id=?", (lid,)).fetchone()
    if not r:
        c.close()
        return None
    lamp = dict(r)
    now = int(time.time())
    since = now - 24 * 3600
    rows = c.execute("""SELECT (ts/600)*600 AS b, AVG(level_pct) lv, AVG(power_w) pw, AVG(target_pct) tg FROM lamp_readings
                        WHERE lamp_id=? AND ts>=? GROUP BY b ORDER BY b""", (lid, since)).fetchall()
    daily = c.execute("SELECT day,kwh,baseline_kwh,waste_kwh FROM lamp_daily WHERE lamp_id=? ORDER BY day DESC LIMIT 14", (lid,)).fetchall()
    c.close()
    pol = get_policy()
    dt = now_local(now)
    rise, sset = sun_times(lamp["lat"], lamp["lng"], dt.date())
    plan = []
    for h in range(24):                                   # plan des 24 prochaines heures (politique + soleil + predictions)
        t = (dt.replace(minute=30, second=0, microsecond=0) + timedelta(hours=h))
        lv, why = target_level(lamp, t, pol, None, use_motion=False)
        pred = prediction_for(lamp, t) if pol["use_ai"] else None
        plan.append(dict(hour=t.strftime("%d/%m %Hh"), level=lv, presence=pred))
    return dict(lamp={k: lamp[k] for k in ("id", "name", "zone", "governorate", "delegation", "lat", "lng", "rated_w", "lamp_type", "last_seen", "last_level", "last_power_w", "last_lux")},
                state=lamp_state(lamp, now), alerts=diagnose(lamp, now), sunrise=fmt_min(rise + pol["dawn_offset_min"]), sunset=fmt_min(sset + pol["dusk_offset_min"]),
                target=target_level(lamp, dt, pol, _last(lamp))[0], reason=target_level(lamp, dt, pol, _last(lamp))[1],
                series=[dict(ts=r["b"], level=round(r["lv"], 1), power_w=round(r["pw"], 1), target=round(r["tg"], 1)) for r in rows],
                daily=[dict(r) for r in daily], plan=plan, now=now)


# ---------- soleil (API) ----------
def sun_info(lat, lng, ts=None):
    """Lever et coucher du soleil pour une position, calcules automatiquement (aucune heure a saisir)."""
    pol = get_policy()
    dt = now_local(ts)
    rise, sset = sun_times(lat, lng, dt.date())
    return dict(date=dt.strftime("%Y-%m-%d"), sunrise=fmt_min(rise), sunset=fmt_min(sset),
                lights_on=fmt_min((sset + pol["dusk_offset_min"]) % 1440), lights_off=fmt_min((rise + pol["dawn_offset_min"]) % 1440),
                day_length_min=int((sset - rise) % 1440), is_night=is_night(lat, lng, dt, pol), local_time=dt.strftime("%H:%M"),
                source="calcul astronomique (precision environ 1 minute), heure locale UTC+1")


def sun_all():
    return [dict(governorate=g, **sun_info(la, lo)) for g, (la, lo) in GOV_CENTERS.items()]


# ---------- rues principales ----------
KIND_RANK = {"motorway": 0, "trunk": 1, "primary": 2, "secondary": 3}


def import_streets(path):
    """Charge data/streets_tunisia.json (produit par tools/fetch_streets.py) dans la base. Retourne le nombre de rues."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    c = db.conn()
    c.execute("DELETE FROM streets")
    c.executemany("INSERT INTO streets VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        (s["id"], s["name"], s.get("name_ar") or "", s.get("ref") or "", s["kind"], s.get("kind_label"), s["length_m"], s["gov"], s["lat"], s["lng"],
         json.dumps(s["lines"], separators=(",", ":"))) for s in data["streets"]])
    c.commit()
    n = c.execute("SELECT COUNT(*) FROM streets").fetchone()[0]
    c.close()
    return n


def streets_count():
    c = db.conn()
    n = c.execute("SELECT COUNT(*) FROM streets").fetchone()[0]
    c.close()
    return n


def search_streets(q="", gov="", kind="", limit=60):
    q = (q or "").strip()
    sql, args = "SELECT id,name,name_ar,ref,kind,kind_label,length_m,gov,lat,lng FROM streets WHERE 1=1", []
    if q:
        sql += " AND (name LIKE ? OR name_ar LIKE ? OR ref LIKE ?)"
        args += [f"%{q}%"] * 3
    if gov in db.GOVERNORATES:
        sql += " AND gov=?"
        args.append(gov)
    if kind in KIND_RANK:
        sql += " AND kind=?"
        args.append(kind)
    c = db.conn()
    rows = [dict(r) for r in c.execute(sql + " LIMIT 2000", args)]
    counts = {r["street_id"]: r["n"] for r in c.execute("SELECT street_id, COUNT(*) n FROM lamps WHERE street_id IS NOT NULL GROUP BY street_id")}
    c.close()
    rows.sort(key=lambda r: (KIND_RANK[r["kind"]], -r["length_m"]))
    for r in rows:
        r["lamps"] = counts.get(r["id"], 0)
    return rows[:limit]


def street_geometry(sid):
    c = db.conn()
    r = c.execute("SELECT * FROM streets WHERE id=?", (sid,)).fetchone()
    n = c.execute("SELECT COUNT(*) FROM lamps WHERE street_id=?", (sid,)).fetchone()[0]
    c.close()
    if not r:
        return None
    d = dict(r)
    d["lines"] = json.loads(d.pop("geometry"))
    d["lamps"] = n
    return d


def _along(lines, spacing):
    """Points regulierement espaces (en metres) le long de chaque polyligne."""
    out = []
    for line in lines:
        carry = spacing / 2                                  # premier luminaire a mi-espacement
        for a, b in zip(line, line[1:]):
            dy, dx = (b[0] - a[0]) * 111320, (b[1] - a[1]) * 111320 * math.cos(math.radians(a[0]))
            seg = math.hypot(dx, dy)
            if seg == 0:
                continue
            d = carry
            while d <= seg:
                t = d / seg
                out.append((round(a[0] + (b[0] - a[0]) * t, 6), round(a[1] + (b[1] - a[1]) * t, 6)))
                d += spacing
            carry = d - seg
    return out


def create_lamps_along(sid, spacing_m, rated_w, lamp_type, zone=""):
    """Declare des luminaires regulierement espaces le long d'une rue. Retourne (lignes CSV, erreur).
    Les positions sont ESTIMEES le long du trace : a valider sur le terrain. Chaque luminaire recoit sa propre cle."""
    st = street_geometry(sid)
    if not st:
        return None, "rue inconnue"
    try:
        spacing, rw = float(spacing_m), int(rated_w)
        assert 15 <= spacing <= 150 and 5 <= rw <= 1000 and lamp_type in LAMP_TYPES
    except Exception:
        return None, "espacement 15-150 m, puissance 5-1000 W et type de lampe requis"
    if st["lamps"]:
        return None, f"Cette rue a deja {st['lamps']} luminaire(s) declare(s). Supprimez-les d'abord pour recommencer."
    pts = _along(st["lines"], spacing)
    if not pts:
        return None, "rue trop courte pour cet espacement"
    c = db.conn()
    if c.execute("SELECT COUNT(*) FROM lamps").fetchone()[0] + len(pts) > MAX_LAMPS or len(pts) > 3000:
        c.close()
        return None, f"{len(pts)} luminaires : maximum 3000 par rue et {MAX_LAMPS} au total. Augmentez l'espacement."
    label = st["name"] or st["ref"]
    rows, now = [], int(time.time())
    for i, (la, lo) in enumerate(pts, 1):
        lid, key = "LP-" + secrets.token_hex(3).upper(), secrets.token_urlsafe(24)
        gov = nearest_governorate(la, lo)
        c.execute("INSERT INTO lamps(id,name,zone,governorate,delegation,lat,lng,rated_w,lamp_type,key_hash,created_at,street_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (lid, f"{label} n°{i}", (zone or label)[:40], gov, "", la, lo, rw, lamp_type, db._th(key), now, sid))
        rows.append((lid, key, f"{label} n°{i}", la, lo, gov, rw))
    c.commit()
    c.close()
    return rows, None
