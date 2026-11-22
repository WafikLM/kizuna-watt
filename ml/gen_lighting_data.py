"""Genere des jeux de donnees SYNTHETIQUES pour entrainer les modeles de l'eclairage public.

⚠️ Donnees SIMULEES (aucune mesure reelle). Elles servent a monter les pipelines d'entrainement et de test ; il faut
ensuite valider les modeles sur des donnees reelles (voir DATASETS_ECLAIRAGE.md). Ne jamais presenter leurs scores comme des performances reelles.

  python ml/gen_lighting_data.py                      # cree ml/data_synth_eclairage/*.csv
  python ml/gen_lighting_data.py --days 180 --lamps 30 --seed 1

Fichiers :
  presence_hourly.csv   frequentation par zone et par heure (+ meteo)            -> modele 1 (prediction de frequentation)
  lux_ambient.csv       luminosite ambiante toutes les 10 min                    -> modele 3 (seuil jour / nuit)
  lamp_power_faults.csv mesures de luminaires avec pannes etiquetees             -> modele 2 (detection de pannes)
Bibliotheque standard uniquement.
"""
import argparse
import csv
import math
import os
import random
from datetime import datetime, timedelta

ap = argparse.ArgumentParser()
ap.add_argument("--days", type=int, default=365)
ap.add_argument("--lamps", type=int, default=20)
ap.add_argument("--lamp-days", type=int, default=30)
ap.add_argument("--seed", type=int, default=7)
ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "data_synth_eclairage"))
a = ap.parse_args()
rnd = random.Random(a.seed)
os.makedirs(a.out, exist_ok=True)
LAT, LNG = 36.8, 10.18          # Tunis
START = datetime(2025, 1, 1)


def solar_elevation(dt):
    """Hauteur du soleil (degres) a Tunis (heure locale UTC+1), approximation astronomique."""
    n = dt.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1 + (dt.hour - 12) / 24)
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    dec = 0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
    tst = dt.hour * 60 + dt.minute + eqt + 4 * LNG - 60          # temps solaire vrai (min)
    ha = math.radians(tst / 4 - 180)
    la = math.radians(LAT)
    return math.degrees(math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha)))


def clear_lux(el):
    """Eclairement exterieur (lux) par ciel clair, selon la hauteur du soleil (ordres de grandeur usuels)."""
    if el > 0:
        return 400 + 110000 * math.sin(math.radians(el)) ** 0.9       # ~400 lx au coucher, ~100 000 lx en plein soleil
    if el > -6:
        return 3.4 * (400 / 3.4) ** ((el + 6) / 6)                   # crepuscule civil : 3,4 a 400 lx
    return max(0.1, 3.4 * 10 ** ((el + 6) / 4))                       # nuit : decroissance jusqu'a ~0,1 lx


def weather(dt, state):
    """Meteo simple : saison + persistance des nuages et de la pluie."""
    season = 18 + 9 * math.sin((dt.timetuple().tm_yday - 110) / 365 * 2 * math.pi)
    state["cloud"] = min(100, max(0, state["cloud"] + rnd.gauss(0, 8) + (50 - state["cloud"]) * 0.05))
    rain = max(0.0, rnd.gauss(0, 1.2)) if state["cloud"] > 75 and rnd.random() < 0.25 and (dt.month in (10, 11, 12, 1, 2, 3)) else 0.0
    return round(season + 4 * math.sin((dt.hour - 9) / 24 * 2 * math.pi) + rnd.gauss(0, 1), 1), round(state["cloud"], 0), round(rain, 1)


# ---------- 1) frequentation par zone et par heure ----------
ZONES = {"Centre-ville": "centre", "Avenue commerciale": "centre", "Quartier residentiel A": "residentiel", "Quartier residentiel B": "residentiel",
         "Zone industrielle": "industriel", "Route nationale": "route"}
PROFILE = {   # forme de la frequentation par heure (0-23), valeurs relatives
    "centre": [.10, .05, .03, .02, .02, .05, .15, .35, .55, .6, .65, .75, .85, .8, .75, .8, .9, 1.0, 1.0, .95, .85, .65, .4, .2],
    "residentiel": [.05, .02, .01, .01, .02, .08, .25, .5, .4, .25, .2, .25, .35, .3, .25, .3, .45, .7, .8, .7, .5, .3, .15, .08],
    "industriel": [.1, .08, .08, .08, .1, .25, .6, .8, .6, .5, .5, .55, .5, .5, .55, .7, .8, .5, .25, .15, .12, .1, .1, .1],
    "route": [.12, .07, .05, .05, .1, .2, .45, .75, .8, .6, .55, .6, .7, .65, .6, .65, .8, 1.0, .9, .7, .5, .35, .25, .15]}
