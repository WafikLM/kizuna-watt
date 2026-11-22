# Integration des modeles de l'equipe dans l'application

## Fichiers recus et leur role
| Fichier | Contenu verifie | Utilise pour |
|---|---|---|
| `isolation_forest_model.pkl` | **Isolation Forest** (scikit-learn 1.6.1), 200 arbres, `contamination = 0.01` | detecter les **heures de consommation anormales** |
| `model_features.pkl` | liste de 6 variables : `power_mean, power_max, power_std, hour, day_of_week, is_weekend` | variables de l'Isolation Forest |
| `kizuna_watt_model.pkl` | **XGBoost** (regression), **1000 arbres**, 28 variables, `base_score ≈ 1,14` | **prevoir la consommation horaire** |
| `model3_reel.pkl` | **Pipeline** scikit-learn : `StandardScaler` + `Ridge` (alpha 1), variables `mom3` et `dcdd`, seuil de climatisation **31 °C**, 177 echantillons | **prevoir l'energie electrique nationale mensuelle** |

Ces fichiers sont conserves tels quels dans `models/original/`. L'application utilise `models/` (Isolation Forest, variables) et
`models/kizuna_watt_xgb.json` (le XGBoost converti, voir plus bas).

## Comment ils sont branches (mesures reelles uniquement)
- Les capteurs envoient des mesures ; l'application garde un **historique par heure** (moyenne, maximum, ecart-type de la puissance).
- **Isolation Forest** : pour chaque heure ayant au moins 10 mesures, on calcule les 6 variables (**puissances en kW**, heure locale UTC+1,
  jour de semaine lundi = 0, week-end) et on affiche « normale / anormale » avec un indice 0-100 (50 = limite du modele). Visible chez le client et
  dans la fiche STEG (badge « Anomalie IA »).
- **XGBoost** : prevision des 24 prochaines heures (en W) + **erreur reellement observee** sur les 24 dernieres heures. Affichee comme **experimentale**.
- Les modeles sont charges **une fois**, verifies par **empreinte SHA-256** (`models/MANIFEST.json`) : un fichier remplace ou modifie est refuse.
  Apres avoir verifie un nouveau modele : `python manage.py trust-models`.
- Installation des bibliotheques : `pip install -r requirements.txt` (numpy, scikit-learn, xgboost, joblib). Sans elles, l'application fonctionne et indique que l'IA est indisponible.

## Ce que j'ai observe en inspectant les modeles
1. **Echelle : kW.** L'Isolation Forest coupe ses variables de puissance autour de 1 kW (median) jusqu'a 26 kW : avec des watts, **tout** serait « anormal ».
   Le XGBoost a un `base_score` de 1,14 : la cible est tres probablement la **puissance moyenne horaire en kW** (profil du dataset UCI).
2. **Le XGBoost est une prevision a J+1** : toutes ses variables sont retardees d'au moins 24 h (consommation 24/48/72/168/336/504 h plus tot, meteo
   24 h et 168 h plus tot). Il faut donc **au moins 3 semaines d'historique par heure**, idealement 4, avant d'obtenir une prevision complete.
3. **Le pickle XGBoost ne se rechargeait pas** avec XGBoost 3.4.1 (« input stream corrupted »), alors que le fichier est complet. Un pickle de modele
   XGBoost est fragile. J'ai donc **extrait le modele sans executer le pickle** (`tools/convert_xgb_pickle.py`) et l'ai enregistre en JSON standard.
4. **Versions** : l'Isolation Forest a ete entrainee avec scikit-learn **1.6.1** ; cette version n'existe pas pour Python 3.14, l'application utilise donc une
   version plus recente (avertissement de compatibilite). Il fonctionne sur des exemples, mais **l'equipe doit confirmer** que les scores sont inchanges.

