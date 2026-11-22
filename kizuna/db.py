"""Base de donnees (SQLite) : comptes, profils, equipements, capteurs, mesures.

Securite : mots de passe haches avec scrypt + sel aleatoire ; jetons de session et cles de capteur hachees ;
cookie HttpOnly + SameSite=Lax. Aucune donnee fictive : tout ce qui est stocke vient des utilisateurs ou des capteurs.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import sqlite3
import time

from . import shedding

DB_PATH = os.environ.get("KIZUNA_DB") or os.path.join(os.path.dirname(__file__), "..", "data", "steg_app.db")
SESSION_SECONDS = 7 * 24 * 3600
ONLINE_SECONDS = 120          # un capteur est « en ligne » si sa derniere mesure date de moins de 2 minutes
MAX_DEVICES = 5
MAX_EQUIPMENT = 40

GOVERNORATES = ["Ariana", "Béja", "Ben Arous", "Bizerte", "Gabès", "Gafsa", "Jendouba", "Kairouan", "Kasserine", "Kébili",
                "Le Kef", "Mahdia", "La Manouba", "Médenine", "Monastir", "Nabeul", "Sfax", "Sidi Bouzid", "Siliana",
                "Sousse", "Tataouine", "Tozeur", "Tunis", "Zaghouan"]
HOUSING = ["Appartement", "Maison", "Villa", "Ferme / exploitation agricole", "Local commercial"]
# Limites geographiques approximatives de la Tunisie (controle de coherence du pin sur la carte)
LAT_RANGE, LNG_RANGE = (30.0, 37.6), (7.4, 11.8)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  pw_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('client','steg')),
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  full_name TEXT NOT NULL, phone TEXT, governorate TEXT, delegation TEXT, address TEXT, postal_code TEXT,
  lat REAL, lng REAL, steg_ref TEXT, meter_number TEXT, breaker_kw REAL, housing_type TEXT, occupants INTEGER,
  consent_terms INTEGER NOT NULL DEFAULT 0, shed_level INTEGER NOT NULL DEFAULT 0, lang TEXT NOT NULL DEFAULT 'fr'
);
CREATE TABLE IF NOT EXISTS equipment (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL, power_w INTEGER NOT NULL, quantity INTEGER NOT NULL,
  circuit TEXT, priority TEXT, defer_h INTEGER
);
CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL, key_hash TEXT NOT NULL, created_at INTEGER NOT NULL,
  last_seen INTEGER, kwh_total REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS readings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  ts INTEGER NOT NULL, power_w REAL, voltage REAL, current_a REAL, cosphi REAL, leak_ma REAL
);
CREATE INDEX IF NOT EXISTS idx_readings ON readings(device_id, ts);
CREATE TABLE IF NOT EXISTS daily (
  device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  day TEXT NOT NULL, kwh REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (device_id, day)
);
CREATE TABLE IF NOT EXISTS hourly (
  device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  ts_hour INTEGER NOT NULL, n INTEGER NOT NULL, sum_w REAL NOT NULL, max_w REAL NOT NULL, sumsq_w REAL NOT NULL,
  PRIMARY KEY (device_id, ts_hour)
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at INTEGER NOT NULL
);
"""


def conn():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA synchronous = NORMAL")
    return c


def init():
    c = conn()
    c.execute("PRAGMA journal_mode = WAL")          # lectures et ecritures simultanees (beaucoup de controleurs)
    c.executescript(SCHEMA)
    from . import billing, forecasts, lighting, mimamori, peaks, rewards, sms
    billing.init(c)
    peaks.init(c)
    rewards.init(c)
    mimamori.init(c)
    sms.init(c)
    forecasts.init(c)
    lighting.init(c)
    cols = {r[1] for r in c.execute("PRAGMA table_info(equipment)")}          # migration des bases existantes
    for col, typ in (("circuit", "TEXT"), ("priority", "TEXT"), ("defer_h", "INTEGER")):
        if col not in cols:
            c.execute(f"ALTER TABLE equipment ADD COLUMN {col} {typ}")
    if "shed_level" not in {r[1] for r in c.execute("PRAGMA table_info(profiles)")}:
        c.execute("ALTER TABLE profiles ADD COLUMN shed_level INTEGER NOT NULL DEFAULT 0")
    if "lang" not in {r[1] for r in c.execute("PRAGMA table_info(profiles)")}:
        c.execute("ALTER TABLE profiles ADD COLUMN lang TEXT NOT NULL DEFAULT 'fr'")
    c.commit()
    c.close()


