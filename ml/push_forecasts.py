"""Envoie les resultats d'un modele (fichier CSV) a l'application.

  python ml/push_forecasts.py --url http://127.0.0.1:8000 --model MD-XXXXXX --key VOTRE_CLE --csv resultats.csv [--version 1.0]

Colonnes du CSV : governorate, period, granularity, temp_mean_c   (obligatoires)
                  temp_min_c, temp_max_c, precip_mm, humidity_pct, confidence, scenario   (facultatives)
Le modele et sa cle se creent dans l'espace STEG : « Modeles IA connectes > Ajouter un modele ».
Bibliotheque standard uniquement.
"""
import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8000")
ap.add_argument("--model", required=True)
ap.add_argument("--key", required=True)
ap.add_argument("--csv", required=True)
ap.add_argument("--version", default="")
a = ap.parse_args()

raw = open(a.csv, "rb").read()
try:
    text = raw.decode("utf-8-sig")
except UnicodeDecodeError:
    text = raw.decode("cp1252")      # fichiers exportes par Excel / Windows
rows = list(csv.DictReader(text.splitlines()))
if not rows:
    sys.exit("Le fichier CSV est vide.")
if "temp_mean_c" not in rows[0]:
    sys.exit("Colonne temp_mean_c absente.")

CHUNK = 2000
for i in range(0, len(rows), CHUNK):
    part = rows[i:i + CHUNK]
    req = urllib.request.Request(a.url.rstrip("/") + "/api/forecast/ingest", method="POST",
                                 data=json.dumps({"version": a.version, "items": part}).encode(),
                                 headers={"Content-Type": "application/json", "X-Model-Id": a.model, "X-Model-Key": a.key})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=30).read())
        print(f"lignes {i + 1}-{i + len(part)} : {r.get('imported')} enregistree(s)")
    except urllib.error.HTTPError as e:
        sys.exit(f"Refuse ({e.code}) : {e.read().decode()}")
    if i + CHUNK < len(rows):
        time.sleep(2.1)          # le serveur accepte 1 envoi toutes les 2 secondes
print("Termine.")
