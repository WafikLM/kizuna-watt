# API de l'eclairage public

Deux types de clients externes : les **controleurs de luminaires** (materiel) et les **modeles d'IA** (prediction de frequentation).
Les deux s'authentifient par une **cle** creee dans l'espace STEG. Utilisez `127.0.0.1` plutot que `localhost` (plus rapide sous Windows).

## 1) Controleur de luminaire
Creation : espace STEG > Eclairage public > **Ajouter un luminaire** (nom, gouvernorat, position sur la carte, puissance, type). Vous obtenez un **identifiant** `LP-XXXXXX` et une **cle** (affichee une seule fois).

```
POST /api/lighting/report
En-tetes : Content-Type: application/json,  X-Lamp-Id: LP-XXXXXX,  X-Lamp-Key: <cle>
Corps    : {"level_pct": 100, "power_w": 98.5, "lux": 3.2, "motion": 1}
```
| Champ | Obligatoire | Sens |
|---|---|---|
| `level_pct` | oui | niveau applique actuellement, 0 a 100 |
| `power_w` | oui | puissance **mesuree** (W) |
| `lux` | non | luminosite ambiante mesuree |
| `motion` | non | nombre de mouvements detectes depuis le dernier rapport |

**Reponse** : `{"ok": true, "target_level": 50, "reason": "plage horaire (50 %)", "night": true, "server_time": 1795282000}`
→ le controleur **applique `target_level`** (variateur PWM, 0-10 V, DALI...) et renvoie son etat au rapport suivant.

Frequence conseillee : un rapport toutes les 10 a 60 secondes (maximum 1 par seconde). Codes : `401` cle fausse, `422` valeurs invalides, `429` trop rapide.
Programme de reference : `tools/lamp_controller.py` (option `--simulate` pour tester sans materiel).

### Comment la consigne est calculee (dans l'ordre)
1. **Jour** (soleil leve, calcule pour la position du luminaire) → **0 %**.
2. **Luminosite mesuree** ≥ seuil (40 lux par defaut) → **0 %** : on n'allume pas tant qu'il fait encore clair, meme apres l'heure theorique du coucher.
3. **Plage horaire** : niveau par tranche d'heures (reglable dans l'application).
4. **Prevision de frequentation** d'un modele d'IA, si disponible : le niveau est module entre le plancher et le niveau de la plage.
5. **Mouvement detecte** : niveau force (100 % par defaut) pendant quelques minutes.

### Ce qui est compte comme gaspillage (uniquement a partir des mesures reelles)
- luminaire **allume en plein jour** ;
- energie consommee **au-dela de la consigne** precedente ;
- luminaire **qui ne suit pas la consigne** (niveau > consigne + 25 points) ;
- alertes : puissance trop faible pour le niveau (lampe defectueuse), consommation anormale, controleur muet depuis plus de 10 minutes.

## 2) Modele d'IA : frequentation prevue
Creez le modele dans **Carte des clients > Modeles IA connectes > Ajouter un modele** (identifiant `MD-XXXXXX` + cle).

```
POST /api/lighting/predict
En-tetes : Content-Type: application/json,  X-Model-Id: MD-XXXXXX,  X-Model-Key: <cle>
Corps    : {"items": [ {"target": "zone:Centre-ville", "hour": "2026-11-22T21", "presence": 0.35}, ... ]}
```
| Champ | Valeurs |
|---|---|
| `target` | un identifiant de luminaire `LP-XXXXXX`, ou `zone:NOM` (le champ « zone » du luminaire), ou `gov:Tunis` (gouvernorat) |
| `hour` | heure **locale** (UTC+1), format `AAAA-MM-JJTHH` |
| `presence` | indice de 0 (personne) a 1 (pic de frequentation de la zone) |

La cible la plus precise l'emporte : luminaire, puis zone, puis gouvernorat. Si une seule ligne est invalide, rien n'est enregistre
(le message indique la ligne). 20 000 lignes maximum par envoi, 1 envoi toutes les 2 secondes.

