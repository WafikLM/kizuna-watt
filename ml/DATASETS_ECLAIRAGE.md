# Eclairage public — datasets et modeles a entrainer

**Objectif :** eviter le gaspillage d'energie en adaptant la puissance de chaque luminaire (allumage/extinction, variation,
prise en compte de la frequentation). L'application applique des **regles** (soleil, horaires, luminosite, presence) et peut les
**moduler avec les previsions de vos modeles d'IA**.

> **Honnetete sur les donnees :** je n'ai trouve **aucun dataset tunisien** d'eclairage public. Les jeux ci-dessous sont
> etrangers ou synthetiques : ils servent a monter les modeles, pas a prouver leur precision en Tunisie.

## Les 3 modeles proposes
| # | Modele | Entrees | Sortie | Utilisation dans l'application |
|---|---|---|---|---|
| 1 | **Prevision de frequentation** (piétons / vehicules) | heure, jour de semaine, week-end, mois, temperature, nuages, pluie, type de zone | indice 0 a 1 par zone et par heure | envoye a `/api/lighting/predict` : il module le niveau de chaque luminaire |
| 2 | **Detection de pannes de luminaire** | niveau demande, puissance mesuree, tension, courant, luminosite | `ok` / `dead` / `flicker` / `overpower` / `stuck_on_day` | alerte de maintenance (a brancher ensuite) |
| 3 | **Luminosite ambiante / seuil jour-nuit** | hauteur du soleil, nuages | lux prevus, seuil d'extinction | ajuste le seuil de luminosite de la politique |

## 1) Jeu d'entrainement immediat : le generateur synthetique
```
python ml/gen_lighting_data.py            # cree ml/data_synth_eclairage/
python ml/gen_lighting_data.py --days 180 --lamps 30 --seed 1
```
| Fichier | Contenu | Pour le modele |
|---|---|---|
| `presence_hourly.csv` | 6 zones (centre, residentiel, industriel, route), 1 ligne par zone et par heure : `zone, zone_type, ts, hour, dow, is_weekend, month, temp_c, cloud_pct, rain_mm, is_night, presence_idx, pedestrians` | 1 |
| `lux_ambient.csv` | toutes les 10 min : `ts, sun_elevation_deg, cloud_pct, lux, is_night` | 3 |
| `lamp_power_faults.csv` | `lamp_id, ts, level_pct, power_w, rated_w, voltage_v, current_a, lux, label` avec pannes etiquetees | 2 |

⚠️ **Donnees simulees** : bruit et meteo generes, pannes injectees. Un modele qui y atteint 99 % ne prouve rien ; il faut ensuite
le tester sur des mesures reelles.

## 2) Datasets reels trouves (a verifier avant usage)
| Dataset | Pour | Ce que j'ai verifie |
|---|---|---|
| [Tampere — pilote d'eclairage intelligent (Viinikka)](https://ckan.energydata.rd.tuni.fi/dataset/1d3a382c-32d0-4a81-a574-aeac0237d82a) | 1, 2 | Environ 400 luminaires pilotes en Finlande, 2019-2022. **Champs et licence non verifies** : ouvrir la page. Certaines entites de test sont vides. |
| [Melbourne — comptage de pietons, par heure](https://data.gov.au/data/dataset/e186c808-8179-4a40-9cfc-09114b3bf90a) | 1 | Comptages horaires par capteur, depuis 2009. **Licence peu claire** : verifier sur le portail de la ville de Melbourne. Pas de direction dans la version horaire. |
| [NYC DOT — comptages de trafic automatises](https://data.cityofnewyork.us/Transportation/Automated-Traffic-Volume-Counts/7ym2-wayt) | 1 | Comptages routiers par heure. Peu de jours comptes par site, surtout en semaine : peu fiable pour la nuit et le week-end. |
| [Etude Bologne : eclairage adaptatif en conditions reelles](https://cris.unibo.it/bitstream/11585/999914/1/sensors-24-05942.pdf) | reference | Un **article** (mesures sur de longs deploiements), pas un dataset : utile pour comparer vos economies. |
| [Kaggle — « Intelligent Streetlight Control System »](https://baselight.app/u/kaggle/dataset/ziya07_intelligent_streetlight_control_system) | prototypage | 1 000 lignes, **synthetiques** (kWh selon trafic, lumiere, meteo, variation). Trop petit pour une evaluation serieuse. |
| [Images de luminaires allumes/eteints, Bristol (Zenodo)](https://doi.org/10.5281/zenodo.6046758) | 2 (vision) | Environ 350 000 **images** : detection visuelle allume/eteint, pas des mesures electriques. |
| [Pannes d'eclairage signalees, data.gov.uk](https://www.data.gov.uk/dataset/lighting-faults) | maintenance | Journaux de signalement du public : pas des capteurs. |
| Kaggle — signature de courant des luminaires | 2 | Cite par un article, **nom et lien non retrouves** : chercher « streetlight fault current » sur Kaggle. |

## 3) Donnees tunisiennes utilisables
- **Meteo** (nuages, rayonnement, pluie, temperature, lever/coucher du soleil) : [Open-Meteo, archive historique](https://open-meteo.com/en/docs/historical-weather-api), gratuit et sans cle (usage non commercial : a verifier).
- **Position du soleil** : l'application la calcule elle-meme par luminaire (algorithme astronomique), rien a telecharger.
- **Mesures reelles de luminaires** : a demander aux **communes** et a la **STEG** (releves de compteurs d'eclairage public, horaires de commande). Aucune source publique trouvee.

## 4) Comment brancher le modele 1 a l'application
Voir [API_ECLAIRAGE.md](API_ECLAIRAGE.md). Le modele envoie, pour chaque heure a venir, un indice de frequentation par
luminaire, par zone (`zone:NOM`) ou par gouvernorat (`gov:Tunis`). L'application calcule alors :
`niveau = niveau_plancher + (niveau_de_la_plage_horaire − niveau_plancher) × frequentation_prevue`.

## 5) Conseils d'evaluation
- Decoupage **temporel** (entrainer avant, tester apres) ; comparer a une **baseline** simple (« meme heure la semaine derniere »).
- Mesurer l'economie en kWh **et** le risque : une zone sous-eclairee alors qu'elle est frequentee est plus grave qu'un luminaire trop allume.
- Ne presentez pas de score obtenu sur les donnees synthetiques comme une performance reelle.
