"""Copie les comptes de SIMULATION (data/demo/demo.db) dans la vraie base (data/steg_app.db), apres une sauvegarde automatique.

    python tools/import_demo_to_real.py            copie (les comptes deja presents, reconnus par e-mail, sont ignores)
    python tools/import_demo_to_real.py --dry-run  affiche ce qui serait copie, sans rien ecrire

Copie : clients (@kizuna-sim.local) avec profil, equipements, capteurs (memes identifiants et cles), mesures, historique horaire et journalier,
pointes et engagements. NE COPIE PAS : le compte STEG de simulation, les codes de recompense (ils dependent de la cle secrete de chaque base :
ils sont recalcules dans la vraie base avec SA cle, des que la STEG ouvre sa page), les sessions.
Les comptes restent etiquetes « (simulation) » dans leur nom.
"""
import os
import sqlite3
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
REAL = os.path.join(ROOT, "data", "steg_app.db")
DEMO = os.path.join(ROOT, "data", "demo", "demo.db")
sys.stdout.reconfigure(encoding="utf-8")
dry = "--dry-run" in sys.argv

if not os.path.exists(REAL) or not os.path.exists(DEMO):
    sys.exit("Base introuvable : " + (REAL if not os.path.exists(REAL) else DEMO))

if not dry:
    bak = os.path.join(ROOT, "data", "steg_app.sauvegarde-" + time.strftime("%Y%m%d-%H%M%S") + ".db")
    src = sqlite3.connect(REAL)
    dst = sqlite3.connect(bak)
    src.backup(dst)            # copie coherente meme si le serveur tourne
    dst.close(); src.close()
    print("Sauvegarde de la vraie base :", bak)

sys.path.insert(0, ROOT)
from kizuna.mimamori import SCHEMA as MIMAMORI_SCHEMA      # la vraie base recoit les tables de la veille meme si le serveur n'a pas encore ete relance

c = sqlite3.connect(REAL, timeout=30)
c.row_factory = sqlite3.Row
c.execute("PRAGMA foreign_keys = ON")
c.execute("ATTACH DATABASE ? AS d", (DEMO,))
if not dry:
    c.executescript(MIMAMORI_SCHEMA)


def cols(table, schema):
    return [r[1] for r in c.execute(f"PRAGMA {schema}.table_info({table})")]


def common(table, skip=()):
    real, demo = cols(table, "main"), cols(table, "d")
    return [x for x in real if x in demo and x not in skip]


uid_map, n_users = {}, 0
for u in c.execute("SELECT * FROM d.users WHERE role='client' AND email LIKE '%@kizuna-sim.local' ORDER BY id").fetchall():
    if c.execute("SELECT 1 FROM main.users WHERE email=?", (u["email"],)).fetchone():
        uid_map[u["id"]] = c.execute("SELECT id FROM main.users WHERE email=?", (u["email"],)).fetchone()[0]
        print("  deja present :", u["email"]); continue
    n_users += 1
    if dry:
        print("  a copier :", u["email"]); continue
    cur = c.execute("INSERT INTO main.users(email,pw_hash,role,created_at) VALUES (?,?,?,?)", (u["email"], u["pw_hash"], u["role"], u["created_at"]))
    new = cur.lastrowid
    uid_map[u["id"]] = new
    for table in ("profiles", "equipment", "mimamori"):
        cs = common(table, skip=("id", "user_id"))
        for r in c.execute(f"SELECT * FROM d.{table} WHERE user_id=?", (u["id"],)).fetchall():
            c.execute(f"INSERT INTO main.{table}(user_id,{','.join(cs)}) VALUES (?,{','.join('?' * len(cs))})", (new, *[r[x] for x in cs]))

n_dev = n_read = n_hour = 0
if not dry:
    for dv in c.execute("SELECT * FROM d.devices").fetchall():
        if dv["user_id"] not in uid_map or c.execute("SELECT 1 FROM main.devices WHERE id=?", (dv["id"],)).fetchone():
            continue
        cs = common("devices", skip=("user_id",))
        c.execute(f"INSERT INTO main.devices(user_id,{','.join(cs)}) VALUES (?,{','.join('?' * len(cs))})", (uid_map[dv["user_id"]], *[dv[x] for x in cs]))
        n_dev += 1
        for table in ("readings", "hourly", "daily"):
            cs = common(table, skip=("id",))
            rows = c.execute(f"SELECT {','.join(cs)} FROM d.{table} WHERE device_id=?", (dv["id"],)).fetchall()
            c.executemany(f"INSERT OR IGNORE INTO main.{table}({','.join(cs)}) VALUES ({','.join('?' * len(cs))})", [tuple(r) for r in rows])
            if table == "readings": n_read += len(rows)
            if table == "hourly": n_hour += len(rows)
    # pointes (une seule fois) et engagements des clients copies
    if not c.execute("SELECT 1 FROM main.peaks WHERE note LIKE '%simulation%' OR note LIKE '%demonstration%' LIMIT 1").fetchone():
        pk_map = {}
        cs = common("peaks", skip=("id",))
        for p in c.execute("SELECT * FROM d.peaks ORDER BY id").fetchall():
            cur = c.execute(f"INSERT INTO main.peaks({','.join(cs)}) VALUES ({','.join('?' * len(cs))})", tuple(p[x] for x in cs))
            pk_map[p["id"]] = cur.lastrowid
        for a in c.execute("SELECT * FROM d.peak_acks").fetchall():
            if a["user_id"] in uid_map and a["peak_id"] in pk_map:
                c.execute("INSERT OR IGNORE INTO main.peak_acks(peak_id,user_id,status,ts) VALUES (?,?,?,?)", (pk_map[a["peak_id"]], uid_map[a["user_id"]], a["status"], a["ts"]))
        print("  pointes copiees :", len(pk_map))
    c.commit()
c.execute("DETACH DATABASE d")
c.close()
print(("[simulation] " if dry else "") + f"Clients copies : {n_users} | capteurs : {n_dev} | mesures : {n_read} | heures d'historique : {n_hour}")
