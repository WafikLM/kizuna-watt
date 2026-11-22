"""Environnement de SIMULATION : plusieurs clients, plusieurs capteurs, un compte STEG, dans une base SEPAREE (data/demo.db).

    python tools/demo.py run              demarre le serveur de simulation (port 8030), cree les comptes si besoin,
                                          comble l'historique (35 jours) puis envoie des mesures en direct
    python tools/demo.py run --no-live    pareil, sans envoi en direct (capteurs figes a l'instant du lancement)
    python tools/demo.py live --url http://127.0.0.1:8020   envoie les mesures simulees vers un autre serveur (ex. la vraie base apres import)
    python tools/demo.py bills-seed [--real]      cree un historique de factures payees simulees (6 periodes) pour chaque client
    python tools/demo.py senior-add               ajoute 2 personnes agees avec veille mimamori (si la simulation existe deja)
    python tools/demo.py mimamori panne [EMAIL] [--heures 11] [--real]   plus d'activite : l'alerte part (SMS simule) ; `mimamori reprise` pour revenir
    python tools/demo.py incident EMAIL 6000 10   ajoute 6000 W pendant 10 min chez ce client (teste l'alerte de consommation anormale)
    python tools/demo.py ai               fait tourner les modeles d'IA sur chaque client simule et affiche le resultat
    python tools/demo.py reset            supprime la base de simulation

HONNETETE : tout ce que cree ce script est SIMULE et etiquete « (simulation) ». Les mesures sont fabriquees a partir des
equipements declares de chaque foyer (frigo cyclique, eclairage le soir, climatisation selon la temperature, etc.) : elles servent a
tester le logiciel et les modeles, PAS a evaluer leur precision. La vraie base (data/steg_app.db) n'est jamais touchee.
Les capteurs simules envoient leurs mesures par la MEME API que de vrais capteurs (/api/ingest, identifiant + cle).
"""
import argparse
import http.cookiejar
import json
import math
import os
import random
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DATA = os.path.join(ROOT, "data", "demo")          # dossier separe : la vraie base et sa cle (data/) ne sont jamais touchees
DB = os.path.join(DATA, "demo.db")
PORT = 8030
BASE = f"http://127.0.0.1:{PORT}"
PASSWORD = "SimKizuna-2026"
EMAIL_DOMAIN = "kizuna-sim.local"
STEG_EMAIL = f"steg@{EMAIL_DOMAIN}"
DEV_FILE = os.path.join(DATA, "demo_devices.json")
INCIDENT_FILE = os.path.join(DATA, "demo_incident.json")
ACCOUNTS_MD = os.path.join(DATA, "demo_comptes.md")
HISTORY_DAYS = 35
os.environ["KIZUNA_DB"] = DB
os.environ["KIZUNA_NO_RATELIMIT"] = "1"      # la simulation cree beaucoup de comptes et de mesures d'un coup : pas de limites de debit
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------- catalogue des equipements par type de logement
BOX = ("Box internet", 15, 1)
FRIDGE_S = ("Réfrigérateur", 130, 1)
FRIDGE = ("Réfrigérateur", 150, 1)
LED = lambda n: ("Éclairage (par lampe LED)", 10, n)
ARCH = {
    "appartement": [FRIDGE, LED(8), ("Télévision", 100, 1), ("Machine à laver", 800, 1), ("Climatiseur", 1000, 1), ("Ordinateur portable", 65, 1), BOX, ("Chargeur téléphone", 10, 3)],
    "maison": [FRIDGE, ("Congélateur", 120, 1), LED(14), ("Télévision", 100, 2), ("Machine à laver", 900, 1), ("Lave-vaisselle", 1200, 1), ("Four électrique", 2000, 1),
               ("Chauffe-eau électrique", 1500, 1), ("Climatiseur", 1200, 2), ("Ordinateur", 150, 2), BOX, ("Fer à repasser", 1000, 1)],
    "villa": [FRIDGE, ("Congélateur", 150, 1), LED(20), ("Télévision", 120, 3), ("Machine à laver", 1000, 1), ("Lave-vaisselle", 1300, 1), ("Four électrique", 2200, 1),
              ("Chauffe-eau électrique", 2000, 1), ("Climatiseur", 1500, 4), ("Pompe piscine", 1100, 1), ("Ordinateur", 150, 3), BOX],
    "ferme": [FRIDGE, LED(10), ("Pompe d'irrigation", 2200, 1), ("Pompe puits surpresseur", 750, 1), ("Chauffe-eau électrique", 1500, 1), ("Télévision", 100, 1),
              ("Machine à laver", 800, 1), ("Climatiseur", 1000, 1), BOX],
    "commerce": [("Réfrigérateur vitrine", 400, 2), ("Congélateur", 300, 1), LED(20), ("Climatiseur", 1500, 2), ("Ordinateur caisse", 150, 1), BOX],
}
ARCH["senior"] = [FRIDGE_S, LED(5), ("Télévision", 80, 1), ("Bouilloire électrique", 1800, 1), ("Radio", 15, 1), ("Four électrique", 1800, 1), ("Chargeur téléphone", 8, 1), BOX]
HOUSING = {"senior": "Appartement", "appartement": "Appartement", "maison": "Maison", "villa": "Villa", "ferme": "Ferme / exploitation agricole", "commerce": "Local commercial"}

