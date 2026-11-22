"""Pointes declarees par la STEG, planification des reports et notifications aux foyers.

Une pointe = une fenetre horaire (debut, fin), un palier maximal demande (1 a 4) et, au choix, un gouvernorat.
Pour chaque foyer PARTICIPANT, on deduit de ses equipements declares (voir shedding.py) :
  - paliers 1 et 2 : eteindre / couper pendant la fenetre ;
  - palier 3 : reporter apres la fin, a une heure de redemarrage ECHELONNEE d'un foyer a l'autre (evite l'effet rebond :
    si tout le monde relance sa machine a l'heure pile, la pointe revient juste apres) et bornee par le delai maximal declare ;
  - palier 4 : moduler (hypothese CONFORT_SHARE).
Le foyer repond « fait » ou « refuse ». Ces reponses sont DECLARATIVES (aucun releve de compteur) ; la STEG ne voit que des totaux,
masques sous MIN_GROUP foyers.
"""
import time

from . import shedding

MAX_DURATION_S = 12 * 3600
MAX_AHEAD_S = 14 * 24 * 3600
MAX_NOTE = 200
STAGGER_STEP_S = 10 * 60      # decalage entre foyers
STAGGER_SLOTS = 6             # 6 creneaux de 10 min : relance repartie sur 1 h apres la fin de la pointe
KEEP_AFTER_S = 24 * 3600      # une pointe terminee reste visible 24 h

SCHEMA = """
CREATE TABLE IF NOT EXISTS peaks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  governorate TEXT NOT NULL DEFAULT '',
  start_ts INTEGER NOT NULL, end_ts INTEGER NOT NULL, level INTEGER NOT NULL, note TEXT,
  created_at INTEGER NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0, reward_millimes INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS peak_acks (
  peak_id INTEGER NOT NULL REFERENCES peaks(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('seen','done','declined')), ts INTEGER NOT NULL,
  PRIMARY KEY (peak_id, user_id)
);
"""


def init(c):
    c.executescript(SCHEMA)
    if "reward_millimes" not in {r[1] for r in c.execute("PRAGMA table_info(peaks)")}:      # migration des bases existantes
        c.execute("ALTER TABLE peaks ADD COLUMN reward_millimes INTEGER NOT NULL DEFAULT 0")


def validate(d, governorates, now=None):
    now = now or time.time()
    try:
        start, end, level = int(d.get("start_ts")), int(d.get("end_ts")), int(d.get("level"))
    except (TypeError, ValueError):
        return None, "Debut, fin et palier sont requis."
    gov = str(d.get("governorate") or "").strip()
    if gov and gov not in governorates:
        return None, "Gouvernorat inconnu."
    if not 1 <= level <= 4:
        return None, "Le palier demande doit etre entre 1 et 4."
    if end <= start:
        return None, "La fin doit etre apres le debut."
    if end - start > MAX_DURATION_S:
        return None, "Une pointe dure 12 h au maximum."
    if end <= now:
        return None, "La pointe est deja terminee."
    if start > now + MAX_AHEAD_S:
        return None, "Une pointe se declare 14 jours a l'avance au maximum."
    try:
        reward = round(float(d.get("reward_dt") or 0) * 1000)
    except (TypeError, ValueError):
        return None, "Recompense invalide."
    if not 0 <= reward <= 20000:
        return None, "La recompense par foyer va de 0 a 20 DT."
    return dict(governorate=gov, start_ts=start, end_ts=end, level=level, note=str(d.get("note") or "").strip()[:MAX_NOTE], reward_millimes=reward), None


def create(c, p, now=None):
    cur = c.execute("INSERT INTO peaks(governorate,start_ts,end_ts,level,note,created_at,reward_millimes) VALUES (?,?,?,?,?,?,?)",
                    (p["governorate"], p["start_ts"], p["end_ts"], p["level"], p["note"], int(now or time.time()), p["reward_millimes"]))
    c.commit()
    return cur.lastrowid


def cancel(c, peak_id):
    try:
        pid = int(peak_id)
    except (TypeError, ValueError):
        return
    c.execute("UPDATE peaks SET cancelled=1 WHERE id=?", (pid,))
    c.commit()


def phase(p, now):
    return "upcoming" if now < p["start_ts"] else "active" if now < p["end_ts"] else "ended"


def actions_for(equipment, peak, shed_level, uid):
    """Gestes demandes a un foyer pour cette pointe (palier effectif = min(palier de la pointe, palier accepte par le foyer))."""
    eff = min(int(peak["level"]), int(shed_level or 0))
    if eff <= 0 or not equipment:
        return eff, []
    offset = (int(uid) % STAGGER_SLOTS) * STAGGER_STEP_S
    out = []
    for row in shedding.plan(equipment, eff)["paliers"]:
        if not row["accepted"]:
            continue
        for e in row["items"]:
            a = dict(name=e["name"], quantity=e["quantity"], w=e["w"], circuit=e["circuit"], priority=e["priority"])
            if e["priority"] == "decalable":
                restart = peak["end_ts"] + offset
                if e.get("defer_h") is not None:           # on respecte le delai maximal declare si la pointe le permet
                    restart = min(restart, max(peak["end_ts"], peak["start_ts"] + int(e["defer_h"]) * 3600))
                a.update(kind="reporter", restart_ts=restart, defer_h=e.get("defer_h"),
                         too_long=e.get("defer_h") is not None and restart - peak["start_ts"] > int(e["defer_h"]) * 3600)
            elif e["priority"] == "confort":
                a.update(kind="moduler", w=round(e["w"] * shedding.CONFORT_SHARE))
            else:
                a.update(kind="eteindre")
            out.append(a)
    return eff, out


