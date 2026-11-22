"""Facture previsionnelle d'electricite (STEG, basse tension, usage domestique) calculee a partir des mesures REELLES du capteur.

TARIFS (communiques par l'equipe, source : tunisie-electricite.com — a VALIDER avec le bareme officiel en vigueur) :
  energie, par tranche mensuelle, en millimes/kWh HT :
    foyers a 1-2 kVA : 0-50 kWh : 62 | 51-100 : 96 | 101-200 : 176 | 201-300 : 218 | 301-500 : 341 | > 500 : 414
    foyers > 2 kVA   : 0-200 kWh : 176 | 201-300 : 218 | 301-500 : 341 | > 500 : 414
  redevance de puissance : 700 millimes / mois / kVA souscrit, TVA 19 %
  TVA sur l'energie : 7 % si la consommation mensuelle <= 300 kWh, 13 % au-dela.

FACTURATION BIMESTRIELLE : la STEG facture tous les 2 MOIS. Les tranches et la redevance ci-dessus sont mensuelles : on les multiplie par PERIOD_MONTHS (= 2)
pour une periode de facturation (tranches 0-100 / 101-200 / ..., redevance 1 400 millimes/kVA, seuil de TVA 600 kWh).
HYPOTHESES (a confirmer) :
  - les tranches s'appliquent PROGRESSIVEMENT (chaque kWh est facture au prix de sa tranche) ;
  - les seuils mensuels sont simplement doubles pour la periode de 2 mois ;
  - les periodes suivent le calendrier (janv.-fevr., mars-avr., mai-juin, juil.-aout, sept.-oct., nov.-dec.) : le vrai cycle depend de la date de releve du compteur ;
  - le bareme « > 2 kVA » commence a la tranche 0-200 kWh mensuelle (les deux premieres tranches ne sont citees que pour 1-2 kVA) ;
  - kVA souscrits = kW declares a l'inscription (cos phi non pris en compte).
NON INCLUS (montants non fournis) : contribution aux collectivites locales, Fonds de Transition Energetique (exoneration <= 100 kWh/mois),
redevance audiovisuelle. La facture reelle sera donc un peu plus elevee.

PROJECTION : consommation de la periode mesuree jusqu'a maintenant + consommation attendue pour le reste de la periode, deduite du PROFIL HORAIRE du foyer
(moyenne des memes heures les jours precedents, semaine / week-end distingues). Fourchette a 80 % : variation observee des totaux journaliers.
Sans assez d'historique : moyenne journaliere. Aucune valeur n'est inventee : sans capteur, aucune estimation.

HISTORIQUE DES FACTURES PAYEES (table bill_history, saisi par le client) — il aide la prevision de trois facons :
  1. le CYCLE de facturation est deduit des periodes saisies (mois pairs ou impairs) ;
  2. tant que le capteur a peu de jours de mesures, la consommation restante est tiree vers la moyenne quotidienne des dernieres factures
     (poids de l'historique = 70 % avec 3 jours de capteur, 15 % a partir de 14 jours) ;
  3. si une periode facturee a ete entierement mesuree par le capteur, le rapport kWh factures / kWh mesures CALIBRE le capteur (borne a 0,8-1,25).
Le kWh d'une facture peut etre saisi, sinon il est deduit du montant en inversant le bareme (approximatif : les taxes non modelisees gonflent un peu le resultat).
"""
import calendar
import math
import time

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS bill_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  period_start TEXT NOT NULL, amount_mil INTEGER NOT NULL, kwh REAL, paid_on TEXT, created_at INTEGER NOT NULL,
  UNIQUE (user_id, period_start)
);
"""


def init(c):
    c.executescript(SCHEMA)

SLABS_LOW = [(50, 62), (100, 96), (200, 176), (300, 218), (500, 341), (None, 414)]      # (borne haute cumulee en kWh, millimes/kWh HT)
SLABS_STD = [(200, 176), (300, 218), (500, 341), (None, 414)]
LOW_KVA = 2.0
PERIOD_MONTHS = 2             # facturation tous les 2 mois
POWER_FEE_MIL = 700            # par kVA et par MOIS
TVA_FEE = 0.19
MIN_SAMPLES = 10
CACHE_S = 15
_cache = {}


def tva_energy(kwh):
    return 0.07 if kwh <= 300 * PERIOD_MONTHS else 0.13


def slabs_for(kva):
    """Tranches de la PERIODE de facturation (bornes mensuelles x PERIOD_MONTHS)."""
    base = SLABS_LOW if kva <= LOW_KVA else SLABS_STD
    return [(None if hi is None else hi * PERIOD_MONTHS, price) for hi, price in base]


def compute(kwh, kva, fee_share=1.0):
    """Facture d'une periode (2 mois) pour `kwh` consommes. fee_share : part de la redevance de puissance deja courue (1 = periode complete). Montants en millimes."""
    kwh = max(0.0, float(kwh))
    rows, prev, energy = [], 0.0, 0.0
    for hi, price in slabs_for(kva):
        top = kwh if hi is None else min(kwh, hi)
        part = max(0.0, top - prev)
        rows.append(dict(from_kwh=prev, to_kwh=hi, price=price, kwh=round(part, 3), mil=round(part * price, 1)))
        energy += part * price
        if hi is not None:
            prev = hi
        if hi is not None and kwh <= hi:
            break
    tva_e = tva_energy(kwh)
    fee = POWER_FEE_MIL * PERIOD_MONTHS * kva * fee_share
    total = energy * (1 + tva_e) + fee * (1 + TVA_FEE)
    return dict(energy_ht=round(energy), tva_energy_rate=tva_e, tva_energy=round(energy * tva_e), fee_ht=round(fee), tva_fee=round(fee * TVA_FEE), total=round(total), slabs=rows)