# name, governorate, delegation, archetype, occupants, breaker_kw, [sensors], shed_level, comportement pendant la pointe d'hier
# comportement : "suit" (baisse reelle), "triche" (dit « fait » mais ne baisse pas), "refuse" (dit « je ne peux pas »), None (pas d'engagement)
CLIENTS = [
    ("Famille Ben Salah", "Tunis", "El Menzah", "appartement", 3, 6, ["Tableau principal"], 3, "suit"),
    ("Famille Trabelsi", "Tunis", "La Marsa", "maison", 5, 9, ["Tableau principal"], 4, "suit"),
    ("Famille Jebali", "Tunis", "Bab Souika", "appartement", 2, 5, ["Tableau principal"], 2, "triche"),
    ("Famille Mejri", "Tunis", "Carthage", "villa", 6, 12, ["Tableau principal", "Climatisation et pompe"], 3, "refuse"),
    ("Epicerie Chaouch", "Tunis", "El Omrane", "commerce", 2, 9, ["Tableau principal"], 0, None),
    ("Famille Gargouri", "Sfax", "Sfax Ville", "maison", 4, 9, ["Tableau principal"], 3, "suit"),
    ("Famille Kammoun", "Sfax", "Sfax Sud", "appartement", 3, 6, ["Tableau principal"], 3, "suit"),
    ("Ferme Zouari", "Sfax", "Agareb", "ferme", 5, 12, ["Tableau principal", "Pompes"], 3, "suit"),
    ("Famille Ayari", "Sousse", "Sahloul", "villa", 4, 12, ["Tableau principal", "Climatisation et pompe"], 4, "suit"),
    ("Famille Hamdi", "Sousse", "Sousse Médina", "appartement", 2, 5, [], 3, "suit"),                # aucun capteur : pas de recompense mesurable
    ("Famille Bouzid", "Nabeul", "Hammamet", "maison", 4, 9, ["Tableau principal"], 2, None),         # capteur qui devient hors ligne
    ("Famille Saidi", "Bizerte", "Bizerte Nord", "appartement", 3, 6, ["Tableau principal"], 3, "suit"),
    ("Ferme Dridi", "Gabès", "Gabès Sud", "ferme", 3, 9, ["Tableau principal"], 0, None),
    ("Mme Hadda Cherif", "Ariana", "Ennasr", "senior", 1, 3, ["Tableau principal"], 0, None),      # personne agee seule : veille mimamori
    ("M. Salah Ayed", "Béja", "Béja Nord", "senior", 1, 3, ["Tableau principal"], 0, None),
]
MIMAMORI = {13: ("Nadia (sa fille)", "22111301"), 14: ("Karim (son fils)", "98222302")}       # proche a prevenir
SENIORS = (13, 14)
INACTIVITY_FILE = os.path.join(DATA, "demo_inactivite.json")
OFFLINE_EMAIL_IDX = 10          # client 11 : capteur arrete il y a 6 h
INCIDENT_IDX = 3                # client 4 : incident simule hier a 2 h du matin (+7 kW pendant 1 h)

TEMP_MONTH = [11, 12, 14, 16, 20, 24, 27, 28, 25, 21, 16, 12]          # temperature moyenne (C) par mois, cote tunisien (approximation)
TEMP_GOV = {"Sfax": 1.0, "Gabès": 1.5, "Sousse": 0.5, "Bizerte": -1.0}


def email_of(i):
    return f"sim{i + 1:02d}@{EMAIL_DOMAIN}"


# ---------------------------------------------------------------- modele de consommation (SIMULE)
def kind_of(name):
    from kizuna import shedding
    n = shedding._norm(name)
    for k, keys in (("kettle", ("bouilloire",)), ("radio", ("radio",)), ("fridge", ("refrig", "frigo", "congel")), ("light", ("eclairage", "lampe", "led")), ("tv", ("televis",)), ("wash", ("laver", "lave-linge")),
                    ("dish", ("lave-vaisselle",)), ("oven", ("four",)), ("heater", ("chauffe-eau",)), ("ac", ("climatis",)), ("pump", ("pompe",)),
                    ("iron", ("repasser", "fer a")), ("box", ("box",)), ("pc", ("ordinateur",)), ("charger", ("chargeur",))):
        if any(x in n for x in keys):
            return k
    return "misc"


def temp_at(gov, ts):
    lt = time.localtime(ts)
    base = TEMP_MONTH[lt.tm_mon - 1] + TEMP_GOV.get(gov, 0)
    day_off = random.Random(lt.tm_yday * 7 + 3).uniform(-2.5, 2.5)
    return base + day_off + 4.5 * math.sin(2 * math.pi * ((lt.tm_hour + lt.tm_min / 60) - 9) / 24)


