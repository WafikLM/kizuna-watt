"""Kizuna Watt : application web avec comptes clients et compte STEG.
    python server.py          ->  http://localhost:8000
Bibliotheque standard uniquement (http.server + sqlite3). Le compte STEG se cree avec : python manage.py create-steg
"""
import json
import os
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import threading

from kizuna import ai, anomaly, billing, db, forecasts, lighting, mimamori, peaks, rewards, security, shedding, sms

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
COOKIE = "kw_session"
PAGES = {"/": "index.html", "/login": "login.html", "/signup": "signup.html", "/app": "app.html", "/steg": "steg.html", "/lighting": "lighting.html", "/mimamori": "mimamori.html"}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}
INGEST_LAST = {}   # capteur -> heure de la derniere mesure recue
FAILS = {}         # (ip, email) -> essais de connexion rates


def _sanitize(x, depth=0):
    """Nettoie toute donnee recue : pas de caracteres de controle ni de renversement de texte, profondeur et tailles bornees."""
    if depth > 8:
        raise ValueError("trop profond")
    if isinstance(x, str):
        return db.CTRL_RE.sub("", x) if len(x) <= 1_000_000 else (_ for _ in ()).throw(ValueError("trop long"))
    if isinstance(x, list):
        if len(x) > 5000:
            raise ValueError("liste trop longue")
        return [_sanitize(i, depth + 1) for i in x]
    if isinstance(x, dict):
        if len(x) > 200:
            raise ValueError("trop de champs")
        return {str(k)[:64]: _sanitize(v, depth + 1) for k, v in x.items()}
    return x


def _peaks_for(user):
    c = db.conn()
    try:
        rewards.settle(c, user["id"])          # evalue les pointes terminees : delivre un code si la reduction est mesuree
        return peaks.for_client(c, user)
    finally:
        c.close()


def _rewards_for(user):
    c = db.conn()
    try:
        return rewards.for_client(c, user["id"])
    finally:
        c.close()


def my_payload(user):
    uid, p = user["id"], user["profile"]
    devs = db.list_devices(uid)
    lr = db.latest_reading(uid)
    online = bool(lr and time.time() - lr["ts"] <= db.ONLINE_SECONDS)
    daily = db.daily_kwh(uid, 14)
    usage = anomaly.evaluate(uid, user["equipment"]) if online else dict(alerts=[], baseline=anomaly.baseline(uid), recent_w=None, declared_w=sum(e["power_w"] * e["quantity"] for e in user["equipment"]))
    today = time.strftime("%Y-%m-%d")
    return dict(
        profile=p, equipment=user["equipment"],
        declared_w=sum(e["power_w"] * e["quantity"] for e in user["equipment"]),
        devices=devs, reading=lr, online=online, alerts=db.alerts_for(p.get("breaker_kw"), lr, online) + usage["alerts"] if online else db.alerts_for(p.get("breaker_kw"), lr, online),
        usage=usage,
        series=db.series_24h(uid), daily=daily,
        peaks=_peaks_for(user), rewards=_rewards_for(user), bill=billing.estimate(uid, p.get("breaker_kw"), power_w=(lr or {}).get("power_w") if online else None),
        plan=shedding.plan(user["equipment"], int(p.get("shed_level") or 0)), overload_order=shedding.overload_order(user["equipment"]),
        today_kwh=next((d["kwh"] for d in daily if d["day"] == today), 0),
        kwh_total=round(sum(d["kwh_total"] for d in devs), 3), now=int(time.time()))