def marginal(kwh, kva):
    """Prix (millimes/kWh HT) et tranche du prochain kWh ; kWh restants avant la tranche suivante."""
    prev = 0.0
    for i, (hi, price) in enumerate(slabs_for(kva)):
        if hi is None or kwh < hi:
            return dict(index=i, price=price, left=None if hi is None else round(hi - kwh, 1), upper=hi)
        prev = hi
    return dict(index=0, price=0, left=None, upper=None)


MOIS = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout", "septembre", "octobre", "novembre", "decembre"]


def _period_bounds(now, parity=1):
    """Debut / fin de la periode de facturation (2 mois), prefixes AAAA-MM des mois couverts, libelle. parity : 1 = periodes commencant un mois impair (janv., mars...), 0 = pair."""
    lt = time.localtime(now)
    sm = lt.tm_mon if lt.tm_mon % 2 == parity else lt.tm_mon - 1
    idx = lt.tm_year * 12 + sm - 1
    months = [((idx + k) // 12, (idx + k) % 12 + 1) for k in range(PERIOD_MONTHS)]
    ey, em = ((idx + PERIOD_MONTHS) // 12, (idx + PERIOD_MONTHS) % 12 + 1)
    start = time.mktime((months[0][0], months[0][1], 1, 0, 0, 0, 0, 0, -1))
    end = time.mktime((ey, em, 1, 0, 0, 0, 0, 0, -1))
    label = " - ".join(MOIS[m - 1] for _, m in months) + f" {months[-1][0]}"
    return start, end, [f"{y}-{m:02d}" for y, m in months], label


def period_info(ps):
    """Infos d'une periode designee par son premier mois 'AAAA-MM' : (debut, fin, jours, libelle, prefixes)."""
    y, m = int(ps[:4]), int(ps[5:7])
    idx = y * 12 + m - 1
    months = [((idx + k) // 12, (idx + k) % 12 + 1) for k in range(PERIOD_MONTHS)]
    ey, em = ((idx + PERIOD_MONTHS) // 12, (idx + PERIOD_MONTHS) % 12 + 1)
    st, en = time.mktime((y, m, 1, 0, 0, 0, 0, 0, -1)), time.mktime((ey, em, 1, 0, 0, 0, 0, 0, -1))
    return st, en, round((en - st) / 86400), " - ".join(MOIS[mm - 1] for _, mm in months) + f" {months[-1][0]}", [f"{yy}-{mm:02d}" for yy, mm in months]


def kwh_from_amount(amount_mil, kva):
    """Consommation (kWh) dont la facture calculee avec le bareme vaut `amount_mil` (inversion par dichotomie). Approximatif : taxes non modelisees."""
    lo, hi = 0.0, 8000.0
    if compute(hi, kva)["total"] < amount_mil:
        return hi
    for _ in range(50):
        mid = (lo + hi) / 2
        if compute(mid, kva)["total"] < amount_mil:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 1)


# ---------- historique des factures payees ----------
def history(c, uid, kva, now=None):
    """Factures payees saisies par le client, enrichies : kWh (saisi ou deduit), ecart avec le bareme, kWh mesures par le capteur sur la meme periode (si entierement couverte)."""
    now = int(now or time.time())
    rows = c.execute("SELECT * FROM bill_history WHERE user_id=? ORDER BY period_start", (uid,)).fetchall()
    daily = {d["day"]: d["kwh"] for d in db.daily_kwh(uid, 500)}
    out, ratios = [], []
    for rw in rows:
        st, en, days, label, _ = period_info(rw["period_start"])
        inferred = rw["kwh"] is None
        kwh = kwh_from_amount(rw["amount_mil"], kva) if inferred else float(rw["kwh"])
        tariff = compute(kwh, kva)["total"]
        d0 = time.strftime("%Y-%m-%d", time.localtime(st))
        covered = [daily[k] for k in daily if d0 <= k < time.strftime("%Y-%m-%d", time.localtime(en)) and daily[k] > 0.02]
        sensor = sum(covered) if len(covered) >= 0.9 * days else None
        ratio = round(kwh / sensor, 3) if sensor and sensor > 5 else None
        if ratio and not inferred:
            ratios.append(ratio)
        out.append(dict(period_start=rw["period_start"], label=label, days=days, amount_mil=rw["amount_mil"], kwh=round(kwh, 1), kwh_inferred=inferred, paid_on=rw["paid_on"],
                        tariff_total=tariff, gap_pct=round(100 * (rw["amount_mil"] / tariff - 1), 1) if tariff else None, sensor_kwh=None if sensor is None else round(sensor, 1), ratio=ratio,
                        daily_kwh=round(kwh / days, 2), ended=en <= now))
    done = [x for x in out if x["ended"]]
    recent = done[-3:]
    calib = 1.0
    if ratios:
        ratios.sort()
        calib = min(1.25, max(0.8, ratios[len(ratios) // 2]))
    parity = int(out[-1]["period_start"][5:7]) % 2 if out else 1
    return dict(rows=out, daily_avg_kwh=round(sum(x["daily_kwh"] for x in recent) / len(recent), 2) if recent else None, calib=round(calib, 3), calibrated=bool(ratios), parity=parity)


def history_view(c, uid, kva, now=None):
    now = int(now or time.time())
    h = history(c, uid, kva, now)
    lt = time.localtime(now)
    cur = _period_bounds(now, h["parity"])[2][0]
    idx = int(cur[:4]) * 12 + int(cur[5:7]) - 1
    periods = []                       # periodes proposees dans le formulaire : les 13 dernieres, la plus recente d'abord
    for k in range(0, 13):
        i2 = idx - 2 * k
        ps = f"{i2 // 12}-{i2 % 12 + 1:02d}"
        periods.append(dict(value=ps, label=period_info(ps)[3], current=k == 0))
    return dict(history=h, periods=periods, kva=kva, current=cur)


def add_history(c, uid, d, now=None):
    """Enregistre (ou remplace) une facture payee. Retourne (None, erreur) ou (True, None)."""
    now = int(now or time.time())
    ps = str(d.get("period_start") or "")
    try:
        assert len(ps) == 7 and ps[4] == "-"
        y, m = int(ps[:4]), int(ps[5:7])
        assert 2015 <= y <= 2100 and 1 <= m <= 12
        amount = round(float(str(d.get("amount_dt")).replace(",", ".")) * 1000)
        assert 1000 <= amount <= 5_000_000
    except Exception:
        return None, "Periode et montant (en DT, entre 1 et 5000) requis."
    if period_info(ps)[0] > now:
        return None, "Cette periode n'a pas encore commence."
    kwh = d.get("kwh")
    if kwh in (None, ""):
        kwh = None
    else:
        try:
            kwh = float(str(kwh).replace(",", "."))
            assert 1 <= kwh <= 20000
        except Exception:
            return None, "Consommation facturee : entre 1 et 20 000 kWh (ou laissez vide)."
    paid = str(d.get("paid_on") or "")[:10] or None
    if paid and not (len(paid) == 10 and paid[4] == "-" and paid[7] == "-"):
        return None, "Date de paiement invalide."
    c.execute("""INSERT INTO bill_history(user_id,period_start,amount_mil,kwh,paid_on,created_at) VALUES (?,?,?,?,?,?)
                 ON CONFLICT(user_id,period_start) DO UPDATE SET amount_mil=excluded.amount_mil, kwh=excluded.kwh, paid_on=excluded.paid_on""", (uid, ps, amount, kwh, paid, now))
    c.commit()
    _cache.pop(uid, None)
    return True, None


def delete_history(c, uid, ps):
    c.execute("DELETE FROM bill_history WHERE user_id=? AND period_start=?", (uid, str(ps)))
    c.commit()
    _cache.pop(uid, None)


def estimate(uid, breaker_kw, now=None, power_w=None):
    """Retourne None sans mesure ; sinon un dict (montants en millimes, energies en kWh)."""
    now = int(now or time.time())
    hit = _cache.get(uid)
    if hit and now - hit[0] < CACHE_S and hit[2] == power_w:
        return hit[1]
    kva = max(1.0, float(breaker_kw or 3))
    cc = db.conn()
    try:
        hist = history(cc, uid, kva, now)
    finally:
        cc.close()
    start, end, prefixes, label = _period_bounds(now, hist["parity"])
    ndays = round((end - start) / 86400)
    daily = {d["day"]: d["kwh"] for d in db.daily_kwh(uid, 75)}
    month_days = {k: v for k, v in daily.items() if k[:7] in prefixes}
    if not month_days:
        _cache[uid] = (now, None, power_w)
        return None
    calib = hist["calib"]
    to_date = sum(month_days.values()) * calib
    first_day = min(month_days)
    elapsed = (now - start) / (end - start)
    # ---- profil horaire (kWh par heure) : (week-end ?, heure) -> moyenne des jours precedents
    series = db.hourly_series(uid, 24 * 35)
    prof = {}
    for ts, h in series.items():
        if h["n"] < MIN_SAMPLES or ts >= now - now % 3600:
            continue
        lt = time.localtime(ts)
        prof.setdefault((lt.tm_wday >= 5, lt.tm_hour), []).append(h["mean_w"] / 1000)
    by_hour = {}
    for (we, hr), v in prof.items():
        by_hour.setdefault(hr, []).extend(v)
    full_days = sorted(d for d in daily if not d.startswith(time.strftime("%Y-%m-%d", time.localtime(now))))[-14:]
    day_tot = [daily[d] * calib for d in full_days if daily[d] > 0.05]
    method, remaining = None, 0.0
    if len(prof) >= 24 and len(day_tot) >= 3:
        method = "profil horaire"
        hs = now - now % 3600
        while hs < end:
            lt = time.localtime(hs)
            vals = prof.get((lt.tm_wday >= 5, lt.tm_hour)) or by_hour.get(lt.tm_hour) or []
            kwh_h = (sum(vals) / len(vals)) if vals else 0.0
            frac = min(hs + 3600, end) - max(hs, now)
            remaining += kwh_h * max(0.0, frac) / 3600 * calib
            hs += 3600
    elif day_tot:
        method = "moyenne journaliere"
        remaining = (sum(day_tot) / len(day_tot)) * (end - now) / 86400
    else:
        method = "consommation a ce jour"
    days_left = (end - now) / 86400
    # ---- l'historique des factures complete la prevision quand le capteur a peu de recul
    w_sensor, hist_w = 1.0, 0
    if hist["daily_avg_kwh"]:
        w_sensor = min(0.85, max(0.3, len(day_tot) / 14 * 0.85))
        remaining = w_sensor * remaining + (1 - w_sensor) * hist["daily_avg_kwh"] * days_left
        hist_w = round((1 - w_sensor) * 100)
        method += " + historique des factures"
    sd = 0.0
    if len(day_tot) >= 3:
        m = sum(day_tot) / len(day_tot)
        sd = math.sqrt(sum((x - m) ** 2 for x in day_tot) / (len(day_tot) - 1))
    band = 1.28 * sd * math.sqrt(max(days_left, 0))           # intervalle a 80 %
    projected = to_date + remaining
    low, high = max(to_date, projected - band), projected + band
    todate_bill = compute(to_date, kva, fee_share=elapsed)
    proj_bill = compute(projected, kva)
    mg = marginal(to_date, kva)
    done = [x for x in hist["rows"] if x["ended"]]
    last = done[-1] if done else None
    ly = next((x for x in hist["rows"] if x["period_start"] == f"{int(prefixes[0][:4]) - 1}-{prefixes[0][5:]}"), None)
    out = dict(
        month=prefixes[0], period_label=label, period_months=PERIOD_MONTHS, days_in_period=ndays, elapsed_pct=round(100 * elapsed, 1), days_left=round(days_left, 1), kva=kva, method=method, measured_since=first_day,
        partial_month=first_day > prefixes[0] + "-01",
        to_date_kwh=round(to_date, 2), projected_kwh=round(projected, 1), low_kwh=round(low, 1), high_kwh=round(high, 1),
        to_date=todate_bill, projected=proj_bill, low_total=compute(low, kva)["total"], high_total=compute(high, kva)["total"],
        marginal=mg, projected_slab=marginal(projected, kva)["index"],
        cost_per_hour=None if power_w is None else round(power_w / 1000 * mg["price"] * (1 + tva_energy(to_date)), 2),
        slabs=[dict(upper=hi, price=p) for hi, p in slabs_for(kva)],
        history=dict(count=len(hist["rows"]), weight_pct=hist_w, daily_avg_kwh=hist["daily_avg_kwh"], calib=hist["calib"], calibrated=hist["calibrated"],
                     last=None if not last else dict(period_start=last["period_start"], label=last["label"], amount_mil=last["amount_mil"], kwh=last["kwh"], delta_pct=round(100 * (proj_bill["total"] / last["amount_mil"] - 1), 1)),
                     last_year=None if not ly else dict(period_start=ly["period_start"], label=ly["label"], amount_mil=ly["amount_mil"], kwh=ly["kwh"], delta_pct=round(100 * (proj_bill["total"] / ly["amount_mil"] - 1), 1))),
        notes=["Tarifs communiques par l'equipe (a valider avec le bareme officiel).", "Facturation tous les 2 mois : tranches et redevance mensuelles doublees, periodes calendaires (hypotheses).",
               "Hors contribution collectivites locales, Fonds de Transition Energetique et redevance audiovisuelle (montants non fournis)."])
    _cache[uid] = (now, out, power_w)
    return out
