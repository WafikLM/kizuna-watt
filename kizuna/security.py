"""Securite du serveur : HTTPS, cookie Secure, limitation de debit par adresse IP, sauvegarde de la base, controle de configuration.

Variables d'environnement (toutes facultatives ; sans elles, le mode local de developpement reste inchange) :
  KIZUNA_TLS_CERT, KIZUNA_TLS_KEY   certificat + cle (PEM) : le serveur parle alors HTTPS lui-meme
  KIZUNA_SECURE=1                   force le cookie « Secure » + en-tete HSTS (a utiliser derriere un proxy HTTPS : Caddy, nginx, tunnel)
  KIZUNA_TRUST_PROXY=1              fait confiance a X-Forwarded-For / X-Forwarded-Proto (seulement si un proxy de confiance est devant)
  KIZUNA_SECRET                     cle des codes de recompense (sinon data/reward.key, cree automatiquement, jamais dans le depot)
  KIZUNA_BACKUP_KEY                 si definie et si le paquet « cryptography » est installe, les sauvegardes sont chiffrees (Fernet)
"""
import os
import sqlite3
import ssl
import threading
import time

from . import db

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _env(k):
    return (os.environ.get(k) or "").strip()


# En-tetes envoyes avec chaque reponse. Les scripts de la page sont en ligne (d'ou 'unsafe-inline') : la protection principale contre le XSS reste l'echappement
# des donnees (esc) ; cette politique limite en plus les chargements aux seules origines connues (le site, Leaflet sur unpkg, les tuiles OpenStreetMap).
CSP = "default-src 'self'; script-src 'self' 'unsafe-inline' https://unpkg.com; style-src 'self' 'unsafe-inline' https://unpkg.com; img-src 'self' data: blob: https://tile.openstreetmap.org https://unpkg.com; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
HEADERS = (
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Permissions-Policy", "geolocation=(self), camera=(), microphone=(), payment=(), usb=()"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)


def tls_enabled():
    return bool(_env("KIZUNA_TLS_CERT") and _env("KIZUNA_TLS_KEY"))


def secure_mode():
    """Cookie Secure + HSTS : HTTPS direct, ou derriere un proxy HTTPS declare par KIZUNA_SECURE=1."""
    return tls_enabled() or _env("KIZUNA_SECURE") == "1"


def trust_proxy():
    return _env("KIZUNA_TRUST_PROXY") == "1"


def tls_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(_env("KIZUNA_TLS_CERT"), _env("KIZUNA_TLS_KEY"))
    return ctx


# ---------- limitation de debit (fenetre glissante, en memoire) ----------
_hits = {}
_lock = threading.Lock()
RULES = {            # nom -> (essais max, fenetre en secondes)
    "login_ip": (20, 300),       # toutes adresses e-mail confondues : bloque le « password spraying »
    "signup": (6, 3600),         # creation de comptes
    "check": (60, 600),          # verification de formulaire d'inscription
    "public": (60, 60),          # page publique du proche (jeton secret a deviner)
    "ingest_ip": (240, 60),      # capteurs / modeles / luminaires derriere une meme adresse
    "api_write": (240, 60),      # ecritures d'un client connecte
}


def allow(rule, key):
    """True si l'appel est permis ; l'enregistre. Nettoie les anciennes entrees au passage."""
    if _env("KIZUNA_NO_RATELIMIT") == "1":          # simulation et tests seulement (tools/demo.py) : ne l'utilisez jamais sur un serveur reel
        return True
    mx, win = RULES[rule]
    now = time.time()
    k = (rule, key)
    with _lock:
        lst = [t for t in _hits.get(k, ()) if now - t < win]
        if len(lst) >= mx:
            _hits[k] = lst
            return False
        lst.append(now)
        _hits[k] = lst
        if len(_hits) > 20000:                       # borne la memoire
            for kk in [kk for kk, v in _hits.items() if not v or now - v[-1] > 3600]:
                _hits.pop(kk, None)
    return True


# ---------- sauvegarde ----------
def backup(dest_dir=None, keep=14):
    """Copie coherente de la base (API de sauvegarde SQLite, sans arreter le serveur). Garde les `keep` plus recentes. Retourne le chemin."""
    dest_dir = dest_dir or os.path.join(ROOT, "data", "backups")
    os.makedirs(dest_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(dest_dir, f"steg_app-{stamp}.db")
    src = sqlite3.connect(db.DB_PATH)
    dst = sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    key = _env("KIZUNA_BACKUP_KEY")
    if key:
        try:
            import base64
            import hashlib
            from cryptography.fernet import Fernet
        except ImportError:
            print("Chiffrement ignore : installez le paquet « cryptography » (pip install cryptography). La sauvegarde reste NON chiffree.")
        else:
            f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))
            with open(path, "rb") as fh:
                blob = f.encrypt(fh.read())
            with open(path + ".enc", "wb") as fh:
                fh.write(blob)
            os.remove(path)
            path += ".enc"
    olds = sorted(x for x in os.listdir(dest_dir) if x.startswith("steg_app-"))
    for x in olds[:-keep]:
        os.remove(os.path.join(dest_dir, x))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def restore_hint():
    return "Restauration : arretez le serveur, puis copiez la sauvegarde (dechiffree si .enc) a la place de data/steg_app.db."


# ---------- controle de configuration ----------
def check():
    """Liste (niveau, texte) : ok / avert / manque."""
    out = []
    out.append(("ok" if secure_mode() else "avert",
                "HTTPS actif (cookie Secure + HSTS)." if secure_mode() else "HTTPS non actif : mots de passe et sessions circulent en clair. Definissez KIZUNA_TLS_CERT + KIZUNA_TLS_KEY, ou KIZUNA_SECURE=1 derriere un proxy HTTPS."))
    if _env("KIZUNA_SECRET"):
        out.append(("ok", "Cle des recompenses : variable KIZUNA_SECRET."))
    else:
        kf = os.path.join(ROOT, "data", "reward.key")
        out.append(("avert", "Cle des recompenses dans data/reward.key" + (" (presente)" if os.path.exists(kf) else " (sera creee)") + " : ne la partagez pas ; en production preferez KIZUNA_SECRET. Si elle a deja ete exposee, changez-la (les anciens codes deviennent invalides)."))
    c = db.conn()
    try:
        steg = c.execute("SELECT email FROM users WHERE role='steg'").fetchall()
    finally:
        c.close()
    out.append(("ok" if steg else "avert", f"{len(steg)} compte(s) STEG." if steg else "Aucun compte STEG : python manage.py create-steg."))
    if any(e["email"].split("@")[0].lower() in ("steg", "admin", "test", "demo") for e in steg):
        out.append(("avert", "Un compte STEG a un e-mail generique (steg@, admin@, demo@...) : utilisez une adresse nominative et un mot de passe fort."))
    kdir = os.path.join(ROOT, "data", "backups")
    last = max((os.path.getmtime(os.path.join(kdir, x)) for x in os.listdir(kdir)), default=0) if os.path.isdir(kdir) else 0
    out.append(("ok" if last and time.time() - last < 2 * 86400 else "avert",
                f"Derniere sauvegarde il y a {(time.time() - last) / 3600:.0f} h." if last else "Aucune sauvegarde : python manage.py backup."))
    out.append(("ok" if _env("KIZUNA_BACKUP_KEY") else "avert", "Sauvegardes chiffrees (KIZUNA_BACKUP_KEY)." if _env("KIZUNA_BACKUP_KEY") else "Sauvegardes non chiffrees : definissez KIZUNA_BACKUP_KEY (et installez « cryptography »)."))
    return out