class Handler(BaseHTTPRequestHandler):
    server_version = "KizunaWatt"
    sys_version = ""
    timeout = 30            # une connexion qui n'envoie rien est coupee au bout de 30 s (attaques « slowloris »)
    protocol_version = "HTTP/1.0"

    # ---------- utilitaires ----------
    def _send(self, code, body, ctype="application/json", cookie=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in security.HEADERS:
            self.send_header(k, v)
        if security.secure_mode():
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)

    def _token(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return c[COOKIE].value if COOKIE in c else None

    def _cookie(self, token, max_age=db.SESSION_SECONDS):
        return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}" + ("; Secure" if security.secure_mode() else "")

    def _ip(self):
        if security.trust_proxy():
            f = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if f:
                return f[:64]
        return self.client_address[0]

    def _limited(self, rule):
        """Repond 429 et retourne True si cette adresse IP depasse la limite de la regle."""
        if security.allow(rule, self._ip()):
            return False
        self._send(429, {"error": "Trop de requetes. Reessayez un peu plus tard."})
        return True

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n < 0 or n > 1_000_000:
            return None
        try:
            return _sanitize(json.loads(self.rfile.read(n) or b"{}"))
        except Exception:
            return None

    def _same_origin(self):
        o = self.headers.get("Origin")
        return (not o) or urlparse(o).netloc == self.headers.get("Host")

    # ---------- GET ----------
    def do_GET(self):
        try:
            self._get()
        except (ConnectionError, TimeoutError):
            pass
        except Exception as e:           # jamais de details techniques (chemins, requetes SQL) dans la reponse
            print("erreur GET :", type(e).__name__, e)
            try:
                self._send(500, {"error": "erreur interne"})
            except Exception:
                pass

    def do_POST(self):
        try:
            self._post()
        except (ConnectionError, TimeoutError):
            pass
        except Exception as e:
            print("erreur POST :", type(e).__name__, e)
            try:
                self._send(500, {"error": "erreur interne"})
            except Exception:
                pass

    def _get(self):
        u = urlparse(self.path)
        if u.path.startswith("/api/"):
            return self.api_get(u.path, parse_qs(u.query))
        rel = PAGES.get(u.path) or (u.path.lstrip("/") if u.path.startswith("/static/") else None)
        if rel and "\\" not in rel and "\x00" not in rel:
            fp = os.path.normpath(os.path.join(WEB, rel))
            if fp.startswith(WEB + os.sep) and os.path.splitext(fp)[1] in TYPES and os.path.isfile(fp):
                with open(fp, "rb") as f:
                    return self._send(200, f.read(), TYPES.get(os.path.splitext(fp)[1], "application/octet-stream"))
        self._send(404, b"Page introuvable", "text/plain; charset=utf-8")

    def api_get(self, path, q):
        if path == "/api/options":
            return self._send(200, dict(governorates=db.GOVERNORATES, housing=db.HOUSING))
        if path == "/api/mimamori/public":      # page du proche : etat seulement, acces par jeton secret (sans compte)
            if self._limited("public"):
                return
            c = db.conn()
            try:
                d = mimamori.public(c, (q.get("t") or [""])[0])
            finally:
                c.close()
            return self._send(200, d) if d else self._send(404, {"error": "lien invalide ou veille desactivee"})
        user = db.session_user(self._token())
        if not user:
            return self._send(401, {"error": "non connecte"})
        if path == "/api/me":
            return self._send(200, user)
        if path == "/api/my":
            return self._send(200, my_payload(user)) if user["role"] == "client" else self._send(403, {"error": "reserve aux clients"})
        if path == "/api/devices":
            return self._send(200, dict(devices=db.list_devices(user["id"])))
        if path == "/api/bills":           # historique des factures payees saisies par le client
            if user["role"] != "client":
                return self._send(403, {"error": "reserve aux clients"})
            c = db.conn()
            try:
                return self._send(200, billing.history_view(c, user["id"], max(1.0, float(user["profile"].get("breaker_kw") or 3))))
            finally:
                c.close()
        if path == "/api/mimamori":
            if user["role"] != "client":
                return self._send(403, {"error": "reserve aux clients"})
            c = db.conn()
            try:
                return self._send(200, mimamori.view(c, user["id"]))
            finally:
                c.close()
        if path == "/api/my/ai":           # analyse par les modeles d'IA de l'equipe (mesures reelles du capteur)
            if user["role"] != "client":
                return self._send(403, {"error": "reserve aux clients"})
            return self._send(200, ai.analysis(user["id"], user["profile"]["governorate"]))
        if path == "/api/ai/status":
            return self._send(200, ai.status())
        if path == "/api/steg/sms":        # etat du module SMS (numeros masques)
            if user["role"] != "steg":
                return self._send(403, {"error": "reserve au compte STEG"})
            c = db.conn()
            try:
                return self._send(200, sms.stats(c))
            finally:
                c.close()
        if path == "/api/steg/shedding":   # potentiel d'effacement par region (foyers participants uniquement)
            if user["role"] != "steg":
                return self._send(403, {"error": "reserve au compte STEG"})
            return self._send(200, shedding.regional(db.all_clients()))
        if path == "/api/steg/peaks":      # pointes declarees + reponses des foyers (totaux, masques sous 3 foyers)
            if user["role"] != "steg":
                return self._send(403, {"error": "reserve au compte STEG"})
            c = db.conn()
            try:
                rewards.settle_all(c)
                return self._send(200, dict(peaks.for_steg(c, db.all_clients()), rewards=rewards.summary(c)))
            finally:
                c.close()
        if path == "/api/steg/demand":     # modele 3 : demande nationale mensuelle
            if user["role"] != "steg":
                return self._send(403, {"error": "reserve au compte STEG"})
            return self._send(200, ai.demand_forecast())
        if path == "/api/forecasts":       # previsions envoyees par les modeles IA (aucune valeur n'est calculee ici)
            gov = (q.get("governorate") or [None])[0] or (user["profile"].get("governorate") if user["role"] == "client" else forecasts.NATIONAL)
            gran = (q.get("granularity") or [None])[0]
            groups = forecasts.get_forecasts(gov, gran)
            if gran in (None, "day") and not any(g["granularity"] == "day" for g in groups):       # aucun modele n'a fourni de prevision journaliere : meteo reelle en secours
                om = forecasts.open_meteo_group(gov)
                if om:
                    groups = [om] + groups
            return self._send(200, dict(governorate=gov, groups=groups, now=int(time.time())))
        if path == "/api/steg/models":
            if user["role"] != "steg":
                return self._send(403, {"error": "reserve au compte STEG"})
            return self._send(200, dict(models=forecasts.list_models()))
        if path == "/api/lighting/sun":    # lever / coucher du soleil, calcules automatiquement
            try:
                if q.get("lat"):
                    return self._send(200, lighting.sun_info(float(q["lat"][0]), float(q["lng"][0])))
                gov = (q.get("gov") or [None])[0]
                if gov in lighting.GOV_CENTERS:
                    return self._send(200, dict(governorate=gov, **lighting.sun_info(*lighting.GOV_CENTERS[gov])))
            except (ValueError, KeyError):
                return self._send(400, {"error": "lat et lng numeriques requis"})
            return self._send(200, dict(items=lighting.sun_all()))
        if path in ("/api/lighting/streets", "/api/lighting/street"):
            if user["role"] != "steg":
                return self._send(403, {"error": "reserve au compte STEG"})
            if path == "/api/lighting/streets":
                g = lambda k: (q.get(k) or [""])[0]
                return self._send(200, dict(total=lighting.streets_count(), streets=lighting.search_streets(g("q"), g("gov"), g("kind"))))
            st = lighting.street_geometry((q.get("id") or [""])[0])
            return self._send(200, st) if st else self._send(404, {"error": "rue inconnue"})
        if path in ("/api/lighting/overview", "/api/lighting/lamp"):
            if user["role"] != "steg":
                return self._send(403, {"error": "reserve au compte STEG"})
            if path == "/api/lighting/overview":
                return self._send(200, lighting.overview())
            d = lighting.detail((q.get("id") or [""])[0])
            return self._send(200, d) if d else self._send(404, {"error": "luminaire inconnu"})
        if path == "/api/steg/clients":
            if user["role"] != "steg":
                return self._send(403, {"error": "reserve au compte STEG"})
            clients = db.all_clients()
            for c in clients:             # derniere heure analysee par l'Isolation Forest (clients avec capteur)
                c["ai"] = ai.latest_anomaly(c["id"]) if c["devices"] else None
                c["abnormal"] = [a["text"] for a in anomaly.evaluate(c["id"], c["equipment"])["alerts"]] if c["online"] else []
            for c in clients:             # facture prevue du mois (clients avec capteur)
                b = billing.estimate(c["id"], c["breaker_kw"]) if c["devices"] else None
                c["bill"] = None if not b else dict(to_date_kwh=b["to_date_kwh"], projected_kwh=b["projected_kwh"], projected_mil=b["projected"]["total"], low_mil=b["low_total"], high_mil=b["high_total"], to_date_mil=b["to_date"]["total"], slab=b["marginal"]["index"])
            return self._send(200, dict(clients=clients, now=int(time.time())))
        self._send(404, {"error": "inconnu"})

    # ---------- POST ----------
    def _post(self):
        u = urlparse(self.path)
        if not u.path.startswith("/api/") or not self._same_origin() or "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._send(400, {"error": "requete refusee"})
        data = self._body()
        if data is None:
            return self._send(400, {"error": "corps invalide"})
        p = u.path
        if p in ("/api/ingest", "/api/forecast/ingest", "/api/lighting/report", "/api/lighting/predict") and self._limited("ingest_ip"):
            return
        if p == "/api/ingest":
            return self.ingest(data)
        if p == "/api/forecast/ingest":
            return self.forecast_ingest(data)
        if p == "/api/lighting/report":
            return self.lighting_report(data)
        if p == "/api/lighting/predict":
            return self.lighting_predict(data)
        if p == "/api/signup":
            chk = "check" in parse_qs(u.query)
            if self._limited("check" if chk else "signup"):
                return
            return self.signup(data, check=chk)
        if p == "/api/login":
            return self.login(data)
        user = db.session_user(self._token())
        if not user:
            return self._send(401, {"error": "non connecte"})
        if self._limited("api_write"):
            return
        if p == "/api/logout":
            db.end_session(self._token())
            return self._send(200, {"ok": True}, cookie=self._cookie("", 0))
        if p.startswith("/api/lighting/"):
            if user["role"] != "steg":
                return self._send(403, {"error": "reserve au compte STEG"})
            if p == "/api/lighting/lamps":       # nouveau luminaire : la cle du controleur n'est montree qu'une fois
                lid, key, err = lighting.create_lamp(data)
                return self._send(422, {"error": err}) if err else self._send(200, {"lamp_id": lid, "key": key})
            if p == "/api/lighting/streets/lamps":   # luminaires regulierement espaces le long d'une rue
                rows, err = lighting.create_lamps_along(str(data.get("street_id", "")), data.get("spacing_m"), data.get("rated_w"), data.get("lamp_type"), str(data.get("zone") or ""))
                if err:
                    return self._send(422, {"error": err})
                csv_text = "lamp_id,key,name,lat,lng,governorate,rated_w\n" + "\n".join(f'{a},{b},"{c}",{d},{e},{f},{g}' for a, b, c, d, e, f, g in rows)
                return self._send(200, {"created": len(rows), "csv": csv_text})
            if p == "/api/lighting/lamps/delete":
                lighting.delete_lamp(str(data.get("lamp_id", "")))
                return self._send(200, {"ok": True})
            if p == "/api/lighting/policy":
                pol, err = lighting.validate_policy(data)
                if err:
                    return self._send(422, {"error": err})
                lighting.set_policy(pol)
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "inconnu"})
        if p == "/api/steg/rewards/redeem":     # encaissement de codes sur une facture (simulation tant que confirm est faux)
            if user["role"] != "steg":
                return self._send(403, {"error": "reserve au compte STEG"})
            try:
                amount = round(float(data.get("amount_dt")) * 1000)
            except (TypeError, ValueError):
                return self._send(422, {"error": "Montant de facture invalide."})
            codes = data.get("codes")
            if not isinstance(codes, list):
                return self._send(422, {"error": "Liste de codes requise."})
            c = db.conn()
            try:
                res, err = rewards.redeem(c, user["id"], codes, amount, str(data.get("bill_ref") or ""), bool(data.get("confirm")))
            finally:
                c.close()
            return self._send(429 if err and "Trop" in err else 422, {"error": err}) if err else self._send(200, res)
        if p in ("/api/steg/peaks", "/api/steg/peaks/cancel"):
            if user["role"] != "steg":
                return self._send(403, {"error": "reserve au compte STEG"})
            c = db.conn()
            try:
                if p.endswith("/cancel"):
                    peaks.cancel(c, data.get("id"))
                    return self._send(200, {"ok": True})
                pk, err = peaks.validate(data, db.GOVERNORATES)
                if err:
                    return self._send(422, {"error": err})
                return self._send(200, {"ok": True, "id": peaks.create(c, pk)})
            finally:
                c.close()
        if p in ("/api/steg/energy/import", "/api/steg/energy/clear"):
            if user["role"] != "steg":
                return self._send(403, {"error": "reserve au compte STEG"})
            if p == "/api/steg/energy/clear":
                forecasts.clear_energy()
                return self._send(200, {"ok": True})
            n, err = forecasts.import_energy_csv(str(data.get("csv", "")))
            return self._send(422, {"error": err}) if err else self._send(200, {"ok": True, "imported": n})
        if p in ("/api/steg/models", "/api/steg/models/delete", "/api/steg/forecasts/import"):
            if user["role"] != "steg":
                return self._send(403, {"error": "reserve au compte STEG"})
            if p == "/api/steg/models":      # nouveau modele : la cle n'est montree qu'une fois
                mid, key = forecasts.create_model(db.clean_label(data.get("name"), 40) or "Modele")
                return self._send(200, {"model_id": mid, "key": key}) if mid else self._send(422, {"error": "Trop de modeles."})
            if p == "/api/steg/models/delete":
                forecasts.delete_model(str(data.get("model_id", "")))
                return self._send(200, {"ok": True})
            n, err = forecasts.import_csv(str(data.get("csv", "")))
            return self._send(422, {"error": err}) if err else self._send(200, {"ok": True, "imported": n})
        if user["role"] != "client":
            return self._send(403, {"error": "reserve aux clients"})
        if p == "/api/profile":
            prof, err = db.validate_profile({**user["profile"], **data, "consent_terms": 1})
            if err:
                return self._send(422, {"errors": err})
            db.update_profile(user["id"], prof)
            return self._send(200, {"ok": True})
        if p == "/api/shedding":           # palier de delestage accepte par le foyer
            try:
                lv = int(data.get("level"))
                assert 0 <= lv <= 4
            except Exception:
                return self._send(422, {"error": "palier entre 0 et 4"})
            db.set_shed_level(user["id"], lv)
            return self._send(200, {"ok": True})
        if p == "/api/peaks/ack":          # le foyer a vu la pointe, ou dit l'avoir suivie / refusee (declaratif)
            c = db.conn()
            try:
                ok = peaks.respond(c, user, data.get("id"), str(data.get("status")))
            finally:
                c.close()
            return self._send(200, {"ok": True}) if ok else self._send(422, {"error": "pointe ou reponse invalide"})
        if p in ("/api/bills", "/api/bills/delete"):
            c = db.conn()
            try:
                if p.endswith("/delete"):
                    billing.delete_history(c, user["id"], data.get("period_start"))
                else:
                    ok, err = billing.add_history(c, user["id"], data)
                    if err:
                        return self._send(422, {"error": err})
            finally:
                c.close()
            return self._send(200, {"ok": True})
        if p in ("/api/mimamori", "/api/mimamori/pause", "/api/mimamori/ok"):      # reglages de la veille (consentement, proche, pause, « je vais bien »)
            c = db.conn()
            try:
                if p.endswith("/pause"):
                    mimamori.pause(c, user["id"], data.get("days") or 0)
                elif p.endswith("/ok"):
                    mimamori.confirm_ok(c, user["id"])
                else:
                    res, err = mimamori.save(c, user["id"], data)
                    if err:
                        return self._send(422, {"error": err})
            finally:
                c.close()
            return self._send(200, {"ok": True})
        if p == "/api/equipment":          # remplace toute la liste
            items, err = db.validate_equipment(data.get("items"))
            if err:
                return self._send(422, {"error": err})
            db.set_equipment(user["id"], items)
            return self._send(200, {"ok": True})
        if p == "/api/devices":            # ajouter un capteur : la cle n'est montree qu'une fois
            dev_id, key = db.create_device(user["id"], db.clean_label(data.get("name"), 40) or "Capteur")
            if not dev_id:
                return self._send(422, {"error": f"Maximum {db.MAX_DEVICES} capteurs par compte."})
            return self._send(200, {"device_id": dev_id, "key": key})
        if p == "/api/devices/delete":
            db.delete_device(user["id"], str(data.get("device_id", "")))
            return self._send(200, {"ok": True})
        self._send(404, {"error": "inconnu"})

    def ingest(self, d):
        """Mesures d'une carte capteur : en-tetes X-Device-Id et X-Device-Key, corps JSON."""
        dev = db.device_auth(self.headers.get("X-Device-Id"), self.headers.get("X-Device-Key"))
        if not dev:
            return self._send(401, {"error": "capteur inconnu ou cle invalide"})
        now = time.time()
        if now - INGEST_LAST.get(dev["id"], 0) < 1.0:
            return self._send(429, {"error": "trop rapide : 1 mesure par seconde maximum"})
        INGEST_LAST[dev["id"]] = now
        r, err = db.validate_reading(d)
        if err:
            return self._send(422, {"error": err})
        db.add_reading(dev, r)
        self._send(200, {"ok": True, "server_time": int(now)})

    def forecast_ingest(self, d):
        """Sorties d'un modele IA : en-tetes X-Model-Id et X-Model-Key, corps {"version": "...", "items": [...]}."""
        mid = forecasts.model_auth(self.headers.get("X-Model-Id"), self.headers.get("X-Model-Key"))
        if not mid:
            return self._send(401, {"error": "modele inconnu ou cle invalide"})
        now = time.time()
        if now - INGEST_LAST.get(mid, 0) < 2.0:
            return self._send(429, {"error": "trop rapide : 1 envoi toutes les 2 secondes maximum"})
        INGEST_LAST[mid] = now
        n, err = forecasts.upsert(mid, d.get("items"), d.get("version", ""))
        if err:
            return self._send(422, {"error": err, "imported": 0})
        self._send(200, {"ok": True, "imported": n})

    def lighting_report(self, d):
        """Rapport d'un controleur de luminaire : X-Lamp-Id / X-Lamp-Key. La reponse contient la consigne a appliquer."""
        lamp = lighting.lamp_auth(self.headers.get("X-Lamp-Id"), self.headers.get("X-Lamp-Key"))
        if not lamp:
            return self._send(401, {"error": "luminaire inconnu ou cle invalide"})
        now = time.time()
        if now - INGEST_LAST.get(lamp["id"], 0) < 1.0:
            return self._send(429, {"error": "trop rapide : 1 rapport par seconde maximum"})
        INGEST_LAST[lamp["id"]] = now
        r, err = lighting.report(lamp, d)
        self._send(422, {"error": err}) if err else self._send(200, r)

    def lighting_predict(self, d):
        """Frequentation prevue par un modele d'IA : X-Model-Id / X-Model-Key, corps {"items": [{target, hour, presence}]}."""
        mid = forecasts.model_auth(self.headers.get("X-Model-Id"), self.headers.get("X-Model-Key"))
        if not mid:
            return self._send(401, {"error": "modele inconnu ou cle invalide"})
        now = time.time()
        if now - INGEST_LAST.get("P" + mid, 0) < 2.0:
            return self._send(429, {"error": "trop rapide : 1 envoi toutes les 2 secondes maximum"})
        INGEST_LAST["P" + mid] = now
        n, err = lighting.upsert_predictions(mid, d.get("items"))
        self._send(422, {"error": err, "imported": 0}) if err else self._send(200, {"ok": True, "imported": n})

    def signup(self, d, check=False):
        email = (d.get("email") if isinstance(d.get("email"), str) else "").strip().lower()
        errors = {}
        if not db.EMAIL_RE.match(email) or len(email) > 120:
            errors["email"] = "Adresse e-mail invalide."
        pw_err = db.validate_password(d.get("password"), d.get("password2"))
        if pw_err:
            errors["password2" if "correspondent" in pw_err else "password"] = pw_err
        prof, perr = db.validate_profile(d)
        errors.update(perr)
        items, eerr = db.validate_equipment(d.get("equipment") or [])
        if eerr:
            errors["equipment"] = eerr
        if not errors and db.by_email(email):
            errors["email"] = "Un compte existe deja avec cette adresse."
        if errors:
            return self._send(422, {"errors": errors})
        if check:   # verification seule : ne cree rien
            return self._send(200, {"ok": True, "checked": True})
        uid = db.create_user(email, d["password"], prof, role="client", equipment=items)
        self._send(200, {"ok": True}, cookie=self._cookie(db.new_session(uid)))

    def login(self, d):
        email = (d.get("email") if isinstance(d.get("email"), str) else "").strip().lower()
        if self._limited("login_ip"):
            return
        key = (self._ip(), email)
        now = time.time()
        if len(FAILS) > 5000:            # borne la memoire
            for k in [k for k, v in FAILS.items() if not v or now - v[-1] > 300]:
                FAILS.pop(k, None)
        FAILS[key] = [t for t in FAILS.get(key, []) if now - t < 300]
        if len(FAILS[key]) >= 5:
            return self._send(429, {"error": "Trop d'essais. Reessayez dans 5 minutes."})
        row = db.by_email(email)
        pw = d.get("password") if isinstance(d.get("password"), str) else ""
        ok = db.check_pw(pw[:128], row["pw_hash"]) if row else (db.check_pw(pw[:128], db.DUMMY_HASH) and False)       # meme duree que le compte existe ou non
        if not row or not ok:
            FAILS[key].append(now)
            return self._send(401, {"error": "E-mail ou mot de passe incorrect."})
        FAILS.pop(key, None)
        if db.needs_rehash(row["pw_hash"]):
            db.rehash(row["id"], pw)
        self._send(200, {"ok": True, "role": row["role"]}, cookie=self._cookie(db.new_session(row["id"])))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    db.init()
    streets_file = os.path.join(ROOT, "data", "streets_tunisia.json")
    if os.path.exists(streets_file) and lighting.streets_count() == 0:
        print(f"Import des rues principales : {lighting.import_streets(streets_file)} rues.")
    counts = db.count_by_role()
    if not counts.get("steg"):
        print("Aucun compte STEG. Creez-le avec :  python manage.py create-steg")
    def _veille():          # veille « mimamori » : toutes les 30 s
        while True:
            time.sleep(30)
            try:
                cc = db.conn()
                try:
                    mimamori.scan(cc)
                finally:
                    cc.close()
            except Exception as e:
                print("veille mimamori :", e)
    threading.Thread(target=_veille, daemon=True).start()
    def _sms_worker():
        while True:
            time.sleep(15)
            try:
                cc = db.conn()
                try:
                    sms.process(cc)
                finally:
                    cc.close()
            except Exception as e:
                print("file SMS :", e)
    threading.Thread(target=_sms_worker, daemon=True).start()
    print("Messages :", sms.describe(), "|", sms.mail_describe())
    port = int(os.environ.get("PORT", 8000))
    print(f"Kizuna Watt -> {'https' if security.tls_enabled() else 'http'}://localhost:{port}   ({counts.get('client', 0)} client(s), {counts.get('steg', 0)} compte(s) STEG)")
    httpd = ThreadingHTTPServer(("", port), Handler)
    if security.tls_enabled():
        httpd.socket = security.tls_context().wrap_socket(httpd.socket, server_side=True)
        print("HTTPS actif.")
    elif not security.secure_mode():
        print("Attention : HTTP simple (developpement). Pour une vraie mise en service : HTTPS, voir « python manage.py security ».")
    httpd.serve_forever()
