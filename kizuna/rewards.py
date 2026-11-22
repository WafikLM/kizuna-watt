"""Recompenses : codes de reduction sur la facture STEG, gagnes en respectant une pointe.

PRINCIPES
- Un code n'est delivre que si la reduction est MESUREE par le capteur du foyer : sa puissance moyenne pendant la pointe doit etre au moins
  REDUCTION_MIN en dessous de SON habitude aux memes heures (jours precedents, voir anomaly.py). Sans capteur ou sans historique : pas de code,
  et le foyer voit pourquoi. Le foyer doit aussi avoir declare « c'est fait » AVANT la fin de la pointe.
- Le code (16 chiffres, comme une carte de recharge) est calcule par HMAC-SHA256 avec une CLE SECRETE du serveur (fichier reward.key, ou variable
  KIZUNA_SECRET) a partir de (pointe, foyer, nonce aleatoire) + un chiffre de controle (Luhn) qui detecte les fautes de frappe. L'algorithme est donc
  confidentiel PAR LA CLE : meme en connaissant la methode, on ne peut pas fabriquer de code sans la cle. La base ne contient que l'empreinte HMAC du
  code (recherche) : une fuite de la base seule ne revele aucun code. SAUVEGARDEZ reward.key : sans elle, plus aucun code ne peut etre verifie.
- Usage unique : l'encaissement est atomique (un code ne peut pas etre utilise deux fois, meme en simultane). Validite : 90 jours.
- La STEG encaisse sur un ecran dedie (au guichet ou dans le paiement en ligne) ; elle ne voit JAMAIS la liste des codes, seulement des totaux.
- Le montant de chaque recompense est fixe par la STEG pour chaque pointe : c'est un choix de politique, pas un calcul.
Limites : un capteur peut etre trafique par son proprietaire ; une vraie mise en production demande un compteur certifie.
"""
import hashlib
import hmac
import os
import secrets
import time

from . import anomaly, db

REDUCTION_MIN = 0.15          # HYPOTHESE : -15 % par rapport a l'habitude du foyer
Z_MIN = 1.0                   # HYPOTHESE : et au moins 1 ecart-type sous sa moyenne (variation naturelle du foyer)
MIN_COVERAGE = 0.7            # au moins 70 % des heures de la pointe avec mesures ET habitude
MIN_SAMPLES = 10
VALIDITY_S = 90 * 86400
SETTLE_DELAY_S = 300          # on evalue 5 min apres la fin de la pointe
SETTLE_WINDOW_S = 3 * 86400
MAX_FAILS, FAIL_WINDOW_S = 10, 600
FAILS = {}                    # user_id (STEG) -> [horodatages d'echecs]

SCHEMA = """
CREATE TABLE IF NOT EXISTS peak_results (
  peak_id INTEGER NOT NULL REFERENCES peaks(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  result TEXT NOT NULL, measured_w REAL, baseline_w REAL, reduction REAL, ts INTEGER NOT NULL,
  PRIMARY KEY (peak_id, user_id)
);
CREATE TABLE IF NOT EXISTS rewards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  peak_id INTEGER NOT NULL REFERENCES peaks(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  value_millimes INTEGER NOT NULL, nonce TEXT NOT NULL, code_hash TEXT NOT NULL UNIQUE,
  issued_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
  redeemed_at INTEGER, redeemed_by INTEGER, bill_ref TEXT, applied_millimes INTEGER,
  UNIQUE (peak_id, user_id)
);
"""

REASONS = {
    "rewarded": "Reduction mesuree : code delivre.",
    "not_committed": "Vous n'aviez pas declare « c'est fait » avant la fin de la pointe.",
    "no_sensor": "Aucun capteur relie : la reduction ne peut pas etre mesuree.",
    "no_data": "Mesures insuffisantes pendant la pointe (capteur hors ligne ?).",
    "no_baseline": "Habitude pas encore connue (il faut au moins 5 jours de mesures aux memes heures).",
    "insufficient": "Reduction mesuree insuffisante par rapport a votre habitude.",
}


def init(c):
    c.executescript(SCHEMA)


# ---------- cle secrete et codes ----------
_KEY = None


def _key():
    global _KEY
    if _KEY:
        return _KEY
    env = os.environ.get("KIZUNA_SECRET")
    if env:
        _KEY = env.encode()
        return _KEY
    path = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "reward.key")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    _KEY = open(path).read().strip().encode()
    return _KEY


