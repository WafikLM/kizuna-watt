"""MODELE 4 - Decomposition (NILM simplifie) : estimer la part de la climatisation
a partir du compteur global.

UCI : cible = sub3 (chauffe-eau + clim). Synthetique : cible = ac_kw.
EXPERIMENTAL : ne pas le promettre dans le pitch.

python ml/train_disagg.py [--uci chemin.txt] [--weather meteo.csv]
"""
import argparse

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

from data import get_data

ap = argparse.ArgumentParser()
ap.add_argument("--uci")
ap.add_argument("--weather")
a = ap.parse_args()

df = get_data(a.uci, a.weather).copy()
target = "sub3" if a.uci else "ac_kw"
if a.uci:
    df[target] = df[target] / 1000.0   # Wh -> kWh sur l'heure
df["hour"] = df.index.hour
df["kw_prev"] = df["kw"].shift(1)
df["kw_next"] = df["kw"].shift(-1)
df = df.dropna()
X = [c for c in ["kw", "kw_prev", "kw_next", "hour", "temp"] if c in df]
n = int(len(df) * 0.8)
gbm = HistGradientBoostingRegressor(max_iter=200).fit(df[X][:n], df[target][:n])
pred = gbm.predict(df[X][n:])
y = df[target][n:]
print("Baseline (moyenne)  MAE = %.3f" % mean_absolute_error(y, np.full(len(y), df[target][:n].mean())))
print("Gradient boosting   MAE = %.3f" % mean_absolute_error(y, pred))
print("Part estimee de la cible sur le test : %.0f %% (reelle : %.0f %%)"
      % (100 * pred.sum() / df.kw[n:].sum(), 100 * y.sum() / df.kw[n:].sum()))
