"""MODELE 3 - Risque de pic regional : predire le pic du lendemain (MW) a partir de la meteo.

Donnees : agregats regionaux generes par le simulateur (la STEG ne publie pas ces donnees).
python ml/train_region.py
"""
import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error

from data import sim

rnd = np.random.default_rng(3)
HH = sim.make_households()
rows = []
for r in sim.REGIONS:
    homes = [h for h in HH if h["region"] == r]
    for d in range(200):
        tmax = 25 + 14 * rnd.random() + rnd.normal(0, 1)
        load = np.zeros(24)
        for h in homes:
            load += np.array(sim.hourly_kw(h, 1, tmax - 4))
        peak_mw = load.max() * 5000 / 1000 * (1 + rnd.normal(0, 0.03))
        rows.append((sim.REGIONS.index(r), d % 7, tmax, peak_mw))

A = np.array(rows)
rnd.shuffle(A)   # melange acceptable ici : chaque jour simule est independant
X, y = A[:, :3], A[:, 3]
n = int(len(A) * 0.8)
print("Baseline (moyenne)   MAE = %.1f MW" % mean_absolute_error(y[n:], np.full(len(y) - n, y[:n].mean())))
lin = LinearRegression().fit(X[:n], y[:n])
print("Regression lineaire  MAE = %.1f MW" % mean_absolute_error(y[n:], lin.predict(X[n:])))
gbm = HistGradientBoostingRegressor(max_iter=200).fit(X[:n], y[:n])
print("Gradient boosting    MAE = %.1f MW" % mean_absolute_error(y[n:], gbm.predict(X[n:])))
joblib.dump(gbm, "ml/region_model.joblib")