def day_profile(cid, idx, kind, arch, gov, qty, w, day_ts):
    """Puissance (W) de CET equipement pour chaque minute du jour local commencant a day_ts."""
    lt = time.localtime(day_ts)
    weekend = lt.tm_wday >= 5
    rnd = random.Random(f"{cid}|{idx}|{lt.tm_year}-{lt.tm_yday}")
    P = [0.0] * 1440

    def span(a, b, level):
        for m in range(max(0, int(a)), min(1440, int(b))):
            P[m] = max(P[m], w * qty * level)

    shop = arch == "commerce"
    if arch == "senior" and kind in ("kettle", "light", "tv", "radio", "oven", "charger"):
        wake = 6.5 + rnd.uniform(-0.3, 0.4)
        if kind == "kettle":
            for t in (wake + .15, 12.4 + rnd.uniform(-.3, .3), 17.3 + rnd.uniform(-.4, .4)):
                span(t * 60, t * 60 + 4, 1.0)
        elif kind == "light":
            span(wake * 60, (wake + 1.5) * 60, .5); span(18 * 60, (22.4 + rnd.uniform(-.3, .3)) * 60, .6)
            if rnd.random() < .5:
                span(3 * 60, 3 * 60 + 5, .4)
        elif kind == "tv":
            if rnd.random() < .85:
                span((wake + 1) * 60, 11.5 * 60, .9)
            span(18 * 60, (22 + rnd.uniform(-.3, .3)) * 60, .9)
        elif kind == "radio":
            span(wake * 60, 9 * 60, 1.0)
        elif kind == "oven":
            for lo, p in ((12, .7), (18.5, .3)):
                if rnd.random() < p:
                    for m in range(int(lo * 60), int(lo * 60) + 40):
                        P[m] = w * qty * (0.95 if (m // 4) % 2 == 0 else 0.3)
        elif kind == "charger":
            span(22.5 * 60, 24 * 60, .5); span(0, 6 * 60, .5)
        return P
    if kind == "fridge":
        period, on, ph = 38 + rnd.randint(-4, 4), 14, rnd.randint(0, 37)
        for m in range(1440):
            P[m] = w * qty * (0.85 if (m + ph) % period < on else 0.03)
    elif kind == "box":
        P = [w * qty] * 1440
    elif kind == "light":
        if shop:
            span(8 * 60, 20 * 60, 0.9)
        else:
            span(6.5 * 60, 8 * 60, 0.25); span((17.5 if weekend else 18) * 60, 23.5 * 60, 0.55 + rnd.uniform(-0.1, 0.1)); span(23.5 * 60, 24 * 60, 0.05); span(0, 6.5 * 60, 0.03)
    elif kind == "tv":
        n_on = 1 + (qty > 1 and rnd.random() < 0.4) + (qty > 2 and rnd.random() < 0.2)
        if weekend:
            span(11 * 60, 13 * 60, 0.9 * n_on / qty); span(18 * 60, 24 * 60, 0.9 * n_on / qty)
        elif rnd.random() < 0.85:
            s = rnd.uniform(19, 20.5) * 60; span(s, s + rnd.uniform(2, 3.5) * 60, 0.9 * n_on / qty)
    elif kind in ("wash", "dish"):
        if rnd.random() < (0.8 if weekend else 0.55):
            s = (rnd.uniform(8, 11) if (kind == "wash" and not weekend and rnd.random() < 0.5) else rnd.uniform(18, 21) if kind == "wash" else rnd.uniform(21, 22.5)) * 60
            span(s, s + 20, 1.0); span(s + 20, s + 60, 0.25); span(s + 60, s + 75, 0.45)
    elif kind == "oven":
        for lo, hi, p in ((12, 13, 0.55), (19, 20, 0.5)):
            if rnd.random() < p:
                s = rnd.uniform(lo, hi) * 60
                for m in range(int(s), int(s) + 45):
                    if m < 1440:
                        P[m] = w * qty * (0.95 if (m // 4) % 2 == 0 else 0.3)
    elif kind == "heater":
        if rnd.random() < 0.9:
            s = rnd.uniform(5.2, 6.2) * 60; span(s, s + 55, 0.9)
        if rnd.random() < 0.7:
            s = rnd.uniform(18.8, 19.8) * 60; span(s, s + 50, 0.9)
        span(0, 1440, 0.015)
    elif kind == "ac":
        for m in range(0, 1440, 5):
            T = temp_at(gov, day_ts + m * 60)
            need = max(0.0, (T - 26) / 6, (14 - T) / 6)
            hour = m / 60
            present = shop and 8 <= hour < 20 or (not shop and (hour >= 17 or hour < 1 or (weekend and 9 <= hour < 17) or 6.5 <= hour < 8))
            if present and need > 0.15:
                duty = min(1.0, 0.35 + 0.6 * need)
                n_on = max(1, round(qty * min(1.0, 0.4 + need)))
                for k in range(m, min(1440, m + 5)):
                    P[k] = w * n_on * 0.8 if rnd.random() < duty else w * n_on * 0.04
    elif kind == "pump":
        if arch == "ferme" and w >= 2000:
            if rnd.random() < 0.85:
                span(6 * 60, 8 * 60, 1.0); span(18 * 60, 19.5 * 60, 1.0)
        elif arch == "ferme":
            for m in range(7 * 60, 19 * 60, 90):
                if rnd.random() < 0.35:
                    span(m, m + 6, 1.0)
        else:
            span(8 * 60, 12 * 60, 1.0)
    elif kind == "pc":
        if shop:
            span(8 * 60, 20 * 60, 0.5)
        elif weekend:
            span(14 * 60, 24 * 60, 0.45)
        else:
            span(9 * 60, 23 * 60, 0.35 + rnd.uniform(0, 0.25))
    elif kind == "charger":
        span(22 * 60, 24 * 60, 0.5); span(0, 7 * 60, 0.5)
    elif kind == "iron":
        if weekend and rnd.random() < 0.4:
            s = rnd.uniform(9, 11) * 60
            for m in range(int(s), int(s) + 60):
                P[m] = w * qty * (0.9 if (m // 3) % 2 == 0 else 0.1)
    return P


class Household:
    """Genere la puissance de chaque capteur d'un foyer, minute par minute (cache des profils journaliers)."""
    def __init__(self, cid, spec, equipment, sensors):
        self.cid, self.gov, self.arch = cid, spec[1], spec[3]
        self.sensors = sensors
        self.eq = [(kind_of(n), n, w, q) for (n, w, q) in equipment]
        self.cache = {}
        self.shed = []             # (debut, fin, niveau) : fenetres ou le foyer applique le plan de pointe
        self.extra = []            # (debut, fin, watts, capteur) : incidents simules

    def _day(self, day_ts):
        if day_ts not in self.cache:
            if len(self.cache) > 6:
                self.cache.clear()
            self.cache[day_ts] = [day_profile(self.cid, i, k, self.arch, self.gov, q, w, day_ts) for i, (k, n, w, q) in enumerate(self.eq)]
        return self.cache[day_ts]

    def sensor_of(self, kind):
        return 1 if (len(self.sensors) > 1 and kind in ("ac", "pump", "heater")) else 0

    def power(self, ts, only_always_on=False):
        """Liste des puissances (W), une par capteur, a la seconde `ts` (valeur de la minute, sans bruit)."""
        from kizuna import shedding
        lt = time.localtime(ts)
        day_ts = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
        minute = min(1439, (ts - day_ts) // 60)
        out = [0.0] * max(1, len(self.sensors))
        prof = self._day(day_ts)
        shedding_now = [lv for (a, b, lv) in self.shed if a <= ts < b]
        for i, (kind, name, w, q) in enumerate(self.eq):
            if only_always_on and kind not in ("fridge", "box"):          # scenario « plus d'activite » : seulement frigo et box
                continue
            p = prof[i][minute]
            if shedding_now and p:
                prio = shedding.defaults_for(name)[1]
                lvl = shedding.LEVEL_OF.get(prio, 0)
                if lvl and lvl <= max(shedding_now) and random.Random(f"{self.cid}|{i}|{shedding_now[0]}").random() < 0.7:   # 70 % des appareils concernes sont vraiment coupes
                    p *= 0.5 if prio == "confort" else 0.0
            out[self.sensor_of(kind)] += p
        for (a, b, watts, sidx) in self.extra:
            if a <= ts < b:
                out[min(sidx, len(out) - 1)] += watts
        return out


# ---------------------------------------------------------------- acces HTTP
class Session:
    def __init__(self):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, path, body=None, headers=None):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), headers=h)
        try:
            r = self.op.open(req, timeout=20)
            return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


def wait_server(proc=None):
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/api/options", timeout=2)
            return True
        except Exception:
            if proc and proc.poll() is not None:
                return False
            time.sleep(0.5)
    return False


# ---------------------------------------------------------------- creation des comptes
def seed(households, only=None):
    from kizuna import db, lighting
    devs = json.load(open(DEV_FILE, encoding="utf-8")) if only and os.path.exists(DEV_FILE) else []
    rnd = random.Random(2026)
    db.init()
    if not db.by_email(STEG_EMAIL):
        db.create_user(STEG_EMAIL, PASSWORD, dict(full_name="Compte STEG (simulation)", consent_terms=1), role="steg")
    now = int(time.time())
    for i, (name, gov, dele, arch, occ, brk, sensors, lvl, comp) in enumerate(CLIENTS):
        if only is not None and i not in only:
            continue
        em = email_of(i)
        s = Session()
        la, lo = lighting.GOV_CENTERS[gov]
        st, r = s.call("/api/signup", dict(full_name=f"{name} (simulation)", email=em, password=PASSWORD, password2=PASSWORD, phone=f"2{i + 1:02d}{rnd.randint(10000, 99999)}"[:8],
                       governorate=gov, delegation=dele, address=f"{rnd.randint(2, 90)} rue de la Simulation, {dele}", postal_code=f"{rnd.randint(1000, 9099)}",
                       housing_type=HOUSING[arch], occupants=occ, lat=round(la + rnd.uniform(-0.05, 0.05), 5), lng=round(lo + rnd.uniform(-0.05, 0.05), 5),
                       steg_ref=f"SIM-{i + 1:04d}-{rnd.randint(1000, 9999)}", meter_number=f"SIM{i + 1:05d}", breaker_kw=brk,
                       equipment=[dict(name=n, power_w=w, quantity=q) for (n, w, q) in ARCH[arch]], consent_terms=True))
        if st != 200:
            print("ECHEC inscription", em, r); continue
        s.call("/api/shedding", {"level": lvl})
        if i in MIMAMORI:           # veille mimamori activee avec le consentement de la personne et un proche a prevenir
            s.call("/api/mimamori", dict(enabled=True, contact_name=MIMAMORI[i][0], contact_phone=MIMAMORI[i][1], consent=True))
        for k, sname in enumerate(sensors):
            st, r = s.call("/api/devices", dict(name=sname))
            devs.append(dict(email=em, client=i, sensor=k, name=sname, device_id=r["device_id"], key=r["key"],
                             offline_after=(now - 6 * 3600) if i == OFFLINE_EMAIL_IDX else None))
        print(f"  {em}  {name:<22} {gov:<8} {HOUSING[arch]:<28} {len(sensors)} capteur(s)  palier {lvl}")
    json.dump(devs, open(DEV_FILE, "w", encoding="utf-8"), indent=1)
    return devs


def write_accounts(devs):
    lines = ["# Comptes de SIMULATION (base data/demo.db, port 8030)", "", f"Mot de passe de tous les comptes : `{PASSWORD}`", "",
             f"- Compte STEG : `{STEG_EMAIL}`  →  http://127.0.0.1:{PORT}/steg", "", "| Client | Email | Gouvernorat | Logement | Capteurs | Palier |", "|---|---|---|---|---|---|"]
    for i, c in enumerate(CLIENTS):
        n = [d["name"] for d in devs if d["client"] == i]
        extra = " (hors ligne depuis 6 h)" if i == OFFLINE_EMAIL_IDX else ""
        lines.append(f"| {c[0]} | `{email_of(i)}` | {c[1]} | {HOUSING[c[3]]} | {', '.join(n) or 'aucun'}{extra} | {c[7]} |")
    lines += ["", "Tout est simule : mesures fabriquees a partir des equipements declares. Voir `tools/demo.py`."]
    open(ACCOUNTS_MD, "w", encoding="utf-8").write("\n".join(lines))


# ---------------------------------------------------------------- historique (comble les trous jusqu'a maintenant)
def build_households(devs):
    from kizuna import db
    hh = {}
    for i, spec in enumerate(CLIENTS):
        names = [d["name"] for d in devs if d["client"] == i]
        if names:
            hh[i] = Household(i, spec, ARCH[spec[3]], names)
    return hh


def yesterday_peak():
    lt = time.localtime(time.time() - 86400)
    s = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 19, 0, 0, 0, 0, -1)))
    return s, s + 3 * 3600


def prepare_scenarios(hh):
    ps, pe = yesterday_peak()
    for i, h in hh.items():
        comp = CLIENTS[i][8]
        if comp == "suit":
            h.shed.append((ps, pe, 3))
    if INCIDENT_IDX in hh:
        lt = time.localtime(time.time() - 86400)
        a = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 2, 10, 0, 0, 0, -1)))
        hh[INCIDENT_IDX].extra.append((a, a + 3600, 7000.0, 0))


