"""Simulateur de consommation (donnees SYNTHETIQUES, ordres de grandeur plausibles)."""
import math
import random

REGIONS = ["Tunis", "Sfax", "Sousse", "Kairouan", "Gabes"]

# Profils horaires (poids sur 24 h, normalises plus bas)
def _norm(w):
    s = sum(w)
    return [x / s for x in w]

PROFILES = {
    "flat": _norm([1] * 24),
    "evening": _norm([1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 4, 4, 4, 4, 5, 6, 8, 9, 9, 8, 4, 2]),
    "afternoon": _norm([1, 1, 1, 1, 1, 1, 2, 3, 4, 5, 7, 8, 9, 10, 10, 10, 9, 8, 7, 5, 3, 2, 1, 1]),
    "morning": _norm([0, 0, 0, 0, 0, 3, 8, 8, 4, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0]),
    "night": _norm([4, 4, 4, 4, 3, 2, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 4, 4]),
}

# nom, puissance kW, heures/jour, profil, sensible a la chaleur, vital
CATALOG = {
    "Climatiseur": (1.2, 3.5, "afternoon", True, False),
    "Refrigerateur": (0.15, 8.0, "flat", False, True),
    "Chauffe-eau": (1.5, 1.5, "night", False, False),
    "Television": (0.12, 4.0, "evening", False, False),
    "Eclairage": (0.2, 5.0, "evening", False, False),
    "Machine a laver": (0.8, 0.7, "morning", False, False),
    "Pompe irrigation": (1.1, 2.5, "morning", False, True),
}

BASE_TEMP = 31.0  # temperature "normale" d'ete


def temperature(day, heatwave_from=None):
    """Temperature moyenne du jour (deterministe)."""
    t = BASE_TEMP + 3 * math.sin(day / 8.0)
    if heatwave_from is not None and day >= heatwave_from:
        t += 6
    return t


def make_households(n=200, seed=42):
    rnd = random.Random(seed)
    hh = []
    # Foyers de demo
    hh.append(dict(id=0, name="Amina", region="Tunis", phone="smartphone", breaker_kw=6.0,
                   appliances=["Climatiseur", "Refrigerateur", "Chauffe-eau",
                               "Television", "Eclairage", "Machine a laver"]))
    hh.append(dict(id=1, name="Hedi", region="Kairouan", phone="basic", breaker_kw=5.0,
                   appliances=["Climatiseur", "Refrigerateur", "Chauffe-eau", "Television",
                               "Eclairage", "Pompe irrigation"]))
    for i in range(2, n):
        names = list(CATALOG)
        apps = ["Refrigerateur", "Eclairage", "Television"]
        apps += rnd.sample(names, rnd.randint(1, 4))
        if rnd.random() < 0.6:
            apps.append("Climatiseur")
        hh.append(dict(id=i, name=f"Foyer {i}", region=rnd.choice(REGIONS),
                       phone=rnd.choice(["smartphone", "smartphone", "basic"]),
                       breaker_kw=rnd.choice([5.0, 6.0, 8.0]), appliances=apps))
    return hh


def _heat_factor(temp):
    return max(0.3, min(2.2, 1 + (temp - 28) / 9))


def appliance_daily_kwh(app, temp, fault=False):
    kw, hours, _, sensitive, _ = CATALOG[app]
    h = hours * (_heat_factor(temp) if sensitive else 1.0)
    kwh = kw * h
    if fault and app == "Refrigerateur":
        kwh *= 5.0  # joint/compresseur defaillant
    return kwh


def has_fault(hh, day):
    """Anomalie injectee : frigo defaillant chez Amina a partir du jour 40."""
    return hh["id"] == 0 and day >= 40


def has_leak(hh, day):
    """Fuite de courant injectee chez Hedi a partir du jour 44 (mA vers la terre)."""
    return 45 if (hh["id"] == 1 and day >= 44) else 0


def daily_kwh(hh, day, temp):
    return sum(appliance_daily_kwh(a, temp, has_fault(hh, day)) for a in hh["appliances"])


def hourly_kw(hh, day, temp, curtail=0.0):
    """Puissance (kW) heure par heure. curtail = part de la clim effacee (0..1) de 12h a 18h."""
    out = [0.0] * 24
    for a in hh["appliances"]:
        _, _, prof, sensitive, _ = CATALOG[a]
        kwh = appliance_daily_kwh(a, temp, has_fault(hh, day))
        for h in range(24):
            v = kwh * PROFILES[prof][h]
            if curtail and a == "Climatiseur" and 12 <= h < 18:
                v *= (1 - curtail)
            out[h] += v
    return out