Exemple depuis Python (le contenu est un exemple de **format** : remplacez-le par les sorties reelles de votre modele) :
```python
import json, urllib.request
items = [{"target": "zone:Centre-ville", "hour": "2026-11-22T21", "presence": 0.0}]
req = urllib.request.Request("http://127.0.0.1:8000/api/lighting/predict", method="POST",
    data=json.dumps({"items": items}).encode(),
    headers={"Content-Type": "application/json", "X-Model-Id": "MD-XXXXXX", "X-Model-Key": "VOTRE_CLE"})
print(urllib.request.urlopen(req).read())
```
Dans l'application, le plan des 24 prochaines heures d'un luminaire affiche en bleu les heures modulees par une prevision.

## 3) Soleil : aucune heure a saisir
Le **coucher et le lever du soleil sont calcules automatiquement** par le serveur (calcul astronomique, precision d'environ 1 minute,
verifie contre un service meteo externe pour 3 villes) pour la position exacte de chaque luminaire et la date du jour.
Les controleurs n'ont **pas besoin d'horloge** ni d'heures de reglage : chaque reponse de `/api/lighting/report` contient `night`, `sunrise`, `sunset` et la consigne.

Consultation (compte connecte) :
```
GET /api/lighting/sun?gov=Tunis              -> un gouvernorat
GET /api/lighting/sun?lat=36.8&lng=10.18     -> une position
GET /api/lighting/sun                        -> les 24 gouvernorats
Reponse : {"date": "2026-11-21", "sunrise": "07:03", "sunset": "17:07", "lights_on": "17:07", "lights_off": "07:03", "is_night": true, ...}
```
`lights_on` / `lights_off` tiennent compte des decalages configures dans les regles (ex. allumer 15 minutes avant le coucher).
Heure locale : UTC+1 toute l'annee (la Tunisie n'applique pas l'heure d'ete).

## 4) Rues principales : declarer des luminaires sans les placer un par un
L'application embarque les autoroutes, routes nationales, principales et secondaires de Tunisie (**OpenStreetMap**, © les contributeurs, licence ODbL).
Espace STEG > Eclairage public > **Rues principales** : choisir une rue, regler l'espacement (15 a 150 m), la puissance et le type de lampe.
Les luminaires sont declares **regulierement espaces le long du trace** et un **fichier CSV** (identifiant + cle de chaque controleur) est telecharge.
- Les positions sont **estimees** (le trace OpenStreetMap, pas un releve des poteaux) : a valider sur le terrain.
- Le gouvernorat de chaque luminaire est celui du chef-lieu le plus proche (approximation, pas les limites administratives exactes).
- Les luminaires restent « Jamais vu » tant qu'un controleur n'a pas envoye de rapport.
- Mise a jour des rues : `python tools/fetch_streets.py` puis `python manage.py import-streets`.

## 5) Flotte de controleurs simules : mettre toute une rue « en ligne »
Quand on declare les luminaires d'une rue, un fichier CSV (identifiant, cle, nom, position, gouvernorat, puissance) est telecharge. Sans controleur,
ces luminaires restent « Jamais vus ». `tools/lamp_fleet.py` fait tourner **le controleur de chaque luminaire du fichier, dans un seul programme** :
```
python tools/lamp_fleet.py --simulate --url http://127.0.0.1:8020 --csv "C:/Users/VOUS/Downloads/luminaires_*_cles.csv"
```
- A chaque cycle (15 s par defaut), chaque luminaire envoie ses mesures et **applique la consigne recue** : l'application decide seule de l'allumage (soleil), du niveau (plages horaires, luminosite, presence, previsions d'IA).
- ⚠️ **Les mesures sont simulees** (luminosite, presence, puissance) : outil de demonstration et de test, a remplacer par de vrais controleurs. L'option `--simulate` est obligatoire.
- `--faults 5` : 5 luminaires « tombent en panne » pour montrer les alertes ; `--interval`, `--count`, `--rated-w` (si le CSV n'a pas la colonne `rated_w`).
- Mesure : environ 800 luminaires en 6 secondes par cycle sur un PC ordinaire.
- Pour voir la **variation** (et pas seulement 100 %), changez les plages horaires dans « Regles d'automatisation » : par defaut la consigne est de 100 % jusqu'a 22h30 puis 50 %.