def backfill(devs, hh, upto):
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    total = 0
    for d in devs:
        h = hh[d["client"]]
        row = c.execute("SELECT last_seen FROM devices WHERE id=?", (d["device_id"],)).fetchone()
        start = (row[0] + 60) if row and row[0] else upto - HISTORY_DAYS * 86400
        stop = min(upto, d["offline_after"]) if d["offline_after"] else upto
        start, stop = start - start % 60, stop - stop % 60
        if stop <= start:
            continue
        rnd = random.Random(f"{d['device_id']}|{start}")
        hours, daily, readings, kwh = {}, {}, [], 0.0
        for ts in range(start, stop, 60):
            p = h.power(ts)[d["sensor"]] * (1 + rnd.gauss(0, 0.012)) + rnd.uniform(0, 2)
            p = max(0.0, p)
            hh_ = ts - ts % 3600
            x = hours.setdefault(hh_, [0, 0.0, 0.0, 0.0])
            x[0] += 1; x[1] += p; x[2] = max(x[2], p); x[3] += p * p
            day = time.strftime("%Y-%m-%d", time.localtime(ts))
            daily[day] = daily.get(day, 0.0) + p / 60000
            kwh += p / 60000
            if ts >= upto - 86400:
                v = 231 - 0.0003 * p + rnd.gauss(0, 0.6)
                cos = min(0.99, max(0.7, 0.93 - 0.00001 * p + rnd.gauss(0, 0.01)))
                readings.append((d["device_id"], ts, round(p, 1), round(v, 1), round(p / (v * cos), 3), round(cos, 3), 0.0))
        for hts, (n, s, m, sq) in hours.items():
            c.execute("""INSERT INTO hourly(device_id,ts_hour,n,sum_w,max_w,sumsq_w) VALUES (?,?,?,?,?,?)
                         ON CONFLICT(device_id,ts_hour) DO UPDATE SET n=n+excluded.n, sum_w=sum_w+excluded.sum_w, max_w=MAX(max_w,excluded.max_w), sumsq_w=sumsq_w+excluded.sumsq_w""", (d["device_id"], hts, n, s, m, sq))
        for day, k in daily.items():
            c.execute("INSERT INTO daily(device_id,day,kwh) VALUES (?,?,?) ON CONFLICT(device_id,day) DO UPDATE SET kwh=kwh+excluded.kwh", (d["device_id"], day, k))
        c.executemany("INSERT INTO readings(device_id,ts,power_w,voltage,current_a,cosphi,leak_ma) VALUES (?,?,?,?,?,?,?)", readings)
        c.execute("DELETE FROM readings WHERE device_id=? AND ts<?", (d["device_id"], upto - 86400))
        c.execute("UPDATE devices SET last_seen=?, kwh_total=kwh_total+? WHERE id=?", (stop - 60, kwh, d["device_id"]))
        total += len(readings)
    c.commit()
    c.close()
    return total


