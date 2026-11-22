# Kizuna Watt 絆⚡

**Kizuna 2026 · Hackathon Tunisie × Japon · Thème : l'énergie** — *kizuna* (絆) signifie « lien ».

🇫🇷 [Français](#-français) · 🇬🇧 [English](#-english) · 📚 [Documentation technique](#documentation-technique)

> **Équipe :** [noms des membres à compléter]

---

## 🇫🇷 Français

### L'idée en une phrase
Relier chaque foyer tunisien au réseau : un petit capteur mesure la vraie consommation, l'application aide le citoyen à maîtriser sa facture, et la STEG voit ses clients sur une carte pour réduire les pointes sans construire de nouvelle centrale.

### Le problème
- **Pointes d'été** : une partie du réseau ne sert que quelques heures par an, mais coûte le plus cher.
- **Facture bimestrielle à tranches progressives** : le citoyen la découvre après coup, sans savoir quel appareil en est la cause.
- **Éclairage public** sur horaire fixe, sans lien avec la présence réelle.
- Aucun moyen simple, pour la STEG, de **parler au citoyen** et de l'**inciter** à effacer sa consommation.

### Ce que fait Kizuna Watt
| Pour le citoyen | Pour la STEG |
|---|---|
| Consommation **mesurée** en direct (capteur), jamais inventée | Carte de tous les foyers connectés, états et alertes |
| **Facture prévue** de la période (tarif progressif) + montant en direct + historique des factures payées | **Pointes** annoncées, consignes par palier selon les équipements du foyer |
| Alerte de **consommation anormale** (modèle d'IA + habitudes + puissance déclarée) | **Redémarrages échelonnés** pour éviter l'effet rebond |
| **Code de récompense** (signé, à usage unique) si la réduction est réellement mesurée | Éclairage public piloté (soleil, présence prévue par l'IA) |
| **Mimamori 見守り** : veille bienveillante d'un proche âgé (rythme d'activité électrique, sans caméra), alerte par e-mail | Prévisions météo / demande par gouvernorat |
| Interface en **français, anglais, arabe (droite→gauche) et japonais** | |

### Captures d'écran *(données de simulation, étiquetées « simulation » dans l'application)*
| | |
|---|---|
| ![Tableau de bord client](docs/screenshots/01-tableau-de-bord-client.jpg) | ![Plan de pointe et récompense](docs/screenshots/02-plan-de-pointe-recompense.jpg) |
| *Client : consommation en direct, facture prévue, tranches* | *Client : pointe annoncée, consignes et code de récompense* |
| ![Veille mimamori](docs/screenshots/03-mimamori-veille.jpg) | ![Carte STEG](docs/screenshots/04-carte-steg.jpg) |
| *Mimamori : veille d'un proche* | *STEG : carte des foyers connectés* |

### Essayer en 3 commandes
Prérequis : [Python 3.11+](https://www.python.org/downloads/) et [Git](https://git-scm.com). Internet est utile pour la carte (OpenStreetMap).
```bash
git clone https://github.com/WafikLM/kizuna-watt.git
cd kizuna-watt
python tools/demo.py run
```
Puis ouvrir **http://127.0.0.1:8030/login** (laisser la fenêtre de commande ouverte : elle envoie les mesures simulées en direct).

| Rôle | Compte | Mot de passe |
|---|---|---|
| Client (famille) | `sim02@kizuna-sim.local` | `SimKizuna-2026` |
| Client avec veille mimamori | `sim14@kizuna-sim.local` | `SimKizuna-2026` |
| Gestionnaire STEG (carte) | `steg@kizuna-sim.local` | `SimKizuna-2026` |

La liste complète des comptes est écrite dans `data/demo/demo_comptes.md` après le lancement. Ces comptes sont **fictifs** : ne réutilisez ce mot de passe nulle part.

Facultatif : `pip install -r requirements.txt` active les modèles d'IA (détection d'anomalie, prévisions) ; le serveur lui-même n'utilise que la bibliothèque standard de Python.

### Honnêteté : ce qui est réel, simulé et à valider
- ✅ **Réel :** le logiciel de bout en bout (comptes, capteur via API, carte, pointes, récompenses, facture, mimamori, éclairage, 4 langues), la sécurité (mots de passe hachés scrypt, en-têtes de sécurité, limites de débit), l'envoi d'e-mails d'alerte (testé avec Brevo).
- 🧪 **Simulé :** les 15 foyers et leurs mesures de la démo (générés à partir des équipements déclarés, **étiquetés « simulation »**) ; ils servent à tester le logiciel, pas à mesurer la précision des modèles. Les SMS sont simulés (aucun fournisseur SMS branché).
- ⚠️ **Hypothèses à valider avec la STEG :** tarifs, tranches, seuils de pointe et d'alerte, pourcentage de réduction pour la récompense.
- 🚧 **Pas encore fait :** pilote sur de vrais foyers, double authentification, vérification de l'e-mail, passage à l'échelle (un seul processus + SQLite).

### Notre vision pour le Japon
Le même socle sert la société vieillissante : la veille *mimamori* repose sur le rythme électrique du foyer, sans caméra ni micro, avec le consentement de la personne.

---

## 🇬🇧 English

### The idea in one sentence
Connect every Tunisian household to the grid: a small sensor measures real consumption, the app helps citizens control their bill, and the utility (STEG) sees its customers on a map to flatten demand peaks without building a new power plant.

### The problem
- **Summer peaks**: part of the grid is used only a few hours a year, yet it is the most expensive part.
- **Bi-monthly bill with progressive tiers**: citizens discover it after the fact, without knowing which appliance caused it.
- **Street lighting** on a fixed schedule, unrelated to real presence.
- No simple way for the utility to **talk to citizens** and **reward** them for reducing consumption.

### What Kizuna Watt does
| For the citizen | For the utility (STEG) |
|---|---|
| **Measured** live consumption (sensor), never invented | Map of all connected homes, states and alerts |
| **Forecast bill** for the period (progressive tariff) + live amount + paid-bill history | **Peaks** announced, level-based instructions adapted to each home's appliances |
| **Abnormal consumption** alerts (AI model + habits + declared power) | **Staggered restarts** to avoid a rebound peak |
| **Reward code** (signed, single use) when the reduction is really measured | Street lighting control (sunset, AI-predicted presence) |
| **Mimamori 見守り**: gentle watch over an elderly relative (electrical activity rhythm, no camera), e-mail alert | Weather / demand forecasts per governorate |
| UI in **French, English, Arabic (right-to-left) and Japanese** | |

### Screenshots *(simulated data, labelled "simulation" in the app)*
See the table above (the screenshots are in French).

### Try it in 3 commands
Requirements: [Python 3.11+](https://www.python.org/downloads/) and [Git](https://git-scm.com). Internet access helps for the map (OpenStreetMap).
```bash
git clone https://github.com/WafikLM/kizuna-watt.git
cd kizuna-watt
python tools/demo.py run
```
Then open **http://127.0.0.1:8030/login** (keep the command window open: it streams the simulated measurements live). Use the accounts in the table above (password `SimKizuna-2026`, fictitious accounts). The language selector is at the top right of every page.

Optional: `pip install -r requirements.txt` enables the AI models (anomaly detection, forecasts); the server itself only uses Python's standard library.

### Honesty: what is real, simulated, and still to validate
- ✅ **Real:** the end-to-end software (accounts, sensor API, map, peaks, rewards, bill forecast, mimamori, lighting, 4 languages), security (scrypt-hashed passwords, security headers, rate limits), alert e-mails (tested with Brevo).
- 🧪 **Simulated:** the 15 demo homes and their measurements (generated from declared appliances, **labelled "simulation"**); they test the software, not the accuracy of the models. SMS are simulated (no SMS provider connected).
- ⚠️ **Assumptions to validate with STEG:** tariffs, tiers, peak and alert thresholds, reduction percentage for the reward.
- 🚧 **Not done yet:** pilot with real homes, two-factor authentication, e-mail verification, scaling beyond one process + SQLite.

### Our vision for Japan
The same foundation serves an ageing society: *mimamori* relies on the household's electrical rhythm, with no camera or microphone, and with the person's consent.

---

## Licence / License
[MIT](LICENSE).

---

## Documentation technique

Application web : les **clients** s'inscrivent avec leur adresse, declarent leurs equipements et relient leur capteur ;
le **compte STEG** voit tous les clients sur une carte. **Aucune donnee fictive** : sans capteur, aucune consommation n'est affichee.

## Lancer
```
python manage.py create-steg     # une seule fois : cree le compte STEG (e-mail + mot de passe saisis dans le terminal)
python server.py                 # puis ouvrir http://localhost:8000
```
Python 3 suffit, sans installation. La carte (OpenStreetMap / Leaflet) demande une connexion internet.

## Parcours
- **Client** : `/signup` (compte, domicile place sur la carte, reference de facture, puissance souscrite, equipements) → `/app`
  - **Consommation** : puissance en direct, courant, tension, puissance habituelle a cette heure, energie par jour, courbe 24 h (uniquement avec un capteur)
  - **Equipements** : ajouter / modifier la liste, avec la puissance de chaque appareil
  - **Capteur** : ajouter un capteur (identifiant + cle) pour relier sa carte
  - **Profil** : modifier adresse, position, facture
  - **Meteo et climat** : previsions envoyees par les modeles d'IA de l'equipe, pour son gouvernorat (vide tant qu'aucun modele n'a envoye de resultats)
- **STEG** : `/steg` — carte de tous les clients inscrits, filtres, fiche detaillee (adresse, telephone, reference de facture, equipements, mesure du capteur), alertes de consommation anormale ou de courant eleve.
  - **Meteo et climat (modeles IA)** : previsions par gouvernorat et par echelle (jour, mois, annee), creation des modeles et de leurs cles, import CSV.
  - Format et envoi : `ml/API_PREVISIONS.md` (script `ml/push_forecasts.py`).

## Eclairage public automatise
Espace STEG > **Eclairage public** (`/lighting`) : carte des luminaires, etat en direct, consigne calculee (soleil, horaires, luminosite, presence,
previsions d'IA), economie d'energie et gaspillage detectes a partir des **mesures reelles** des controleurs.
- Soleil calcule automatiquement (aucune heure a saisir) ; rues principales de Tunisie (OpenStreetMap) pour declarer des luminaires le long d'une rue
- Mettre des luminaires « en ligne » sans materiel : `tools/lamp_fleet.py --simulate` (controleurs SIMULES, voir `ml/API_ECLAIRAGE.md`)
- API et regles : `ml/API_ECLAIRAGE.md` · controleur de luminaire : `tools/lamp_controller.py`
- Datasets et modeles a entrainer : `ml/DATASETS_ECLAIRAGE.md` · generateur de donnees synthetiques : `ml/gen_lighting_data.py`

## Modeles d'IA de l'equipe
`models/` : Isolation Forest (heures de consommation anormales) et XGBoost (prevision horaire, experimentale), branches sur les **mesures reelles** des capteurs ; modele 3 (demande nationale mensuelle, liee aux previsions climatiques) dans l'espace STEG.
- Installation : `pip install -r requirements.txt` (sans ces bibliotheques, l'application fonctionne et indique que l'IA est indisponible)
- Verification des modeles par empreinte : `python manage.py trust-models` · details, hypotheses et questions a l'equipe : `ml/INTEGRATION_MODELES.md`

## Capteur
Une carte (ESP32 + pince amperemetrique, ou circuit simule dans Proteus) envoie ses mesures a `POST /api/ingest` avec l'identifiant et la cle du capteur.
- Materiel et firmware : `HARDWARE.md`
- Simulation dans Proteus : `PROTEUS.md` (pont serie : `tools/serial_bridge.py`)
- **Carte virtuelle sans pilote** : `emulator/` execute le `.hex` de l'Arduino dans un emulateur et l'envoie a l'application (`emulator/LISEZMOI.md`)

## Donnees et vie privee
- La **reference de facture n'est pas verifiee** aupres de la STEG.
- Le compte STEG voit le nom, l'adresse, le telephone, les equipements et la consommation mesuree. Le client l'accepte a l'inscription.
- Mots de passe haches (scrypt), cles de capteur hachees, cookie de session HttpOnly. **Pas de HTTPS en local** : a ajouter avant tout vrai deploiement.
- Base : `data/steg_app.db` (SQLite). Pas de recuperation de mot de passe par e-mail : `python manage.py reset-password`.

## Identite visuelle et tableau de bord
Theme calque sur la maquette « Finora » : fond degrade menthe-turquoise, cartes blanches tres arrondies, rail d'icones a gauche (libelles au survol), barre de pilules en haut
(Analyse IA, Plan de pointe, Recompenses) avec profil et cloche, grand titre dynamique. Palette : turquoise `#1f7a6c`, violet `#7b6ee6`, vert `#1db97c`, bleu `#2fa8ee`, orange `#f29a4a`.
Accueil client = 3 rangees : 4 cartes (puissance en direct sur fond turquoise, energie, pointe, charge) ; courbe **mesure + prevision IA** (infobulle, plage d'erreur = erreur observee du modele)
a cote d'une demi-jauge de repartition par circuit ; apercu IA (compte a rebours de la prochaine pointe, gestes demandes), consommation par circuit, equipements principaux.
Toutes les valeurs viennent des mesures du capteur ou des equipements **declares** (indiques comme tels). Mode clair (defaut) / sombre : bouton soleil-lune, variables en tete de `web/static/style.css`.
Logo : `web/static/logo-*.png` (fez + ampoule + fleche), pose sur pastille blanche ; variantes claire et sombre. Fonctions de graphiques : `web/static/common.js` (`miniLine`, `miniBars`, `semiGauge`, `donut`).

## Historique des factures payees (onglet « Factures »)
Le client saisit ses factures deja payees (periode de 2 mois, montant, kWh facultatif, date) ; elles apparaissent en barres avec la prevision en cours. Elles **ameliorent la prevision** : cycle de facturation
deduit des periodes, moyenne des dernieres factures melangee a la prevision quand le capteur a peu de recul (70 % a 3 jours, 15 % des 14 jours), **calibrage du capteur** (kWh factures / kWh mesures, borne 0,8-1,25)
quand une facture couvre une periode entierement mesuree. Le kWh d'une facture sans kWh est deduit du montant (approximatif). Simulation : `python tools/demo.py bills-seed [--real]` (6 factures simulees par client).

## Meteo et climat
L'onglet affiche les previsions envoyees par les modeles de l'equipe (mois, annee, scenarios). Tant qu'aucun modele n'a rien envoye, il affiche la **meteo reelle a 16 jours** (Open-Meteo, chef-lieu du gouvernorat,
internet requis) : `forecasts.open_meteo_group`.

## Veille « mimamori » 見守り (kizuna/mimamori.py)
Pour une personne agee seule : le capteur sert a detecter une **absence d'activite** inhabituelle (pas de bouilloire, pas de lumiere... a l'heure habituelle), sans camera ni micro.
Activite = puissance horaire au-dessus du socle du foyer (frigo, box) de max(40 W, 30 %) ; rythme habituel = 90e percentile des plus longues periodes d'inactivite des 14 derniers jours (la nuit, ~8 h) ;
« attention » apres +1,5 h, « alerte » apres +3 h : **SMS au proche** (simule : table `sms_outbox`, a brancher sur une vraie passerelle dans `send_sms`) ; leve automatiquement quand l'activite reprend.
La personne garde la main : consentement, proche choisi par elle, « Je vais bien », mode voyage. Le proche a une **page sans compte** (`/mimamori?t=...`) qui ne montre qu'un etat, jamais la consommation.
Simulation : 2 personnes agees (`sim14`, `sim15`). Demo : `python tools/demo.py mimamori panne sim14@kizuna-sim.local --heures 11` (alerte + SMS en < 1 min), puis `... mimamori reprise sim14@kizuna-sim.local`.
Limites : une sortie sans activer la pause peut declencher une fausse alerte ; ce n'est pas un dispositif medical ; seuils = hypotheses a valider sur de vraies personnes.

## Facture previsionnelle (kizuna/billing.py)
Estimation **en temps reel** de la facture STEG (facturation **tous les 2 mois**) a partir du capteur : consommation de la periode a ce jour + consommation attendue jusqu'a la fin (profil horaire du foyer),
tarif par tranches (62 a 414 millimes/kWh), redevance de puissance 700 millimes/kVA/mois, TVA 7 % (<= 300 kWh/mois) ou 13 %. Affichee dans le tableau de bord client (montant prevu, fourchette a 80 %, tranches,
cout par heure en ce moment) et cote STEG (facturation prevue par client et totale). Tarifs fournis par l'equipe : **a valider** ; hypotheses (tranches progressives, bornes doublees pour 2 mois, periodes calendaires,
kVA = kW declares) et taxes non incluses (collectivites locales, FTE, redevance audiovisuelle) listees dans le fichier et sur la carte.

## Simulation (plusieurs clients et capteurs)
`python tools/demo.py run` cree une base **separee** (`data/demo/`) avec 13 clients, 15 capteurs et un compte STEG, comble 35 jours d'historique puis envoie des mesures
en direct par `/api/ingest` (port 8030). Tout est simule et etiquete « (simulation) ». Comptes : `data/demo/demo_comptes.md`. Autres commandes :
`incident` (declenche une consommation anormale), `ai` (fait tourner les modeles d'IA sur chaque client), `reset`. Tests des modeles : `python tools/test_models.py`.

## Controle de saisie et securite des entrees
- Navigateur : chaque champ texte est verifie (attributs `required`, `data-vtype`, `data-validate` ; code dans `common.js`), avec un message d'erreur sous le champ.
- Serveur : les memes regles sont reappliquees (`db.validate_profile`, `db.clean_label`, `_sanitize` dans `server.py`) : caracteres de controle et de direction retires, longueurs limitees. Le serveur ne fait jamais confiance au navigateur.

## Securite de mise en service (kizuna/security.py)
`python manage.py security` liste ce qui reste a faire. Tout est optionnel en local, a activer pour une vraie mise en service :
- **HTTPS** : `KIZUNA_TLS_CERT` + `KIZUNA_TLS_KEY` (le serveur parle HTTPS lui-meme), ou `KIZUNA_SECURE=1` derriere un proxy HTTPS (Caddy, nginx, tunnel). Le cookie devient `Secure` et l'en-tete HSTS est envoye. Derriere un proxy de confiance, ajoutez `KIZUNA_TRUST_PROXY=1` pour lire la vraie adresse du visiteur.
- **Cle des recompenses** : `KIZUNA_SECRET` (sinon `data/reward.key`, exclu du depot). Changer la cle invalide les anciens codes.
- **Compte STEG** : pas de compte par defaut ; `create-steg` exige 12 caracteres et 3 types de caracteres ; mots de passe courants refuses pour tous.
- **Limitation de debit par adresse IP** : connexion (en plus de la limite par e-mail), inscription, verification de formulaire, page publique du proche, capteurs/modeles/luminaires, ecritures des clients. Reponse 429.
- **Sauvegarde** : `python manage.py backup` (copie coherente a chaud dans `data/backups/`, 14 gardees). Pour chiffrer : `KIZUNA_BACKUP_KEY` + `pip install cryptography`. A planifier chaque nuit (tache planifiee Windows ou cron). Une sauvegarde jamais restauree n'est pas une sauvegarde : essayez-la une fois.

### Durcissement de l'application (audit)
Verifie par des tests automatiques (environ 2 900 requetes mal formees sur tous les points d'entree : aucune erreur 500, aucune fuite de detail technique).
- En-tetes sur toutes les reponses : Content-Security-Policy (sources limitees au site, Leaflet sur unpkg, tuiles OpenStreetMap ; pas d'iframe, pas de `<base>` ni de formulaire externe), X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy, COOP/CORP, HSTS si HTTPS.
- Les scripts de pages sont en ligne (`'unsafe-inline'`) : la protection contre l'injection de code reste l'echappement de toute donnee affichee (`esc`) + le nettoyage a l'entree (`_sanitize`, `clean_label`). Pas de faille d'echappement trouvee a l'audit.
- Fichiers statiques : traversee de dossier refusee (`..`, `\`, octet nul), seuls les types connus sont servis.
- Requetes : `Content-Length` invalide ou negatif refuse, delai de 30 s par connexion, corps limite a 1 Mo, types de champs verifies (liste/objet a la place d'un texte). Toute erreur interne repond « erreur interne » sans detail.
- Connexion : meme duree de reponse pour un e-mail inconnu ou connu, mot de passe plafonne a 128 caracteres, au plus 10 sessions par compte, **changer un mot de passe deconnecte tous les appareils**.
- SQL : requetes parametrees partout ; les seules requetes construites par texte n'utilisent que des noms de colonnes internes fixes.
- Limites connues : un seul processus Python + SQLite (pas de montee en charge), pas d'e-mail de verification a l'inscription, pas de double authentification, l'inscription indique si un e-mail existe deja (facilite le recensement des comptes), pas de journal d'audit des actions STEG, limites de debit en memoire (remises a zero au redemarrage).

### Mots de passe
- **Mots de passe : hachage, pas chiffrement.** Un mot de passe « chiffre » peut etre dechiffre ; un hache, non. Ils sont haches avec scrypt (N=2^15, r=8, p=3, 32 Mio, sel aleatoire par compte, format `scrypt$15$8$3$sel$empreinte`). Les anciens comptes sont acceptes puis re-haches automatiquement a leur prochaine connexion. Au plus 4 calculs en meme temps (limite la memoire). Aucun mot de passe n'est stocke ni journalise en clair.
- Pas de CAPTCHA (retire a la demande de l'equipe). La protection contre les essais repetes repose sur les limites de debit : 5 echecs par e-mail et par adresse IP = blocage de 5 minutes, 20 tentatives par adresse IP, 6 inscriptions par heure et par adresse IP.

## Module SMS (kizuna/sms.py)
- Par defaut : **simulation** (rien ne part, statut « simule »). Un SMS reel coute de l'argent et utilise un numero de telephone : il faut un contrat avec un fournisseur.
- `KIZUNA_SMS_PROVIDER=twilio` + `KIZUNA_SMS_ACCOUNT`, `KIZUNA_SMS_TOKEN`, `KIZUNA_SMS_FROM` (ou `KIZUNA_SMS_SERVICE`) ; ou `http` pour une passerelle generique (operateur tunisien, agregateur) : `KIZUNA_SMS_URL` (https obligatoire), `KIZUNA_SMS_TOKEN`, `KIZUNA_SMS_FROM`, corps JSON `{to, text, from}` (noms modifiables par `KIZUNA_SMS_FIELDS`). Non teste avec un vrai fournisseur : verifie seulement avec des appels simules.
- File d'attente avec statuts `simule / en_attente / envoye / echec / bloque`, 3 tentatives (1, 2, 4 min), quota de 6 SMS par foyer et par 24 h (`KIZUNA_SMS_DAILY_MAX`), numeros valides (8 chiffres tunisiens ou `+indicatif`, donc Japon possible), calcul des segments (le japonais passe en UCS-2 : 70 caracteres par SMS, donc plus cher).
- Les identifiants du fournisseur ne sont jamais stockes en base, ni affiches. `GET /api/steg/sms` (compte STEG) donne l'etat et les compteurs, numeros masques.
- Commandes : `python manage.py sms-status`, `python manage.py sms-test +216XXXXXXXX`.
- **E-mail (canal actuel de la veille mimamori)** : le proche est maintenant prevenu par e-mail (champ « Son e-mail »), le SMS ne servant plus que de secours pour d'anciens reglages. Variables : `KIZUNA_MAIL_PROVIDER=brevo`, `KIZUNA_MAIL_TOKEN` (cle API Brevo), `KIZUNA_MAIL_FROM` (adresse expeditrice **verifiee dans Brevo**), `KIZUNA_MAIL_FROM_NAME`. Sans elles : simulation. Test : `python manage.py mail-test adresse@exemple.com`. Meme file, memes statuts, meme quota que les SMS.
- Utilise aujourd'hui par la veille mimamori (alerte + message rassurant). Les messages sont bilingues FR + JA : un SMS bilingue fait 2 a 3 segments.

## Langues (francais, anglais, arabe, japonais)
- Le client choisit sa langue dans Profil (ou dans la barre du haut) ; elle est enregistree dans son profil (`profiles.lang`). L'arabe passe l'interface de droite a gauche.
- Les textes sont ecrits en francais ; `web/static/i18n.js` les remplace a l'affichage. Dictionnaires : `tools/i18n_source.py` et `tools/i18n_source_more.py`, puis `python tools/i18n_build.py` genere `web/static/i18n/{en,ar,ja}.js`. Un texte absent du dictionnaire reste en francais.
- Non traduits : pages STEG et eclairage public, noms saisis par les clients, notes techniques de l'IA.
- Les traductions arabe et japonaise sont a faire relire par un locuteur natif avant usage reel.

## Consommation anormale
Remplace l'ancienne alerte de fuite de courant. Deux controles, sur mesures reelles : la puissance depasse la somme des equipements
**declares** (x1,10), ou elle depasse de beaucoup l'**habitude du foyer** a la meme heure (apres 5 jours d'historique : moyenne + 3 ecarts-types,
au moins x1,5 et +300 W). L'alerte propose quels equipements declares pourraient expliquer l'exces. Seuils = hypotheses : `kizuna/anomaly.py`.

## Plan de pointe
Priorites par equipement, plan de delestage client et potentiel d'effacement regional (foyers participants
seulement) : voir [SHEDDING.md](SHEDDING.md).

## Structure
`server.py` serveur · `manage.py` administration · `kizuna/db.py` base de donnees · `web/` pages · `tools/serial_bridge.py` pont serie ·
`ml/` entrainement des modeles (separe de l'application) · `archive/` ancienne version simulee (supprimable)
