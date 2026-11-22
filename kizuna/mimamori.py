"""Veille « mimamori » (見守り) : alerter un proche quand un foyer n'a plus d'activite electrique alors qu'elle est attendue.

PRINCIPE (uniquement a partir des mesures du capteur, aucune camera, aucun micro) :
  - Un foyer est « actif » pendant une heure si sa puissance moyenne depasse son SOCLE (consommation de fond : frigo, box...)
    d'au moins max(40 W, 30 %). Le socle = 20e percentile des puissances horaires des 14 derniers jours.
  - Le rythme habituel = les plus longues periodes sans activite des 14 derniers jours (en general la nuit). On retient le 90e percentile.
  - Alerte « attention » si la periode d'inactivite en cours depasse le rythme habituel de ATTN_H heures ; « alerte » apres ALERT_H heures :
    un SMS est alors envoye au proche designe. L'alerte se leve d'elle-meme des que l'activite reprend (et le proche est rassure).
  - Il faut au moins MIN_DAYS jours d'historique. Capteur hors ligne = on ne juge pas (ce n'est pas de l'inactivite).
CONTROLE PAR LA PERSONNE : activation volontaire avec consentement, proche choisi par elle, bouton « Je vais bien », mode voyage (pause).
Le proche ne voit PAS la consommation : seulement un etat (ok / attention / alerte) et l'heure de la derniere activite.

LIMITES : une personne qui sort ou voyage sans activer la pause peut declencher une fausse alerte ; une personne qui ne consomme presque rien
(pas d'appareil allume) est difficile a suivre ; ce n'est pas un dispositif medical. Les SMS sont SIMULES : ils sont ecrits dans la table sms_outbox
(aucun envoi reel tant qu'une passerelle SMS n'est pas branchee, voir send_sms).
"""
import os
import re
import secrets
import time

from . import db, sms

MIN_SAMPLES = 10
MIN_DAYS = 5
ATTN_H = 1.5
ALERT_H = 3.0
DEFAULT_TYPICAL_H = 9.0
OFFLINE_S = 1800
SNOOZE_S = 3 * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS mimamori (
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  enabled INTEGER NOT NULL DEFAULT 0, contact_name TEXT, contact_phone TEXT, token TEXT,
  pause_until INTEGER, consent_ts INTEGER, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS mimamori_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  ts INTEGER NOT NULL, level TEXT NOT NULL, gap_h REAL, message TEXT, resolved_ts INTEGER, resolved_by TEXT
);
CREATE TABLE IF NOT EXISTS sms_outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER, to_phone TEXT, to_name TEXT, body TEXT, ts INTEGER NOT NULL, kind TEXT, status TEXT NOT NULL DEFAULT 'simule'
);
"""


def init(c):
    c.executescript(SCHEMA)
    if "contact_email" not in {r[1] for r in c.execute("PRAGMA table_info(mimamori)")}:
        c.execute("ALTER TABLE mimamori ADD COLUMN contact_email TEXT")


def _pct(vals, p):
    v = sorted(vals)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def analyse(uid, now=None):
    """Etat de la veille d'un foyer. Retourne toujours un dict avec `state` : learning | offline | ok | attention | alert."""
    now = int(now or time.time())
    series = db.hourly_series(uid, 24 * 16)
    hrs = sorted(h for h, v in series.items() if v["n"] >= MIN_SAMPLES)
    if len(hrs) < 24 * MIN_DAYS:
        return dict(state="learning", days=round(len(hrs) / 24, 1), needed=MIN_DAYS)
    means = [series[h]["mean_w"] for h in hrs]
    floor = _pct(means, 20)
    thr = floor + max(40.0, 0.3 * floor)
    active = {h: series[h]["mean_w"] > thr for h in hrs}
    runs, run, prev = [], 0, None
    for h in hrs:                                   # periodes d'inactivite terminees (une coupure de donnees interrompt la periode)
        if prev is not None and h - prev != 3600 and run:
            runs.append(run); run = 0
        if active[h]:
            if run:
                runs.append(run); run = 0
        else:
            run += 1
        prev = h
    typical = _pct(runs, 90) if len(runs) >= 5 else DEFAULT_TYPICAL_H
    act_hours = [h for h in hrs if active[h]]
    last_active = (act_hours[-1] + 3600 if act_hours[-1] + 3600 <= now else now) if act_hours else None
    cur = now - now % 3600
    gap = 0.0 if active.get(cur) else ((now - last_active) / 3600 if last_active else None)
    lr = db.latest_reading(uid)
    tl = []
    for h in range(cur - 47 * 3600, cur + 1, 3600):
        s = series.get(h)
        tl.append(dict(ts=h, w=round(s["mean_w"]) if s and s["n"] >= MIN_SAMPLES else None, active=bool(active.get(h)) if h in active else None))
    out = dict(floor_w=round(floor), thr_w=round(thr), typical_h=round(typical, 1), gap_h=None if gap is None else round(gap, 1), last_active_ts=last_active, timeline=tl, days=round(len(hrs) / 24, 1))
    if not lr or now - lr["ts"] > OFFLINE_S:
        out["state"] = "offline"
    elif gap is None:
        out["state"] = "learning"
    elif gap > typical + ALERT_H:
        out["state"] = "alert"
    elif gap > typical + ATTN_H:
        out["state"] = "attention"
    else:
        out["state"] = "ok"
    return out


