"""Module SMS : file d'attente, envoi par une passerelle, nouvelles tentatives, quotas. Aucun SMS n'est envoye sans fournisseur configure.

Fournisseur choisi par KIZUNA_SMS_PROVIDER :
  (vide) ou « simulation »   rien ne part : les messages sont ecrits dans sms_outbox avec le statut « simule » (mode par defaut)
  « twilio »                 KIZUNA_SMS_ACCOUNT, KIZUNA_SMS_TOKEN, et KIZUNA_SMS_FROM (numero ou identifiant d'expediteur) ou KIZUNA_SMS_SERVICE (Messaging Service SID)
  « brevo »                  KIZUNA_SMS_TOKEN (cle API Brevo « xkeysib-... ») et KIZUNA_SMS_FROM (nom d'expediteur, 11 caracteres max, lettres/chiffres)
  « http »                   passerelle generique (operateur tunisien, agregateur...) : KIZUNA_SMS_URL, KIZUNA_SMS_TOKEN (envoye en « Authorization: Bearer »),
                             KIZUNA_SMS_FROM ; corps JSON {"to": "+216...", "text": "...", "from": "..."} (noms modifiables : KIZUNA_SMS_FIELDS="to,text,from")
Autres reglages : KIZUNA_SMS_DAILY_MAX (SMS par foyer et par jour, defaut 6), KIZUNA_SMS_PREFIX (indicatif par defaut, defaut +216).

Statuts : simule · en_attente · envoye · echec · bloque (quota). Un echec reseau est retente 3 fois (1, 2 puis 4 minutes).
Les identifiants du fournisseur ne sont jamais ecrits dans la base, ni dans les journaux, ni renvoyes par l'API.
Un SMS reel coute de l'argent et expose un numero de telephone : n'activez un fournisseur qu'avec le consentement du proche (deja demande par la veille mimamori).
"""
import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_TRIES = 3
SCHEMA = """
CREATE TABLE IF NOT EXISTS sms_outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER, to_phone TEXT, to_name TEXT, body TEXT, ts INTEGER NOT NULL, kind TEXT, status TEXT NOT NULL DEFAULT 'simule'
);
"""
NEW_COLS = (("channel", "TEXT"), ("subject", "TEXT"), ("attempts", "INTEGER NOT NULL DEFAULT 0"), ("next_try", "INTEGER"), ("provider", "TEXT"), ("provider_id", "TEXT"), ("error", "TEXT"), ("segments", "INTEGER"))


def init(c):
    c.executescript(SCHEMA)
    have = {r[1] for r in c.execute("PRAGMA table_info(sms_outbox)")}
    for col, typ in NEW_COLS:
        if col not in have:
            c.execute(f"ALTER TABLE sms_outbox ADD COLUMN {col} {typ}")


def _e(k, d=""):
    return (os.environ.get(k) or d).strip()


def provider():
    p = _e("KIZUNA_SMS_PROVIDER", "simulation").lower()
    if p == "twilio" and _e("KIZUNA_SMS_ACCOUNT") and _e("KIZUNA_SMS_TOKEN") and (_e("KIZUNA_SMS_FROM") or _e("KIZUNA_SMS_SERVICE")):
        return "twilio"
    if p == "brevo" and _e("KIZUNA_SMS_TOKEN") and re.fullmatch(r"[A-Za-z0-9]{3,11}", _e("KIZUNA_SMS_FROM")):
        return "brevo"
    if p == "http" and _e("KIZUNA_SMS_URL").startswith("https://"):
        return "http"
    return "simulation"


def describe():
    asked = _e("KIZUNA_SMS_PROVIDER", "simulation").lower()
    p = provider()
    if p == "simulation" and asked not in ("", "simulation"):
        return f"fournisseur « {asked} » mal configure (variables manquantes, ou URL non HTTPS) : SIMULATION"
    return "envoi REEL via " + p if p != "simulation" else "SIMULATION (aucun SMS reel)"


def real():
    return provider() != "simulation"


# ---------- e-mail (Brevo, API transactionnelle) ----------
# KIZUNA_MAIL_PROVIDER=brevo, KIZUNA_MAIL_TOKEN (cle API ; a defaut KIZUNA_SMS_TOKEN), KIZUNA_MAIL_FROM (adresse expeditrice VERIFIEE dans Brevo), KIZUNA_MAIL_FROM_NAME.
EMAIL_RE = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[A-Za-z]{2,}$")


def mail_provider():
    if _e("KIZUNA_MAIL_PROVIDER", "simulation").lower() == "brevo" and (_e("KIZUNA_MAIL_TOKEN") or _e("KIZUNA_SMS_TOKEN")) and EMAIL_RE.match(_e("KIZUNA_MAIL_FROM")):
        return "brevo"
    return "simulation"


