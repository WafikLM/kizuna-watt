# Simuler la carte capteur dans Proteus et la relier a l'application

> **Important (Windows 11) :** la liaison ISIS → application passe par un port serie virtuel, dont le pilote doit etre signe.
> Sur certains PC, com0com et d'anciennes versions d'Eltima echouent (Code 52). Dans ce cas, utilisez la **carte virtuelle** :
> voir [emulator/LISEZMOI.md](emulator/LISEZMOI.md). ISIS reste utile pour le schema et la presentation du circuit.

**Principe :** Proteus simule le microcontroleur et son circuit. Il ne sait pas faire de Wi-Fi de facon officielle,
donc la carte envoie ses mesures par **port serie**, et un petit **pont Python** les poste a l'application.

```
 Proteus (ATmega328P + 2 potentiometres)  --serie-->  COMPIM (COM5)
                                                         ║ paire virtuelle com0com
 Pont Python (tools/serial_bridge.py, lit COM6)  <───────╝
        │  POST /api/ingest  (identifiant + cle du capteur)
        ▼
 Application Kizuna Watt  → « Capteur reel en ligne »
```
Ce que j'ai teste : le pont Python et l'application, avec des lignes JSON envoyees a la main.
**Ce que je n'ai pas pu tester :** le circuit dans Proteus lui-meme (je n'ai pas Proteus ici). Les etapes ci-dessous
suivent la methode classique decrite dans des tutoriels ([guide COMPIM + com0com](https://endsers.netlify.app/virtual-serial-port-simulation-proteus),
[Arduino + port virtuel](https://ee-diary.blogspot.com/2023/02/arduino-simulation-in-proteus-with.html)) ; les noms de composants peuvent varier selon votre version.

## Etape 1 — Creer la paire de ports virtuels (une seule fois)
1. Installez **com0com** (pilote gratuit de ports serie virtuels, droits administrateur requis). Installez-le vous-meme depuis son site officiel.
2. Dans son outil de configuration, creez une paire : par exemple **COM5 ↔ COM6**.
   Les numeros peuvent etre differents chez vous ; notez-les.

## Etape 2 — Compiler le programme de la carte
1. Ouvrez [kizuna_sensor.ino](proteus/kizuna_sensor/kizuna_sensor.ino) dans l'IDE Arduino.
2. Choisissez la carte **Arduino Uno**, puis **Croquis > Exporter les binaires compiles**.
3. Dans le dossier du croquis, prenez le fichier `.hex` **sans** « with_bootloader ».

## Etape 3 — Dessiner le circuit dans Proteus (ISIS)
**Schema complet : [branchement_isis.svg](proteus/branchement_isis.svg)** (pour la carte `ARDUINO UNO` de la bibliotheque The Engineering Projects).
Si vous utilisez cette carte, elle contient deja le microcontroleur : renseignez le `.hex` dans ses proprietes (*Program File*) et ignorez les branchements d'alimentation de l'ATmega328P ci-dessous.

Composants a chercher (touche **P**) :
| Composant | Quantite | Role |
|---|---|---|
| `ATMEGA328P` (ou une carte Arduino Uno si votre version l'a) | 1 | Microcontroleur |
| `POT-HG` (potentiometre) | 2 | RV1 = courant (pince), RV2 = fuite |
| `LED-RED` + resistance 330 Ω | 1 | Alerte |
| `COMPIM` | 1 | Sortie vers le port serie du PC |
| `VIRTUAL TERMINAL` | 1 | Voir les lignes envoyees (facultatif) |
| `RES` 10 kΩ | 1 | Rappel du RESET vers +5 V |

Branchements (ATmega328P) :
- Alimentation : broches VCC (7), AVCC (20) et AREF (21) vers **+5 V** ; GND (8, 22) vers la masse. RESET (1) vers +5 V par 10 kΩ.
- **RV1** : une extremite a +5 V, l'autre a la masse, curseur vers **PC0 / A0 (broche 23)**.
- **RV2** : idem, curseur vers **PC1 / A1 (broche 24)**.
- **LED** : PB5 / D13 (broche 19) → resistance 330 Ω → LED → masse.
- **TX** : PD1 / TXD (broche 3) → **RXD de COMPIM** (et aussi vers le Virtual Terminal RXD).

Proprietes a regler (double-clic sur le composant) :
- **ATMEGA328P** : *Program File* = le `.hex` de l'etape 2 ; *Clock Frequency* = 16 MHz.
- **COMPIM** : *Physical port* = **COM5** ; *Physical Baud Rate* = 9600 ; *Virtual Baud Rate* = 9600 ; 8 bits, pas de parite, 1 stop.
- **Virtual Terminal** : 9600 bauds.

## Etape 4 — Creer le capteur dans l'application
1. Lancez le serveur (`python server.py`), connectez-vous, puis **Capteur > Ajouter un capteur**.
2. Copiez l'**identifiant** et la **cle** (affichee une seule fois).

## Etape 5 — Lancer le pont, puis la simulation
```powershell
pip install pyserial
```
```powershell
python tools/serial_bridge.py --port COM6 --id KW-XXXXXX --key VOTRE_CLE
```
Puis dans Proteus, cliquez sur **Play**. Vous devez voir dans le pont des lignes `200 {"power_w":...}`, et l'application
passe en **« Capteur en ligne »** et affiche votre consommation.

## Etape 6 — Jouer avec la carte (pour la demo)
- Tournez **RV1** : la puissance affichee dans l'application change en direct (0 a 30 A, soit 0 a environ 6,3 kW).
- RV2 (fuite) n'est plus utilise par l'application : le serveur ignore ce champ. Pour tester l'alerte de **consommation anormale**, montez RV1 (courant) bien au-dessus de la puissance habituelle du foyer (apres quelques jours d'historique) ou au-dessus de la somme des equipements declares.
- Tournez **RV1** a plus de 80 % : alerte de surcharge.

## Alternatives si Proteus pose probleme
- **Wokwi** (simulateur en ligne, ESP32 avec Wi-Fi) : les [docs](https://docs.wokwi.com/guides/esp32-wifi) indiquent que l'acces a votre ordinateur passe par une passerelle privee (adresse `host.wokwi.internal`), qui semble reservee a un abonnement payant. A verifier avant de compter dessus.
- **Bibliotheques Wi-Fi tierces pour Proteus** (type TEP) : non officielles, elles simulent un reseau virtuel et ne joignent pas votre serveur reel. Je ne les recommande pas pour ce projet.