def _luhn(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10


def make_code(peak_id, user_id, nonce):
    mac = hmac.new(_key(), f"code|{peak_id}|{user_id}|{nonce}".encode(), hashlib.sha256).digest()
    body = str(int.from_bytes(mac[:8], "big") % 10 ** 15).zfill(15)
    return body + str(_luhn(body))


def pretty(code):
    return " ".join(code[i:i + 4] for i in range(0, 16, 4))


def _lookup(code):
    return hmac.new(_key(), b"lookup|" + code.encode(), hashlib.sha256).hexdigest()


def normalize_code(s):
    d = "".join(ch for ch in str(s) if ch.isdigit())
    return d if len(d) == 16 and _luhn(d[:15]) == int(d[15]) else None


# ---------- evaluation d'une pointe ----------
def _hours(peak):
    """Heures pleines contenues dans la pointe ; a defaut (pointe courte ou non alignee), les heures qui la recouvrent."""
    s, e = peak["start_ts"], peak["end_ts"]
    first = s if s % 3600 == 0 else s - s % 3600 + 3600
    full = list(range(first, e - 3599, 3600))
    return full or list(range(s - s % 3600, e, 3600))


def evaluate(uid, peak, has_device):
    """Compare la puissance moyenne mesuree pendant la pointe a l'habitude du foyer : les AUTRES jours, sur la meme fenetre horaire,
    de meme type (semaine / week-end) s'il y en a assez. Il faut une baisse d'au moins REDUCTION_MIN ET qui sorte de la variation
    naturelle du foyer (au moins Z_MIN ecarts-types sous sa moyenne), sinon un foyer irregulier serait recompense par hasard."""
    if not has_device:
        return dict(result="no_sensor")
    series = db.hourly_series(uid, 24 * 35)
    hours = _hours(peak)
    ok_hours = [h for h in hours if series.get(h) and series[h]["n"] >= MIN_SAMPLES]
    if len(ok_hours) / max(1, len(hours)) < MIN_COVERAGE:
        return dict(result="no_data")
    meas = sum(series[h]["mean_w"] for h in ok_hours) / len(ok_hours)
    weekend = time.localtime(peak["start_ts"]).tm_wday >= 5
    days = []                         # (est_weekend, moyenne de la fenetre ce jour-la)
    for d in range(1, 35):
        vals = [series.get(h - d * 86400) for h in ok_hours]
        if all(v and v["n"] >= MIN_SAMPLES for v in vals):
            days.append((time.localtime(ok_hours[0] - d * 86400).tm_wday >= 5, sum(v["mean_w"] for v in vals) / len(vals)))
    same = [m for w, m in days if w == weekend]
    ref = same if len(same) >= anomaly.MIN_DAYS else [m for _, m in days]
    if len(ref) < anomaly.MIN_DAYS:
        return dict(result="no_baseline")
    mu = sum(ref) / len(ref)
    sd = (sum((m - mu) ** 2 for m in ref) / (len(ref) - 1)) ** 0.5
    if mu <= 0:
        return dict(result="no_baseline")
    red = 1 - meas / mu
    good = red >= REDUCTION_MIN and meas <= mu - Z_MIN * sd
    return dict(result="rewarded" if good else "insufficient", measured_w=round(meas), baseline_w=round(mu), reduction=round(red, 3))


def settle(c, uid, now=None):
    """Evalue les pointes terminees (avec recompense) de ce foyer pas encore evaluees ; delivre un code si la reduction est mesuree."""
    now = int(now or time.time())
    gov = (c.execute("SELECT governorate FROM profiles WHERE user_id=?", (uid,)).fetchone() or {"governorate": ""})["governorate"] or ""
    rows = c.execute("""SELECT * FROM peaks WHERE cancelled=0 AND reward_millimes>0 AND end_ts<=? AND end_ts>? AND (governorate='' OR governorate=?)
                        AND id NOT IN (SELECT peak_id FROM peak_results WHERE user_id=?)""", (now - SETTLE_DELAY_S, now - SETTLE_WINDOW_S, gov, uid)).fetchall()
    has_dev = c.execute("SELECT 1 FROM devices WHERE user_id=? LIMIT 1", (uid,)).fetchone() is not None
    for p in rows:
        ack = c.execute("SELECT status,ts FROM peak_acks WHERE peak_id=? AND user_id=?", (p["id"], uid)).fetchone()
        if not ack or ack["status"] != "done" or ack["ts"] > p["end_ts"]:
            ev = dict(result="not_committed")
        else:
            ev = evaluate(uid, dict(p), has_dev)
        c.execute("INSERT OR IGNORE INTO peak_results(peak_id,user_id,result,measured_w,baseline_w,reduction,ts) VALUES (?,?,?,?,?,?,?)",
                  (p["id"], uid, ev["result"], ev.get("measured_w"), ev.get("baseline_w"), ev.get("reduction"), now))
        if ev["result"] == "rewarded":
            nonce = secrets.token_hex(8)
            code = make_code(p["id"], uid, nonce)
            c.execute("INSERT OR IGNORE INTO rewards(peak_id,user_id,value_millimes,nonce,code_hash,issued_at,expires_at) VALUES (?,?,?,?,?,?,?)",
                      (p["id"], uid, p["reward_millimes"], nonce, _lookup(code), now, now + VALIDITY_S))
    c.commit()


def settle_all(c, now=None):
    for r in c.execute("SELECT id FROM users WHERE role='client'").fetchall():
        settle(c, r["id"], now)


def for_client(c, uid, now=None):
    now = int(now or time.time())
    out = []
    for r in c.execute("SELECT * FROM rewards WHERE user_id=? ORDER BY issued_at DESC", (uid,)):
        status = "used" if r["redeemed_at"] else "expired" if r["expires_at"] < now else "available"
        out.append(dict(id=r["id"], peak_id=r["peak_id"], value_millimes=r["value_millimes"], status=status, expires_at=r["expires_at"],
                        code=pretty(make_code(r["peak_id"], r["user_id"], r["nonce"])) if status == "available" else None,
                        redeemed_at=r["redeemed_at"], applied_millimes=r["applied_millimes"]))
    return out


def summary(c, now=None):
    """Totaux pour la STEG (jamais de codes)."""
    now = int(now or time.time())
    rows = c.execute("SELECT value_millimes v, redeemed_at r, applied_millimes a, expires_at e FROM rewards").fetchall()
    return dict(issued=len(rows), issued_millimes=sum(r["v"] for r in rows), redeemed=sum(1 for r in rows if r["r"]), redeemed_millimes=sum(r["a"] or 0 for r in rows),
                pending=sum(1 for r in rows if not r["r"] and r["e"] >= now), pending_millimes=sum(r["v"] for r in rows if not r["r"] and r["e"] >= now),
                expired=sum(1 for r in rows if not r["r"] and r["e"] < now))


# ---------- encaissement sur une facture ----------
def redeem(c, steg_id, codes, amount_millimes, bill_ref="", confirm=False, now=None):
    """Applique des codes sur une facture. confirm=False : simulation, rien n'est consomme. Retourne (resultat, erreur)."""
    now = int(now or time.time())
    FAILS[steg_id] = [t for t in FAILS.get(steg_id, []) if now - t < FAIL_WINDOW_S]
    if len(FAILS[steg_id]) >= MAX_FAILS:
        return None, "Trop d'essais de codes invalides : reessayez dans quelques minutes."
    if not codes or len(codes) > 10:
        return None, "Entrez entre 1 et 10 codes."
    if not 0 < amount_millimes <= 100_000_000:
        return None, "Montant de facture invalide."
    remaining, lines, seen = amount_millimes, [], set()
    for raw in codes:
        code = normalize_code(raw)
        if not code:
            lines.append(dict(input=str(raw)[:24], status="invalid", message="Code invalide (16 chiffres, faute de frappe ?)")); FAILS[steg_id].append(now); continue
        if code in seen:
            lines.append(dict(input=pretty(code), status="duplicate", message="Code deja saisi")); continue
        seen.add(code)
        r = c.execute("SELECT * FROM rewards WHERE code_hash=?", (_lookup(code),)).fetchone()
        if not r:
            lines.append(dict(input=pretty(code), status="unknown", message="Code inconnu")); FAILS[steg_id].append(now); continue
        if r["redeemed_at"]:
            lines.append(dict(input=pretty(code), status="used", message="Code deja utilise")); continue
        if r["expires_at"] < now:
            lines.append(dict(input=pretty(code), status="expired", message="Code expire")); continue
        if remaining <= 0:
            lines.append(dict(input=pretty(code), status="not_needed", message="Facture deja couverte : code conserve, non consomme")); continue
        applied = min(r["value_millimes"], remaining)
        if confirm:       # consommation atomique : une seule requete peut reussir
            cur = c.execute("UPDATE rewards SET redeemed_at=?, redeemed_by=?, bill_ref=?, applied_millimes=? WHERE id=? AND redeemed_at IS NULL", (now, steg_id, bill_ref[:40], applied, r["id"]))
            if cur.rowcount != 1:
                lines.append(dict(input=pretty(code), status="used", message="Code deja utilise")); continue
        remaining -= applied
        lines.append(dict(input=pretty(code), status="applied" if confirm else "valid", value_millimes=r["value_millimes"], applied_millimes=applied,
                          message="Reduction appliquee" if confirm else "Valide (non consomme : simulation)"))
    if confirm:
        c.commit()
    discount = amount_millimes - remaining
    return dict(confirmed=bool(confirm), amount_millimes=amount_millimes, discount_millimes=discount, net_millimes=remaining, lines=lines), None
