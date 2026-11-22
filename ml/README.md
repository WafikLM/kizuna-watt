# Entraînement des modèles — guide d'équipe (4 personnes)

Chacun entraîne **un modèle**, sur son ordinateur, avec le même code de base.

## Installation (chaque personne)
```
pip install -r ml/requirements.txt
python ml/train_forecast.py          # test immédiat avec données synthétiques
```
Sans argument, les scripts utilisent le simulateur : **ça tourne sans rien télécharger**. Avec `--uci chemin.txt`, ils utilisent le vrai dataset.

## Qui fait quoi
| Personne | Modèle | Script | Algorithme | Cible | Statut |
|---|---|---|---|---|---|
| 1 | Prévision de consommation du foyer | `train_forecast.py` | régression linéaire, puis gradient boosting | kW à l'heure t | Cœur |
| 2 | Détection d'anomalie | `train_anomaly.py` | Isolation Forest | point normal / anormal + score 0-100 | Cœur |
| 3 | Risque de pic régional | `train_region.py` | gradient boosting | pic du lendemain (MW) | Cœur |
| 4 | Décomposition par appareil (NILM simplifié) | `train_disagg.py` | gradient boosting | part de la clim | **Expérimental** |

Résultats de test avec les données synthétiques (à ne pas présenter comme des performances réelles) :
- Prévision : baseline « même heure hier » MAE 0,079 kW, gradient boosting MAE 0,052 kW.
- Anomalie : précision 0,53, rappel 0,85 sur anomalies injectées.
- Région : baseline 27,5 MW, gradient boosting 3,6 MW de MAE.
- Décomposition : ⚠️ MAE de 0,005 : **trop facile**, car la clim est une formule déterministe dans le simulateur. Seul le test sur UCI (`sub3`) a un sens.

## Datasets recommandés
| Dataset | Pour quoi | Détails |
|---|---|---|
| **[UCI Individual Household Electric Power Consumption](https://archive.ics.uci.edu/dataset/235/individual+household+electric+power+consumption)** | Modèles 1, 2, 4 | 2 075 259 mesures à la minute, un foyer à Sceaux (France), déc. 2006 à nov. 2010. Variables : Date, Time, Global_active_power (kW), Global_reactive_power, Voltage, Global_intensity, Sub_metering_1 (cuisine), 2 (buanderie), 3 (chauffe-eau + clim), en Wh. Environ 1,25 % de valeurs manquantes (marquées « ? »). Licence annoncée CC BY 4.0 : à confirmer sur la page. À citer : Hebrail & Berard (2006), DOI 10.24432/C58K54. |
| **[Open-Meteo, archive historique](https://open-meteo.com/en/docs/historical-weather-api)** | Température pour tous les modèles | Gratuit, sans clé, données horaires depuis 1940. Endpoint `archive-api.open-meteo.com/v1/archive` avec `latitude`, `longitude`, `start_date`, `end_date`, `hourly=temperature_2m`. Usage non commercial : à vérifier dans les conditions. |
| **[UCI ElectricityLoadDiagrams](https://archive.ics.uci.edu/dataset/321/electricityloaddiagrams/)** | Modèle 3 (beaucoup de clients agrégés) | 370 clients, CC BY 4.0. |
| **[Mendeley : consommation mensuelle STEG 2009-2024](https://data.mendeley.com/datasets/cyf324d5vs)** | Contexte national | Mensuel, avec température et PIB. Pas assez fin pour le pic horaire. |
| REDD, UK-DALE, REFIT | Modèle 4 (mesures par appareil) | Connus de ma connaissance, **non vérifiés ici** : cherche leur page officielle avant de les utiliser. |

**Ce que je n'ai pas trouvé :** aucune donnée horaire STEG publique. Dis-le au jury : les données tunisiennes réelles supposent un partenariat ou des foyers volontaires.

**Téléchargement :** je n'ai rien téléchargé pour vous. Chacun télécharge le fichier UCI lui-même (environ 20 Mo zippés), le décompresse, puis passe son chemin avec `--uci`.

## Étapes pour chaque personne
1. Lancer le script en synthétique pour vérifier que tout marche.
2. Télécharger UCI, relancer avec `--uci`.
3. Récupérer la météo d'un lieu (Sceaux : latitude 48,78, longitude 2,29) et relancer avec `--weather`.
4. Comparer au **baseline** et noter MAE et RMSE.
5. Sauvegarder (`.joblib`) et écrire 3 lignes : ce qui marche, ce qui ne marche pas, pourquoi.

## Règles pour ne pas se tromper
- **Découpage temporel** (train avant, test après), jamais aléatoire pour une série temporelle.
- **Toujours un baseline.** Un modèle qui ne bat pas « même heure hier » n'a pas d'intérêt.
- **Anomalies injectées** : UCI n'a pas de vérité terrain. Le score est une mesure sur anomalies fabriquées, à dire comme telle.
- **UCI est un foyer français, pas tunisien** : le climat, la clim et les habitudes diffèrent. Le modèle prouve la méthode, pas la précision pour la Tunisie.
- Ne pas copier un score de test synthétique dans le pitch.

## Contrat d'intégration (pour brancher les modèles à l'application)
Chaque modèle expose un fichier `.joblib` et reçoit les mêmes colonnes que celles de `data.make_features`. L'intégration dans `kizuna/engine.py` (remplacer la règle par `model.predict`) se fait **après** la validation par chacun.