# ---------- reglages ----------
def get(c, uid):
    r = c.execute("SELECT * FROM mimamori WHERE user_id=?", (uid,)).fetchone()
    return dict(r) if r else dict(user_id=uid, enabled=0, contact_name="", contact_phone="", contact_email="", token=None, pause_until=None, consent_ts=None)


def save(c, uid, d, now=None):
    now = int(now or time.time())
    enabled = bool(d.get("enabled"))
    name = str(d.get("contact_name") or "").strip()[:60]
    phone = sms.normalize(str(d.get("contact_phone") or "")) if d.get("contact_phone") else None
    email = str(d.get("contact_email") or "").strip().lower()
    if email and (len(email) > 120 or not sms.EMAIL_RE.match(email)):
        return None, "Adresse e-mail du proche invalide."
    if enabled and not d.get("consent"):
        return None, "Le consentement est requis pour activer la veille."
    if enabled and (len(name) < 2 or not (email or phone)):
        return None, "Indiquez le nom et l'adresse e-mail du proche a prevenir."
    cur = get(c, uid)
    token = cur.get("token") or secrets.token_hex(16)
    c.execute("""INSERT INTO mimamori(user_id,enabled,contact_name,contact_phone,contact_email,token,pause_until,consent_ts,created_at) VALUES (?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled, contact_name=excluded.contact_name, contact_phone=excluded.contact_phone, contact_email=excluded.contact_email, token=excluded.token,
                 consent_ts=COALESCE(mimamori.consent_ts, excluded.consent_ts)""",
              (uid, int(enabled), name, phone or "", email, token, None, now if enabled else None, now))
    c.commit()
    return get(c, uid), None


def pause(c, uid, days, now=None):
    now = int(now or time.time())
    try:
        days = max(0, min(30, int(days)))
    except (TypeError, ValueError):
        days = 0
    c.execute("UPDATE mimamori SET pause_until=? WHERE user_id=?", (now + days * 86400 if days else None, uid))
    if days:
        _resolve(c, uid, now, "pause")
    c.commit()


def confirm_ok(c, uid, now=None):
    """Bouton « Je vais bien » : leve les alertes et suspend la veille quelques heures."""
    now = int(now or time.time())
    n = _resolve(c, uid, now, "client")
    c.execute("UPDATE mimamori SET pause_until=? WHERE user_id=?", (now + SNOOZE_S, uid))
    c.commit()
    return n


# ---------- evenements et SMS simules ----------
def _first_name(c, uid):
    r = c.execute("SELECT full_name FROM profiles WHERE user_id=?", (uid,)).fetchone()
    n = re.sub(r"\(.*?\)", "", (r["full_name"] if r else "") or "").strip()
    titles = {"mme", "m", "mr", "mlle", "dr", "madame", "monsieur", "famille", "mademoiselle"}
    words = [w for w in n.replace(".", " ").split() if w.lower() not in titles]
    return words[0] if words else "votre proche"


def notify(c, uid, m, subject, body, kind, now=None):
    """Previent le proche : par e-mail s'il en a un, sinon par SMS (file de kizuna/sms.py ; simulation par defaut, envoi reel si un fournisseur est configure)."""
    if m.get("contact_email"):
        sms.enqueue(c, uid, m["contact_email"], m["contact_name"], body, kind, now, channel="email", subject=subject)
    elif m.get("contact_phone"):
        sms.enqueue(c, uid, m["contact_phone"], m["contact_name"], body, kind, now)


def _url(token):
    return os.environ.get("KIZUNA_PUBLIC_URL", "").rstrip("/") + "/mimamori?t=" + token


def _resolve(c, uid, now, by):
    rows = c.execute("SELECT id, level FROM mimamori_events WHERE user_id=? AND resolved_ts IS NULL", (uid,)).fetchall()
    for r in rows:
        c.execute("UPDATE mimamori_events SET resolved_ts=?, resolved_by=? WHERE id=?", (now, by, r["id"]))
    return rows


