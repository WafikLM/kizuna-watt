# Carte capteur virtuelle (sans port serie, sans pilote)

Elle execute **le meme fichier `.hex`** que celui compile pour l'Arduino Uno, dans un emulateur de microcontroleur
ATmega328P ([avr8js](https://github.com/wokwi/avr8js), libre). Les deux potentiometres sont remplaces par deux **curseurs**
a l'ecran. Chaque ligne JSON envoyee par le programme sur son port serie est transmise a l'application.

## Utilisation
1. Installer les dependances (une seule fois, necessite Node.js) :
   ```
   cd emulator
   npm install
   ```
2. Dans l'application : **Capteur > Ajouter un capteur**, puis copier l'identifiant et la cle.
3. Lancer la carte (le serveur `python server.py` doit tourner) :
   ```
   node board.mjs --hex ../proteus/kizuna_sensor/kizuna_sensor.ino.hex --url http://127.0.0.1:8000 --id KW-XXXXXX --key VOTRE_CLE
   ```
4. Ouvrir **http://127.0.0.1:8040** : curseurs, LED de la broche 13 et sortie serie.
5. Dans l'application, la consommation du client s'affiche en direct ; sur la carte du compte STEG, le client passe au vert.

## Correspondance avec le circuit
| Circuit ISIS | Carte virtuelle |
|---|---|
| RV1 sur A0 (courant, 0 a 30 A) | curseur « RV1 », 0 a 5 V |
| RV2 sur A1 (fuite, 0 a 100 mA) | curseur « RV2 », 0 a 5 V |
| LED sur la broche 13 | voyant rouge |
| TX / COMPIM / pont serie | sortie serie envoyee directement a l'application |

## Ce qui est reel et ce qui est simule
- **Reel :** le programme de la carte (le `.hex`), tel que compile par l'IDE Arduino, avec son calcul et son format de message.
- **Simule :** le microcontroleur (emulateur), la lecture analogique (curseurs) et la pince.
- Pour utiliser une **autre version** du programme : recompiler dans l'IDE Arduino (*Croquis > Exporter les binaires compiles*) et passer le nouveau `.hex` a `--hex`.