def seed_peaks(devs):
    """Une pointe terminee hier soir (avec recompense) + une pointe annoncee pour demain, via l'API STEG."""
    c = sqlite3.connect(DB, timeout=30)
    if c.execute("SELECT COUNT(*) FROM peaks").fetchone()[0]:
        c.close(); return
    ps, pe = yesterday_peak()
    c.execute("INSERT INTO peaks(governorate,start_ts,end_ts,level,note,created_at,reward_millimes) VALUES ('',?,?,3,'Pointe de demonstration (hier soir)',?,2000)", (ps, pe, ps - 7200))
    pid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    for i, spec in enumerate(CLIENTS):
        if spec[8] in ("suit", "triche"):
            uid = c.execute("SELECT id FROM users WHERE email=?", (email_of(i),)).fetchone()[0]
            c.execute("INSERT OR REPLACE INTO peak_acks(peak_id,user_id,status,ts) VALUES (?,?, 'done', ?)", (pid, uid, ps - 1800))
        elif spec[8] == "refuse":
            uid = c.execute("SELECT id FROM users WHERE email=?", (email_of(i),)).fetchone()[0]
            c.execute("INSERT OR REPLACE INTO peak_acks(peak_id,user_id,status,ts) VALUES (?,?, 'declined', ?)", (pid, uid, ps - 1800))
    c.commit(); c.close()
    s = Session(); s.call("/api/login", {"email": STEG_EMAIL, "password": PASSWORD})
    lt = time.localtime(time.time() + 86400)
    t0 = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 19, 0, 0, 0, 0, -1)))
    st, r = s.call("/api/steg/peaks", dict(start_ts=t0, end_ts=t0 + 3 * 3600, level=3, governorate="Tunis", note="Pointe annoncee pour demain (simulation)", reward_dt=2))
    print("  pointe d'hier (terminee, avec recompense) + pointe de demain :", st)


