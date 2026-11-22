"""Delestage domestique par priorite : quoi couper ou decaler en premier pendant une pointe.

Tout est calcule a partir des EQUIPEMENTS DECLARES par le foyer (nom, puissance, quantite, circuit, priorite). Aucune mesure n'est inventee :
la puissance affichee est la puissance DECLAREE (maximale), pas la consommation simultanee reelle.

Paliers (du moins gênant au plus gênant) :
  protege    jamais coupe (refrigerateur, congelateur, equipement medical, box internet)
  1 inutile  a eteindre (eclairage des pieces vides, veilles)
  2 tolerable  a couper ou differer un peu (chargeurs, PC, television)
  3 decalable  a reporter apres la pointe (lave-linge, lave-vaisselle, four, chauffe-eau, pompe)
  4 confort  a moduler (climatiseur, ventilateur) : on compte une reduction PARTIELLE (hypothese reglable ci-dessous)
"""
import unicodedata

CIRCUITS = ["Eclairage", "Prises", "Cuisine", "Chambres", "Salon", "Climatisation", "Eau chaude", "Autre"]
PRIORITIES = ["protege", "inutile", "tolerable", "decalable", "confort"]
LEVEL_OF = {"inutile": 1, "tolerable": 2, "decalable": 3, "confort": 4}
LABELS = {"protege": "Protege (jamais coupe)", "inutile": "1 · Inutile : eteindre", "tolerable": "2 · Tolerable : couper ou differer un peu",
          "decalable": "3 · Decalable : reporter apres la pointe", "confort": "4 · Confort : moduler"}
CONFORT_SHARE = 0.5       # HYPOTHESE : un equipement de confort (climatiseur) est module de 50 % et non coupe
MIN_GROUP = 3             # vue regionale : en dessous de 3 foyers participants, aucune valeur n'est affichee (anonymat)

# mots-cles -> (circuit, priorite, delai maximal en heures ou None). Valeurs par defaut, modifiables par le foyer.
RULES = [
    (("medical", "oxygene", "respirat", "dialyse"), "Autre", "protege", None),
    (("refrigerateur", "frigo", "congelateur"), "Cuisine", "protege", None),
    (("box", "modem", "routeur", "internet"), "Prises", "protege", None),
    (("chauffe-eau", "chauffe eau", "cumulus"), "Eau chaude", "decalable", 6),
    (("lave-linge", "machine a laver", "lave linge"), "Cuisine", "decalable", 12),
    (("lave-vaisselle", "lave vaisselle"), "Cuisine", "decalable", 12),
    (("four",), "Cuisine", "decalable", 2),
    (("pompe", "irrigation", "puits", "surpresseur"), "Autre", "decalable", 12),
    (("fer a repasser", "repasser"), "Prises", "decalable", 6),
    (("climatiseur", "clim"), "Climatisation", "confort", None),
    (("ventilateur",), "Chambres", "confort", None),
    (("television", "tele", "tv"), "Salon", "tolerable", None),
    (("ordinateur", "pc", "laptop", "imprimante"), "Prises", "tolerable", None),
    (("chargeur", "telephone"), "Prises", "tolerable", None),
    (("micro-ondes", "micro ondes", "bouilloire", "grille-pain", "mixeur"), "Cuisine", "tolerable", None),
    (("eclairage", "lampe", "ampoule", "led", "lumiere"), "Eclairage", "inutile", None),
]


def _norm(s):
    return unicodedata.normalize("NFKD", str(s).lower()).encode("ascii", "ignore").decode()


def defaults_for(name):
    n = _norm(name)
    for keys, circuit, prio, defer in RULES:
        if any(k in n for k in keys):
            return circuit, prio, defer
    return "Autre", "confort", None


def normalize(e):
    """Complete un equipement avec circuit / priorite / delai (valeurs par defaut d'apres son nom si absents)."""
    c, p, d = defaults_for(e.get("name", ""))
    e = dict(e)
    e["circuit"] = e.get("circuit") if e.get("circuit") in CIRCUITS else c
    e["priority"] = e.get("priority") if e.get("priority") in PRIORITIES else p
    d = e.get("defer_h") if e.get("defer_h") not in (None, "") else d
    e["defer_h"] = int(d) if d not in (None, "") and e["priority"] == "decalable" else None
    return e


