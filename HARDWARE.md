# Carte capteur — materiel, branchement, liaison au compte

Objectif : une petite carte installee pres du tableau electrique mesure la consommation de la maison et l'envoie
au compte du membre. **Ce document decrit une conception ; je ne l'ai pas testee sur du materiel reel.**

## ⚠️ Securite avant tout
Le reseau 230 V peut tuer. **Ne branchez jamais vous-meme un fil dans le tableau.**
- La pince amperemetrique (type SCT-013) s'**accroche autour** d'un fil sans le couper et sans contact avec le cuivre.
  C'est la seule partie qui touche l'installation. Faites-la poser par un electricien si vous n'etes pas qualifie.
- Alimentez la carte par un **chargeur USB certifie**, jamais directement sur le secteur.
- Pour la demonstration du hackathon : utilisez le **circuit simule dans Proteus** (voir `PROTEUS.md`), sans toucher au 230 V.

## Liste du materiel (une carte)
| Piece | Role | Remarque |
|---|---|---|
| ESP32 (carte de developpement) | Cerveau, Wi-Fi | Alimentee en 5 V par USB |
| Pince SCT-013-030 (30 A, sortie tension 1 V) | Mesure le courant sans contact | Choisir la version « sortie tension » : plus simple. La version 000 demande une resistance de charge de 33 Ω |
| Prise jack 3,5 mm femelle | Raccorder la pince | |
| 2 resistances 10 kΩ + 1 condensateur 10 µF | Decalage de tension (point milieu 1,65 V) pour l'entree analogique de l'ESP32 | L'ESP32 ne lit que 0 a 3,3 V, la pince donne un signal alternatif |
| Module ZMPT101B (option) | Mesure la tension : permet la puissance reelle et le cos φ | Sans lui, on suppose 230 V et un cos φ de 0,92 : la puissance est alors une **estimation apparente** |
| Boitier plastique isole | Protection | |

**Courant de fuite :** abandonne. Une pince SCT-013 ne detecte pas 30 mA et un capteur differentiel dedie sort du cadre du projet.
Le serveur **ignore** le champ `leak_ma` s'il est encore envoye par un ancien firmware. L'alerte du projet est maintenant la
**consommation anormale** (voir `kizuna/anomaly.py`).

## Schema de branchement (courant)
```
 Fil de phase ─── [pince SCT-013 autour du fil] ── jack 3,5 mm ── pointe  ──┬── GPIO34 (ESP32)
                                                                           │
                                         3,3 V ──[10 kΩ]──┬──[10 kΩ]── GND  (point milieu 1,65 V)
                                                          └── manchon du jack
                                                          └── condensateur 10 µF vers GND
```
Le point milieu (1,65 V) est relie au manchon du jack ; la pointe va sur GPIO34.

## Comment la carte est liee a un membre
1. Le membre se connecte, ouvre **Capteur > Ajouter un capteur**.
2. L'application cree un capteur lie **a son compte** et affiche un **identifiant** (ex. `KW-4B2DE4`) et une **cle secrete**, une seule fois.
   Seul le hash de la cle est stocke cote serveur.
3. Le membre copie ces deux valeurs dans le firmware de la carte, avec le nom du Wi-Fi, son mot de passe et l'adresse du serveur.
4. Toutes les 5 secondes, la carte envoie une mesure :
```
POST http://<adresse du serveur>:8000/api/ingest
En-tetes : Content-Type: application/json
           X-Device-Id: KW-4B2DE4
           X-Device-Key: <cle secrete>
Corps    : {"power_w": 812.5, "voltage": 229.8, "current_a": 3.84, "cosphi": 0.92, "leak_ma": 2.1}
```
5. Le serveur verifie la cle, controle les valeurs (limites realistes, 1 mesure par seconde maximum), enregistre et rattache la mesure au compte.
6. L'application affiche la consommation des qu'une mesure arrive. Si plus rien n'arrive pendant 2 minutes, le capteur est marque « hors ligne » (aucune valeur n'est inventee).

Chaque membre a donc ses propres capteurs (5 maximum), et ne voit que les siens. Un membre qui supprime un capteur invalide sa cle.

## Firmware d'exemple (ESP32, Arduino IDE) — non teste sur materiel
Bibliotheques : **EmonLib** (calcul du courant efficace). Remplacez les valeurs entre `<...>`.
```cpp
#include <WiFi.h>
#include <HTTPClient.h>
#include "EmonLib.h"

const char* WIFI_SSID = "<nom du wifi>";
const char* WIFI_PASS = "<mot de passe>";
const char* URL       = "http://<IP du serveur>:8000/api/ingest";   // ex. http://192.168.1.20:8000/api/ingest
const char* DEVICE_ID = "<KW-XXXXXX>";
const char* DEVICE_KEY = "<cle secrete>";

EnergyMonitor emon;

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  emon.current(34, 30.0);          // GPIO34, calibration SCT-013-030 (a ajuster avec un appareil de puissance connue)
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(500);
}

void loop() {
  double irms = emon.calcIrms(1480);        // courant efficace en ampere
  if (irms < 0.05) irms = 0;                // bruit de mesure
  double volt = 230.0, cosphi = 0.92;       // hypotheses sans capteur de tension
  double power = irms * volt * cosphi;      // watts (estimation)

  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.begin(URL);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-Device-Id", DEVICE_ID);
    http.addHeader("X-Device-Key", DEVICE_KEY);
    String body = String("{\"power_w\":") + power + ",\"voltage\":" + volt + ",\"current_a\":" + irms +
                  ",\"cosphi\":" + cosphi + ",\"leak_ma\":0}";
    int code = http.POST(body);
    Serial.printf("envoi: %d  %.1f W\n", code, power);
    http.end();
  }
  delay(5000);
}
```
**Calibration :** branchez un appareil dont vous connaissez la puissance (par exemple une bouilloire de 2 000 W) et ajustez
le coefficient `30.0` jusqu'a ce que la valeur affichee corresponde.

## Pour passer de la demonstration a un vrai deploiement
- **HTTPS** obligatoire (certificat) ; en local le protocole est en clair.
- Mise a jour du firmware a distance, gestion des coupures Wi-Fi (file d'attente locale).
- Certification electrique du boitier, et accord de la STEG pour tout raccordement au comptage.
- A terme : lire directement le compteur intelligent STEG (si une interface officielle existe), sans carte supplementaire.
  Cela necessite un partenariat : rien n'est acquis.
