"""Telecharge les rues et routes principales de Tunisie depuis OpenStreetMap (API Overpass) et les prepare pour l'application.

  python tools/fetch_streets.py            # telecharge, cree data/streets_tunisia.json (puis : python manage.py import-streets)
  python tools/fetch_streets.py --resplit  # re-decoupe le fichier deja telecharge (sans reseau)

Classes retenues : autoroutes (motorway), routes nationales (trunk), routes principales (primary) et secondaires (secondary) NOMMEES ou numerotees.
Les troncons de meme nom sont regroupes **seulement s'ils se touchent** : « Avenue Habib Bourguiba » de Sfax et celle de Mahdia sont deux rues.
Chaque rue est simplifiee (~5 m). Le gouvernorat est approche par le chef-lieu le plus proche.
Donnees © les contributeurs d'OpenStreetMap, licence ODbL (https://www.openstreetmap.org/copyright). Il faut conserver cette mention.
Bibliotheque standard uniquement. Utilisation raisonnable du serveur public : ne relancez pas en boucle.
"""
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kizuna.lighting import nearest_governorate  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "streets_tunisia.json")
UA = "KizunaWatt-street-import/1.0 (projet hackathon, usage unique)"
KINDS = ["motorway", "trunk", "primary", "secondary"]
QUERY = ('[out:json][timeout:240];area["ISO3166-1"="TN"][admin_level=2]->.a;'
         '(way(area.a)["highway"~"^(' + "|".join(KINDS) + ')$"]["name"];way(area.a)["highway"~"^(' + "|".join(KINDS) + ')$"]["ref"];);out tags geom;')
LABEL = {"motorway": "Autoroute", "trunk": "Route nationale", "primary": "Route principale", "secondary": "Route secondaire"}
CELL = 0.0004          # ~40 m : deux troncons dont des points sont dans des cases voisines sont consideres comme connectes


def fetch():
    body = urllib.parse.urlencode({"data": QUERY}).encode()
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request("https://overpass-api.de/api/interpreter", data=body, headers={"User-Agent": UA})
            print(f"Telechargement (essai {attempt}/3), cela peut prendre 1 a 3 minutes...", flush=True)
            return json.loads(urllib.request.urlopen(req, timeout=300).read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            print("  echec :", e, flush=True)
            time.sleep(40 * attempt)
    sys.exit("Impossible de telecharger les donnees. Reessayez plus tard.")


def hav(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742000 * math.asin(math.sqrt(h))


def simplify(pts, tol=0.00005):
    """Douglas-Peucker (iteratif) sur [lat, lng], tolerance en degres (~5 m)."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        (y1, x1), (y2, x2) = pts[i], pts[j]
        dx, dy = x2 - x1, y2 - y1
        n = math.hypot(dx, dy) or 1e-12
        best, idx = 0.0, None
        for k in range(i + 1, j):
            d = abs(dy * (pts[k][1] - x1) - dx * (pts[k][0] - y1)) / n
            if d > best:
                best, idx = d, k
        if idx is not None and best > tol:
            keep[idx] = True
            stack += [(i, idx), (idx, j)]
    return [p for p, k in zip(pts, keep) if k]


def components(lines):
    """Regroupe les polylignes qui se touchent (union-find sur une grille de ~40 m). Retourne une liste de listes de polylignes."""
    parent = list(range(len(lines)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    grid = {}
    for idx, line in enumerate(lines):
        for la, lo in line:
            grid.setdefault((int(la / CELL), int(lo / CELL)), set()).add(idx)
    for (gy, gx), ids in grid.items():
        near = set(ids)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                near |= grid.get((gy + dy, gx + dx), set())
        first = None
        for i in near:
            if first is None:
                first = i
            else:
                parent[find(i)] = find(first)
    comps = {}
    for idx, line in enumerate(lines):
        comps.setdefault(find(idx), []).append(line)
    return list(comps.values())


def build(groups):
    streets = []
    for g in sorted(groups, key=lambda x: (KINDS.index(x["kind"]), x["name"] or "")):
        for comp in components([simplify(l) for l in g["lines"]]):
            length = sum(hav(l[k], l[k + 1]) for l in comp for k in range(len(l) - 1))
            pts = [p for l in comp for p in l]
            clat, clng = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
            if length < 150:                              # tronçons isoles tres courts : ignores
                continue
            streets.append(dict(name=g["name"], name_ar=g["name_ar"], ref=g["ref"], kind=g["kind"], kind_label=LABEL[g["kind"]], length_m=round(length),
                                gov=nearest_governorate(clat, clng), lat=round(clat, 5), lng=round(clng, 5), lines=comp))
    streets.sort(key=lambda s: (KINDS.index(s["kind"]), -s["length_m"]))
    for i, s in enumerate(streets):
        s["id"] = f"S-{i + 1:05d}"
    return streets


def save(streets):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(dict(source="OpenStreetMap (Overpass API)", license="ODbL, © les contributeurs d'OpenStreetMap", fetched=time.strftime("%Y-%m-%d"), streets=streets),
                  f, ensure_ascii=False, separators=(",", ":"))
    n = {k: sum(1 for s in streets if s["kind"] == k) for k in KINDS}
    print(f"{len(streets)} rues enregistrees dans {os.path.abspath(OUT)} ({os.path.getsize(OUT) / 1e6:.1f} Mo) : {n}")


def main():
    if "--resplit" in sys.argv:
        old = json.load(open(OUT, encoding="utf-8"))["streets"]
        groups = {}
        for s in old:
            g = groups.setdefault((s["name"], s["ref"], s["kind"]), dict(name=s["name"], name_ar=s["name_ar"], ref=s["ref"], kind=s["kind"], lines=[]))
            g["lines"] += s["lines"]
        save(build(list(groups.values())))
        return
    data = fetch()
    groups = {}
    for el in data.get("elements", []):
        t, geom = el.get("tags", {}), el.get("geometry")
        if not geom or len(geom) < 2:
            continue
        name = t.get("name:fr") or t.get("name") or t.get("ref")
        ref = t.get("ref", "")
        g = groups.setdefault((name, ref, t.get("highway")), dict(name=name, name_ar=t.get("name:ar") or (t.get("name") if t.get("name") != name else ""), ref=ref,
                                                                    kind=t["highway"], lines=[]))
        g["lines"].append([[round(p["lat"], 6), round(p["lon"], 6)] for p in geom])
    save(build(list(groups.values())))


if __name__ == "__main__":
    main()