def plan(equipment, level=0):
    """Echelle de delestage d'un foyer. `level` = dernier palier accepte (0 = ne participe pas, 1 a 4)."""
    items = [normalize(e) for e in equipment]
    for e in items:
        e["w"] = e["power_w"] * e["quantity"]
    total = sum(e["w"] for e in items)
    rows, cum = [], 0.0
    for prio in PRIORITIES:
        its = [e for e in items if e["priority"] == prio]
        w = sum(e["w"] for e in its)
        shed = w * (CONFORT_SHARE if prio == "confort" else 1.0) if prio != "protege" else 0.0
        lv = LEVEL_OF.get(prio, 0)
        accepted = lv != 0 and lv <= level
        if accepted:
            cum += shed
        rows.append(dict(priority=prio, label=LABELS[prio], level=lv, declared_w=round(w), shed_w=round(shed), accepted=accepted, cumulative_w=round(cum),
                         items=sorted(its, key=lambda x: -x["w"])))
    circuits = {}
    for e in items:
        c = circuits.setdefault(e["circuit"], dict(circuit=e["circuit"], declared_w=0, by_priority={}))
        c["declared_w"] += e["w"]
        c["by_priority"][e["priority"]] = c["by_priority"].get(e["priority"], 0) + e["w"]
    potential = round(cum)
    return dict(level=level, declared_w=round(total), protected_w=rows[0]["declared_w"], potential_w=potential,
                potential_pct=round(100 * potential / total) if total else 0, paliers=rows, circuits=sorted(circuits.values(), key=lambda c: -c["declared_w"]),
                confort_share=CONFORT_SHARE)


def overload_order(equipment, limit=8):
    """Ordre conseille en cas de surcharge : on commence par le palier le moins genant, et dans un palier par la plus grosse puissance."""
    items = [normalize(e) for e in equipment if normalize(e)["priority"] != "protege"]
    for e in items:
        e["w"] = e["power_w"] * e["quantity"]
    items.sort(key=lambda e: (LEVEL_OF[e["priority"]], -e["w"]))
    return [dict(name=e["name"], w=e["w"], priority=e["priority"], circuit=e["circuit"]) for e in items[:limit]]


def regional(clients):
    """Potentiel d'effacement par gouvernorat, a partir des SEULS foyers participants. Valeurs masquees sous MIN_GROUP foyers."""
    regs = {}
    for c in clients:
        level = int(c.get("shed_level") or 0)
        g = regs.setdefault(c["governorate"], dict(governorate=c["governorate"], homes=0, participating=0, declared_w=0, potential_w=0, by_priority={}))
        g["homes"] += 1
        if level > 0 and c["equipment"]:
            p = plan(c["equipment"], level)
            g["participating"] += 1
            g["declared_w"] += p["declared_w"]
            g["potential_w"] += p["potential_w"]
            for row in p["paliers"]:
                if row["accepted"]:
                    g["by_priority"][row["priority"]] = g["by_priority"].get(row["priority"], 0) + row["shed_w"]
    out = []
    for g in regs.values():
        masked = 0 < g["participating"] < MIN_GROUP
        if masked:
            g.update(declared_w=None, potential_w=None, by_priority={})
        g["masked"] = masked
        out.append(g)
    out.sort(key=lambda g: -(g["potential_w"] or 0))
    part = sum(g["participating"] for g in out)
    tot = dict(homes=sum(g["homes"] for g in out), participating=part, masked=0 < part < MIN_GROUP,
               potential_w=None if 0 < part < MIN_GROUP else sum(g["potential_w"] or 0 for g in out),
               declared_w=None if 0 < part < MIN_GROUP else sum(g["declared_w"] or 0 for g in out))
    return dict(regions=out, total=tot, min_group=MIN_GROUP, confort_share=CONFORT_SHARE)
