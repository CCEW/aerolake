# AeroLake — Document de passation

> **Projet** : AeroLake — lakehouse RF du LASSENA (ÉTS Montréal)
> **Auteur** : Théo Schmitt (stagiaire, 2026) · **Superviseur** : Abdessamad (Abdu) Amrhar
> **Date du document** : 2026-07-21
>
> **But de ce document : qu'une personne qui n'a JAMAIS vu le projet — ni la radio
> logicielle, ni le "cloud", ni Python — puisse comprendre, installer, utiliser et
> faire évoluer AeroLake sans l'auteur.** Tout y est : les concepts expliqués à
> partir de zéro, le mode d'emploi, le code ligne de conduite par ligne de conduite,
> les décisions passées et la feuille de route.

---

## Comment lire ce document (choisis ton parcours)

| Tu es… | Lis en priorité |
|---|---|
| **Utilisateur** (je veux juste enregistrer/retrouver des signaux) | Parties 1, 2, 4 |
| **Nouveau responsable technique** (je reprends le projet) | Tout, dans l'ordre — compte ~une journée |
| **Développeur pressé** (je dois modifier le code) | Parties 2.3, 5, 6, 7 |
| **Chef de projet** (où en est-on ?) | Parties 1 et 8 |

Suggestion de première semaine pour un successeur :
**Jour 1** — Parties 1 et 2 (comprendre). **Jour 2** — Partie 3 (installer, faire
une capture synthétique). **Jour 3** — Partie 4 (toutes les fonctions, GUI incluse).
**Jours 4-5** — Parties 5 à 7 (le code + les décisions). Ensuite, Partie 8 = ta
to-do list.

---

# Partie 1 — Le projet en deux pages

## 1.1 Le problème de départ

Le LASSENA enregistre des **signaux radio** (GNSS/GPS, Iridium, ADS-B, Starlink…)
avec des récepteurs logiciels (SDR). Avant AeroLake, chaque enregistrement était
un fichier binaire **muet** sur le disque de quelqu'un : impossible de savoir sans
demander à son auteur à quelle fréquence il a été pris, à quelle cadence
d'échantillonnage, où, quand, avec quel matériel. Résultat : des données
inexploitables par les autres, perdues quand la personne part, irrejouables.

## 1.2 La réponse : AeroLake

AeroLake est le **lakehouse RF** du labo : une chaîne complète qui

1. **capture** un signal (vrai SDR — RTL-SDR, BladeRF — ou signal de test),
2. l'**écrit au format standard SigMF** (le signal brut + sa carte d'identité JSON),
3. le **range dans un stockage partagé** (MinIO sur le serveur FAST du labo) avec
   des **métadonnées et des tags cherchables** et un **aperçu du spectre** en PNG,
4. permet de le **retrouver** (catalogue filtrable), de le **relire par fenêtre
   temporelle** (même dans un fichier de plusieurs Go, on ne télécharge que la
   fenêtre voulue), de le **rejouer** à sa cadence d'origine, de le **diffuser**
   sur le réseau (ZeroMQ) et de l'**exporter vers GNU Radio** pour la ré-émission RF.

Une **interface web** (un clic, zéro terminal) rend tout cela accessible aux
non-développeurs.

```
  [Poste d'acquisition]  ← le SDR est branché ici, AeroLake installé ici
        │  upload SigMF (HTTPS)
        ▼
  [FAST : MinIO]         ← le lakehouse PARTAGÉ (tout le monde lit/écrit ici)
        ▲  navigateur (console web)
  [n'importe quel PC]    ← parcourir/voir/rejouer les captures, zéro install
```

## 1.3 Les personnes

| Qui | Rôle |
|---|---|
| **Abdu** (Abdessamad Amrhar) | Chef de projet, admin FAST/MinIO — c'est lui qui donne les accès |
| **Malek** | Tuteur |
| **Camila** | Référente GNU Radio / ré-émission RF (la branche "RF pur", ADR-019) |
| **Wissem / Ahmad** | Propriétaires des récepteurs (RFSoC…) |
| **Théo Schmitt** | Auteur d'AeroLake (parti — d'où ce document) |

## 1.4 Où vivent les choses

| Quoi | Où |
|---|---|
| **Code source** | Dépôt `aerolake` — GitHub `Lafraise6813/aerolake` (⚠ perso, à transférer sur le GitLab du labo, voir Partie 8) |
| **Stockage partagé** | FAST : console **https://minio.fast.etsmtl.ca/browser**, API S3 **https://minio-api.fast.etsmtl.ca** |
| **Portail FAST** | https://fast.etsmtl.ca |
| **Documentation en ligne** | Confluence, espace LASSENA, page « Project: AeroLake » |
| **Interface web AeroLake** | `http://<poste-acquisition>:8501` |
| **Ce document + toute la doc** | dossier `docs/` du dépôt (la copie du dépôt fait foi) |

---

# Partie 2 — Les concepts, à partir de zéro

*Cette partie ne suppose AUCUNE connaissance préalable. Chaque section se lit
indépendamment.*

## 2.1 Un signal radio dans un ordinateur : les échantillons IQ

Une radio logicielle (**SDR**, *Software Defined Radio*) est une antenne + un
convertisseur qui transforme les ondes radio en **nombres**. Elle mesure le champ
reçu plusieurs millions de fois par seconde ; chaque mesure est un **échantillon**.

Chaque échantillon est un **nombre complexe** : une partie **I** (*in-phase*) et
une partie **Q** (*quadrature*). Pourquoi complexe ? Parce qu'un nombre seul ne
capture que l'amplitude instantanée ; le couple (I, Q) capture **amplitude ET
phase**, ce qui permet de représenter fidèlement une bande de fréquences entière
autour de la fréquence d'écoute. Deux règles d'or :

- **La cadence d'échantillonnage (sample rate) = la largeur de bande observée.**
  Échantillonner à 2 MHz ⇒ on « voit » 2 MHz de spectre autour de la fréquence
  centrale.
- **La fréquence centrale (center frequency)** est l'endroit du spectre où l'on
  écoute. Ex. : GPS L1 = 1 575,42 MHz.

Dans AeroLake, chaque échantillon est stocké en **`cf32_le`** : deux nombres
flottants 32 bits (I puis Q), petit-boutiste (*little-endian*) — soit **8 octets
par échantillon**. Une capture GPS de 10 s à 2 MHz = 20 millions d'échantillons =
160 Mo. C'est le format que GNU Radio lit nativement (« complex »).

## 2.2 Qu'est-ce qu'un data lakehouse ?

Trois architectures de stockage de données, dans l'ordre historique :