# ---------- mots de passe ----------
# Les mots de passe ne sont jamais « chiffres » (ce qui se dechiffre) : ils sont HACHES avec scrypt (a sens unique, lent, gourmand en memoire), avec un sel
# aleatoire par compte. Format : scrypt$<log2 N>$<r>$<p>$<sel>$<empreinte>. Parametres actuels : N=2^15, r=8, p=3 (32 Mio, recommandation OWASP).
# Les anciens comptes (format « sel$empreinte », N=2^14) sont reconnus puis re-haches automatiquement a leur prochaine connexion.
SCRYPT_LOG_N, SCRYPT_R, SCRYPT_P = 15, 8, 3
_hash_slots = threading.BoundedSemaphore(4)      # au plus 4 calculs en meme temps : limite la memoire utilisee si quelqu'un spamme la connexion


def _scrypt(password, salt, ln, r, p):
    with _hash_slots:
        return hashlib.scrypt(password.encode(), salt=salt, n=2 ** ln, r=r, p=p, dklen=32, maxmem=128 * 2 ** ln * r * p + 2 ** 22 + 128 * 2 ** ln * r)


def hash_pw(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    return f"scrypt${SCRYPT_LOG_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${_scrypt(password, salt, SCRYPT_LOG_N, SCRYPT_R, SCRYPT_P).hex()}"


def _parse_hash(stored):
    parts = stored.split("$")
    if parts[0] == "scrypt" and len(parts) == 6:
        return int(parts[1]), int(parts[2]), int(parts[3]), bytes.fromhex(parts[4]), parts[5]
    if len(parts) == 2:                                    # ancien format
        return 14, 8, 1, bytes.fromhex(parts[0]), parts[1]
    raise ValueError("format inconnu")


def check_pw(password, stored):
    try:
        ln, r, p, salt, h_hex = _parse_hash(stored)
        if not (10 <= ln <= 17 and 1 <= r <= 16 and 1 <= p <= 8):
            return False
        return hmac.compare_digest(_scrypt(password, salt, ln, r, p).hex(), h_hex)
    except Exception:
        return False


def needs_rehash(stored):
    try:
        return _parse_hash(stored)[:3] != (SCRYPT_LOG_N, SCRYPT_R, SCRYPT_P)
    except Exception:
        return True


def rehash(uid, password):
    """Met a jour l'empreinte d'un compte avec les parametres actuels (sans deconnecter personne)."""
    c = conn()
    c.execute("UPDATE users SET pw_hash=? WHERE id=?", (hash_pw(password), uid))
    c.commit()
    c.close()


DUMMY_HASH = hash_pw("pas-un-vrai-mot-de-passe")      # sert a egaliser le temps de reponse d'une connexion avec un e-mail inconnu


def _th(token):
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- validation ----------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_phone(p):
    d = re.sub(r"[\s.\-]", "", p or "")
    if d.startswith("+216"):
        d = d[4:]
    elif d.startswith("00216"):
        d = d[5:]
    return d if re.fullmatch(r"[2-9]\d{7}", d) else None


LANGS = ("fr", "en", "ar", "ja")
CTRL_RE = re.compile("[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F\u202A-\u202E\u2066-\u2069]")      # caracteres de controle et de renversement de texte (usurpation d'affichage)


def clean_label(s, maxlen=40):
    """Texte court libre (nom de capteur, de modele...) : sans caracteres de controle ni < >, espaces normalises, longueur limitee."""
    s = CTRL_RE.sub("", str(s or ""))
    s = re.sub(r"[<>]", "", s)
    return re.sub(r"\s+", " ", s).strip()[:maxlen]


COMMON_PW = {"password", "password1", "motdepasse", "azerty123", "azertyuiop", "qwerty123", "12345678", "123456789", "1234567890", "steg2026", "kizuna2026", "kizunawatt", "administrateur", "admin1234", "bienvenue1"}


def validate_password(pw, pw2=None, strict=False):
    """strict=True pour le compte STEG : 12 caracteres minimum et au moins 3 familles (minuscule, majuscule, chiffre, symbole)."""
    if not isinstance(pw, str) or (pw2 is not None and not isinstance(pw2, str)):
        return "Mot de passe invalide."
    if len(pw or "") > 128:
        return "128 caracteres maximum."
    if len(pw or "") < 8 or not re.search(r"\d", pw) or not re.search(r"[A-Za-z]", pw):
        return "8 caracteres minimum, avec au moins une lettre et un chiffre."
    if re.sub(r"[^a-z0-9]", "", pw.lower()) in COMMON_PW or len(set(pw)) < 4:
        return "Mot de passe trop courant : choisissez-en un moins previsible."
    if strict:
        fam = sum(bool(re.search(x, pw)) for x in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
        if len(pw) < 12 or fam < 3:
            return "Compte STEG : 12 caracteres minimum, avec au moins 3 types parmi minuscule, majuscule, chiffre, symbole."
    if pw2 is not None and pw != pw2:
        return "Les mots de passe ne correspondent pas."
    return None


def validate_profile(d, need_consent=True):
    """Retourne (profil, erreurs). La reference de facture est en format libre : nous ne connaissons pas
    le format officiel STEG et nous ne le verifions pas."""
    err, p = {}, {}
    p["full_name"] = clean_label(d.get("full_name"), 80)
    if len(p["full_name"]) < 3 or not all(ch.isalpha() or ch in " '’-.()" for ch in p["full_name"]):
        err["full_name"] = "Nom complet requis : lettres, espaces, ' - . ( ) uniquement (3 a 80 caracteres)."
    p["phone"] = clean_phone(d.get("phone"))
    if not p["phone"]:
        err["phone"] = "Numero tunisien a 8 chiffres requis (ex. 22 123 456)."
    p["governorate"] = d.get("governorate")
    if p["governorate"] not in GOVERNORATES:
        err["governorate"] = "Choisissez votre gouvernorat."
    p["delegation"] = clean_label(d.get("delegation"), 60)
    p["address"] = clean_label(d.get("address"), 160)
    if len(p["address"]) < 5:
        err["address"] = "Adresse requise."
    pc = (d.get("postal_code") or "").strip()
    if not re.fullmatch(r"\d{4}", pc):
        err["postal_code"] = "Code postal a 4 chiffres."
    p["postal_code"] = pc
    try:
        p["lat"], p["lng"] = float(d.get("lat")), float(d.get("lng"))
        assert LAT_RANGE[0] <= p["lat"] <= LAT_RANGE[1] and LNG_RANGE[0] <= p["lng"] <= LNG_RANGE[1]
    except Exception:
        err["location"] = "Placez votre domicile sur la carte (position en Tunisie)."
    ref = (d.get("steg_ref") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9\-/ ]{4,24}", ref):
        err["steg_ref"] = "Reference de facture : 4 a 24 caracteres (lettres, chiffres, - /)."
    p["steg_ref"] = ref
    meter = (d.get("meter_number") or "").strip()
    if meter and not re.fullmatch(r"[A-Za-z0-9\-]{4,24}", meter):
        err["meter_number"] = "Numero de compteur : 4 a 24 caracteres."
    p["meter_number"] = meter
    try:
        p["breaker_kw"] = float(d.get("breaker_kw"))
        assert 1 <= p["breaker_kw"] <= 60
    except Exception:
        err["breaker_kw"] = "Puissance souscrite entre 1 et 60 kW (voir votre facture)."
    p["housing_type"] = d.get("housing_type") if d.get("housing_type") in HOUSING else None
    if not p["housing_type"]:
        err["housing_type"] = "Type de logement requis."
    try:
        p["occupants"] = int(d.get("occupants"))
        assert 1 <= p["occupants"] <= 30
    except Exception:
        err["occupants"] = "Nombre d'occupants entre 1 et 30."
    p["lang"] = d.get("lang") if d.get("lang") in LANGS else "fr"
    p["consent_terms"] = 1 if d.get("consent_terms") else 0
    if need_consent and not p["consent_terms"]:
        err["consent_terms"] = "Vous devez accepter les conditions."
    return p, err


def validate_equipment(items):
    """Liste de {name, power_w, quantity}. Retourne (liste propre, erreur)."""
    if not isinstance(items, list):
        return None, "Liste d'equipements invalide."
    if len(items) > MAX_EQUIPMENT:
        return None, f"Maximum {MAX_EQUIPMENT} equipements."
    out = []
    for it in items:
        try:
            name = str(it.get("name", "")).strip()[:40]
            power, qty = int(it.get("power_w")), int(it.get("quantity", 1))
            assert len(name) >= 2 and 1 <= power <= 20000 and 1 <= qty <= 20
            dh = it.get("defer_h")
            dh = None if dh in (None, "") else int(dh)
            assert dh is None or 0 <= dh <= 48
        except Exception:
            return None, "Chaque equipement : nom (2 a 40 car.), puissance 1 a 20000 W, quantite 1 a 20, delai de report 0 a 48 h."
        out.append(shedding.normalize(dict(name=name, power_w=power, quantity=qty, circuit=it.get("circuit"), priority=it.get("priority"), defer_h=dh)))
    return out, None


# ---------- utilisateurs ----------
def create_user(email, password, profile, role="client", equipment=None):
    c = conn()
    try:
        cur = c.execute("INSERT INTO users(email,pw_hash,role,created_at) VALUES (?,?,?,?)",
                        (email.lower(), hash_pw(password), role, int(time.time())))
        uid = cur.lastrowid
        cols = ",".join(profile)
        c.execute(f"INSERT INTO profiles(user_id,{cols}) VALUES ({','.join('?' * (len(profile) + 1))})", (uid, *profile.values()))
        for e in equipment or []:
            c.execute("INSERT INTO equipment(user_id,name,power_w,quantity,circuit,priority,defer_h) VALUES (?,?,?,?,?,?,?)", (uid, e["name"], e["power_w"], e["quantity"], e["circuit"], e["priority"], e["defer_h"]))
        c.commit()
        return uid
    finally:
        c.close()


def set_password(email, password):
    c = conn()
    n = c.execute("UPDATE users SET pw_hash=? WHERE email=?", (hash_pw(password), email.lower())).rowcount
    c.execute("DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE email=?)", (email.lower(),))        # un changement de mot de passe deconnecte tous les appareils
    c.commit()
    c.close()
    return n


def update_profile(uid, profile):
    c = conn()
    c.execute(f"UPDATE profiles SET {','.join(f'{k}=?' for k in profile)} WHERE user_id=?", (*profile.values(), uid))
    c.commit()
    c.close()


def set_equipment(uid, items):
    c = conn()
    c.execute("DELETE FROM equipment WHERE user_id=?", (uid,))
    for e in items:
        c.execute("INSERT INTO equipment(user_id,name,power_w,quantity,circuit,priority,defer_h) VALUES (?,?,?,?,?,?,?)", (uid, e["name"], e["power_w"], e["quantity"], e["circuit"], e["priority"], e["defer_h"]))
    c.commit()
    c.close()


def by_email(email):
    c = conn()
    r = c.execute("SELECT * FROM users WHERE email=?", ((email or "").lower(),)).fetchone()
    c.close()
    return r


def get_equipment(uid):
    c = conn()
    rows = c.execute("SELECT name,power_w,quantity,circuit,priority,defer_h FROM equipment WHERE user_id=? ORDER BY id", (uid,)).fetchall()
    c.close()
    return [shedding.normalize(dict(r)) for r in rows]


def get_user(uid):
    c = conn()
    u = c.execute("SELECT id,email,role,created_at FROM users WHERE id=?", (uid,)).fetchone()
    p = c.execute("SELECT * FROM profiles WHERE user_id=?", (uid,)).fetchone()
    c.close()
    if not u:
        return None
    out = dict(u)
    out["profile"] = dict(p) if p else {}
    out["equipment"] = get_equipment(uid)
    return out


def count_by_role():
    c = conn()
    rows = c.execute("SELECT role, COUNT(*) n FROM users GROUP BY role").fetchall()
    c.close()
    return {r["role"]: r["n"] for r in rows}


# ---------- sessions ----------
def new_session(uid):
    token = secrets.token_urlsafe(32)
    c = conn()
    c.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
    c.execute("INSERT INTO sessions VALUES (?,?,?)", (_th(token), uid, int(time.time()) + SESSION_SECONDS))
    c.execute("DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN (SELECT token_hash FROM sessions WHERE user_id=? ORDER BY expires_at DESC LIMIT 10)", (uid, uid))      # 10 appareils connectes au plus
    c.commit()
    c.close()
    return token


def session_user(token):
    if not token:
        return None
    c = conn()
    r = c.execute("SELECT user_id FROM sessions WHERE token_hash=? AND expires_at>?", (_th(token), int(time.time()))).fetchone()
    c.close()
    return get_user(r["user_id"]) if r else None


def end_session(token):
    c = conn()
    c.execute("DELETE FROM sessions WHERE token_hash=?", (_th(token or ""),))
    c.commit()
    c.close()


# ---------- capteurs ----------
def create_device(uid, name):
    """La cle secrete n'est montree qu'une fois ; seul son hash est conserve."""
    c = conn()
    try:
        if c.execute("SELECT COUNT(*) FROM devices WHERE user_id=?", (uid,)).fetchone()[0] >= MAX_DEVICES:
            return None, None
        dev_id = "KW-" + secrets.token_hex(3).upper()
        key = secrets.token_urlsafe(24)
        c.execute("INSERT INTO devices(id,user_id,name,key_hash,created_at) VALUES (?,?,?,?,?)",
                  (dev_id, uid, (name or "Capteur principal").strip()[:40], _th(key), int(time.time())))
        c.commit()
        return dev_id, key
    finally:
        c.close()


def list_devices(uid):
    now = int(time.time())
    c = conn()
    rows = c.execute("SELECT id,name,created_at,last_seen,kwh_total FROM devices WHERE user_id=? ORDER BY created_at", (uid,)).fetchall()
    c.close()
    return [dict(r, online=bool(r["last_seen"] and now - r["last_seen"] <= ONLINE_SECONDS)) for r in rows]


def delete_device(uid, dev_id):
    c = conn()
    c.execute("DELETE FROM devices WHERE id=? AND user_id=?", (dev_id, uid))
    c.commit()
    c.close()


def device_auth(dev_id, key):
    c = conn()
    r = c.execute("SELECT id,user_id,last_seen,key_hash FROM devices WHERE id=?", (dev_id or "",)).fetchone()
    c.close()
    if r and hmac.compare_digest(r["key_hash"], _th(key or "")):
        return dict(id=r["id"], user_id=r["user_id"], last_seen=r["last_seen"])
    return None


def validate_reading(d):
    """power_w est obligatoire ; les autres champs ont des valeurs par defaut (tension 230 V, cos phi 0,92)."""
    def num(k, lo, hi, default=None):
        v = d.get(k, default)
        if v is None:
            raise ValueError(f"{k} manquant")
        v = float(v)
        if not lo <= v <= hi:
            raise ValueError(f"{k} hors limites")
        return v
    try:
        power = num("power_w", 0, 60000)
        volt = num("voltage", 100, 300, 230)
        cosphi = num("cosphi", 0.3, 1.0, 0.92)
        current = num("current_a", 0, 250, power / (volt * cosphi))
    except (ValueError, TypeError) as e:
        return None, str(e)
    return dict(power_w=power, voltage=volt, current_a=current, cosphi=cosphi), None


def add_reading(dev, r):
    now = int(time.time())
    dt = min(120, max(0, now - (dev["last_seen"] or now)))
    kwh = r["power_w"] * dt / 3_600_000
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    c = conn()
    c.execute("INSERT INTO readings(device_id,ts,power_w,voltage,current_a,cosphi) VALUES (?,?,?,?,?,?)",
              (dev["id"], now, r["power_w"], r["voltage"], r["current_a"], r["cosphi"]))
    c.execute("UPDATE devices SET last_seen=?, kwh_total=kwh_total+? WHERE id=?", (now, kwh, dev["id"]))
    c.execute("INSERT INTO daily(device_id,day,kwh) VALUES (?,?,?) ON CONFLICT(device_id,day) DO UPDATE SET kwh=kwh+excluded.kwh", (dev["id"], day, kwh))
    c.execute("DELETE FROM readings WHERE device_id=? AND ts<?", (dev["id"], now - 24 * 3600))
    p = r["power_w"]
    c.execute("""INSERT INTO hourly(device_id,ts_hour,n,sum_w,max_w,sumsq_w) VALUES (?,?,1,?,?,?)
                 ON CONFLICT(device_id,ts_hour) DO UPDATE SET n=n+1, sum_w=sum_w+excluded.sum_w, max_w=MAX(max_w,excluded.max_w), sumsq_w=sumsq_w+excluded.sumsq_w""",
              (dev["id"], now - now % 3600, p, p, p * p))
    c.execute("DELETE FROM hourly WHERE device_id=? AND ts_hour<?", (dev["id"], now - 120 * 86400))
    c.commit()
    c.close()


def latest_reading(uid):
    """Derniere mesure parmi les capteurs du compte (meme ancienne). Retourne None s'il n'y en a aucune."""
    c = conn()
    r = c.execute("""SELECT d.id AS device_id, d.name, r.ts, r.power_w, r.voltage, r.current_a, r.cosphi
                     FROM devices d JOIN readings r ON r.device_id = d.id
                     WHERE d.user_id=? ORDER BY r.ts DESC LIMIT 1""", (uid,)).fetchone()
    c.close()
    return dict(r) if r else None


def series_24h(uid, bucket=600):
    """Puissance moyenne et maximale par tranche de 10 minutes sur 24 h, tous capteurs du compte additionnes."""
    since = int(time.time()) - 24 * 3600
    c = conn()
    rows = c.execute("""SELECT (r.ts / ?) * ? AS b, r.device_id, AVG(r.power_w) a, MAX(r.power_w) m
                        FROM readings r JOIN devices d ON d.id = r.device_id
                        WHERE d.user_id=? AND r.ts>=? GROUP BY b, r.device_id ORDER BY b""", (bucket, bucket, uid, since)).fetchall()
    c.close()
    agg = {}
    for r in rows:
        x = agg.setdefault(r["b"], [0.0, 0.0])
        x[0] += r["a"]
        x[1] += r["m"]
    return [dict(ts=b, avg_w=round(v[0], 1), max_w=round(v[1], 1)) for b, v in sorted(agg.items())]


def daily_kwh(uid, days=14):
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - (days - 1) * 86400))
    c = conn()
    rows = c.execute("""SELECT day, SUM(kwh) k FROM daily d JOIN devices v ON v.id = d.device_id
                        WHERE v.user_id=? AND day>=? GROUP BY day ORDER BY day""", (uid, since)).fetchall()
    c.close()
    return [dict(day=r["day"], kwh=round(r["k"], 3)) for r in rows]


