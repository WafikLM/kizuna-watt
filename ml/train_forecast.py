"""MODELE 1 - Prevision de consommation du foyer (heure par heure).

python ml/train_forecast.py                              # donnees synthetiques
python ml/train_forecast.py --uci chemin.txt [--weather meteo.csv]
"""
import argparse

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error

from data import get_data, make_features, split_time

ap = argparse.ArgumentParser()
ap.add_argument("--uci")
ap.add_argument("--weather")
a = ap.parse_args()

f = make_features(get_data(a.uci, a.weather))
train, test = split_time(f)
X = [c for c in f.columns if c != "y"]


def report(name, pred):
    mae = mean_absolute_error(test.y, pred)
    rmse = np.sqrt(mean_squared_error(test.y, pred))
    print(f"{name:32s} MAE={mae:.3f} kW   RMSE={rmse:.3f} kW")


report("Baseline : meme heure hier", test["lag_24"])
report("Baseline : meme heure sem. -1", test["lag_168"])
lin = LinearRegression().fit(train[X], train.y)
report("Regression lineaire", lin.predict(test[X]))
gbm = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.08).fit(train[X], train.y)
report("Gradient boosting", gbm.predict(test[X]))
joblib.dump(gbm, "ml/forecast_model.joblib")
