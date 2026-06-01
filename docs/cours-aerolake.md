# Cours d'ingénieur — AeroLake de A à Z

> Document pédagogique pour comprendre **tout** le code d'AeroLake, des concepts
> de base (qu'est-ce qu'un signal RF, un échantillon IQ) jusqu'à l'architecture
> complète. Lis-le dans l'ordre : chaque partie s'appuie sur la précédente.
> Un **glossaire** des sigles est à la fin.

## Table des matières

- [Partie 0 — La vision : à quoi sert AeroLake](#partie-0)
- [Partie 1 — Les fondations : RF, IQ, et le format SigMF](#partie-1)
- [Partie 2 — L'outillage Python (uv, structure, config, logs)](#partie-2)
- [Partie 3 — La couche stockage (StorageClient + MinIO/S3)](#partie-3)
- [Partie 4 — Le Producer (générer → encoder → uploader)](#partie-4)
- [Partie 5 — Le Consumer (lire les captures)](#partie-5)
- [Partie 6 — La couche Qualité (le cœur du projet)](#partie-6)
- [Partie 7 — Les CLI (les outils en ligne de commande)](#partie-7)
- [Partie 8 — L'IHM de visualisation (Streamlit + DSP)](#partie-8)
- [Partie 9 — Playback + streaming ZeroMQ](#partie-9)
- [Partie 10 — GNU Radio (record / playback)](#partie-10)
- [Partie 11 — Les tests (pourquoi et comment)](#partie-11)
- [Partie 12 — Les 5 concepts d'ingénierie à retenir](#partie-12)
- [Glossaire des sigles](#glossaire)

---

<a name="partie-0"></a>
## Partie 0 — La vision : à quoi sert AeroLake

**Le problème.** Un laboratoire (LASSENA) veut **capter des signaux radio**
(GNSS, Iridium, Starlink), les **stocker proprement**, et pouvoir les **rejouer**
plus tard sur de vrais récepteurs pour les tester. Mais une captation, c'est des
**gigaoctets** de données brutes : il faut les organiser, en garantir la
**qualité**, et pouvoir les retrouver et les rejouer.

**La solution AeroLake = un « data lakehouse RF ».** Un *data lake* = un grand
réservoir où on déverse des données brutes. Un *lakehouse* ajoute par-dessus de
la **structure** (métadonnées, catalogue, qualité) — le meilleur des deux mondes.

**Le pipeline, en une ligne :**

```
Producer  →  MinIO (stockage)  →  Consumer  →  {Qualité, Catalogue, Playback, ZeroMQ, IHM}
```

- **Producer** : fabrique une capture (aujourd'hui synthétique) et l'envoie au stockage.
- **MinIO** : le réservoir (compatible Amazon S3).
- **Consumer** : relit les captures, les valide, les rejoue.

**L'objectif final** (fixé par le responsable) : *construire un dataset curé* —
c'est-à-dire un jeu de données dont **chaque capture a une qualité garantie**.
D'où l'importance centrale de la **couche Qualité**.

---

<a name="partie-1"></a>
## Partie 1 — Les fondations : RF, IQ, et le format SigMF

### Un signal radio, c'est quoi ?

Une onde électromagnétique qui oscille à une **fréquence** (en Hz). La GPS L1
oscille à **1575,42 MHz**. Un récepteur ne peut pas « tout » écouter : il se cale
sur une **fréquence centrale** (`center_freq`) et écoute une **bande passante**
(`bandwidth`) autour, par exemple ±1 MHz.

### Pourquoi des échantillons « IQ » (et pas juste des nombres) ?

Pour représenter numériquement une onde, on l'**échantillonne** : on mesure son
amplitude N fois par seconde (le **taux d'échantillonnage**, `sample_rate`, ex.
2 000 000 échantillons/s = 2 MS/s). Mais une seule mesure (un nombre réel) ne
suffit pas à décrire une onde : il manque la **phase**. On utilise donc deux
mesures par échantillon :

- **I** (*In-phase*) = la partie réelle
- **Q** (*Quadrature*) = la partie imaginaire

Un échantillon IQ est donc un **nombre complexe** `z = I + jQ`. En Python/numpy,
c'est un `complex64` (deux `float32` : 4 octets pour I + 4 pour Q = 8 octets).

Deux quantités utiles tirées d'un échantillon :
- **Magnitude** = `√(I² + Q²)` = l'amplitude instantanée (la « force » du signal).
- **Phase** = `atan2(Q, I)` = où en est l'oscillation.

> Pourquoi complexe ? Parce que ça permet de distinguer une fréquence **+100 kHz**
> d'une fréquence **−100 kHz** par rapport au centre (un signal réel ne le peut
> pas — il est « symétrique »). C'est tout l'intérêt de l'échantillonnage IQ : il
> capture toute la bande autour de la fréquence centrale.

### dBFS, RMS, SNR : mesurer la « force » d'un signal

- **dBFS** (*decibels Full Scale*) : niveau d'un signal relatif au maximum
  encodable (0 dBFS = le plus fort sans saturer). `dBFS = 20·log10(amplitude)`.
  Ex. amplitude 0,1 → −20 dBFS (un bon niveau, avec de la marge).
- **RMS** (*Root Mean Square*) : la « moyenne quadratique », c.-à-d. la puissance
  moyenne du signal. Plus robuste qu'une moyenne simple.
- **SNR** (*Signal-to-Noise Ratio*) : rapport signal/bruit, en dB. Plus c'est
  haut, plus le signal est propre.
- **Clipping** (saturation) : quand le signal dépasse le max encodable, l'info
  est **perdue** (le sommet de l'onde est « rasé »). À éviter absolument.

### SigMF : le format de fichier

[SigMF](https://github.com/sigmf/SigMF) (*Signal Metadata Format*) est un standard
pour stocker des captures RF. Une capture = **deux fichiers** :

- **`.sigmf-data`** : les octets bruts des échantillons (pour nous, du `cf32_le`
  = *complex float32 little-endian*). C'est juste I,Q,I,Q,I,Q… en binaire.
- **`.sigmf-meta`** : un fichier **JSON** qui décrit la donnée : `core:datatype`
  (cf32_le), `core:sample_rate`, `core:frequency`, `core:datetime`, etc.

> 💡 Détail clé pour la suite : comme `.sigmf-data` est juste du complex float32,
> **GNU Radio sait le lire/écrire directement** (bloc File Source/Sink en mode
> *complex*). C'est le « pont » entre AeroLake et GNU Radio.

---

<a name="partie-2"></a>
## Partie 2 — L'outillage Python

### `uv` et la structure du projet

`uv` est un gestionnaire de paquets/projets Python (rapide, moderne). Tout est
décrit dans **`pyproject.toml`** : les dépendances, les outils (ruff, mypy,
pytest), et les **entry points** (les commandes `aerolake-*`).

Structure dite **« src layout »** : le code importable vit dans `src/aerolake/`.
4 packages :
- `common/` — l'infra partagée (config, stockage, logs)
- `producer/` — la chaîne de génération
- `consumer/` — la chaîne de lecture
- `quality/` — la couche qualité
- (+ `gui/` pour l'IHM, `scripts/` pour les CLI)

### La configuration — `common/config.py`

```python
class Settings(BaseSettings):
    s3_access_key: str
    s3_secret_key: SecretStr   # ← jamais affiché dans les logs
    s3_endpoint: str
    s3_bucket: str
    s3_region: str = "us-east-1"
```

- **`pydantic-settings`** lit ces valeurs depuis les **variables
  d'environnement** préfixées `AEROLAKE_` (et le fichier `.env` en local). Si une
  valeur manque, ça **plante au démarrage** (fail-fast) plutôt qu'au milieu du
  traitement — c'est voulu.
- **`SecretStr`** : le mot de passe est « emballé » pour ne **jamais fuiter** dans
  un log ou une trace d'erreur.
- **`get_settings()`** est mis en cache (`lru_cache`) : le `.env` n'est lu qu'une
  fois par processus.

### Les logs — `common/logging.py`

On utilise **structlog** (logs structurés). Point important : `configure_logging()`
envoie les logs vers **stderr**, pas stdout. Pourquoi ? Pour que **stdout reste
propre** pour le *résultat* du programme (un tableau, du JSON) — convention Unix.
Sinon, `aerolake-list --json | jq` serait pollué par les lignes de log.

---

<a name="partie-3"></a>
## Partie 3 — La couche stockage — `common/storage.py`

C'est **le point de passage unique** vers le stockage : *toute* lecture/écriture
passe par `StorageClient`. (Concept clé, voir Partie 12.)

### MinIO et S3

**S3** (*Simple Storage Service*) est le service de stockage d'objets d'Amazon.
Un *objet* = un fichier + une clé (son « chemin »). **MinIO** est un serveur
**compatible S3** qu'on héberge soi-même. L'avantage : le **même code** marche
sur MinIO local, MinIO du labo, ou AWS — on change juste l'URL.

```python
def _build_client(self):
    kwargs = {... clés ...}
    if self._settings.s3_endpoint:        # endpoint défini → MinIO
        kwargs["endpoint_url"] = self._settings.s3_endpoint
    return boto3.client("s3", **kwargs)   # endpoint vide → AWS (et moto en test)
```

- On utilise **boto3** (le SDK AWS officiel) plutôt que le SDK MinIO → portable
  (ADR-001). MinIO exige `signature_version="s3v4"` + *path-style addressing*.
- **`StorageError`** : une exception « maison » qui enveloppe toutes les erreurs
  de stockage, pour que l'appelant n'ait qu'un seul type à gérer.

### Les méthodes importantes

- `health_check()` — le bucket est-il joignable ? (`head_bucket`)
- `upload_bytes(key, data, metadata=, tags=)` — écrire un objet, avec des
  **métadonnées** (en-têtes `x-amz-meta-*`) et des **tags**.
- `download_bytes(key)` — lire un objet entier.
- `get_object_metadata(key)` / `get_object_tags(key)` — lire métadonnées/tags
  **sans télécharger le corps** (requête HEAD, quasi gratuite).
- `list_objects(prefix)` — lister les clés (paginé automatiquement).
- **`update_tags(key, tags)`** — ⚠️ **REMPLACE** tout le jeu de tags (l'API S3
  `PutObjectTagging` écrase tout). Pour changer **un** tag, il faut **lire →
  fusionner → réécrire**. Oublier la fusion efface les autres tags !

### Métadonnées vs Tags (ADR-003)

Deux mécanismes S3, deux usages :
| | Métadonnées (`x-amz-meta-*`) | Tags |
|---|---|---|
| Pour quoi | valeurs **techniques/continues** (sample_rate, freq) | valeurs **catégorielles** (signal-type, quality) |
| Lecture | HEAD (gratuit) | `get_object_tagging` |
| Avantage | inspection rapide sans télécharger | **indexables**, pilotent le cycle de vie |

Les deux sont attachés **uniquement au `.sigmf-data`** (le `.sigmf-meta` est sa
propre description, en JSON).

---

<a name="partie-4"></a>
## Partie 4 — Le Producer

Trois étapes : **générer → encoder → uploader**.

### 4a. Générer — `producer/synthetic.py`

```python
def generate_tone(duration_s, sample_rate, center_freq,
                  tone_offset_hz=100_000, tone_amplitude=0.1, snr_db=20, seed=None):
    ...
    tone = tone_amplitude * np.exp(2j*np.pi*tone_offset_hz*t)   # une sinusoïde complexe
    noise = ...                                                  # bruit gaussien (AWGN)
    samples = tone + noise
```

- `np.exp(2j·π·f·t)` = un **tone pur** (une seule fréquence) en complexe.
- `tone_amplitude=0.1` → −20 dBFS : un niveau réaliste avec de la marge (pas de
  clipping). *C'est la couche qualité qui a révélé qu'on saturait au début !*
- On ajoute du **bruit blanc gaussien** (AWGN) calibré pour atteindre le SNR voulu.
- `seed` rend le bruit **reproductible** (essentiel pour les tests).

### 4b. Encoder en SigMF — `producer/sigmf_writer.py`

`encode(signal)` produit deux flux d'octets :
- `data_bytes = signal.samples.tobytes()` — les échantillons en binaire.
- `meta_bytes` — le JSON SigMF, **validé** contre le schéma SigMF (si la structure
  est fausse, ça plante **ici**, avant tout upload).

### 4c. Orchestrer — `producer/orchestrator.py`

`capture_and_upload(...)` colle tout :

```
session_id = uuid court (8 hex)
clé = {signal_type}/{YYYY-MM-DD}/{session_id}/capture.sigmf-{data,meta}
```

Deux subtilités importantes :
1. **L'ordre d'upload** : on envoie le `.sigmf-meta` **AVANT** le `.sigmf-data`.
   Ainsi, si un consumer arrive pile entre les deux, il voit le JSON
   (interprétable) plutôt que des octets orphelins.
2. Les **tags initiaux** : `quality=raw` (+ signal-type, recorder, hardware). Le
   `raw` veut dire « pas encore validé ».

---

<a name="partie-5"></a>
## Partie 5 — Le Consumer — `consumer/reader.py`

`CaptureReader` propose 3 niveaux d'accès, du moins cher au plus cher :

1. **`list_captures(prefix)`** — liste les captures **complètes** (les deux
   fichiers présents ; les orphelins sont ignorés).
2. **`inspect(key)`** — métadonnées + tags **sans télécharger** les octets (HEAD).
   « Est-ce que cette capture m'intéresse ? »
3. **`read(key)`** — télécharge et **décode** : transforme les octets en tableau
   numpy `complex64` en lisant le `core:datatype` du JSON.

Et la méthode d'orchestration **`validate()`** (le lien avec la qualité) :
```
read → QualityChecker.check → (écrire quality_report.json) → promouvoir le tag quality
```
Elle suit le **lire → fusionner → réécrire** pour le tag (voir Partie 3).
Deux interrupteurs : `store_report` et `promote_tag` (mis à False ensemble = un
*dry-run* qui ne modifie rien).

---

<a name="partie-6"></a>
## Partie 6 — La couche Qualité (le cœur du projet)

C'est **la** raison d'être d'AeroLake. Deux fichiers, deux responsabilités.

### 6a. Les métriques — `quality/metrics.py` (fonctions PURES)

Des fonctions **pures** : elles prennent des échantillons, renvoient un nombre,
**sans effet de bord** (pas d'I/O, pas de log, pas de décision). Donc triviales à
tester. Les 6 métriques :

| Fonction | Mesure | Pourquoi ça compte |
|---|---|---|
| `compute_clipping_ratio` | % d'échantillons saturés | clipping = info perdue |
| `compute_rms_power_dbfs` | puissance moyenne (dBFS) | trop fort/faible = inexploitable |
| `count_invalid_samples` | nb de NaN/Inf | corruption, un seul NaN pollue une FFT |
| `compute_dc_offset_iq` | biais constant sur I et Q | défaut matériel courant (RTL-SDR) |
| `compute_sample_completeness` | échantillons reçus / attendus | détecte les échantillons perdus |
| `validate_sigmf_metadata` | champs SigMF requis présents | sans métadonnées, donnée illisible |

### 6b. Le checker — `quality/checker.py`

- **`QualityThresholds`** : les seuils (clipping max, dBFS min/max, etc.),
  **configurables** (un usage SETI veut plus propre qu'un usage GNSS).
- **`QualityChecker.check(...)`** : calcule les 6 métriques, les compare aux
  seuils, **accumule toutes les violations** (pas juste la première), et produit
  un **`QualityReport`** (toutes les mesures + un verdict `is_valid` + la liste
  des échecs).

C'est ce verdict qui fait passer une capture de `raw` à `validated` ou `rejected`
(cycle de vie, ADR-005).

---

<a name="partie-7"></a>
## Partie 7 — Les CLI — `scripts/`

6 outils en ligne de commande, tous bâtis sur le même moule :
- **`argparse`** pour les arguments, **`rich`** pour l'affichage (tableaux colorés).
- **Codes de sortie documentés** : `0` ok / `1` erreur stockage / `2` config ou
  inattendu. (Une convention Unix : un script qui appelle le nôtre sait réagir.)
- Tous appellent `configure_logging()` d'abord (logs → stderr).
- Un **« seam » d'injection** (`reader=`, `player=`…) pour les tests.

| CLI | Rôle |
|---|---|
| `aerolake-healthcheck` | vérifier que MinIO répond |
| `aerolake-producer` | générer + uploader une capture |
| `aerolake-validate` | **curer** tout un préfixe (valider en masse) |
| `aerolake-list` | **cataloguer** : lister/filtrer par tag (HEAD only) |
| `aerolake-play` | rejouer une capture à sa cadence |
| `aerolake-stream` | publier les frames sur ZeroMQ |

---

<a name="partie-8"></a>
## Partie 8 — L'IHM de visualisation — `gui/` (ADR-006)

Une app web **Streamlit** + graphes **Plotly**. Même philosophie « logique pure
vs glue » :

- **`plots.py` (PUR)** : le DSP (*Digital Signal Processing*). Trois vues :
  - **Spectre (FFT/Welch)** : la **FFT** (*Fast Fourier Transform*) transforme le
    signal du domaine **temps** vers le domaine **fréquence** → on voit quelles
    fréquences portent de l'énergie. La méthode de **Welch** moyenne plusieurs
    FFT pour lisser le bruit.
  - **Spectrogramme (STFT)** : la FFT **au cours du temps** (on glisse une
    fenêtre) → une image temps×fréquence×puissance.
  - **Constellation** : chaque échantillon en point (I, Q) → un tone = un anneau,
    une modulation numérique = des amas.
  - `describe_signal()` : une phrase en français simple (« énergie la plus forte
    vers X MHz, Y dB au-dessus du bruit »).
- **`theme.py`** : tout le style « mission control » (couleurs, polices) au même endroit.
- **`app.py` (GLUE)** : la partie Streamlit, **fine**. Lit via `CaptureReader`
  (jamais S3 directement), met en cache les lectures (`@st.cache_data`), propose
  les vues en onglets et un **mode « Explain »** pour les non-initiés.

---

<a name="partie-9"></a>
## Partie 9 — Playback + streaming ZeroMQ

### Playback logiciel — `consumer/player.py` (ADR-007)

`CapturePlayer` rejoue une capture en **frames** (paquets de N échantillons),
**à la cadence d'enregistrement** : entre deux frames, il attend
`frame_size / sample_rate` secondes. Astuce de test : l'**horloge est injectée**
(`sleep=`), donc on teste la logique de cadence **sans attendre** réellement.

`iter_frames()` (découpage) est une fonction **pure** → testable seule. Un hook
`on_frame(index, frame)` permet de brancher un consommateur.

### Streaming ZeroMQ — `consumer/stream.py` (ADR-008)

**ZeroMQ** est une bibliothèque de messagerie. Le motif **PUB/SUB**
(*Publish/Subscribe*) : un **publisher** émet des messages, N **subscribers**
les reçoivent (filtrés par *topic*). Parfait pour « un flux, plusieurs
consommateurs ».

`FramePublisher.publish(index, frame)` a **exactement** la signature de
`on_frame` → on les branche directement :
```python
player.play(key, on_frame=publisher.publish)
```
Format de message (3 parties) : `[topic, header JSON, octets IQ]`.
`encode_frame`/`decode_frame` sont **purs** (testés sans réseau).

---

<a name="partie-10"></a>
## Partie 10 — GNU Radio — `gnuradio/` (ADR-007 couche 2)

**GNU Radio** est un logiciel de traitement de signal par **flowgraphs** (on relie
des blocs : source → traitement → sortie). On l'édite avec **GRC** (*GNU Radio
Companion*).

- **`record.grc`** : source → File Sink → `.sigmf-data` (enregistrer).
- **`playback.grc`** : File Source → Throttle (cadence) → affichage spectre/waterfall.

**Le pont** : nos `.sigmf-data` sont du `cf32`, que les blocs File Source/Sink de
GNU Radio lisent/écrivent nativement → **pas besoin de module SigMF**. On l'a
**prouvé** : un fichier écrit par `record.grc` est relu par le DSP d'AeroLake (pic
spectral à la bonne fréquence).

⚠️ **Pour émettre vraiment par les ondes** (couche 3), il faut un SDR
**émetteur** : le **BladeRF** (le RTL-SDR est récepteur seulement).

---

<a name="partie-11"></a>
## Partie 11 — Les tests (pourquoi et comment)

110 tests, lancés par **pytest**. Trois idées :

1. **moto** simule S3 **en mémoire** → aucun MinIO réel nécessaire, tests rapides
   et isolés. Quand `s3_endpoint=""`, boto3 vise « AWS » → c'est moto qui
   intercepte.
2. Les **fixtures** (`tests/conftest.py`) : `test_settings` (config de test),
   `mock_s3` (S3 simulé avec le bucket créé), `storage_client` (un client câblé
   dessus). Une fixture *autouse* route les logs vers stderr.
3. **Pourquoi tester puisque « ça marche déjà » ?** (la question piège du tuteur)
   - **Non-régression** : on change du code → les tests confirment qu'on n'a rien
     cassé ailleurs.
   - **Environnement isolé** : moto = pas de dépendance externe → reproductible.
   - **Préparation à la CI** : à chaque `git push`, **GitHub Actions** relance
     ruff + mypy + pytest automatiquement.

Outils qualité : **ruff** (lint + format), **mypy** (vérif de types), **pytest**.

---

<a name="partie-12"></a>
## Partie 12 — Les 5 concepts d'ingénierie à retenir

Si tu ne devais retenir que 5 choses pour défendre le projet :

1. **Point de passage unique (chokepoint).** *Tout* le stockage passe par
   `StorageClient`. Changer de backend (MinIO local → labo → AWS) = changer une
   URL, pas le code.

2. **Fonctions pures vs glue.** La logique « intelligente » (métriques qualité,
   DSP) est dans des **fonctions pures** (sans I/O), donc **testable** facilement.
   Les CLI/l'IHM ne sont qu'une fine **couche de glue** par-dessus.

3. **Injection de dépendances (DI).** On *passe* les dépendances (le client de
   stockage, le reader, l'horloge…) au lieu de les créer en dur. Ça rend tout
   **testable** (on injecte un faux) et **flexible**.

4. **Fail-fast + erreurs maison.** La config plante au démarrage si mal réglée ;
   toutes les erreurs de stockage sont une seule `StorageError`. On échoue tôt et
   clairement.

5. **Décisions tracées (ADR).** Chaque choix d'architecture important a un
   *Architecture Decision Record* (`docs/adr/`). On sait **pourquoi** le code est
   comme il est — et on n'efface jamais une décision, on en ajoute une nouvelle.

---

<a name="glossaire"></a>
## Glossaire des sigles

| Sigle | Signification | En clair |
|---|---|---|
| **RF** | Radio Frequency | les ondes radio |
| **SDR** | Software-Defined Radio | une radio pilotée par logiciel (RTL-SDR, BladeRF) |
| **IQ** | In-phase / Quadrature | les 2 composantes d'un échantillon complexe |
| **SigMF** | Signal Metadata Format | le format de fichier des captures (data + meta) |
| **cf32_le** | complex float32 little-endian | le type binaire des échantillons |
| **DSP** | Digital Signal Processing | traitement du signal numérique |
| **FFT** | Fast Fourier Transform | passe du temps à la fréquence (le spectre) |
| **STFT** | Short-Time FFT | FFT au cours du temps (le spectrogramme) |
| **dBFS** | decibels Full Scale | niveau relatif au max encodable |
| **RMS** | Root Mean Square | puissance moyenne |
| **SNR** | Signal-to-Noise Ratio | rapport signal/bruit |
| **AWGN** | Additive White Gaussian Noise | bruit blanc gaussien |
| **GNSS** | Global Navigation Satellite System | GPS, Galileo… (L1 = 1575,42 MHz) |
| **S3** | Simple Storage Service | le stockage objet d'Amazon (MinIO est compatible) |
| **HEAD** | (requête HTTP) | lire les en-têtes/métadonnées sans le corps |
| **CLI** | Command-Line Interface | un outil en ligne de commande |
| **CI** | Continuous Integration | tests auto à chaque push (GitHub Actions) |
| **DI** | Dependency Injection | passer les dépendances au lieu de les créer en dur |
| **ADR** | Architecture Decision Record | une fiche qui trace une décision d'archi |
| **PUB/SUB** | Publish / Subscribe | motif de messagerie ZeroMQ (1 émetteur, N récepteurs) |
| **GRC** | GNU Radio Companion | l'éditeur visuel de flowgraphs GNU Radio |
| **uv** | (nom propre) | le gestionnaire de paquets Python du projet |

---

*Pour les décisions d'architecture détaillées, voir `docs/adr/`. Pour le contexte
projet (objectifs, personnes, roadmap), voir `docs/context/historique-discussions.md`.*