def all_clients():
    """Pour le compte STEG : tous les clients avec profil, equipements, capteurs et derniere mesure."""
    now = int(time.time())
    c = conn()
    users = c.execute("""SELECT u.id, u.email, u.created_at, p.* FROM users u JOIN profiles p ON p.user_id = u.id
                         WHERE u.role='client' ORDER BY u.created_at DESC""").fetchall()
    eq = {}
    for r in c.execute("SELECT user_id, name, power_w, quantity, circuit, priority, defer_h FROM equipment ORDER BY id"):
        eq.setdefault(r["user_id"], []).append(shedding.normalize(dict(name=r["name"], power_w=r["power_w"], quantity=r["quantity"], circuit=r["circuit"], priority=r["priority"], defer_h=r["defer_h"])))
    devs = {}
    for r in c.execute("SELECT user_id, COUNT(*) n, MAX(last_seen) ls, SUM(kwh_total) k FROM devices GROUP BY user_id"):
        devs[r["user_id"]] = dict(n=r["n"], last_seen=r["ls"], kwh=r["k"] or 0)
    last = {}
    for r in c.execute("""SELECT d.user_id, r.ts, r.power_w, r.current_a FROM readings r JOIN devices d ON d.id = r.device_id
                          WHERE r.id IN (SELECT MAX(id) FROM readings GROUP BY device_id)"""):
        cur = last.get(r["user_id"])
        if not cur or r["ts"] > cur["ts"]:
            last[r["user_id"]] = dict(ts=r["ts"], power_w=r["power_w"], current_a=r["current_a"])
        else:
            pass
    c.close()
    out = []
    for u in users:
        d = devs.get(u["id"], dict(n=0, last_seen=None, kwh=0))
        lr = last.get(u["id"])
        online = bool(lr and now - lr["ts"] <= ONLINE_SECONDS)
        out.append(dict(
            id=u["id"], email=u["email"], created_at=u["created_at"], full_name=u["full_name"], phone=u["phone"],
            governorate=u["governorate"], delegation=u["delegation"], address=u["address"], postal_code=u["postal_code"],
            lat=u["lat"], lng=u["lng"], steg_ref=u["steg_ref"], meter_number=u["meter_number"], breaker_kw=u["breaker_kw"],
            housing_type=u["housing_type"], occupants=u["occupants"], equipment=eq.get(u["id"], []), shed_level=u["shed_level"],
            devices=d["n"], kwh_total=round(d["kwh"], 3), online=online, last_seen=lr["ts"] if lr else None,
            power_w=round(lr["power_w"]) if lr else None, current_a=lr["current_a"] if lr else None))
    return out


