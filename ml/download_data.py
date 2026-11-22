"""Telecharge les deux jeux de donnees dans ml/data_raw/ (a lancer par chaque personne).

python ml/download_data.py

- UCI Individual Household Electric Power Consumption (zip, environ 20 Mo) -> household_power_consumption.txt
- Meteo horaire Open-Meteo pour Sceaux (France), meme periode que UCI -> weather_sceaux.csv
"""
import os
import urllib.request
import zipfile

OUT = os.path.join(os.path.dirname(__file__), "data_raw")
os.makedirs(OUT, exist_ok=True)

UCI = "https://archive.ics.uci.edu/static/public/235/individual+household+electric+power+consumption.zip"
METEO = ("https://archive-api.open-meteo.com/v1/archive?latitude=48.78&longitude=2.29"
         "&start_date=2006-12-16&end_date=2010-11-26&hourly=temperature_2m"
         "&timezone=Europe%2FParis&format=csv")

zip_path = os.path.join(OUT, "uci.zip")
if not os.path.exists(os.path.join(OUT, "household_power_consumption.txt")):
    print("Telechargement UCI...")
    urllib.request.urlretrieve(UCI, zip_path)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(OUT)
    os.remove(zip_path)

meteo_path = os.path.join(OUT, "weather_sceaux.csv")
if not os.path.exists(meteo_path):
    print("Telechargement meteo...")
    urllib.request.urlretrieve(METEO, meteo_path)

print("OK. Lancer par exemple :")
print("  python ml/train_forecast.py --uci ml/data_raw/household_power_consumption.txt "
      "--weather ml/data_raw/weather_sceaux.csv")