# ---------------------------------------------------------------- envoi en direct
LIVE_EVENTS = [("bouilloire", 1900, 70), ("micro-ondes", 1100, 55), ("seche-cheveux", 1700, 110), ("aspirateur", 850, 240), ("grille-pain", 800, 50), ("fer a repasser", 1000, 150), ("mixeur", 450, 40)]


def live(devs, hh, step):
    """Mesures en direct, variables seconde par seconde : profil du foyer interpole entre deux minutes, derive lente aleatoire (±~6 %),
    bruit de mesure, et petits evenements (bouilloire, micro-ondes, aspirateur...) qui s'allument et s'eteignent au hasard."""
    keys = {d["device_id"]: d for d in devs if not d["offline_after"]}
    print(f"Envoi en direct : {len(keys)} capteurs, une mesure toutes les {step} s. Ctrl+C pour arreter.")
    rnd = random.Random()
    state = {k: dict(ou=rnd.uniform(-.05, .05), ev=[]) for k in keys}
    n = 0
    while True:
        t0 = time.time()
        extra = None
        if os.path.exists(INCIDENT_FILE):
            try:
                extra = json.load(open(INCIDENT_FILE, encoding="utf-8"))
                if extra["until"] < t0:
                    os.remove(INCIDENT_FILE); extra = None
            except Exception:
                extra = None
        inactive = None
        if os.path.exists(INACTIVITY_FILE):
            try:
                x = json.load(open(INACTIVITY_FILE, encoding="utf-8"))
                inactive = x["email"] if x["until"] > t0 else None
            except Exception:
                inactive = None
        for d in keys.values():
            st = state[d["device_id"]]
            h = hh[d["client"]]
            now_i = int(t0)
            quiet = inactive is not None and inactive == email_of(d["client"])
            a = h.power(now_i, only_always_on=quiet)[d["sensor"]]
            b = h.power(now_i + 60, only_always_on=quiet)[d["sensor"]]
            p = a + (b - a) * ((t0 % 60) / 60)                                    # pas de marche d'escalier a chaque minute
            st["ou"] += -0.07 * st["ou"] + rnd.gauss(0, 0.04)                    # derive lente (consommation « vivante »)
            st["ou"] = max(-0.2, min(0.2, st["ou"]))
            p *= 1 + st["ou"]
            if not quiet and d["sensor"] == 0 and 6 <= time.localtime(t0).tm_hour < 23 and rnd.random() < step / 700 and CLIENTS[d["client"]][3] != "senior":     # ~1 evenement toutes les 12 min
                nm, w, dur = rnd.choice(LIVE_EVENTS)
                st["ev"].append((t0 + dur * rnd.uniform(.7, 1.3), w * rnd.uniform(.8, 1.1)))
            st["ev"] = [(e, w) for e, w in st["ev"] if e > t0]
            p += sum(w for _, w in st["ev"])
            if extra and email_of(d["client"]) == extra["email"] and d["sensor"] == 0:
                p += extra["watts"]
            p = max(0.0, p * (1 + rnd.gauss(0, 0.03)) + rnd.uniform(0, 6))
            v = 231 - 0.0003 * p + rnd.gauss(0, 0.8)
            cos = min(0.99, max(0.7, 0.93 - 0.00001 * p + rnd.gauss(0, 0.012)))
            body = json.dumps(dict(power_w=round(p, 1), voltage=round(v, 1), current_a=round(p / (v * cos), 3), cosphi=round(cos, 3))).encode()
            req = urllib.request.Request(BASE + "/api/ingest", data=body, headers={"Content-Type": "application/json", "X-Device-Id": d["device_id"], "X-Device-Key": d["key"]})
            try:
                urllib.request.urlopen(req, timeout=10).read()
            except Exception as e:
                print("capteur", d["device_id"], "erreur :", e)
        n += 1
        if n % 20 == 0:
            print(time.strftime("%H:%M:%S"), f"{n} envois effectues")
        time.sleep(max(0.0, step - (time.time() - t0)))


