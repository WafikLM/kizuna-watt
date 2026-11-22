"""Flotte de controleurs de luminaires SIMULES : fait tourner, dans un seul programme, le controleur de chaque luminaire d'un fichier CSV.

⚠️  SIMULATION : la luminosite, la presence et la puissance sont GENEREES. Ce programme sert a la demonstration et aux tests,
    tant que les vrais controleurs (materiel) ne sont pas installes. Il n'envoie rien tant qu'il ne tourne pas.

Principe (identique a un vrai controleur) : pour chaque luminaire, toutes les N secondes,
  1) lire les capteurs (ici : simules) ;  2) envoyer l'etat a l'application (POST /api/lighting/report) ;
  3) recevoir la CONSIGNE (target_level) et l'appliquer au prochain rapport.
L'application decide donc seule : allumage au coucher du soleil (calcule pour la position du luminaire), niveau par plage horaire,
extinction si la luminosite mesuree est elevee, maintien en cas de mouvement, prevision des modeles d'IA.

Utilisation (le fichier CSV est telecharge quand on declare les luminaires d'une rue ; il contient l'identifiant et la cle de chaque controleur) :

  python tools/lamp_fleet.py --simulate --url http://127.0.0.1:8020 --csv "C:/Users/VOUS/Downloads/luminaires_*_cles.csv"

Options : --interval 15 (secondes) · --rated-w 100 (si le CSV n'a pas de colonne rated_w) · --faults 5 (nombre de lampes qui tombent en panne,
pour montrer les alertes) · --count 0 (nombre de cycles, 0 = sans fin) · --workers 16.   Bibliotheque standard uniquement.
"""
import argparse
import csv
import glob
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ap = argparse.ArgumentParser()
ap.add_argument("--csv", nargs="+", required=True, help="fichier(s) CSV de cles (les jokers * sont acceptes)")
ap.add_argument("--url", default="http://127.0.0.1:8000")
ap.add_argument("--simulate", action="store_true", help="obligatoire : confirme que les mesures sont simulees")
ap.add_argument("--interval", type=float, default=15.0)
ap.add_argument("--rated-w", type=float, default=100.0)
ap.add_argument("--faults", type=int, default=0, help="nombre de luminaires qui tombent en panne (demonstration des alertes)")
ap.add_argument("--count", type=int, default=0)
ap.add_argument("--workers", type=int, default=16)
a = ap.parse_args()

if not a.simulate:
    sys.exit("Ce programme SIMULE des controleurs. Relancez avec --simulate pour confirmer que les mesures envoyees seront simulees.")

# ---------- lecture des fichiers de cles ----------
lamps, seen = [], set()
for pattern in a.csv:
    files = glob.glob(pattern) or [pattern]
    for fp in files:
        try:
            with open(fp, encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    if r.get("lamp_id") and r.get("key") and r["lamp_id"] not in seen:
                        seen.add(r["lamp_id"])
                        lamps.append(dict(id=r["lamp_id"], key=r["key"], name=r.get("name", r["lamp_id"]), rated=float(r.get("rated_w") or a.rated_w),
                                          level=0.0, night=None, fail_after=None, rnd=random.Random(r["lamp_id"])))
        except OSError as e:
            print("fichier illisible :", fp, e)
if not lamps:
    sys.exit("Aucun luminaire trouve dans le(s) fichier(s) CSV (colonnes attendues : lamp_id, key).")
for l in random.Random(1).sample(lamps, min(a.faults, len(lamps))):
    l["fail_after"] = random.Random(l["id"]).randint(2, 4)          # tombe en panne apres 2 a 4 cycles
print("=" * 78)
print(f" SIMULATION de {len(lamps)} controleur(s) de luminaires -> {a.url}   (mesures GENEREES, pas reelles)")
print("=" * 78, flush=True)


def sensors(l, cycle):
    """Capteurs simules. La luminosite suit l'indication jour/nuit renvoyee par l'application ; la presence suit l'heure."""
    h = time.localtime().tm_hour + time.localtime().tm_min / 60
    night = l["night"] if l["night"] is not None else not (7 <= h < 17)
    r = l["rnd"]
    lux = round(r.uniform(0.2, 3.0) if night else r.uniform(2500, 20000), 1)
    busy = 0.35 if 18 <= h <= 23 else 0.06
    motion = 1 if r.random() < busy else 0
    power = l["rated"] * l["level"] / 100 * r.uniform(0.97, 1.03)
    if l["fail_after"] is not None and cycle >= l["fail_after"] and l["level"] > 0:
        power = l["rated"] * 0.03                                      # lampe grillee : consommation quasi nulle
    return dict(level_pct=round(l["level"], 1), power_w=round(power, 1), lux=lux, motion=motion)


def one(l, cycle):
    body = json.dumps(sensors(l, cycle)).encode()
    req = urllib.request.Request(a.url.rstrip("/") + "/api/lighting/report", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Lamp-Id": l["id"], "X-Lamp-Key": l["key"]})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=20).read())
        l["level"], l["night"] = float(r["target_level"]), bool(r["night"])          # on APPLIQUE la consigne recue
        return "ok", r["target_level"]
    except urllib.error.HTTPError as e:
        return f"http{e.code}", None
    except Exception as e:
        return "reseau", str(e)[:60]


cycle = 0
try:
    while a.count == 0 or cycle < a.count:
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            res = list(ex.map(lambda l: one(l, cycle), lamps))
        ok = sum(1 for s, _ in res if s == "ok")
        bad = {}
        for s, _ in res:
            if s != "ok":
                bad[s] = bad.get(s, 0) + 1
        levels = {}
        for s, v in res:
            if s == "ok":
                levels[int(v)] = levels.get(int(v), 0) + 1
        night = sum(1 for l in lamps if l["night"])
        print(f"{time.strftime('%H:%M:%S')}  cycle {cycle + 1:>3} : {ok}/{len(lamps)} luminaires en ligne | nuit : {night} | consignes {dict(sorted(levels.items()))}"
              + (f" | ERREURS {bad}" if bad else "") + f" | {time.time() - t0:.1f} s", flush=True)
        if bad.get("http401") == len(lamps):
            sys.exit("Toutes les cles sont refusees : verifiez le fichier CSV et l'adresse du serveur (--url).")
        cycle += 1
        time.sleep(max(0.0, a.interval - (time.time() - t0)))
except KeyboardInterrupt:
    print("\nArret demande.")
