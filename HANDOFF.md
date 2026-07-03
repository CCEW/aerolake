# AeroLake — Guide de passation (handoff)

But : permettre à n'importe qui au labo de **reprendre, héberger et utiliser**
AeroLake après le départ de l'auteur, **sans dépendre de sa machine**.

---

## 1. C'est quoi (en 30 s)
Pipeline RF : **capture (SDR) → SigMF → MinIO (lakehouse) → aperçu + métadonnées**.
Une capture = `capture.sigmf-data` (IQ brut) + `capture.sigmf-meta` (JSON) +
`capture-preview.png` (spectre), rangés sous `{signal_type}/{date}/{session}/`.
Le *pourquoi* de chaque choix est dans `docs/adr/` ; vue d'ensemble dans
`README.md` et `docs/pitch-architecture.md`.

## 2. Architecture cible (multi-utilisateurs)
```
  [Poste d'acquisition]  ← le SDR est branché ici, AeroLake installé ici
        │  upload SigMF
        ▼
  [NAS du labo : MinIO]  ← le lakehouse PARTAGÉ (tout le monde lit/écrit ici)
        ▲  navigateur (console MinIO :9001)
  [n'importe quel PC]    ← pour parcourir/voir les captures, zéro install
```

## 3. ⚠️ À FAIRE EN PRIORITÉ avant le départ

### a) Mettre le code sur le GitLab du labo
Le code est sur le GitHub **perso** `Lafraise6813/aerolake` → l'accès part avec
l'auteur. Une fois l'accès au **GitLab du labo** obtenu :
```bash
git remote add gitlab git@gitlab.<labo>:<groupe>/aerolake.git
git push gitlab main
```
(ou créer le projet sur GitLab puis pousser). **Donner les droits à Abdu / un
collègue** comme mainteneur.

### b) Mettre MinIO sur le NAS (au lieu du PC de dev)
Aujourd'hui MinIO tourne en Docker **en local**. Pour qu'il survive :
- Sur le NAS, lancer MinIO (le `docker/docker-compose.yml` du repo marche tel
  quel ; ou utiliser le service S3/MinIO natif du NAS s'il existe).
- Créer le bucket **`aerolake-captures`** et un **compte de service**
  (access key + secret key) pour l'équipe.
- Noter l'endpoint, ex. `http://<nas>:9000` (API) et `:9001` (console).
- (Optionnel) migrer les captures déjà faites depuis le MinIO local.

## 4. Préparer une machine d'acquisition (celle qui aura le SDR)
1. **Prérequis** : `uv`, `git`, SoapySDR + le driver du SDR
   (`sudo apt install soapysdr-tools soapysdr-module-rtlsdr`), et sur **Windows**
   WSL2 (Ubuntu) + **usbipd-win** (pour passer l'USB au WSL).
2. **Récupérer + installer** :
   ```bash
   git clone <url-du-repo> && cd aerolake
   uv sync
   bash setup-soapy.sh          # pont SoapySDR (à relancer après chaque uv sync)
   ```
3. **Configurer le `.env`** :
   ```bash
   cp .env.example .env
   # éditer .env : AEROLAKE_S3_ENDPOINT = http://<nas>:9000
   #               AEROLAKE_S3_ACCESS_KEY / SECRET_KEY = le compte de service NAS
   #               AEROLAKE_S3_BUCKET = aerolake-captures
   ```
4. **(Windows) attacher le SDR à WSL** — une fois `bind` (admin, persistant) :
   ```powershell
   usbipd list           # repérer le BUSID du SDR
   usbipd bind --busid <X-Y>
   ```
   Ensuite `acquire.sh` fait le `attach` automatiquement à chaque lancement.

## 5. Faire une acquisition

**Option A — l'interface web (recommandée pour les collègues, zéro terminal) :**
```bash
uv sync --extra gui && uv run aerolake-gui    # à lancer UNE fois sur le poste d'acquisition
```
Sous Windows : **double-cliquer `launch-gui.vbs`** à la racine du repo fait tout
ça sans terminal (démarrage caché + navigateur). Un raccourci vers ce fichier
dans `shell:startup` = GUI lancé automatiquement au boot du poste.
Chacun ouvre ensuite `http://<poste>:8501` dans son navigateur : déposer une
config TOML/JSON → (option) cliquer la position de l'antenne sur la carte →
Démarrer → revoir le spectre → Pousser dans MinIO / Garder / Jeter. Un onglet
**Playback** permet de parcourir le lakehouse, visualiser le spectre de
n'importe quelle fenêtre et exporter le SigMF pour GNU Radio.

**Option B — la ligne de commande :**
```bash
./acquire.sh examples/<config>.toml      # ex. examples/test-complet.toml
```
`acquire.sh` détecte **tout seul** si MinIO est local ou sur le NAS (via
l'endpoint du `.env`) et enchaîne : (Docker si local) → USB/SDR → SoapySDR →
healthcheck → capture → upload (data + meta + aperçu PNG). Répondre `y` pour
pousser. Les configs (**TOML recommandé** — commentées — ou JSON) sont dans
`examples/` (voir `examples/README.md` ; `capture.full.toml` = le modèle complet).

## 6. Parcourir / visualiser (zéro install)
- **Console MinIO** : `http://<nas>:9001` → bucket `aerolake-captures` → naviguer
  → cliquer **`capture-preview.png`** pour voir le spectre direct.
- **Analyse poussée** : télécharger `*.sigmf-data` + `*.sigmf-meta` (mêmes noms),
  ouvrir dans **Inspectrum** ou **GNU Radio** (`gnuradio/playback.grc`).

## 7. Vérifier que tout va bien (santé du code)
```bash
uv run ruff check .   &&   uv run mypy src   &&   uv run pytest
```

## 8. Évolutions prévues (non faites)
- **GUI : formulaire de config** (fréquence/durée → génère le TOML) pour ne plus
  manipuler de fichier du tout ; la carte cliquable en était la première brique.
- **Ré-émission RF** : GNU Radio + BladeRF TX, câblé + atténué — avec Camelia
  (division du travail : ADR-019).
- **Couche SQL / Apache Iceberg** : le « vrai » lakehouse requêtable — évolution
  future (cf. ADR-013, `docs/pitch-architecture.md`).

## 9. Points d'attention
- **`.env` n'est jamais commité** (secrets). Utiliser `.env.example` comme modèle.
- **`setup-soapy.sh`** : à relancer après chaque `uv sync` (recrée le pont SoapySDR).
- **`usbipd attach`** : à refaire après un redémarrage/débranchement (acquire.sh le fait).
- Le `.env` de dev actuel contient des clés en double (cosmétique, sans impact).

## 10. Contacts / mémoire
- Auteur : **Théo Schmitt**. Superviseur : **Abdu**.
- Historique et contexte projet : `docs/context/`, `docs/adr/` (ADR-001 → 018).