def scan(c, now=None):
    """Passe sur tous les foyers qui ont active la veille ; cree / leve les alertes et ecrit les SMS. Appelee toutes les 30 s par le serveur."""
    now = int(now or time.time())
    for m in c.execute("SELECT * FROM mimamori WHERE enabled=1").fetchall():
        uid = m["user_id"]
        if m["pause_until"] and m["pause_until"] > now:
            continue
        a = analyse(uid, now)
        open_ev = c.execute("SELECT * FROM mimamori_events WHERE user_id=? AND resolved_ts IS NULL ORDER BY id DESC", (uid,)).fetchall()
        who = _first_name(c, uid)
        if a["state"] in ("attention", "alert"):
            cur = open_ev[0] if open_ev else None
            if cur is None or (cur["level"] == "attention" and a["state"] == "alert"):
                _resolve(c, uid, now, "escalade") if cur else None
                gap, typ = a["gap_h"], a["typical_h"]
                msg = (f"{who} : aucune activite electrique depuis {gap:.0f} h (habituel : jusqu'a {typ:.0f} h)." if a["state"] == "alert"
                       else f"{who} : activite plus tardive que d'habitude ({gap:.0f} h sans activite, habituel : {typ:.0f} h).")
                c.execute("INSERT INTO mimamori_events(user_id,ts,level,gap_h,message) VALUES (?,?,?,?,?)", (uid, now, a["state"], gap, msg))
                if a["state"] == "alert" and (m["contact_email"] or m["contact_phone"]):
                    body = (f"Kizuna Watt · Veille : {who} n'a aucune activite electrique depuis {gap:.0f} h (habituel : {typ:.0f} h). Merci de l'appeler. {_url(m['token'])}\n"
                            f"【見守り】{who}さん宅で{gap:.0f}時間、電気の使用が確認できません（普段は最長{typ:.0f}時間）。ご連絡をお願いします。")
                    notify(c, uid, m, f"Kizuna Watt · Veille : {who} - aucune activite / 活動が確認できません", body, "alerte", now)
        elif a["state"] == "ok" and open_ev:
            was_alert = any(r["level"] == "alert" for r in open_ev)
            _resolve(c, uid, now, "activite")
            if was_alert and (m["contact_email"] or m["contact_phone"]):
                notify(c, uid, m, f"Kizuna Watt · Veille : activite reprise chez {who} / 電気の使用が再開", f"Kizuna Watt · Veille : activite reprise chez {who}, tout va bien.\n【見守り】{who}さん宅で電気の使用が再開しました。ご安心ください。", "rassurant", now)
    c.commit()


# ---------- vues ----------
def view(c, uid, now=None):
    now = int(now or time.time())
    m = get(c, uid)
    a = analyse(uid, now)
    if m["enabled"] and m["pause_until"] and m["pause_until"] > now:
        a = dict(a, state="paused", paused_until=m["pause_until"])
    if not m["enabled"]:
        a = dict(a, state="disabled")
    evs = [dict(r) for r in c.execute("SELECT id,ts,level,gap_h,message,resolved_ts,resolved_by FROM mimamori_events WHERE user_id=? ORDER BY id DESC LIMIT 12", (uid,))]
    msgs = [dict(r) for r in c.execute("SELECT ts,to_name,to_phone,body,kind,status,segments,error FROM sms_outbox WHERE user_id=? ORDER BY id DESC LIMIT 8", (uid,))]
    return dict(settings=dict(enabled=bool(m["enabled"]), contact_name=m["contact_name"], contact_phone=m["contact_phone"], contact_email=m.get("contact_email") or "", token=m["token"], pause_until=m["pause_until"], consent_ts=m["consent_ts"]),
                status=a, events=evs, sms=msgs, sms_mode=(sms.mail_provider() if m.get("contact_email") else sms.provider()), now=now)


def public(c, token, now=None):
    """Page du proche : un etat et l'heure de la derniere activite, rien d'autre (pas de consommation)."""
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        return None
    m = c.execute("SELECT * FROM mimamori WHERE token=? AND enabled=1", (token,)).fetchone()
    if not m:
        return None
    a = analyse(m["user_id"], now)
    if m["pause_until"] and m["pause_until"] > int(now or time.time()):
        a = dict(a, state="paused", paused_until=m["pause_until"])
    return dict(name=_first_name(c, m["user_id"]), state=a["state"], gap_h=a.get("gap_h"), typical_h=a.get("typical_h"), last_active_ts=a.get("last_active_ts"), paused_until=a.get("paused_until"), now=int(now or time.time()))