def for_client(c, user, now=None):
    """Pointes visibles par un foyer (debutees ou a venir, ou terminees depuis moins de 24 h), avec ses gestes et sa reponse."""
    now = int(now or time.time())
    prof = user["profile"]
    rows = c.execute("SELECT * FROM peaks WHERE cancelled=0 AND end_ts > ? AND (governorate='' OR governorate=?) ORDER BY start_ts",
                     (now - KEEP_AFTER_S, prof.get("governorate") or "")).fetchall()
    acks = {r["peak_id"]: r["status"] for r in c.execute("SELECT peak_id,status FROM peak_acks WHERE user_id=?", (user["id"],))}
    results = {r["peak_id"]: dict(r) for r in c.execute("SELECT peak_id,result,measured_w,baseline_w,reduction FROM peak_results WHERE user_id=?", (user["id"],))}
    out = []
    for r in rows:
        p = dict(r)
        eff, acts = actions_for(user["equipment"], p, prof.get("shed_level"), user["id"])
        out.append(dict(id=p["id"], governorate=p["governorate"], start_ts=p["start_ts"], end_ts=p["end_ts"], level=p["level"], note=p["note"],
                        reward_millimes=p["reward_millimes"], result=results.get(p["id"]),
                        phase=phase(p, now), participates=eff > 0, effective_level=eff, actions=acts, freed_w=sum(a["w"] for a in acts), status=acks.get(p["id"])))
    return out


def respond(c, user, peak_id, status, now=None):
    if status not in ("seen", "done", "declined"):
        return False
    pk = c.execute("SELECT governorate,cancelled,end_ts FROM peaks WHERE id=?", (int(peak_id),)).fetchone()
    if pk and status in ("done", "declined") and (now or time.time()) > pk["end_ts"]:     # on ne peut plus s'engager apres la fin (recompenses)
        return False
    if not pk or pk["cancelled"] or (pk["governorate"] and pk["governorate"] != user["profile"].get("governorate")):
        return False
    old = c.execute("SELECT status FROM peak_acks WHERE peak_id=? AND user_id=?", (int(peak_id), user["id"])).fetchone()
    if status == "seen" and old:                           # « vu » ne reecrit jamais une vraie reponse
        return True
    c.execute("INSERT OR REPLACE INTO peak_acks(peak_id,user_id,status,ts) VALUES (?,?,?,?)", (int(peak_id), user["id"], status, int(now or time.time())))
    c.commit()
    return True


def for_steg(c, clients, now=None):
    """Pointes declarees + impact attendu : foyers cibles, participants, reponses et puissance liberable. Totaux masques sous MIN_GROUP participants."""
    now = int(now or time.time())
    rows = c.execute("SELECT * FROM peaks WHERE end_ts > ? ORDER BY start_ts", (now - KEEP_AFTER_S,)).fetchall()
    acks = {}
    for r in c.execute("SELECT peak_id,user_id,status FROM peak_acks"):
        acks.setdefault(r["peak_id"], {})[r["user_id"]] = r["status"]
    out = []
    for r in rows:
        p = dict(r)
        homes = [h for h in clients if not p["governorate"] or h["governorate"] == p["governorate"]]
        part = [h for h in homes if min(int(p["level"]), int(h.get("shed_level") or 0)) > 0 and h["equipment"]]
        masked = 0 < len(part) < shedding.MIN_GROUP
        a = acks.get(p["id"], {})
        potential = sum(shedding.plan(h["equipment"], min(int(p["level"]), int(h["shed_level"])))["potential_w"] for h in part)
        out.append(dict(id=p["id"], governorate=p["governorate"], start_ts=p["start_ts"], end_ts=p["end_ts"], level=p["level"], note=p["note"], reward_millimes=p["reward_millimes"],
                        cancelled=bool(p["cancelled"]), phase="cancelled" if p["cancelled"] else phase(p, now), homes=len(homes), participating=len(part), masked=masked,
                        potential_w=None if masked else potential,
                        done=None if masked else sum(1 for h in part if a.get(h["id"]) == "done"),
                        declined=None if masked else sum(1 for h in part if a.get(h["id"]) == "declined"),
                        seen=None if masked else sum(1 for h in part if a.get(h["id"]) in ("seen", "done", "declined"))))
    return dict(peaks=out, min_group=shedding.MIN_GROUP, now=now)