- **L'entrepôt de données (*data warehouse*)** — les données sont **structurées à
  l'entrée** (tableaux, schémas stricts, SQL). Parfait pour des rapports fiables ;
  mais rigide, cher, et inadapté aux données brutes volumineuses (un signal radio
  n'est pas un tableau).
- **Le lac de données (*data lake*)** — on **verse tout, brut**, tel quel, dans un
  stockage bon marché. Souple et peu coûteux ; mais sans discipline, le lac devient
  un **marécage** (*data swamp*) : des téraoctets de fichiers que plus personne ne
  sait interpréter. C'est exactement la situation qu'AeroLake devait corriger.
- **Le lakehouse** — le meilleur des deux : **le stockage brut et bon marché du
  lac**, PLUS **la discipline de l'entrepôt** : chaque donnée est décrite
  (métadonnées), cataloguée (tags cherchables), vérifiable (empreinte d'intégrité)
  et lisible par des outils standard.

**AeroLake est un lakehouse pour la radio :**

| Côté « lac » (brut, bon marché) | Côté « entrepôt » (discipline) |
|---|---|
| Les échantillons IQ bruts, intouchés (`.sigmf-data`) | La description JSON normée (`.sigmf-meta`, format SigMF) |
| Stockage objet S3 (MinIO), extensible | Métadonnées HTTP + tags S3 cherchables (ADR-003) |
| N'importe quel volume, n'importe quel signal | Empreinte sha512, catalogue `aerolake-list`, aperçu PNG |

L'évolution future « couche SQL » (Apache Iceberg — voir Partie 8) compléterait le
tableau, mais le cœur du lakehouse est déjà là.

## 2.3 SigMF — la carte d'identité du signal

**Le problème** : un fichier de 160 Mo d'échantillons IQ ne contient **aucune**
information sur lui-même. Sans savoir la cadence, la fréquence et le format des
octets, il est **définitivement illisible**.

**La solution** : [SigMF](https://sigmf.org) (*Signal Metadata Format*), un
standard **ouvert** de la communauté SDR. Une « recording » SigMF = **deux
fichiers de même nom, côte à côte** :

```
capture.sigmf-data   ← le signal : les échantillons IQ BRUTS, sans en-tête, sans
                       compression — exactement ce que l'antenne a reçu.
capture.sigmf-meta   ← la carte d'identité : un fichier JSON, lisible par un
                       humain ET par n'importe quel outil.
```

Le `.sigmf-meta` a trois sections :

1. **`global`** — la fiche technique : `core:datatype` (`cf32_le` chez nous),
   `core:sample_rate`, `core:version` (version de la spec), `core:sha512`
   (l'**empreinte d'intégrité** : la preuve mathématique que les octets stockés
   sont bit à bit ceux capturés), auteur, description, licence, matériel, plus nos
   champs `aerolake:*` (type de signal, opérateur, lieu, overflows…).
2. **`captures`** — le contexte d'acquisition : fréquence centrale, date/heure
   (`core:datetime`, UTC), **géolocalisation** (`core:geolocation`, un Point
   GeoJSON — attention, l'ordre GeoJSON est **[longitude, latitude, altitude]**).
3. **`annotations`** — des régions étiquetées du signal (de tel échantillon à tel
   échantillon, telle bande de fréquences) : « ici un burst Iridium », le pointage
   d'antenne (azimut/élévation/polarisation)…

S'y ajoutent les **extensions** (nous utilisons `antenna:` pour décrire
l'antenne : modèle, gain…) et les **collections** (`.sigmf-collection` : un
document qui regroupe plusieurs recordings d'une même campagne — ADR-014).

**Pourquoi SigMF change tout** : le couple data+meta est lisible par GNU Radio,
Inspectrum, la librairie Python `sigmf`, et par n'importe qui dans dix ans. Le
signal est conservé **échantillon par échantillon** (intégrité sha512) : ce qu'on
rejoue est **exactement** ce qui a été reçu — condition indispensable au
playback/ré-émission exigé par le cahier des charges.

## 2.4 NAS, stockage objet S3, MinIO et FAST

- **Un NAS** (*Network Attached Storage*) est « le disque dur du réseau » : une
  machine toujours allumée dont le seul métier est de stocker, branchée au réseau
  du labo pour que **tout le monde** lise et écrive au même endroit. Fini les
  données prisonnières du PC d'un stagiaire.
- **Le stockage objet S3** est la manière moderne de parler à ce stockage. Au lieu
  de dossiers/fichiers, on manipule des **objets** rangés dans des **buckets**
  (des seaux), désignés par une **clé** (ex.
  `gnss_l1/2026-07-16/…/capture.sigmf-data`). L'API S3 (créée par Amazon) est
  devenue le **standard de fait** : des dizaines de logiciels la parlent.
- **MinIO** est un serveur S3 **auto-hébergé** : le labo a « son Amazon S3 à lui »,
  sur ses machines, sans cloud externe.
- **FAST** (https://fast.etsmtl.ca) est la plateforme de services auto-hébergés de
  l'ÉTS/LASSENA. Elle héberge le MinIO du labo — c'est **notre lakehouse de
  production**. Console web : https://minio.fast.etsmtl.ca/browser ; API S3 :
  https://minio-api.fast.etsmtl.ca (en HTTPS via le proxy Traefik ; le port
  direct :9000 est fermé).

**Point d'architecture capital** : dans AeroLake, l'adresse du stockage est un
**réglage** (`.env`), pas du code. Passer du MinIO local de dev au FAST de
production — ou demain à **Garage** (voir Partie 8) — c'est changer une ligne de
configuration, zéro ligne de code (ADR-001/020).

## 2.5 Métadonnées HTTP vs tags S3 (la convention ADR-003)

Le stockage S3 offre deux façons d'accrocher des informations à un objet, et nous
utilisons **les deux, chacune pour ce qu'elle sait faire** :

| | Métadonnées HTTP (`x-amz-meta-*`) | Tags S3 |
|---|---|---|
| **Nature** | Valeurs techniques/continues | Valeurs catégorielles/énumérables |
| **Exemples** | sample-rate, center-freq, sample-count, session-id | signal-type, hardware, operator, location |
| **Lecture** | Requête HEAD (gratuite, zéro octet du corps téléchargé) | GetObjectTagging (indexable → **recherche**) |
| **Usage** | Inspecter une capture sans la télécharger | Filtrer le catalogue (`aerolake-list --signal-type gnss_l1`) |

Les deux sont attachées **uniquement au `.sigmf-data`** — le `.sigmf-meta` n'en a
pas besoin : son corps *est* la description.

## 2.6 Lire un morceau sans tout télécharger : HTTP Range et multipart

Deux mécanismes S3 rendent les gros fichiers vivables :

- **HTTP Range** (lecture, ADR-009) — on demande « les octets 1 600 000 000 à
  1 616 000 000 » et le serveur n'envoie **que ça**. C'est ce qui permet de
  visualiser la fenêtre *t=200 s, durée 10 s* d'une capture de plusieurs Go en
  quelques secondes (`read_segment`, le « scrub » du GUI).
- **Multipart upload** (écriture, ADR-010) — l'envoi se fait par morceaux de
  8 Mio : la RAM reste bornée quelle que soit la taille du fichier, et un échec
  en cours de route est proprement annulé.

## 2.7 Diffuser en direct : ZeroMQ Pub/Sub

**ZeroMQ** est une bibliothèque de messagerie réseau légère. Le motif
**Pub/Sub** : un **publisher** émet des messages sur un port, des **subscribers**
s'y abonnent — sans serveur central. AeroLake s'en sert pour **rejouer une capture
en direct sur le réseau** (ADR-008) : le player relit les échantillons à leur
cadence d'origine et les publie trame par trame ; n'importe quelle machine du labo
peut s'abonner et recevoir le flux (format filaire : 3 parties — topic, en-tête
JSON, octets complex64).

## 2.8 SDR, SoapySDR et GNU Radio — qui fait quoi (ADR-019)

- **SoapySDR** est la couche d'abstraction matérielle : une API unique pour parler
  à tous les SDR (RTL-SDR, BladeRF…). AeroLake l'utilise pour l'acquisition
  pilotée par config.
- **GNU Radio** est l'atelier de traitement du signal par excellence (flowgraphs
  graphiques). **Division du travail actée (ADR-019)** : GNU Radio possède les
  « bords RF » exigeants — l'enregistrement très haut débit et la **ré-émission
  RF** (avec Camila) — tandis qu'AeroLake possède le **lakehouse** (ranger,
  cataloguer, servir, rejouer en logiciel).
- **Le contrat entre les deux mondes est le fichier `.sigmf-data` lui-même** : du
  `cf32_le` brut, que les blocs File Source/File Sink de GNU Radio lisent et
  écrivent nativement (type « complex »), sans aucun bloc spécial. Un fichier
  enregistré par GNU Radio entre dans le lakehouse par `aerolake-ingest` ; une
  capture du lakehouse sort vers GNU Radio par le bouton d'export du GUI.

---

# Partie 3 — Installer et faire tourner

## 3.1 Vue d'ensemble de l'installation

Il y a **deux rôles de machine** :

- **Le poste d'acquisition** (celui qui a le SDR branché) : AeroLake y est
  installé ; il sert aussi l'interface web à tout le monde.
- **Tous les autres PC** : rien à installer — un navigateur suffit (console MinIO
  pour parcourir, interface AeroLake du poste d'acquisition pour capturer/rejouer).

## 3.2 Prérequis du poste d'acquisition

1. **Git** et **uv** (le gestionnaire Python du projet — https://github.com/astral-sh/uv).
   Python 3.12+ est installé par uv automatiquement.
2. **SoapySDR + le driver du SDR** (Linux/WSL) :
   `sudo apt install soapysdr-tools soapysdr-module-rtlsdr` (adapter au matériel).
3. **Sous Windows** : WSL2 (Ubuntu) + **usbipd-win** pour passer l'USB du SDR à
   WSL.
4. *(Optionnel, pour la branche RF)* **GNU Radio système** :
   `sudo apt install gnuradio` (3.10+). Il vit HORS du projet uv — voir §5.19.

## 3.3 Installation pas à pas

```bash
git clone <url-du-depot> && cd aerolake
uv sync --extra gui              # installe tout, interface web comprise
bash setup-soapy.sh              # pont SoapySDR système → venv
                                 # (⚠ à RELANCER après chaque `uv sync`)
cp .env.example .env             # puis éditer .env (voir ci-dessous)
uv run aerolake-healthcheck      # vérifie .env + stockage joignable + bucket OK
```

### Le `.env` — LE fichier de configuration

Toutes les valeurs sont des variables `AEROLAKE_*` (chargées par
pydantic-settings ; les vraies variables d'environnement priment sur le fichier).
**Le `.env` contient des secrets : il n'est JAMAIS commité** (`.env.example` sert
de modèle).

```dotenv
# ---- Production : le FAST du labo ----
AEROLAKE_S3_ENDPOINT=https://minio-api.fast.etsmtl.ca
AEROLAKE_S3_ACCESS_KEY=<clé d'accès créée dans la console MinIO>
AEROLAKE_S3_SECRET_KEY=<clé secrète associée>
AEROLAKE_S3_BUCKET=aerolake-captures
# Le certificat TLS de FAST est signé par l'autorité interne de l'ÉTS :
AEROLAKE_S3_CA_BUNDLE=/chemin/vers/ets-root-ca.pem   # (demander le .pem à Abdu)
# — à défaut, temporairement : AEROLAKE_S3_VERIFY_SSL=false

# ---- Dev local (sans réseau labo) : MinIO en Docker ----
# AEROLAKE_S3_ENDPOINT=http://localhost:9000
# AEROLAKE_S3_ACCESS_KEY=minioadmin
# AEROLAKE_S3_SECRET_KEY=minioadmin
# AEROLAKE_S3_BUCKET=aerolake-captures
```

### MinIO local de développement (facultatif)

Pour travailler sans le réseau du labo :

```bash
cd docker && docker compose up -d    # API :9000, console :9001, bucket auto-créé
```

### (Windows) attacher le SDR à WSL

Une fois pour toutes (en administrateur) :

```powershell
usbipd list                  # repérer le BUSID du SDR
usbipd bind --busid <X-Y>    # persistant
```

Ensuite `acquire.sh` fait le `attach` automatiquement à chaque lancement (à
refaire après un débranchement/redémarrage — le script s'en charge).

## 3.4 Lancer l'interface web

```bash
uv run aerolake-gui        # sert sur 0.0.0.0:8501
```

Sous Windows : **double-cliquer `launch-gui.vbs`** à la racine du dépôt fait tout
sans terminal (démarrage caché + ouverture du navigateur). Un raccourci vers ce
fichier dans `shell:startup` = interface lancée automatiquement au démarrage du
poste. Les collègues ouvrent ensuite `http://<poste>:8501`.

## 3.5 Vérifier la santé du projet

```bash
uv run ruff check .    # lint (0 erreur attendu)
uv run mypy src        # types (0 erreur attendu)
uv run pytest          # ~210 tests, tous verts, sans matériel ni serveur
```

---

# Partie 4 — Utiliser AeroLake au quotidien

## 4.1 Faire une capture (interface web — recommandé)

**Étape 0 — ouvrir l'interface** : double-clic « AeroLake GUI » sur le poste
d'acquisition, ou `http://<poste>:8501` depuis n'importe quel PC.

**Étape 1 — déposer une config.** Glisser un fichier **`.toml`** (recommandé —
les commentaires sont permis) ou `.json`. Des **modèles commentés** sont dans
`examples/` : `capture.example.toml` = modèle minimal ; `capture.full.toml` =
**tous** les champs, marqués *(obligatoire)* / *(optionnel)*.

```toml
signal_type = "gnss_l1"          # catégorie → rangement + tag de recherche
center_freq = 1_575_420_000      # fréquence centrale en Hz
sample_rate = 2_000_000          # échantillonnage en Hz (= bande captée)
duration_s  = 10                 # durée en secondes

[source]
type   = "soapy"                 # "soapy" = vrai SDR ; "synthetic" = signal de test
driver = "rtlsdr"                # rtlsdr, bladerf, …
```

**Étape 2 — (option) pointer l'antenne sur la carte** : volet « 📍 Régler la
position », un clic sur la carte → la position part dans les métadonnées SigMF
(sinon, celle du fichier de config est utilisée ; sinon, rien — jamais de
position inventée).

**Étape 3 — Démarrer.** L'appli **valide la config avant de toucher au
matériel**, capture, puis affiche : nombre d'échantillons, taille, durée et le
**spectre**.

**Étape 4 — décider** : **⬆ Pousser dans MinIO** (rejoint le lakehouse partagé) /
**💾 Garder en local** (dossier `captures/` du poste) / **🗑 Jeter**.
C'est un choix humain **à chaque capture** — c'est voulu (ADR-018) : il n'y a pas
de « verdict qualité » automatique, l'opérateur voit le spectre et décide.

## 4.2 Faire une capture (ligne de commande)

```bash
./acquire.sh examples/<config>.toml
```

`acquire.sh` est le tout-en-un : il détecte si le stockage est local ou distant
(via l'endpoint du `.env`), démarre Docker si besoin, attache l'USB, vérifie
SoapySDR, lance le healthcheck, capture, affiche le récapitulatif et demande la
confirmation avant de pousser. Équivalent plus bas niveau :
`uv run aerolake-capture --config ma_capture.toml`.

## 4.3 Retrouver et regarder une capture

- **Sans rien installer** : console MinIO (https://minio.fast.etsmtl.ca/browser)
  → bucket → naviguer par type de signal puis par date → cliquer
  **`capture-preview.png`** : le spectre s'affiche immédiatement.
- **Dans l'interface AeroLake** : onglet **▶ Playback** → choisir une capture →
  métadonnées + aperçu → curseurs *Début / Fenêtre* pour visualiser le **spectre
  de n'importe quel instant** (seule la fenêtre demandée est téléchargée — HTTP
  Range).
- **En ligne de commande** : `uv run aerolake-list --signal-type gnss_l1`
  (catalogue filtrable sans télécharger un octet de signal).

## 4.4 Rejouer une capture — les trois modes

1. **Visualiser** un instant précis : onglet Playback (ci-dessus).
2. **Diffuser en direct sur le réseau** (ZeroMQ) — l'onglet Playback affiche la
   commande prête à copier :
   ```bash
   uv run aerolake-stream --key <capture> --bind tcp://*:5555     # émetteur
   uv run aerolake-subscribe --address tcp://<poste>:5555         # récepteur
   ```
3. **Ré-émettre en RF** (GNU Radio + SDR émetteur, ex. BladeRF) : bouton
   **« Exporter pour GNU Radio »** de l'onglet Playback → charger le
   `.sigmf-data` dans `gnuradio/playback.grc`. *(Branche RF — référente :
   Camila ; ADR-019.)*

## 4.5 Ingérer un enregistrement existant

Pour faire entrer dans le lakehouse un fichier IQ enregistré **ailleurs** (GNU
Radio, RFSoC…) :

```bash
uv run aerolake-ingest capture.bin --signal-type gnss_l1 \
    --sample-rate 2e6 --center-freq 1575.42e6
```

Accepte un fichier **ou un dossier** de paquets RFSoC (`RX0_pkt_*.bin`,
concaténés en ordre numérique) ; formats `cf32/cu8/cs16/cs32` (convertis et
normalisés en cf32) ; l'envoi est en flux (RAM bornée quelle que soit la taille).

## 4.6 Grouper une campagne en collection

```bash
uv run aerolake-collection --prefix gnss_l1/2026-07-16/ \
    --name "campagne-toit" --description "…"      # --dry-run pour prévisualiser
```

Écrit un `.sigmf-collection` (SigMF v1.2) à la racine du préfixe ; les
enregistrements incomplets (orphelins) sont signalés et ignorés.

## 4.7 Dépannage express

| Symptôme | Cause probable | Remède |
|---|---|---|
| « Could not connect to the endpoint URL » au Push | Stockage injoignable | FAST : réseau/VPN + `.env`. Local : `cd docker && docker compose up -d` |
| « No SDR found for driver=… » | SDR pas vu par WSL | Rebrancher puis `./acquire.sh` (refait l'attach) ; vérifier `usbipd list` côté Windows |
| `CERTIFICATE_VERIFY_FAILED` | CA interne de l'ÉTS inconnue | `AEROLAKE_S3_CA_BUNDLE=<ets-root-ca.pem>` (le demander à Abdu) |
| « AccessDenied » partout | La clé n'a pas de politique S3 | Demander à l'admin FAST (Abdu) une politique lecture/écriture sur le bucket |
| Spectre « écrasé » / plat | Gain trop fort (clipping) | Baisser la puissance d'entrée ou laisser `agc = true` |
| Le GUI ne s'ouvre pas | Appli pas lancée sur le poste | Double-clic « AeroLake GUI » ; sinon §3.4 |
| Import SoapySDR cassé après `uv sync` | Pont venv recréé | Relancer `bash setup-soapy.sh` |
| `aerolake-healthcheck` échoue | `.env` incomplet/faux | Vérifier endpoint, clés, nom du bucket |

---

# Partie 5 — Le code, expliqué en entier

*Le code est volontairement **très commenté** (choix pédagogique assumé) : le
fichier source répond souvent lui-même aux questions. Cette partie donne la carte
et le rôle de chaque pièce ; en cas de doute, ouvrir le fichier.*

## 5.1 La carte

```
                    ┌── synthetic.py (signal de test)
  CONFIG (.toml) ──►│── soapy_source.py (vrai SDR)        PRODUCER
                    └── ingest.py (fichier IQ existant)
                              │  échantillons IQ (complex64)
                              ▼
                    sigmf_writer.py  → .sigmf-data + .sigmf-meta
                              │
                    orchestrator.py  → clés, métadonnées HTTP, tags S3
                              ▼
                    storage.py ───────► MinIO  (LE point d'accès unique)
                              ▲
                    reader.py (list / inspect / read / read_segment)   CONSUMER
                              │
            ┌─────────────────┼──────────────────┐
        player.py         stream.py         collection.py
     (replay cadencé)   (bus ZeroMQ)     (.sigmf-collection)

        gui/app.py = façade Streamlit sur exactement ces briques
```

**Si tu ne retiens que 3 fichiers** : `orchestrator.py` (l'enchaînement),
`sigmf_writer.py` (le format), `storage.py` (l'accès stockage). Tout le reste
gravite autour. **Si tu en maîtrises 6, tu maîtrises AeroLake** : ces trois +
`config.py`, `capture_config.py`, `reader.py`.

## 5.2 Structure du dépôt

```
aerolake/
├── src/aerolake/
│   ├── common/       config, logging, storage  (infra partagée)
│   ├── producer/     acquisition → SigMF → préparation upload
│   ├── consumer/     lecture MinIO → replay / streaming / collections
│   ├── gui/          interface web Streamlit (extra optionnel [gui])
│   └── scripts/      les 8 CLIs (entrées pyproject [project.scripts])
├── tests/            miroir de src/ (moto = S3 simulé) + tests/integration/
├── examples/         configs modèles TOML/JSON (validées par les tests)
├── gnuradio/         record.grc / playback.grc (GNU Radio système, hors venv)
├── docker/           MinIO local de dev (docker-compose)
├── docs/             ADRs + guides (dont ce document)
├── acquire.sh        capture tout-en-un (USB + Soapy + healthcheck + capture)
├── launch-gui.bat/.vbs   lanceur Windows sans terminal
└── setup-soapy.sh    pont SoapySDR système → venv (à relancer après uv sync)
```

## 5.3 Les principes de conception (à respecter en modifiant le code)

1. **Un seul point d'accès S3** : tout passe par `StorageClient` (ADR-001).
   Jamais de boto3 ailleurs.
2. **Injection de dépendances partout** : le SDR (`device_opener`), gpsd
   (`reader`), l'horloge du player (`sleep`), les sockets ZeroMQ, le
   `storage_client` des CLIs — tout est injectable, donc **testable sans matériel
   ni serveur**.
3. **Préparer ≠ stocker** : `prepare_capture()` fabrique tout en mémoire,
   `push_capture()` téléverse. Entre les deux, **l'humain décide** (ADR-018).
4. **Les erreurs remontent typées** : `StorageError`, `ConfigError` — les CLIs
   les attrapent et sortent avec des codes documentés (0 ok / 1 stockage /
   2 config / 3 capture-inattendu).
5. **Code pédagogique** : la densité de commentaires est volontaire — la
   maintenir.

## 5.4 Les invariants transverses (à connaître ABSOLUMENT)

- **Disposition du bucket** :
  `{signal_type}/{YYYY-MM-DD}/{YYYY-MM-DD_HHhMMmSS}_{source}_{id8}/capture.*` —
  date parente en UTC (tri stable), dossier feuille en heure locale (lisible),
  `id8` = 8 hex aléatoires (anti-collision). Une capture est « complète » si
  `.sigmf-data` **et** `.sigmf-meta` existent ; les orphelins sont ignorés.
- **Métadonnées vs tags** : voir §2.5 (ADR-003). Les deux **uniquement sur le
  `.sigmf-data`**.
- **Ordre d'upload : `.sigmf-meta` AVANT `.sigmf-data`** — un lecteur qui arrive
  entre les deux voit du JSON interprétable, pas des octets orphelins.
- **`update_tags` = REMPLACEMENT total** (c'est l'API S3, pas un choix) : pour
  changer UN tag → lire, fusionner, réécrire. Oublier la fusion **efface** les
  autres tags.
- **Datatype** : tout est normalisé **`cf32_le`** (8 octets/échantillon) — ce que
  GNU Radio lit nativement.
- **Bascule d'endpoint** : `s3_endpoint` vide → AWS réel (ce que **moto**
  intercepte dans les tests) ; renseigné → MinIO/S3 compatible (signature v4 +
  adressage path-style, déjà configurés). Migrer de stockage = changer le
  `.env`, zéro code.

## 5.5 `common/config.py` — les réglages

- **`Settings(BaseSettings)`** — `s3_endpoint`, `s3_access_key`, `s3_secret_key`
  (**`SecretStr`** : jamais en clair dans les logs/tracebacks), `s3_bucket`,
  `s3_region`, `s3_verify_ssl` (bool), `s3_ca_bundle` (chemin d'un CA interne).
  Chargés depuis `AEROLAKE_*` + `.env`.
- **`get_settings()`** — accès **caché** (`lru_cache`) : le `.env` n'est parsé
  qu'une fois par process. **Toujours passer par elle.**

## 5.6 `common/logging.py` — logs propres

- **`configure_logging(level)`** — structlog → **stderr**, pour laisser stdout
  aux résultats (`--json`, tableaux). Chaque CLI l'appelle en premier.
- `_StderrLogger` résout stderr **à l'appel** (pas à l'import) pour que les
  redirections de test fonctionnent.

## 5.7 `common/storage.py` — LE point d'accès S3

- `_safe_tag_value` / `_tagging_header` — assainissent les tags (S3 n'accepte
  que lettres/chiffres/` +-=._:/@` ; le reste devient `_`). *(Nés d'un bug
  réel : une virgule dans `location` faisait échouer tout l'upload.)*
- **`StorageError`** — l'exception unique de la couche stockage.
- **`StorageClient`** :
  - `health_check()` ; `object_exists`, `object_size`, `get_object_metadata`
    (HEAD, zéro octet de corps), `get_object_tags`, `update_tags` ;
  - `upload_bytes(key, data, content_type, *, metadata, tags)` — petit objet en
    un PUT ;
  - **`upload_multipart(key, chunks, …, part_size=8 Mio)`** (ADR-010) — upload
    d'un **flux** de chunks, RAM bornée, abort propre en cas d'échec ;
  - `download_bytes`, **`download_range(key, start, end)`** (ADR-009) — la
    lecture partielle qui rend le « seek » possible ;
  - `list_objects(prefix)` (paginé), `delete_object`.
- `_build_client` applique la TLS : `s3_ca_bundle` renseigné → `verify=<chemin>` ;
  sinon `s3_verify_ssl=false` → `verify=False` (dépannage uniquement).

## 5.8 `producer/synthetic.py` — le signal de test

- **`generate_tone(duration_s, sample_rate, center_freq, tone_offset_hz,
  tone_amplitude, snr_db, seed)`** → `SyntheticSignal`. Sinusoïde complexe
  décalée + bruit AWGN dosé par `snr_db` ; `seed` rend la capture
  **reproductible**. Permet de tester TOUTE la chaîne sans matériel.

## 5.9 `producer/soapy_source.py` — le vrai SDR (ADR-015)

- `list_devices()` — énumère les SDR visibles.
- **`SdrRecorder`** — l'objet qui possède le **cycle de vie complet** du
  périphérique : `open() → configure() → start() → read(n) → stop() → close()`,
  utilisable en `with`. Points clés :
  - **`device_opener` injectable** : les tests fournissent un faux device — tout
    le recorder est testé sans matériel ;
  - `configure()` **relit les valeurs effectives** (le matériel arrondit : on
    stocke ce qui a vraiment été appliqué, pas ce qu'on a demandé) ;
  - `read()` compte les **overflows** (échantillons perdus), remontés jusqu'aux
    métadonnées ;
  - `capture(duration_s, …)` → `SdrCapture` (samples + provenance complète :
    driver, n° de série, gain, antenne, overflows).
- `capture_from_sdr(…)` — shim fonctionnel rétro-compatible, appelé par
  l'orchestrateur.

## 5.10 `producer/gps.py` — position live via gpsd (ADR-016)

- **`read_geolocation(reader=None)`** — lit UN rapport TPV de gpsd → Point
  GeoJSON `core:geolocation`, **ou `None` si pas de fix** (jamais de position
  inventée) ; lève si gpsd est injoignable alors qu'on l'a demandé.
- Évite le « piège GPSD » : ordre **[lon, lat, alt]** respecté, pas de dump brut.
  Le `reader` est injectable → conversion testée sans démon.

## 5.11 `producer/capture_config.py` — le schéma de config (pydantic)

- `_StrictModel` : `extra="forbid"` — **toute clé inconnue est rejetée** (les
  fautes de frappe sont attrapées à la validation, pas au runtime).
- **`CaptureConfig`** — quoi (`signal_type`, `center_freq`, `sample_rate`,
  `duration_s`), comment (`source` : union discriminée `synthetic` | `soapy`),
  descriptif (`author`, `description`, `license`, `operator`), où
  (`LocationConfig`), plus `AnnotationConfig` / `AntennaConfig` optionnels.
- Validations croisées : `location.gps` **exclusif** avec la géoloc manuelle ;
  `freq_lower_edge`/`freq_upper_edge` **par paire** (règle SigMF) ;
  `GeolocationConfig.to_geojson()` émet l'ordre **[lon, lat, alt]**.
- Les valeurs **calculées** (datatype, version, datetime, sha512…) ne sont PAS
  dans la config : l'encodeur les remplit à la capture.

## 5.12 `producer/config_loader.py` — TOML/JSON → config validée

- **`load_capture_config(path)`** → `CaptureConfig`. Parseur choisi par
  l'extension : `.toml` → `tomllib` (stdlib), sinon JSON. Trois familles d'échec
  (fichier absent, syntaxe, schéma) → **une** exception lisible : `ConfigError`
  (affichée sans traceback, exit 2).

## 5.13 `producer/sigmf_writer.py` — l'encodage SigMF

- **`encode(signal, *, author, recorder, hardware, signal_type, …)`** →
  `SigMFCapture(data_bytes, meta_bytes)`. Écrit le Global complet
  (`core:datatype=cf32_le`, `core:version` tirée de `sigmf.__specification__` —
  pas codée en dur —, `core:sample_rate`, **`core:sha512`**, `core:num_channels`,
  auteur/description/licence, champs `aerolake:*`), la **géolocalisation** dans
  le segment captures, l'**annotation** unique (label/commentaire/bords de bande
  + pointage antenne) et l'extension **`antenna:`** (champs scalaires ; le
  pointage va dans l'annotation, conformément à la spec).
- `EncodableSignal` (Protocol) — le contrat minimal d'une source (`samples`,
  `sample_rate`, `center_freq`, `description`) : l'encodeur est **agnostique de
  la source**.

## 5.14 `producer/orchestrator.py` — le chef d'orchestre

- **`prepare_capture(…)`** → `PreparedCapture`. Enchaîne : résolution de la
  source (synthétique ou SDR) → acquisition → `encode()` → construction des clés
  (disposition §5.4), des en-têtes `x-amz-meta-*` et des tags (dont provenance
  SDR : sdr-serial/sdr-gain/sdr-antenna). **Rien n'est stocké.**
- **`push_capture(prepared, *, with_preview=False)`** → `CaptureResult`. Upload
  méta **puis** data ; l'aperçu PNG est **best-effort** (un échec d'aperçu ne
  fait jamais échouer la capture).
- `save_capture_locally(prepared)` — même arborescence que le bucket, sur disque
  (la branche « garder en local »).
- `RichMetadata` — le paquet descriptif optionnel construit par la CLI/le GUI ;
  l'orchestrateur ne connaît pas `CaptureConfig` (découplage).

## 5.15 `producer/ingest.py` — entrer un enregistrement existant

- **`ingest_files(…)`** → `IngestResult`. Ingestion **en flux** : lit par
  chunks, convertit `cu8`/`cs16`/`cs32` → cf32 normalisé, sha512 au fil de
  l'eau, `upload_multipart`, puis écrit le `.sigmf-meta`. Multi-fichiers = un
  seul capture continu (cas RFSoC). **C'est le pont d'entrée GNU Radio →
  lakehouse** (ADR-019).

## 5.16 `producer/preview.py` — l'aperçu visuel

- **`render_spectrum_png(samples, sample_rate, center_freq)`** → octets PNG :
  PSD en haut, spectrogramme (waterfall) en bas. Import matplotlib **paresseux**
  + backend Agg (pas d'écran requis) ; sous-échantillonne au-delà de ~2 M
  d'échantillons.

## 5.17 `consumer/` — relire, rejouer, diffuser, grouper

- **`reader.py` — `CaptureReader`** : `list_captures(prefix)` (paires complètes
  uniquement), `inspect(key)` → `CaptureInfo` (métadonnées + tags **sans
  télécharger un octet** de signal), `read(key)` (tout le signal décodé),
  **`read_segment(key, start_s, duration_s)`** — LA lecture partielle
  (secondes → échantillons → octets → `download_range` ; fenêtre hors bornes
  tronquée proprement).
- **`player.py` — `CapturePlayer.play(…)`** (ADR-007) : émet les trames **au
  rythme du sample rate d'origine** ; `realtime=False` pour aller au plus
  vite ; `on_frame(index, frame)` = le point de branchement (c'est là que se
  greffe le publisher ZeroMQ). L'horloge (`sleep`) est **injectée** → cadence
  testée sans attendre.
- **`stream.py`** (ADR-008) : `encode_frame`/`decode_frame` (format filaire pur,
  testé sans réseau), `FramePublisher.bind("tcp://*:5555")` (PUB ;
  `publish` a la signature de `on_frame`), `FrameSubscriber.connect(addr)` (SUB).
- **`collection.py` — `CollectionBuilder`** (ADR-014) : `scan(prefix)` (paires +
  orphelins signalés), `build(…)` → `CollectionPlan` (rien d'écrit : le
  `--dry-run` naturel), `write(plan)` (upload du `.sigmf-collection`).

## 5.18 `gui/app.py` — l'interface web (Streamlit)

Façade **sans logique de capture propre** — si tu comprends le happy path, tu
comprends le GUI. À savoir pour la maintenir :

- Streamlit **ré-exécute tout le script à chaque interaction** ; ce qui doit
  survivre (la capture préparée) vit dans `st.session_state`.
- Onglet **Capture** : `_load_uploaded` (fichier → temp → `load_capture_config`,
  même chemin de validation que la CLI) → `_location_picker` (carte folium ; un
  clic **remplace** la géoloc de la config ; se dégrade en silence hors-ligne) →
  `_do_capture` (réutilise `_resolve_geolocation` + `_build_rich_metadata` de la
  CLI puis `prepare_capture`) → `_render_result` (métriques, spectre, boutons
  Pousser/Garder/Jeter).
- Onglet **Playback** : `_render_playback` — liste → `inspect` → aperçu → scrub
  (`read_segment` + `render_spectrum_png`) → commande ZeroMQ affichée → export
  `.sigmf-meta`/`.sigmf-data` (au-delà de 100 Mo : renvoi vers la console MinIO).
- Esthétique : CSS injecté (`_CSS`) + fond WebGL **ColorBends** (three.js dans un
  `st.iframe` épinglé plein écran via le sélecteur `iframe[srcdoc]` — nécessite
  d'être en ligne pour le CDN ; sans réseau, le fond est simplement absent).
  Thème de base dans `.streamlit/config.toml`.

## 5.19 `gnuradio/` — les flowgraphs (hors venv !)

`record.grc` / `playback.grc` tournent avec le **GNU Radio système**
(`sudo apt install gnuradio`, 3.10+) et son Python à lui — PAS le `.venv` du
projet. Le pont avec AeroLake est le fichier `.sigmf-data` lui-même (cf32 brut,
type « complex » des blocs File Source/Sink). Valider un `.grc` sans interface :
`grcc -o /tmp gnuradio/playback.grc` (le `.py` généré est gitignoré).

## 5.20 `scripts/` — les 8 lignes de commande

Toutes : `configure_logging()` d'abord, sortie `rich`, codes de sortie
documentés (0/1/2/3), une dépendance injectable pour les tests.

| CLI | Fichier | Rôle |
|---|---|---|
| `aerolake-healthcheck` | healthcheck.py | `.env` + stockage joignable + bucket OK (`--json` pour scripter) |
| `aerolake-capture` | capture.py | LA capture pilotée par config (validation → géoloc → capture → confirmation) |
| `aerolake-ingest` | ingest.py | fichier ou dossier IQ existant → lakehouse (tri naturel des `RX0_pkt_N`) |
| `aerolake-list` | catalog.py | catalogue : lister/filtrer par tags, requêtes HEAD uniquement |
| `aerolake-collection` | collection.py | grouper un préfixe en `.sigmf-collection` (`--dry-run`) |
| `aerolake-play` | play.py | replay cadencé (`--start/--duration` = lecture partielle ; `--no-realtime`) |
| `aerolake-stream` | stream.py | player + publisher ZeroMQ (`--bind tcp://*:5555`, `--topic`) |
| `aerolake-subscribe` | subscribe.py | s'abonner ; affiche l'en-tête + le RMS dBFS de chaque trame |

---

# Partie 6 — Les tests et la CI

## 6.1 Philosophie

**Aucun test unitaire ne touche du vrai matériel ni un vrai serveur.** Tout est
simulé par l'injection de dépendances :

| Dépendance réelle | Substitut de test |
|---|---|
| S3/MinIO | **moto** (S3 simulé en mémoire) via les fixtures de `tests/conftest.py` |
| SDR (SoapySDR) | faux `device_opener` |
| gpsd | faux `reader` |
| horloge du player | faux `sleep` (la cadence est vérifiée sans attendre) |
| sockets ZeroMQ | faux sockets |
| prepare/push dans les CLIs | stubs injectés |

Fixtures à connaître (`tests/conftest.py`) : `test_settings` (isolé du `.env`
du développeur ; `s3_endpoint=""` pour que moto intercepte), `mock_s3` (bucket
pré-créé), `storage_client` (un `StorageClient` branché dessus). **Injecter ces
fixtures**, jamais de vrai backend dans les tests unitaires.

À noter aussi : `tests/test_examples_valid.py` valide **tous** les modèles
d'`examples/` contre le schéma (un modèle qui dérive casse la CI) ;
`tests/gui/` fait un smoke test Streamlit **AppTest** (sauté si l'extra gui
n'est pas installé).

## 6.2 Le test d'intégration = le test de conformité stockage

`tests/integration/` (marqueur `integration`, opt-in `AEROLAKE_RUN_INTEGRATION=1`)
fait un aller-retour **réel** : multipart + Range + tagging contre un vrai
serveur. Il a une double casquette :

1. En CI, il tourne contre un conteneur MinIO réel.
2. **C'est le test de conformité de tout stockage S3 candidat** : c'est lui,
   inchangé, qui a validé SeaweedFS (ADR-020) — et c'est lui qu'il faudra lancer
   contre Garage (Partie 8).

## 6.3 Commandes

```bash
uv run pytest                          # tout (~210 tests)
uv run pytest tests/consumer/test_reader.py            # un fichier
uv run pytest -k clipping              # par expression
AEROLAKE_RUN_INTEGRATION=1 uv run pytest -m integration   # intégration (serveur réel requis)
```

## 6.4 La CI (`.github/workflows/ci.yml`)

Deux jobs : **lint + types + tests** (`ruff check`, `mypy src`,
`pytest -m "not integration"`, dépendances gelées par `uv sync --frozen`) et
**intégration** (conteneur MinIO + `pytest -m integration`).

---

# Partie 7 — Les décisions (ADR) : le « pourquoi » du code

Les **ADR** (*Architectural Decision Records*, `docs/adr/`) sont la mémoire du
projet : chaque choix structurant, daté et argumenté. **Règle : ne jamais
inverser un choix sans lire son ADR ; toute décision de poids mérite un nouvel
ADR** (on n'édite pas silencieusement un ADR accepté). Résumé :

| ADR | Décision | L'essentiel |
|---|---|---|
| 001 | **boto3** plutôt que le SDK MinIO | portabilité S3 + testable avec moto ; endpoint vide = AWS/moto, renseigné = MinIO |
| 002 | upload par lot d'abord, streaming ensuite | le streaming est arrivé via ADR-008/010 |
| 003 | **métadonnées vs tags** | la convention de la Partie 2.5 + la disposition du bucket |
| 004 | ~~qualité avant streaming~~ | *retiré* (corrigé par ADR-013/018) |
| 005 | ~~cycle de vie des tags qualité~~ | *retiré* (ADR-018) |
| 006 | ~~GUI de visualisation~~ | *archivé* (ADR-013) — le GUI actuel (2026-06) est un NOUVEAU composant, dans le mandat |
| 007 | **stratégie de playback** | replay logiciel cadencé maintenant ; ré-émission SDR plus tard |
| 008 | **streaming ZeroMQ Pub/Sub** | le bus de diffusion des trames |
| 009 | **lectures partielles HTTP Range** | `read_segment` + offset/length côté GNU Radio |
| 010 | **upload multipart en flux** | RAM bornée quelle que soit la taille |
| 011 | ~~viewer d'analyse `.h5`~~ | *archivé* (ADR-013) |
| 012 | ~~ré-émission RF (v1)~~ | *archivé* (ADR-013) — repris par ADR-019 |
| 013 | **réalignement sur le mandat** (recadrage 2026-06-08) | la priorité est RX → MinIO → ZMQ ; GUI/analysis/TX archivés sur `archive/explorations-v1` |
| 014 | **SigMF Collections** | grouper une campagne sous un préfixe |
| 015 | **`SdrRecorder` OOP** | cycle de vie du SDR en un objet ; `device_opener` injectable |
| 016 | **géoloc SigMF native via gpsd** | fix validé ou `None` — jamais de position inventée |
| 018 | **suppression de la couche qualité** | l'humain décide à chaque capture ; remplace 004/005 |
| 019 | **partage record/playback** | GNU Radio = bords RF ; AeroLake = lakehouse ; le `.sigmf-data` est le contrat |
| 020 | **fin de vie MinIO communautaire** | rester sur MinIO épinglé court terme ; SeaweedFS validé en secours ; **Garage exclu à l'époque faute de tagging** — mais voir Partie 8 |

---

# Partie 8 — État des lieux au départ de l'auteur & feuille de route

*Cette partie est LA to-do list du successeur. État au 2026-07-21.*

## 8.1 Ce qui marche, vérifié

- ✅ Chaîne complète **capture réelle** : RTL-SDR validé de bout en bout sur banc
  générateur (générateur → RTL-SDR → SigMF → MinIO) ; BladeRF supporté ;
  synthétique disponible sans matériel.
- ✅ **Interface web** complète (capture 1-clic, carte, spectre, Playback avec
  scrub, export GNU Radio, commande ZeroMQ) + lanceur Windows sans terminal.
- ✅ Lecture partielle (Range), replay cadencé, streaming ZeroMQ, collections,
  ingest (fichiers + dossiers RFSoC), catalogue par tags, aperçus PNG.
- ✅ ~210 tests verts sans matériel ; CI lint+types+tests+intégration.
- ✅ **Connexion au serveur FAST établie** : endpoint HTTPS joignable, TLS interne
  géré (options `s3_ca_bundle`/`s3_verify_ssl`), clés d'accès créées.
- ✅ Docs publiées sur Confluence (manuel utilisateur + référence du code).

## 8.2 ⚠ Bloqué / en attente (à débloquer en PREMIER)

1. **Droits S3 sur FAST** — la clé d'accès voit les buckets (`raw-data`,
   `data-parquet`) mais n'a **aucun droit** (AccessDenied en lecture ET
   écriture). **Demander à Abdu** une politique lecture/écriture
   (Put/Get/DeleteObject, ListBucket, Get/PutObjectTagging, multipart) sur un
   bucket `aerolake-captures` OU un préfixe de `raw-data`, selon son choix —
   puis faire **la première capture réelle poussée sur FAST**.
2. **Certificat CA de l'ÉTS** — demander à Abdu le `.pem` de « ETS Montreal Root
   CA », le mettre dans `AEROLAKE_S3_CA_BUNDLE` et **supprimer**
   `AEROLAKE_S3_VERIFY_SSL=false` (posé temporairement).
3. **GitLab du labo** — le code est sur le GitHub perso de l'auteur. Dès l'accès
   à `gitlab.lassena.etsmtl.ca` :
   ```bash
   git remote add gitlab git@gitlab.lassena.etsmtl.ca:<groupe>/aerolake.git
   git push gitlab main
   ```
   et donner les droits mainteneur à Abdu + un collègue.

## 8.3 La migration Garage (décision FAST, travail à faire — futur ADR-021)

**FAST a décidé de migrer MinIO → [Garage](https://garagehq.deuxfleurs.fr/)**
(l'édition communautaire de MinIO est en fin de vie — ADR-020). Conséquence
directe pour AeroLake : **Garage ne supporte PAS le tagging d'objets S3**
(vérifié), or notre couche découverte s'appuie sur les tags (ADR-003).

**Plan d'adaptation (dans l'ordre)** :

1. Déplacer les valeurs catégorielles (signal-type, operator, hardware,
   location…) des **tags S3** vers les **métadonnées `x-amz-meta-*`** (le
   catalogue `aerolake-list` lit déjà les métadonnées via HEAD ; pendant la
   transition, l'orchestrateur peut écrire les deux).
2. **Valider avec le test d'intégration** contre un conteneur Garage — la même
   méthode, suite inchangée, qui a validé SeaweedFS (ADR-020).
3. Écrire l'**ADR-021** qui documente l'adaptation.
4. Basculer le `.env` vers l'endpoint Garage le jour J — c'est tout.

## 8.4 Évolutions prévues (non commencées)

- **Playback GNU Radio validé sur banc** : dérouler `gnuradio/playback.grc` sur
  une vraie capture (validation headless : `grcc -o /tmp gnuradio/playback.grc`),
  écrire le petit runbook.
- **Ré-émission RF** avec Camila : BladeRF TX, câblé + atténué (ADR-019 ;
  jamais en rayonné sans autorisation).
- **GUI : formulaire de config** (fréquence/durée → génère le TOML) pour ne plus
  manipuler de fichier du tout — la carte cliquable en était la première brique.
- **Couche SQL / Apache Iceberg** : le lakehouse requêtable (« toutes les
  captures GNSS de plus de 10 s prises en juin ») — évolution de fond, voir
  `docs/pitch-architecture.md`.

## 8.5 Pièges connus (résumé des cicatrices)

- `setup-soapy.sh` **à relancer après chaque `uv sync`** (le pont venv est recréé).
- `usbipd attach` à refaire après débranchement/redémarrage (`acquire.sh` le fait).
- `update_tags` **remplace** tout le jeu de tags (lire-fusionner-réécrire).
- Les caractères spéciaux dans les tags sont assainis (une virgule dans
  `location` a déjà fait échouer un upload — c'est corrigé, mais c'est
  l'exemple type du bug S3 sournois).
- L'ordre GeoJSON est **[lon, lat, alt]** — l'inverse de l'intuition « lat, lon ».
- Le `.env` n'est **jamais** commité ; `.env.example` est le modèle.

---

# Partie 9 — Annexes

## 9.1 Contenu de l'archive de code jointe

L'archive `aerolake-code-<date>.zip` livrée avec ce document contient **tout le
dépôt** (code, tests, docs, ADRs, exemples, flowgraphs) **à l'exception** des
secrets (`.env`) et des artefacts locaux (`.venv`, captures, caches). Après
extraction : suivre la Partie 3 (le `git clone` en moins). L'historique git
complet, lui, vit dans le dépôt distant (GitHub, puis GitLab après transfert).

## 9.2 Les documents du dépôt (par ordre de lecture)

| Document | Rôle |
|---|---|
| `docs/carte-du-code.md` | la carte du code en UNE page — commencer là |
| `docs/manuel-utilisateur.md` | le mode d'emploi utilisateur (aussi sur Confluence) |
| `docs/documentation-code.md` | la référence détaillée du code (aussi sur Confluence) |
| `HANDOFF.md` | l'aide-mémoire opérationnel de reprise (version courte de ce document) |
| `docs/adr/001…020` | chaque décision, datée et argumentée |
| `docs/passation.md` | **ce document** (source Markdown) |
| `docs/context/historique-discussions.md` | l'histoire d'avant le dépôt (mai 2026) |
| `README.md` + `CLAUDE.md` | vue d'ensemble + guide pour l'assistant IA |

## 9.3 Glossaire minute

| Terme | En un mot |
|---|---|
| **IQ** | les échantillons complexes (I=in-phase, Q=quadrature) d'un signal radio numérisé |
| **cf32_le** | complex float 32 bits little-endian — 8 octets/échantillon, le format natif GNU Radio |
| **SigMF** | le standard ouvert « signal brut + carte d'identité JSON » |
| **SDR** | radio logicielle (RTL-SDR, BladeRF…) |
| **SoapySDR** | l'API universelle pour piloter les SDR |
| **GNU Radio** | l'atelier de traitement du signal (flowgraphs) |
| **Lakehouse** | stockage brut du *data lake* + discipline du *data warehouse* |
| **S3** | l'API standard du stockage objet (buckets, clés, objets) |
| **MinIO / Garage / SeaweedFS** | des serveurs S3 auto-hébergés |
| **FAST** | la plateforme de services auto-hébergés du labo (héberge le stockage) |
| **Bucket** | le « seau » racine du stockage objet |
| **HTTP Range** | demander seulement une plage d'octets d'un objet |
| **Multipart** | envoyer un objet par morceaux (RAM bornée) |
| **ZeroMQ Pub/Sub** | messagerie réseau légère éditeur/abonnés |
| **gpsd** | le démon Linux qui parle aux récepteurs GPS |
| **ADR** | Architectural Decision Record — le « pourquoi » écrit d'un choix |
| **moto** | la simulation S3 en mémoire utilisée par les tests |
| **uv** | le gestionnaire d'environnement/dépendances Python du projet |

## 9.4 Contacts

- **Superviseur / admin FAST** : Abdu (Abdessamad Amrhar) — accès, droits S3,
  certificat CA.
- **Branche RF / GNU Radio** : Camila.
- **Auteur** : Théo Schmitt — theo.schmitt02@gmail.com (questions d'archéologie
  uniquement : tout ce qui est nécessaire est censé être dans ce document — si
  quelque chose manque, c'est un bug de la passation, à corriger dans
  `docs/passation.md`).

---

*Fin du document. Bonne continuation — et prenez soin du lakehouse.* 🛰️
