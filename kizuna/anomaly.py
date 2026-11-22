"""Consommation anormale : comparee aux EQUIPEMENTS DECLARES et aux HABITUDES MESUREES du foyer.

Deux controles, uniquement a partir de mesures reelles du capteur (rien n'est invente) :

1. Capacite declaree : la puissance mesuree depasse CAPACITY_MARGIN x la somme des puissances declarees.
   Aucun appareil declare ne peut expliquer cela : appareil non declare, defaut, ou declaration incomplete.
2. Habitudes : a la meme heure de la journee, les jours precedents (>= MIN_DAYS jours) donnent une moyenne et un ecart-type.
   Alerte si la puissance des 10 dernieres minutes depasse moyenne + Z_SCORE x ecart-type, ET au moins RATIO x la moyenne,
   ET d'au moins MIN_EXCESS_W watts (pour ne pas alerter sur des ecarts negligeables).

Les seuils ci-dessous sont des HYPOTHESES de depart, a valider avec des donnees reelles.
Quand une alerte se declenche, on indique quels equipements declares pourraient expliquer l'exces (a titre indicatif).
"""
import itertools
import time

from . import db, shedding

CAPACITY_MARGIN = 1.10
MIN_DAYS = 5
Z_SCORE = 3.0
RATIO = 1.5
MIN_EXCESS_W = 300
MIN_SAMPLES_PER_HOUR = 10     # une heure historique n'est utilisee que si elle contient assez de mesures
RECENT_S = 600


def _explain(equipment, excess_w):
    """Combinaison (au plus 3) d'equipements declares non proteges dont la puissance se rapproche le plus de l'exces."""
    items = [shedding.normalize(e) for e in equipment]
    items = [dict(name=e["name"], w=e["power_w"]) for e in items if e["priority"] != "protege" for _ in range(min(e["quantity"], 3))]
    items = [x for x in items if x["w"] >= 0.1 * excess_w]      # on ignore les petites charges (une lampe n'explique pas 2 kW)
    best, gap = None, None
    for r in (1, 2, 3):
        for combo in itertools.combinations(items[:14], r):
            g = abs(sum(x["w"] for x in combo) - excess_w)
            if gap is None or g < gap - 1e-9:
                best, gap = combo, g
    if best is None or gap > 0.35 * excess_w:
        return None
    names = {}
    for x in best:
        names[x["name"]] = names.get(x["name"], 0) + 1
    return " + ".join(n + (f" ×{k}" if k > 1 else "") for n, k in names.items())


def baseline(uid, now=None):
    """Habitude du foyer a l'heure courante : moyenne et ecart-type des moyennes horaires des jours precedents."""
    now = int(now or time.time())
    hour_now = now - now % 3600
    hod = time.localtime(now).tm_hour
    vals = []
    for ts, h in db.hourly_series(uid).items():
        if ts < hour_now and time.localtime(ts).tm_hour == hod and h["n"] >= MIN_SAMPLES_PER_HOUR and now - ts > 3600:
            vals.append(h["mean_w"])
    if len(vals) < MIN_DAYS:
        return dict(days=len(vals), needed=MIN_DAYS, ready=False)
    mu = sum(vals) / len(vals)
    sd = (sum((v - mu) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5
    return dict(days=len(vals), needed=MIN_DAYS, ready=True, mean_w=round(mu), std_w=round(sd), hour=hod)


def evaluate(uid, equipment, now=None):
    """Retourne {alerts: [...], baseline: {...}, recent_w, declared_w}. Aucune alerte si le capteur est hors ligne."""
    now = int(now or time.time())
    declared = sum(e["power_w"] * e["quantity"] for e in equipment)
    recent = db.recent_power(uid, RECENT_S, now)
    base = baseline(uid, now)
    out = dict(alerts=[], baseline=base, recent_w=None if recent is None else round(recent), declared_w=declared)
    if recent is None:
        return out
    if declared and recent > CAPACITY_MARGIN * declared:
        out["alerts"].append(dict(level="warn", kind="over_declared",
                                  text=f"Consommation de {recent:.0f} W alors que vos equipements declares totalisent {declared} W. Un appareil n'est pas declare, ou un equipement consomme plus que prevu."))
    if base["ready"]:
        mu, sd = base["mean_w"], max(base["std_w"], 0.1 * base["mean_w"])
        excess = recent - mu
        if recent > mu + Z_SCORE * sd and recent > RATIO * mu and excess >= MIN_EXCESS_W:
            hint = _explain(equipment, excess)
            out["alerts"].append(dict(level="warn", kind="abnormal",
                                      text=f"Consommation anormale : {recent:.0f} W en ce moment, contre {mu:.0f} W d'habitude a cette heure (exces de {excess:.0f} W)."
                                           + (f" Cela pourrait correspondre a : {hint}." if hint else " Aucune combinaison d'equipements declares ne correspond a cet exces : verifiez vos equipements.")))
    return out
