"""MODELE 2 - Detection d'anomalie (Isolation Forest).

Il n'y a pas de verite terrain dans UCI : on INJECTE des anomalies connues
pour pouvoir mesurer precision et rappel.

python ml/train_anomaly.py [--uci chemin.txt] [--weather meteo.csv]
"""
import argparse

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.metrics import precision_score, recall_score

from data import get_data

ap = argparse.ArgumentParser()
ap.add_argument("--uci")
ap.add_argument("--weather")
a = ap.parse_args()

df = get_data(a.uci, a.weather).copy()
rnd = np.random.default_rng(1)
label = np.zeros(len(df), dtype=int)
col = df.columns.get_loc("kw")
for i in rnd.choice(len(df) - 12, size=max(5, len(df) // 400), replace=False):
    df.iloc[i:i + 8, col] = df.iloc[i:i + 8, col] * rnd.uniform(1.8, 3.0)   # appareil defaillant
    label[i:i + 8] = 1

# Ecart par rapport a la valeur habituelle de cette heure et de ce jour de semaine
usual = df.groupby([df.index.hour, df.index.dayofweek])["kw"].transform("median")
X = np.c_[df["kw"] / (usual + 1e-6),
          df["kw"].rolling(6).mean().bfill() / (usual + 1e-6),
          df.index.hour]

cut = int(len(df) * 0.6)
model = IsolationForest(n_estimators=200, contamination=0.03, random_state=0).fit(X[:cut])
pred = (model.predict(X[cut:]) == -1).astype(int)
print(f"Precision={precision_score(label[cut:], pred):.2f}   Rappel={recall_score(label[cut:], pred):.2f}")
score = -model.score_samples(X[cut:])
print("Score d'anomalie (0-100) du pire point :", round(100 * (score.max() - score.min()) / (score.max() + 1e-9)))
joblib.dump(model, "ml/anomaly_model.joblib")