def mail_describe():
    return "envoi REEL par e-mail via brevo" if mail_provider() != "simulation" else "SIMULATION e-mail (aucun e-mail reel)"


# ---------- numeros et longueur ----------
def normalize(phone):
    """Numero au format international +XXXXXXXX, ou None. Un numero a 8 chiffres est tunisien (+216) ; 0033... et 00216... sont acceptes."""
    s = re.sub(r"[\s().\-]", "", str(phone or ""))
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s.startswith("+") and re.fullmatch(r"\d{8}", s):          # 8 chiffres : numero tunisien local ; tout autre pays : format international (+81...) obligatoire
        s = _e("KIZUNA_SMS_PREFIX", "+216") + s
    return s if re.fullmatch(r"\+[1-9]\d{7,14}", s) else None


_GSM = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà^{}\\[~]|€")


def segments(text):
    """Nombre de SMS factures : 160 caracteres (153 si plusieurs) en alphabet GSM, 70 (67) des qu'il y a un caractere hors GSM (japonais, arabe, accents rares)."""
    gsm = all(ch in _GSM for ch in text)
    n = len(text) + sum(text.count(ch) for ch in "^{}\\[~]|€") if gsm else len(text)
    one, many = (160, 153) if gsm else (70, 67)
    return 1 if n <= one else -(-n // many)


# ---------- file d'attente ----------
def enqueue(c, uid, phone, name, body, kind, now=None, channel="sms", subject=None):
    """Ajoute un message (SMS ou e-mail) a la file ; retourne son id. Applique le quota journalier par foyer. Pour un e-mail, `phone` est l'adresse."""
    now = int(now or time.time())
    mail = channel == "email"
    addr = str(phone or "").strip().lower()
    num = (addr if EMAIL_RE.match(addr) and len(addr) <= 120 else None) if mail else normalize(phone)
    seg = 1 if mail else segments(body)
    daily = int(_e("KIZUNA_SMS_DAILY_MAX", "6") or 6)
    sent_today = c.execute("SELECT COUNT(*) FROM sms_outbox WHERE user_id=? AND ts>? AND status!='bloque'", (uid, now - 86400)).fetchone()[0]
    if not num:
        status, err = "echec", "adresse invalide" if mail else "numero invalide"
    elif sent_today >= daily:
        status, err = "bloque", f"quota de {daily} SMS par 24 h atteint"
    else:
        status, err = ("en_attente" if (mail_provider() if mail else provider()) != "simulation" else "simule"), None
    cur = c.execute("INSERT INTO sms_outbox(user_id,to_phone,to_name,body,ts,kind,status,error,segments,provider,channel,subject) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uid, num or str(phone or "")[:60], name, body, now, kind, status, err, seg, mail_provider() if mail else provider(), channel, (subject or "")[:150]))
    return cur.lastrowid


def _post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, r.read(20000).decode("utf-8", "replace")


def _send_real(to, body):
    """Retourne l'identifiant du message chez le fournisseur ; leve OSError (reessayable) ou ValueError (refus definitif)."""
    p = provider()
    try:
        if p == "twilio":
            acct = _e("KIZUNA_SMS_ACCOUNT")
            form = {"To": to, "Body": body}
            if _e("KIZUNA_SMS_SERVICE"):
                form["MessagingServiceSid"] = _e("KIZUNA_SMS_SERVICE")
            else:
                form["From"] = _e("KIZUNA_SMS_FROM")
            auth = base64.b64encode(f"{acct}:{_e('KIZUNA_SMS_TOKEN')}".encode()).decode()
            _, txt = _post(f"https://api.twilio.com/2010-04-01/Accounts/{urllib.parse.quote(acct)}/Messages.json", urllib.parse.urlencode(form).encode(),
                           {"Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded"})
            return str(json.loads(txt).get("sid", ""))[:40]
        if p == "brevo":
            _, txt = _post("https://api.brevo.com/v3/transactionalSMS/sms", json.dumps({"sender": _e("KIZUNA_SMS_FROM"), "recipient": to.lstrip("+"), "content": body, "type": "transactional"}).encode(),
                           {"api-key": _e("KIZUNA_SMS_TOKEN"), "Content-Type": "application/json", "Accept": "application/json"})
            j = json.loads(txt)
            return str(j.get("messageId") or j.get("reference") or "")[:40]
        f = (_e("KIZUNA_SMS_FIELDS", "to,text,from").split(",") + ["", "", ""])[:3]
        payload = {f[0]: to, f[1]: body}
        if _e("KIZUNA_SMS_FROM"):
            payload[f[2]] = _e("KIZUNA_SMS_FROM")
        hdr = {"Content-Type": "application/json"}
        if _e("KIZUNA_SMS_TOKEN"):
            hdr["Authorization"] = "Bearer " + _e("KIZUNA_SMS_TOKEN")
        _, txt = _post(_e("KIZUNA_SMS_URL"), json.dumps(payload).encode(), hdr)
        try:
            j = json.loads(txt)
            return str(j.get("id") or j.get("message_id") or j.get("sid") or "")[:40] if isinstance(j, dict) else ""
        except ValueError:
            return ""
    except urllib.error.HTTPError as e:
        if 400 <= e.code < 500 and e.code not in (408, 429):
            raise ValueError(f"refuse par le fournisseur (HTTP {e.code})")
        raise OSError(f"fournisseur indisponible (HTTP {e.code})")
    except (urllib.error.URLError, TimeoutError) as e:
        raise OSError("reseau : " + type(e).__name__)


def _send_mail(to, name, subject, body):
    key = _e("KIZUNA_MAIL_TOKEN") or _e("KIZUNA_SMS_TOKEN")
    payload = {"sender": {"name": _e("KIZUNA_MAIL_FROM_NAME", "Kizuna Watt"), "email": _e("KIZUNA_MAIL_FROM")}, "to": [{"email": to, "name": (name or to)[:60]}],
               "subject": subject or "Kizuna Watt", "textContent": body}
    try:
        _, txt = _post("https://api.brevo.com/v3/smtp/email", json.dumps(payload).encode(), {"api-key": key, "Content-Type": "application/json", "Accept": "application/json"})
        return str(json.loads(txt).get("messageId", ""))[:60]
    except urllib.error.HTTPError as e:
        if 400 <= e.code < 500 and e.code not in (408, 429):
            try:
                why = json.loads(e.read().decode()).get("message", "")[:120]
            except Exception:
                why = ""
            raise ValueError(f"refuse par le fournisseur (HTTP {e.code}) {why}".strip())
        raise OSError(f"fournisseur indisponible (HTTP {e.code})")
    except (urllib.error.URLError, TimeoutError) as e:
        raise OSError("reseau : " + type(e).__name__)


def process(c, now=None, limit=20):
    """Envoie les messages en attente (appelee toutes les 15 s par le serveur). Retourne le nombre envoye."""
    if not real() and mail_provider() == "simulation":
        return 0
    now = int(now or time.time())
    sent = 0
    rows = c.execute("SELECT id, to_phone, to_name, subject, channel, body, attempts FROM sms_outbox WHERE status='en_attente' AND (next_try IS NULL OR next_try<=?) ORDER BY id LIMIT ?", (now, limit)).fetchall()
    for r in rows:
        try:
            pid = _send_mail(r["to_phone"], r["to_name"], r["subject"], r["body"]) if r["channel"] == "email" else _send_real(r["to_phone"], r["body"])
            c.execute("UPDATE sms_outbox SET status='envoye', provider_id=?, attempts=attempts+1, error=NULL, provider=? WHERE id=?", (pid, provider(), r["id"]))
            sent += 1
        except ValueError as e:
            c.execute("UPDATE sms_outbox SET status='echec', attempts=attempts+1, error=? WHERE id=?", (str(e)[:200], r["id"]))
        except OSError as e:
            n = r["attempts"] + 1
            if n >= MAX_TRIES:
                c.execute("UPDATE sms_outbox SET status='echec', attempts=?, error=? WHERE id=?", (n, str(e)[:200], r["id"]))
            else:
                c.execute("UPDATE sms_outbox SET attempts=?, next_try=?, error=? WHERE id=?", (n, now + 60 * 2 ** (n - 1), str(e)[:200], r["id"]))
    c.commit()
    return sent


def mask(phone):
    s = str(phone or "")
    if "@" in s:
        a, _, d = s.partition("@")
        return a[:1] + "***@" + d
    return s[:4] + "•" * max(0, len(s) - 6) + s[-2:] if len(s) > 6 else s


def stats(c):
    """Pour le compte STEG : etat du module et compteurs ; numeros masques, aucun identifiant."""
    cnt = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM sms_outbox GROUP BY status")}
    last = [dict(ts=r["ts"], kind=r["kind"], status=r["status"], to=mask(r["to_phone"]), segments=r["segments"], error=r["error"])
            for r in c.execute("SELECT ts,kind,status,to_phone,segments,error FROM sms_outbox ORDER BY id DESC LIMIT 20")]
    return dict(mode=provider(), real=real(), mail_mode=mail_provider(), description=describe() + " | " + mail_describe(), counts=cnt, last=last, daily_max=int(_e("KIZUNA_SMS_DAILY_MAX", "6") or 6))