# ---------------------------------------------------------------- commandes
def cmd_run(a):
    os.makedirs(DATA, exist_ok=True)
    env = dict(os.environ, KIZUNA_DB=DB, PORT=str(PORT), PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_server(proc):
            print("Le serveur de simulation n'a pas demarre (port 8030 deja pris ?)."); return 1
        first = not os.path.exists(DEV_FILE)
        if first:
            print("Creation des comptes de simulation :")
            devs = seed(None)
            write_accounts(devs)
        devs = json.load(open(DEV_FILE, encoding="utf-8"))
        hh = build_households(devs)
        if first:
            prepare_scenarios(hh)
        t = time.time()
        n = backfill(devs, hh, int(time.time()))
        print(f"Historique {HISTORY_DAYS} jours comble ({time.time() - t:.0f} s). Capteurs : {len(devs)}.")
        if first:
            seed_peaks(devs)
        print(f"\nSimulation prete :  http://127.0.0.1:{PORT}/steg   (compte STEG) et /login (clients)\nComptes et mot de passe : {ACCOUNTS_MD}\n")
        if a.no_live:
            print("Mode --no-live : serveur actif, Ctrl+C pour arreter."); proc.wait()
        else:
            live(devs, hh, a.step)
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
    return 0


def cmd_live(a):
    """Envoie les mesures simulees vers un AUTRE serveur (ex. la vraie base apres import) : les capteurs copies restent « en ligne »."""
    global BASE
    BASE = a.url.rstrip("/")
    devs = json.load(open(DEV_FILE, encoding="utf-8"))
    hh = build_households(devs)
    live(devs, hh, a.step)


def cmd_senior_add(a):
    """Ajoute les 2 personnes agees (veille mimamori) a une simulation DEJA creee : comptes, capteurs, historique."""
    devs_all = seed(None, only=set(SENIORS))
    write_accounts(devs_all)
    hh = build_households(devs_all)
    n = backfill([d for d in devs_all if d["client"] in SENIORS], hh, int(time.time()))
    print(f"Personnes agees ajoutees ({n} mesures d'historique). Relancez la simulation pour les voir en direct.")


def _rewrite_hours(path, mine, h, start, now, quiet):
    """Recalcule les mesures horaires et les mesures recentes d'un foyer : seulement frigo + box (quiet) ou profil normal."""
    c = sqlite3.connect(path, timeout=30)
    rnd = random.Random(7)
    for d in mine:
        h0 = start - start % 3600
        for hts in range(h0, now - now % 3600 + 3600, 3600):
            m = sum(h.power(hts + 60 * k, only_always_on=quiet)[d["sensor"]] for k in range(60)) / 60
            row = c.execute("SELECT n FROM hourly WHERE device_id=? AND ts_hour=?", (d["device_id"], hts)).fetchone()
            if row:
                n = row[0]
                c.execute("UPDATE hourly SET sum_w=?, max_w=?, sumsq_w=? WHERE device_id=? AND ts_hour=?", (n * m, m + 12, n * (m * m + 36), d["device_id"], hts))
        c.execute("DELETE FROM readings WHERE device_id=? AND ts>=?", (d["device_id"], start))
        c.executemany("INSERT INTO readings(device_id,ts,power_w,voltage,current_a,cosphi,leak_ma) VALUES (?,?,?,?,?,?,?)",
                      [(d["device_id"], t, round(h.power(t, only_always_on=quiet)[d["sensor"]] * (1 + rnd.gauss(0, .02)), 1), 231, 0.3, 0.9, 0.0) for t in range(start - start % 60, now, 60)])
    c.commit(); c.close()


def cmd_mimamori(a):
    """Scenario : `panne` = le foyer n'a plus d'activite (depuis N heures, pour voir l'alerte tout de suite) ; `reprise` = l'activite revient (l'historique recent est retabli)."""
    devs = json.load(open(DEV_FILE, encoding="utf-8"))
    idx = next((i for i in range(len(CLIENTS)) if email_of(i) == a.email), None)
    mine = [d for d in devs if d["client"] == idx]
    if idx is None or not mine:
        sys.exit("Client inconnu ou sans capteur : " + a.email)
    h = build_households(devs)[idx]
    now = int(time.time())
    path = os.path.join(ROOT, "data", "steg_app.db") if a.real else DB
    if a.action == "reprise":
        if os.path.exists(INACTIVITY_FILE):
            os.remove(INACTIVITY_FILE)
        _rewrite_hours(path, mine, h, now - int(a.heures * 3600), now, False)
        print("Activite reprise et historique recent retabli : la veille se rassure dans la minute.")
        return
    json.dump(dict(email=a.email, until=now + 24 * 3600), open(INACTIVITY_FILE, "w", encoding="utf-8"))
    _rewrite_hours(path, mine, h, now - int(a.heures * 3600), now, True)
    print(f"{a.email} : plus d'activite depuis {a.heures:g} h ({'vraie base' if a.real else 'base de simulation'}). La veille declenche l'alerte dans la minute ; `mimamori reprise` pour revenir a la normale.")


def cmd_bills_seed(a):
    """Cree un HISTORIQUE de factures payees SIMULEES (6 periodes de 2 mois, dont novembre-decembre de l'an dernier) pour chaque client avec capteur :
    consommation = moyenne mesuree x saison x aleatoire, montant = bareme + 3,5 % (taxes non modelisees). Un kWh sur deux n'est pas saisi (deduit du montant)."""
    from kizuna import billing
    path = os.path.join(ROOT, "data", "steg_app.db") if a.real else DB
    c = sqlite3.connect(path, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(billing.SCHEMA)
    rnd = random.Random(11)
    lt = time.localtime()
    cur_year = lt.tm_year
    plan = [(f"{cur_year - 1}-11", .9, True), (f"{cur_year}-01", .95, False), (f"{cur_year}-03", .85, True), (f"{cur_year}-05", 1.0, False), (f"{cur_year}-07", 1.45, True), (f"{cur_year}-09", 1.15, False)]
    n = 0
    for i in range(len(CLIENTS)):
        u = c.execute("SELECT u.id, p.breaker_kw FROM users u JOIN profiles p ON p.user_id=u.id WHERE u.email=?", (email_of(i),)).fetchone()
        if not u:
            continue
        row = c.execute("""SELECT SUM(k) / COUNT(*) FROM (SELECT d.day, SUM(d.kwh) k FROM daily d JOIN devices v ON v.id=d.device_id WHERE v.user_id=? AND d.day < date('now','localtime') GROUP BY d.day ORDER BY d.day DESC LIMIT 28)""", (u["id"],)).fetchone()
        base = row[0] if row and row[0] else None
        if not base:
            continue
        kva = max(1.0, u["breaker_kw"] or 3)
        for ps, factor, give_kwh in plan:
            st, en, days, label, _ = billing.period_info(ps)
            kwh = base * factor * days * rnd.uniform(.93, 1.07)
            amount = round(billing.compute(kwh, kva)["total"] * 1.035 / 10) * 10
            paid = time.strftime("%Y-%m-%d", time.localtime(en + 12 * 86400))
            c.execute("""INSERT INTO bill_history(user_id,period_start,amount_mil,kwh,paid_on,created_at) VALUES (?,?,?,?,?,?)
                         ON CONFLICT(user_id,period_start) DO NOTHING""", (u["id"], ps, amount, round(kwh, 1) if give_kwh else None, paid, int(time.time())))
            n += 1
    c.commit(); c.close()
    print(f"{n} factures payees simulees ajoutees ({'vraie base' if a.real else 'base de simulation'}).")


def cmd_incident(a):
    json.dump(dict(email=a.email, watts=float(a.watts), until=time.time() + 60 * float(a.minutes)), open(INCIDENT_FILE, "w", encoding="utf-8"))
    print(f"Incident simule : +{a.watts} W chez {a.email} pendant {a.minutes} min (pris en compte au prochain envoi du simulateur en direct).")


def cmd_ai(a):
    from kizuna import ai, db
    st = ai.status()
    print("Etat des modeles :", {k: st[k] for k in ("available", "isolation_forest", "xgboost", "error")})
    for w in st["warnings"]:
        print("  attention :", w)
    print(f"\n{'Client':<26}{'Heures analysees':>17}{'Anormales':>10}   Prevision XGBoost (24 h)")
    for i, c in enumerate(CLIENTS):
        u = db.by_email(email_of(i))
        if not u:
            continue
        ai._cache.clear()
        r = ai.analysis(u["id"], c[1])
        an, fc = r.get("anomaly"), r.get("forecast") or {}
        txt = fc.get("status", "?")
        if fc.get("status") == "ok":
            bt = fc.get("backtest") or {}
            kw = [x["kw"] for x in fc["items"]]
            txt = f"ok : {min(kw):.2f}-{max(kw):.2f} kW | erreur sur 24 h passees : {bt.get('mape_pct', '?')} % (MAE {bt.get('mae_w', '?')} W) | variables manquantes {fc.get('missing_inputs_pct')} %"
        elif fc.get("message"):
            txt += " : " + fc["message"][:70]
        print(f"{c[0]:<26}{(an or {}).get('hours_analyzed', '-'):>17}{(an or {}).get('flagged', '-'):>10}   {txt}")
        if an and an.get("flagged"):
            print("     heures signalees :", [f"{x['hour']} ({x['mean_w']} W, score {x['score']})" for x in an["items"] if x["anomaly"]][:4])


def cmd_reset(a):
    for f in (DB, DB + "-wal", DB + "-shm", DEV_FILE, INCIDENT_FILE, ACCOUNTS_MD, os.path.join(DATA, "reward.key")):
        if os.path.exists(f):
            os.remove(f)
    print("Base de simulation supprimee (la vraie base n'est pas touchee).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--no-live", action="store_true"); r.add_argument("--step", type=float, default=3.0)
    lv = sub.add_parser("live"); lv.add_argument("--url", default="http://127.0.0.1:8020"); lv.add_argument("--step", type=float, default=3.0)
    i = sub.add_parser("incident"); i.add_argument("email"); i.add_argument("watts"); i.add_argument("minutes")
    sub.add_parser("senior-add")
    bs = sub.add_parser("bills-seed"); bs.add_argument("--real", action="store_true")
    mm = sub.add_parser("mimamori"); mm.add_argument("action", choices=["panne", "reprise"]); mm.add_argument("email", nargs="?", default=email_of(13)); mm.add_argument("--heures", type=float, default=11.0); mm.add_argument("--real", action="store_true")
    sub.add_parser("ai"); sub.add_parser("reset")
    a = ap.parse_args()
    sys.exit({"run": cmd_run, "live": cmd_live, "senior-add": cmd_senior_add, "bills-seed": cmd_bills_seed, "mimamori": cmd_mimamori, "incident": cmd_incident, "ai": cmd_ai, "reset": cmd_reset}[a.cmd](a) or 0)