state = {"cloud": 50.0}
with open(os.path.join(a.out, "presence_hourly.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["zone", "zone_type", "ts", "hour", "dow", "is_weekend", "month", "temp_c", "cloud_pct", "rain_mm", "is_night", "presence_idx", "pedestrians"])
    for i in range(a.days * 24):
        dt = START + timedelta(hours=i)
        temp, cloud, rain = weather(dt, state)
        night = solar_elevation(dt + timedelta(minutes=30)) < -0.83
        wk = dt.weekday() >= 5
        for zone, zt in ZONES.items():
            base = PROFILE[zt][dt.hour]
            if wk:
                base *= 1.25 if zt == "centre" else 0.6 if zt in ("industriel", "route") else 1.05
            base *= (0.55 if rain > 0.5 else 1.0) * (0.85 if temp > 33 or temp < 6 else 1.0)
            idx = max(0.0, min(1.0, base + rnd.gauss(0, 0.04)))
            w.writerow([zone, zt, dt.strftime("%Y-%m-%dT%H:00"), dt.hour, dt.weekday(), int(wk), dt.month, temp, cloud, rain, int(night), round(idx, 3),
                        max(0, int(idx * (400 if zt == "centre" else 150) + rnd.gauss(0, 6)))])

# ---------- 2) luminosite ambiante ----------
state = {"cloud": 50.0}
with open(os.path.join(a.out, "lux_ambient.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["ts", "sun_elevation_deg", "cloud_pct", "lux", "is_night"])
    for i in range(a.days * 144):
        dt = START + timedelta(minutes=10 * i)
        if i % 6 == 0:
            _, cloud, _ = weather(dt, state)
        el = solar_elevation(dt)
        lux = max(0.1, clear_lux(el) * (1 - 0.8 * cloud / 100) * rnd.uniform(0.9, 1.1))
        w.writerow([dt.strftime("%Y-%m-%dT%H:%M"), round(el, 2), cloud, round(lux, 1), int(el < -0.83)])

# ---------- 3) mesures de luminaires avec pannes ----------
FAULTS = ["dead", "flicker", "overpower", "stuck_on_day"]
with open(os.path.join(a.out, "lamp_power_faults.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["lamp_id", "ts", "level_pct", "power_w", "rated_w", "voltage_v", "current_a", "lux", "label"])
    for k in range(a.lamps):
        lid, rated = f"LP-SYN{k:03d}", rnd.choice([60, 90, 120, 150])
        fault, f0 = (rnd.choice(FAULTS), rnd.randrange(a.lamp_days * 144 // 3, a.lamp_days * 144 * 2 // 3)) if rnd.random() < 0.35 else (None, None)
        for i in range(a.lamp_days * 144):
            dt = START + timedelta(minutes=10 * i)
            el = solar_elevation(dt)
            night = el < -0.83
            level = (100 if dt.hour < 23 else 50) if night else 0
            volt = 230 + rnd.gauss(0, 3)
            power = rated * level / 100 * rnd.uniform(0.97, 1.03)
            label = "ok"
            if fault and i >= f0 and i < f0 + 6 * 24:                         # panne pendant 6 jours
                label = fault
                if fault == "dead" and level > 0:
                    power = rated * 0.03
                elif fault == "flicker" and level > 0:
                    power *= rnd.uniform(0.3, 1.3)
                elif fault == "overpower" and level > 0:
                    power *= rnd.uniform(1.5, 1.9)
                elif fault == "stuck_on_day" and not night:
                    level, power = 100, rated * rnd.uniform(0.97, 1.03)
            lux = max(0.1, (3000 * max(0, math.sin(math.radians(max(el, 0)))) + 3) * rnd.uniform(0.6, 1.0))
            w.writerow([lid, dt.strftime("%Y-%m-%dT%H:%M"), level, round(power, 1), rated, round(volt, 1), round(power / (volt * 0.95), 3), round(lux, 1), label])
print("Fichiers crees dans", a.out)
for fn in sorted(os.listdir(a.out)):
    p = os.path.join(a.out, fn)
    print(f"  {fn}  {os.path.getsize(p) / 1e6:.1f} Mo")