def alerts_for(breaker_kw, lr, online):
    """Alertes calculees uniquement a partir de la VRAIE derniere mesure et des limites declarees."""
    out = []
    if not lr:
        return out
    limit_a = (breaker_kw or 0) * 1000 / 230
    if online and limit_a and (lr.get("current_a") or 0) > 0.8 * limit_a:
        out.append(dict(level="warn", kind="overload", text=f"Courant eleve : {lr['current_a']:.1f} A pour un disjoncteur de {limit_a:.0f} A (plus de 80 %)."))
    if not online:
        out.append(dict(level="info", kind="offline", text="Le capteur n'envoie plus de mesures depuis plus de 2 minutes."))
    return out


def recent_power(uid, seconds=600, now=None):
    """Puissance moyenne mesuree sur les `seconds` dernieres secondes (tous capteurs du compte additionnes). None si aucune mesure."""
    now = int(now or time.time())
    c = conn()
    rows = c.execute("""SELECT AVG(r.power_w) a FROM readings r JOIN devices d ON d.id = r.device_id
                        WHERE d.user_id=? AND r.ts>=? GROUP BY r.device_id""", (uid, now - seconds)).fetchall()
    c.close()
    vals = [r["a"] for r in rows if r["a"] is not None]
    return sum(vals) if vals else None


