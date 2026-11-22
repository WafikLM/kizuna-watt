# Plan de pointe : priorites des equipements

Le client declare ses equipements (nom, puissance, quantite). Chacun recoit un **circuit** du tableau
electrique (Eclairage, Prises, Cuisine, Chambres, Salon, Climatisation, Eau chaude, Autre), une
**priorite en pointe** et, pour les charges deplacables, un **report maximal** en heures.
Des valeurs par defaut sont deduites du nom (modifiables) : `kizuna/shedding.py`.

| Palier | Priorite | Exemples | Role |
|---|---|---|---|
| 0 | protege | refrigerateur, equipements vitaux | jamais coupe |
| 1 | inutile | eclairage | a eteindre en premier |
| 2 | tolerable | prises : chargeurs, PC, TV | a couper ou differer un peu |
| 3 | decalable | machine a laver, four, chauffe-eau | a reporter apres la pointe |
| 4 | confort | climatiseurs | module (50 %) |

## Ce que fait l'application
- **Client** (onglet « Plan de pointe ») : echelle de delestage par palier, puissance liberable, et choix
  du niveau de participation (`shed_level` 0 a 4). 0 = ne participe pas.
- **STEG** (carte « Potentiel d'effacement ») : somme par gouvernorat, **uniquement pour les foyers qui
  participent**.

## Pointes annoncees, reports planifies et notifications (`kizuna/peaks.py`)
1. La STEG annonce une pointe (carte « Annoncer une pointe ») : debut, fin (12 h max), palier max, region ou toutes.
2. Chaque foyer **participant** recoit, dans l'onglet « Plan de pointe » (badge + message + notification du navigateur) :
   - paliers 1-2 : equipements a eteindre pendant la fenetre ;
   - palier 3 : equipements a reporter, avec **une heure de relance** apres la fin ;
   - palier 4 : equipements a moduler.
   Le palier effectif est `min(palier de la pointe, palier accepte par le foyer)`.
3. **Anti-rebond** : l'heure de relance est decalee de 0 a 50 min selon le foyer (6 creneaux de 10 min), pour que tous
   ne relancent pas a l'heure pile. Elle est bornee par le delai maximal declare ; si la pointe est plus longue que ce delai,
   l'appareil est signale « depasse votre delai ».
4. Le foyer repond « c'est fait » ou « je ne peux pas ». La STEG voit des totaux (suivies / refus / vues) et le potentiel de la pointe.

Limites : notifications seulement **tant que l'application est ouverte** (pas de SMS ni de push en arriere-plan : il faudrait
une passerelle SMS ou un service worker) ; les reponses sont **declaratives**, pas verifiees par un releve de compteur ;
totaux masques sous 3 foyers participants. API : `GET/POST /api/steg/peaks`, `POST /api/steg/peaks/cancel`, `POST /api/peaks/ack`, champ `peaks` de `/api/my`.

## Recompenses : codes de reduction sur la facture (`kizuna/rewards.py`)
- La STEG fixe, pour chaque pointe, une recompense par foyer (0 a 20 DT). C'est un choix de politique.
- Un foyer recoit un code **seulement si** (1) il a dit « c'est fait » **avant la fin** de la pointe, (2) il a un capteur, et (3) sa puissance moyenne
  mesuree pendant la pointe est **au moins 15 % sous son habitude** aux memes heures ET sort de sa variation naturelle (au moins 1 ecart-type sous sa moyenne,
  jours de meme type semaine / week-end, 5 jours d'historique minimum). Sinon il voit la raison. Test en simulation : un foyer qui dit « fait » sans rien couper n'est pas recompense.
- Code a 16 chiffres (comme une carte de recharge) avec chiffre de controle Luhn (detecte les fautes de frappe). Il est calcule par **HMAC-SHA256
  avec une cle secrete** (`data/reward.key`, ou variable `KIZUNA_SECRET`) : l'algorithme est confidentiel grace a la cle, pas a l'obscurite.
  La base ne stocke que l'empreinte HMAC : une fuite de la base seule ne revele aucun code. **Sauvegardez `reward.key`** (sans elle, plus aucun code n'est verifiable) et ne la commitez pas.
- Encaissement : carte « Recompenses » de la page STEG (simule le guichet / le paiement en ligne). « Verifier » simule, « Appliquer » consomme.
  Usage unique et atomique, validite 90 jours, 10 codes faux en 10 min bloquent la saisie. La STEG ne voit que des totaux, jamais les codes.
- Limites : un capteur peut etre trafique (compteur certifie requis en production) ; seuils (15 %, 70 % de couverture) = hypotheses ;
  l'integration reelle au systeme de facturation de la STEG reste a faire.

## Limites assumees
- Les puissances sont **declarees** (maximales), pas mesurees ; tout ne fonctionne pas en meme temps.
- La part de confort modulable `CONFORT_SHARE = 0.5` est une **hypothese** a valider.
- L'application **recommande** : elle ne coupe rien. Une coupure reelle demande des relais commandes.
- Vie privee : participation volontaire ; une region de moins de 3 foyers participants n'affiche aucune
  valeur (`MIN_GROUP = 3`), pour qu'on ne puisse pas deviner un foyer.

## API
`GET /api/my` (champs `plan`, `overload_order`), `POST /api/shedding {level}`,
`POST /api/equipment` (champs `circuit`, `priority`, `defer_h`), `GET /api/steg/shedding` (STEG seulement).
