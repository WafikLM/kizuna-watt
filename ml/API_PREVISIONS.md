# Envoyer les previsions meteo / climat d'un modele a l'application

L'application **n'invente aucune prevision** : elle affiche ce que vos modeles lui envoient (espace STEG : « Meteo et climat » ;
espace client : onglet « Meteo et climat », pour son gouvernorat).

## 1. Creer le modele et sa cle
Compte STEG > section « Modeles IA connectes » > **+ Ajouter un modele**.
Vous obtenez un **identifiant** (`MD-XXXXXX`) et une **cle secrete**, affichee une seule fois. Un modele = une cle.

## 2. Format des donnees
Une ligne par zone et par periode.

| Colonne | Obligatoire | Valeurs |
|---|---|---|
| `governorate` | oui | un des 24 gouvernorats (ex. `Tunis`, `Sfax`, `Gabès`, `Le Kef`) ou `Tunisie` pour le national |
| `granularity` | oui | `day`, `month` ou `year` |
| `period` | oui | `2030-07-15` (day), `2030-07` (month), `2030` (year) — doit correspondre a `granularity` |
| `temp_mean_c` | oui | temperature moyenne, de −30 a 60 °C |
| `temp_min_c`, `temp_max_c` | non | −40 a 70 °C ; `min ≤ mean ≤ max` |
| `precip_mm` | non | 0 a 3000 |
| `humidity_pct` | non | 0 a 100 |
| `confidence` | non | 0 a 1 |
| `scenario` | non | texte court, ex. `SSP2-4.5` (pour comparer des trajectoires climatiques) |

Re-envoyer la meme ligne (meme modele, zone, echelle, periode, scenario) **met a jour** la valeur. Les noms de gouvernorats s'ecrivent comme dans la liste de l'application (avec accents : `Béja`, `Gabès`, `Kébili`, `Médenine`).
Si **une seule ligne** est invalide, **rien n'est enregistre** et la reponse indique la ligne en cause.

## 3. Trois facons d'envoyer
**a) Fichier CSV, par le script** (le plus simple depuis un notebook) :
```
python ml/push_forecasts.py --url http://127.0.0.1:8000 --model MD-XXXXXX --key VOTRE_CLE --csv resultats.csv --version 1.0
```
**b) Fichier CSV, depuis l'interface STEG** : « Importer un fichier CSV » (memes colonnes).

**c) Directement depuis Python** :
```python
import json, urllib.request
items = [  # exemple de FORMAT uniquement : remplacez par les sorties reelles de votre modele
    {"governorate": "Tunis", "granularity": "month", "period": "2030-07", "temp_mean_c": 0.0},
]
req = urllib.request.Request("http://127.0.0.1:8000/api/forecast/ingest", method="POST",
    data=json.dumps({"version": "1.0", "items": items}).encode(),
    headers={"Content-Type": "application/json", "X-Model-Id": "MD-XXXXXX", "X-Model-Key": "VOTRE_CLE"})
print(urllib.request.urlopen(req).read())
```
Depuis un DataFrame pandas : `df.to_csv("resultats.csv", index=False)`, puis le script (a).

## 4. Limites et erreurs
- 5 000 lignes par envoi, 1 envoi toutes les 2 secondes par modele (le script decoupe tout seul).
- `401` : identifiant ou cle faux · `422` : donnees invalides (le message donne la ligne) · `429` : envoi trop rapide.
- Utilisez `127.0.0.1` plutot que `localhost` dans l'adresse (plus rapide sous Windows).

## 5. Ce qu'il faut dire au jury
Les previsions affichees viennent des modeles entraines par l'equipe ; elles sont **estimees, pas mesurees**, et leur qualite
depend des donnees d'entrainement. Indiquez la version du modele (`--version`) et, si possible, la `confidence`.
