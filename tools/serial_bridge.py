"""Pont serie -> application : relie une carte (Proteus, Arduino reel, ESP32 en USB) a un compte Kizuna Watt.

La carte envoie une ligne JSON par mesure sur son port serie, par exemple :
  {"power_w":812.5,"voltage":230.0,"current_a":3.84,"cosphi":0.92,"leak_ma":2.1}
Ce script lit ces lignes et les poste sur /api/ingest, avec l'identifiant et la cle du capteur.

Avec Proteus : COMPIM est branche sur COM5, ce script lit COM6 (paire virtuelle com0com).

  pip install pyserial
  python tools/serial_bridge.py --port COM6 --id KW-XXXXXX --key LA_CLE

Test sans port serie (lit les lignes sur l'entree standard) :
  echo {"power_w":500} | python tools/serial_bridge.py --stdin --id KW-XXXXXX --key LA_CLE
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8000")
ap.add_argument("--id", required=True)
ap.add_argument("--key", required=True)
ap.add_argument("--port", help="port serie, ex. COM6 (paire virtuelle de celui de COMPIM)")
ap.add_argument("--baud", type=int, default=9600)
ap.add_argument("--stdin", action="store_true", help="lire les lignes sur l'entree standard (test)")
a = ap.parse_args()


def lines():
    if a.stdin:
        for line in sys.stdin:
            yield line
        return
    if not a.port:
        sys.exit("Indiquez --port COMx (ou --stdin pour tester).")
    try:
        import serial
    except ImportError:
        sys.exit("pyserial est requis : pip install pyserial")
    try:
        ser = serial.Serial(a.port, a.baud, timeout=3)
    except serial.SerialException as e:
        sys.exit(f"Impossible d'ouvrir {a.port} : {e}\nVerifiez que com0com a cree la paire et qu'aucun autre programme n'utilise ce port.")
    print(f"Ecoute de {a.port} a {a.baud} bauds...")
    while True:
        raw = ser.readline()
        if raw:
            yield raw.decode("utf-8", errors="ignore")


def post(obj):
    req = urllib.request.Request(a.url.rstrip("/") + "/api/ingest", data=json.dumps(obj).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Device-Id": a.id, "X-Device-Key": a.key})
    try:
        r = urllib.request.urlopen(req, timeout=5)
        return r.status, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, str(e)


last = 0.0
for line in lines():
    line = line.strip()
    if not line.startswith("{"):
        continue                       # texte de debug de la carte : ignore
    try:
        obj = json.loads(line)
    except ValueError:
        print("ligne ignoree (JSON invalide) :", line[:80])
        continue
    if time.time() - last < 1.05:      # le serveur accepte 1 mesure par seconde
        continue
    last = time.time()
    code, msg = post(obj)
    print(time.strftime("%H:%M:%S"), code, line[:90], msg)
    if code == 401:
        sys.exit("Identifiant ou cle refuse : verifiez --id et --key.")