def hourly_series(uid, hours=24 * 35):
    """Historique PAR HEURE du compte (tous capteurs additionnes) : {debut_d_heure_epoch: {mean_w, max_w, std_w, n}}.
    Somme des capteurs : moyenne = somme des moyennes, ecart-type = racine de la somme des variances (capteurs supposes independants)."""
    since = int(time.time()) - hours * 3600
    c = conn()
    rows = c.execute("""SELECT h.ts_hour, h.n, h.sum_w, h.max_w, h.sumsq_w FROM hourly h JOIN devices d ON d.id = h.device_id
                        WHERE d.user_id=? AND h.ts_hour>=? ORDER BY h.ts_hour""", (uid, since)).fetchall()
    c.close()
    out = {}
    for r in rows:
        mean = r["sum_w"] / r["n"]
        var = max(0.0, r["sumsq_w"] / r["n"] - mean * mean)
        o = out.setdefault(r["ts_hour"], dict(mean_w=0.0, max_w=0.0, var=0.0, n=0))
        o["mean_w"] += mean
        o["max_w"] += r["max_w"]
        o["var"] += var
        o["n"] = max(o["n"], r["n"])
    return {h: dict(mean_w=v["mean_w"], max_w=v["max_w"], std_w=v["var"] ** 0.5, n=v["n"]) for h, v in out.items()}


def set_shed_level(uid, level):
    """Dernier palier de delestage que le foyer accepte (0 = ne participe pas, 1 a 4)."""
    c = conn()
    c.execute("UPDATE profiles SET shed_level=? WHERE user_id=?", (int(level), uid))
    c.commit()
    c.close()