## Questions a poser a l'equipe (pour que la prevision soit juste)
Je n'ai **pas** le code qui a fabrique les 28 variables. J'ai fait ces hypotheses, a **confirmer ou corriger** :
| Variable | Hypothese utilisee |
|---|---|
| cible | puissance moyenne horaire, en **kW** |
| `lag_k` (k = 24, 48, 72, 168, 336, 504) | valeur de l'heure `T − k` heures |
| `mean_3d`, `mean_7d`, `std_7d` | moyenne / ecart-type des 72 h / 168 h **se terminant a `T − 24 h`** |
| `same_hour_mean_4w`, `same_hour_std_4w` | moyenne / ecart-type de la meme heure aux 4 semaines precedentes (`T − 168 h × 1..4`) |
| meteo `*_lag24`, `*_lag168` | meteo Open-Meteo du **chef-lieu du gouvernorat** : `temp_c` (°C), `humidity` (%), `feels_like` (°C), `precipitation_mm`, `wind_speed` (**km/h**) |
| `hour_*`, `dow_*`, `month_*` | sin/cos avec periodes 24, 7 et 12 (mois de 1 a 12) |
| `is_weekend` | samedi et dimanche |

**Demandez-leur :** le code d'ingenierie des variables (ou le notebook), l'unite du vent, le decalage exact des moyennes glissantes, la definition de `month_*`,
le dataset d'entrainement (UCI ? quelle meteo ?) et leur MAE/RMSE de reference. Pour l'Isolation Forest : sur quelle fenetre (heure ? jour ?) les
`power_mean/max/std` ont ete calcules.

## Pour la suite
- **Exporter autrement** : `model.save_model("modele.json")` (XGBoost) plutot que pickle ; `joblib.dump` avec la version de scikit-learn notee.
- **Reentrainer sur des mesures tunisiennes** quand l'historique sera assez long (les capteurs de l'application en accumulent).
- La qualite **observee** de la prevision s'affiche dans l'application : jugez-la sur les mesures reelles, pas sur le dataset d'entrainement.


## Modele 3 : demande d'electricite nationale mensuelle (`model3_reel.pkl`)
**Formule (indiquee dans le fichier)** : `E_t prevu = E_(t-12) × exp( Ridge(mom3, dcdd) )`, la cible etant `log(E_t) − log(E_(t-12))`.
Il a ete entraine sur **177 mois** : exactement 192 mois (2009-2024) moins 15 mois de decalage, donc tres probablement sur les **donnees mensuelles de la STEG**
(dataset Mendeley « STEG 2009-2024 »). Ses coefficients : `mom3` +0,023 et `dcdd` +0,018 (variables centrees-reduites), constante +0,027 (≈ +2,7 % par an).

**Branchement dans l'application (espace STEG > « Demande d'electricite nationale »)**
1. Importer l'**historique mensuel** (CSV `period, energy, temp_max_c`, au moins 27 mois) : c'est le fichier de l'equipe, que l'application ne peut pas inventer.
2. Les **previsions climatiques** des modeles (granularite `month`, zone `Tunisie`, colonne `temp_max_c`) donnent la temperature du mois a prevoir, donc `dcdd`.
3. L'application calcule les **12 mois suivants**, un scenario par prevision climatique (par exemple un scenario SSP plus chaud), plus un scenario de reference « meme climat que l'an dernier ».
4. Une verification sur les mois connus est affichee (**optimiste** : ces mois ont probablement servi a l'entrainement).

**Hypotheses a confirmer avec l'equipe** (le code de leurs variables manque) :
| Variable | Hypothese utilisee |
|---|---|
| `mom3` | moyenne de `log(E_s / E_(s-12))` sur les 3 mois `s = t−14, t−13, t−12`. Indice : sa moyenne dans le fichier (0,0277) est egale a celle de la cible (0,0272) ; une simple variation sur 3 mois serait proche de 0 |
| `dcdd` | `CDD(t) − CDD(t−12)`, avec `CDD(T) = max(0, T − 31)` |
| `T` | temperature **maximale moyenne du mois** (le dataset parle de temperature des jours chauds). Avec une temperature moyenne, `CDD` serait nul presque toute l'annee |
| unite de `E` | celle de votre fichier d'historique (GWh si c'est le dataset STEG) |

**Questions pour l'equipe** : definition exacte de `mom3` et de `T`, unite de l'energie, perimetre (net fourni ? consommation ?), et l'erreur obtenue en validation **hors entrainement** (par exemple sur 2023-2024).
