"""Chargement des donnees + features, communs aux 4 modeles.
Deux sources : le dataset UCI (reel, France) ou le mode synthetique (notre simulateur),
pour tester sans rien telecharger."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import sim_synth as sim  # noqa: E402  (simulateur utilise uniquement pour tester les scripts sans dataset)


def load_uci(path):
    """UCI Individual Household Electric Power Consumption (fichier .txt, separateur ';').
    Retourne un DataFrame HORAIRE : kw (puissance moyenne), sub1/sub2/sub3 (Wh par heure)."""
    df = pd.read_csv(path, sep=";", na_values="?", low_memory=False)
    df.index = pd.to_datetime(df["Date"] + " " + df["Time"], dayfirst=True)
    df = df.drop(columns=["Date", "Time"]).astype(float)
    hourly = pd.DataFrame({
        "kw": df["Global_active_power"].resample("h").mean(),
        "sub1": df["Sub_metering_1"].resample("h").sum(min_count=30),   # cuisine
        "sub2": df["Sub_metering_2"].resample("h").sum(min_count=30),   # buanderie
        "sub3": df["Sub_metering_3"].resample("h").sum(min_count=30),   # chauffe-eau + clim
    })
    return hourly.interpolate(limit=6).dropna()


def add_weather(df, weather_csv):
    """Fusionne une meteo horaire (colonnes : time, temperature_2m), ex. export Open-Meteo."""
    # Le CSV Open-Meteo commence par 3 lignes de metadonnees, puis : time,temperature_2m (°C)
    w = pd.read_csv(weather_csv, skiprows=3)
    w = w.iloc[:, :2]
    w.columns = ["time", "temp"]
    w["time"] = pd.to_datetime(w["time"])
    return df.join(w.set_index("time"), how="left").interpolate()


def synth_household(days=150, seed=0):
    """Foyer simule avec bruit. Colonnes : kw, temp, ac_kw (part clim, pour la decomposition)."""
    rnd = np.random.default_rng(seed)
    hh = sim.make_households()[0]
    rows = []
    idx = pd.date_range("2026-05-01", periods=days * 24, freq="h")
    for d in range(days):
        temp = 24 + 12 * abs(np.sin(d / 9.0)) + rnd.normal(0, 1.5)
        total = sim.hourly_kw(hh, 1, temp)
        ac_kwh = sim.appliance_daily_kwh("Climatiseur", temp)
        for h in range(24):
            noise = 1 + rnd.normal(0, 0.12)
            rows.append((total[h] * noise,
                         temp + 3 * np.sin((h - 9) / 24 * 2 * np.pi),
                         ac_kwh * sim.PROFILES["afternoon"][h] * noise))
    return pd.DataFrame(rows, index=idx, columns=["kw", "temp", "ac_kw"])


def make_features(df):
    """Features de prevision : calendrier + meteo + retards. La cible est 'kw' a l'heure t."""
    f = pd.DataFrame(index=df.index)
    f["hour"] = df.index.hour
    f["dow"] = df.index.dayofweek
    f["month"] = df.index.month
    if "temp" in df:
        f["temp"] = df["temp"]
    f["lag_24"] = df["kw"].shift(24)        # meme heure hier
    f["lag_168"] = df["kw"].shift(168)      # meme heure la semaine derniere
    f["roll_24"] = df["kw"].shift(1).rolling(24).mean()
    f["y"] = df["kw"]
    return f.dropna()


def get_data(uci_path=None, weather=None):
    if uci_path:
        df = load_uci(uci_path)
        if weather:
            df = add_weather(df, weather)
        return df
    return synth_household()


def split_time(f, test_frac=0.2):
    """Decoupage TEMPOREL (jamais aleatoire pour une serie temporelle)."""
    n = int(len(f) * (1 - test_frac))
    return f.iloc[:n], f.iloc[n:]
