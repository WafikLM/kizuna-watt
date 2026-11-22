"""Controleur de luminaire : envoie l'etat de la lampe a l'application et applique la CONSIGNE recue.

Boucle : lire les capteurs -> POST /api/lighting/report -> la reponse contient `target_level` (0-100 %) -> appliquer.

  python tools/lamp_controller.py --url http://127.0.0.1:8000 --id LP-XXXXXX --key VOTRE_CLE --rated-w 100 --simulate

Sans --simulate, adaptez read_sensors() et apply_level() a votre materiel (Raspberry Pi, ESP32, variateur DALI / PWM).
--simulate : SIMULATION d'un luminaire (luminosite, presence et puissance generees) pour tester la boucle sans materiel.
Aucune donnee n'est envoyee a l'application tant que ce programme ne tourne pas. Bibliotheque standard uniquement.
"""
import argparse
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8000")
ap.add_argument("--id", required=True)
ap.add_argument("--key", required=True)
ap.add_argument("--rated-w", type=float, default=100.0, help="puissance nominale de la lampe (W)")
ap.add_argument("--interval", type=float, default=10.0, help="secondes entre deux rapports")
ap.add_argument("--simulate", action="store_true", help="simuler les capteurs et la lampe (test sans materiel)")
ap.add_argument("--fault-after", type=int, default=0, help="(simulation) la lampe tombe en panne apres N rapports")
ap.add_argument("--count", type=int, default=0, help="nombre de rapports (0 = sans fin)")
a = ap.parse_args()

level = 0.0                      # niveau applique actuellement (%)


def read_sensors(n):
    """A REMPLACER par la lecture reelle : luminosite (lux), compteur de mouvements, puissance mesuree (W)."""
    if not a.simulate:
        sys.exit("Mode materiel : implementez read_sensors() et apply_level(), ou lancez avec --simulate.")
    h = time.localtime().tm_hour + time.localtime().tm_min / 60
    day = max(0.0, math.sin((h - 6) / 13 * math.pi)) if 6 <= h <= 19 else 0.0
    lux = round(day * 20000 * random.uniform(0.7, 1.0) + random.uniform(0.2, 2.0), 1)
    busy = 0.4 if 18 <= h <= 23 else 0.08
    motion = 1 if random.random() < busy else 0
    dead = a.fault_after and n >= a.fault_after
    power = a.rated_w * level / 100 * random.uniform(0.97, 1.03) + (0 if level == 0 else 0)
    if dead and level > 0:
        power = a.rated_w * 0.03                      # lampe grillee : presque plus de consommation
    return dict(lux=lux, motion=motion, power_w=round(power, 1))


def apply_level(pct):
    """A REMPLACER : commander le variateur (PWM, 0-10 V, DALI...). Ici on memorise simplement le niveau."""
    global level
    level = float(pct)


def post(body):
    req = urllib.request.Request(a.url.rstrip("/") + "/api/lighting/report", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Lamp-Id": a.id, "X-Lamp-Key": a.key})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=8).read()), None
    except urllib.error.HTTPError as e:
        return None, f"{e.code} {e.read().decode()[:120]}"
    except Exception as e:
        return None, f"application injoignable : {e}"


n = 0
print(f"Controleur {a.id} -> {a.url}  (Ctrl+C pour arreter){'  [SIMULATION]' if a.simulate else ''}")
while a.count == 0 or n < a.count:
    s = read_sensors(n)
    r, err = post(dict(level_pct=round(level, 1), power_w=s["power_w"], lux=s["lux"], motion=s["motion"]))
    t = time.strftime("%H:%M:%S")
    if err:
        print(t, "ERREUR", err)
        if err.startswith("401"):
            sys.exit(1)
    else:
        print(f"{t} niveau {level:5.1f} % · {s['power_w']:6.1f} W · {s['lux']:8.1f} lx · mouvement {s['motion']} -> consigne {r['target_level']} % ({r['reason']})")
        apply_level(r["target_level"])
    n += 1
    time.sleep(a.interval)
