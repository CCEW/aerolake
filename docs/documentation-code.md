# AeroLake — Documentation complète du code

> **But** : comprendre et pouvoir modifier **chaque partie** du code. Ce document
> descend au niveau des classes et fonctions. Pour la vue d'ensemble en une page,
> lire d'abord `carte-du-code.md` ; pour le *pourquoi* des choix, `docs/adr/`.

---

## 1. Vue d'ensemble

Le pipeline est **Producer → MinIO → Consumer**, avec une interface web par-dessus :

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

**Principes de conception (à respecter en modifiant le code)**
1. **Un seul point d'accès S3** : tout passe par `StorageClient` (ADR-001).
   Jamais de boto3 ailleurs.
2. **Injection de dépendances partout** : le SDR (`device_opener`), gpsd
   (`reader`), l'horloge du player (`sleep`), les sockets ZeroMQ, le
   `storage_client` des CLIs — tout est injectable, donc testable sans
   matériel ni serveur.
3. **Préparer ≠ stocker** : `prepare_capture()` fabrique tout en mémoire,
   `push_capture()` téléverse. Entre les deux, l'humain décide.
4. **Les erreurs remontent typées** : `StorageError`, `ConfigError` — les CLIs
   les attrapent et sortent avec des codes documentés (0 ok, 1 stockage,
   2 config, 3 capture/inattendu).
5. **Code pédagogique** : la densité de commentaires est volontaire.

## 2. Structure du dépôt

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

## 3. Conventions transverses (les invariants du projet)

- **Disposition du bucket** :
  `{signal_type}/{YYYY-MM-DD}/{YYYY-MM-DD_HHhMMmSS}_{source}_{id8}/capture.*`
  — date parent en UTC (tri stable), dossier feuille en heure locale
  (lisible), `id8` = 8 hex aléatoires (anti-collision). Une capture est
  « complète » si `.sigmf-data` **et** `.sigmf-meta` existent ; les orphelins
  sont ignorés par `list_captures`.
- **Métadonnées vs tags (ADR-003)** : valeurs techniques/continues → en-têtes
  HTTP `x-amz-meta-*` (lisibles par HEAD sans télécharger) ; valeurs
  catégorielles/cherchables → **tags S3** (`signal-type`, `operator`,
  `hardware`, `location`…). Les deux **uniquement sur le `.sigmf-data`**.
- **Ordre d'upload** : `.sigmf-meta` **avant** `.sigmf-data` — un lecteur qui
  arrive entre les deux voit du JSON interprétable, pas des octets orphelins.
- **`update_tags` = REMPLACEMENT total** (API S3) : pour changer UN tag, lire →
  fusionner → réécrire, sinon on efface les autres.
- **Datatype** : tout est normalisé **`cf32_le`** (complex64 little-endian,
  8 octets/échantillon). C'est aussi ce que lit GNU Radio nativement.
- **Bascule d'endpoint (ADR-001/020)** : `s3_endpoint` vide → AWS réel (ce que
  moto intercepte en test) ; renseigné → MinIO/S3 compatible. Migrer de
  stockage = changer le `.env`, zéro code.

---

## 4. Référence par module

### 4.1 `common/config.py` — les réglages
- **`Settings(BaseSettings)`** — champs `s3_access_key`, `s3_secret_key`
  (**`SecretStr`** : jamais en clair dans les logs), `s3_endpoint`, `s3_bucket`,
  `s3_region`. Chargés depuis les variables d'env `AEROLAKE_*` et le `.env`
  (pydantic-settings) ; les vraies variables d'env priment sur le fichier.
- **`get_settings()`** — accès **caché** (`lru_cache`) : le `.env` n'est parsé
  qu'une fois par process. Toujours passer par elle.

### 4.2 `common/logging.py` — logs propres
- **`configure_logging(level)`** — structlog → **stderr**, pour laisser stdout
  aux résultats (`--json`, tableaux). Chaque CLI l'appelle en premier.
- `_StderrLogger` — résout stderr **à l'appel** (pas à l'import), pour que les
  redirections de test fonctionnent.

### 4.3 `common/storage.py` — LE point d'accès S3 (430 l.)
- `_safe_tag_value(value)` / `_tagging_header(tags)` — assainissent les tags
  (S3 n'accepte que lettres/chiffres/` +-=._:/@` ; le reste devient `_`) et
  construisent l'en-tête `Tagging` URL-encodé. *(Né d'un bug réel : une
  virgule dans `location` faisait échouer tout l'upload.)*
- **`StorageError`** — l'exception unique de la couche stockage.
- **`StorageClient`** — méthodes :
  - `health_check()` — bucket joignable et accessible ;
  - `object_exists(key)`, `object_size(key)` (HEAD, zéro octet de corps),
    `get_object_metadata(key)`, `get_object_tags(key)`, `update_tags(key, tags)` ;
  - `upload_bytes(key, data, content_type, *, metadata, tags)` — petit objet
    en un PUT ;
  - **`upload_multipart(key, chunks, …, part_size=8 Mio)`** (ADR-010) — upload
    d'un **flux** de chunks sans tout charger en RAM ; agrège jusqu'à
    `part_size` puis envoie ; renvoie le total d'octets ; nettoie (abort) en
    cas d'échec ;
  - `download_bytes(key)`, **`download_range(key, start, end)`** (ADR-009) —
    l'accès partiel qui rend le « seek » possible sur des captures de Go ;
  - `list_objects(prefix)` (paginé), `delete_object(key)`.

### 4.4 `producer/synthetic.py` — le signal de test
- **`generate_tone(duration_s, sample_rate, center_freq, tone_offset_hz, tone_amplitude, snr_db, seed)`**
  → **`SyntheticSignal`** (samples complex64, sample_rate, center_freq,
  description). Sinusoïde complexe décalée de `tone_offset_hz` + bruit AWGN
  dosé par `snr_db` ; `seed` rend la capture **reproductible**.
- `SyntheticParams` — le bloc « source synthétique » côté orchestrateur.

### 4.5 `producer/soapy_source.py` — le vrai SDR (591 l., ADR-015)
- `list_devices()` — énumère les SDR visibles par SoapySDR.
- **`SdrRecorder`** — l'objet qui possède le **cycle de vie complet** du
  périphérique : `open()` → `configure(sample_rate, center_freq)` → `start()`
  → `read(n)` → `stop()` → `close()`, utilisable en `with`. Points clés :
  - **`device_opener` injectable** : les tests fournissent un faux device —
    tout le recorder est testé sans matériel ;
  - `configure()` **relit les valeurs effectives** (le matériel arrondit :
    on stocke ce qui a vraiment été appliqué, pas ce qu'on a demandé) ;
  - `read()` compte les **overflows** (échantillons perdus) — remontés
    jusqu'aux métadonnées ;
  - propriétés de provenance : `serial`, `hardware_info`, `effective_*`.
  - `capture(duration_s, sample_rate, center_freq)` → **`SdrCapture`**
    (samples + provenance complète : driver, série, gain, antenne, overflows).
- `capture_from_sdr(…)` — shim fonctionnel rétro-compatible au-dessus du
  recorder ; c'est lui qu'appelle l'orchestrateur.
- `SoapyParams(driver, agc, antenna)` — le bloc « source SDR ».

### 4.6 `producer/gps.py` — position live via gpsd (ADR-016)
- **`read_geolocation(reader=None)`** — lit UN rapport TPV de gpsd et renvoie
  un Point GeoJSON `core:geolocation` **ou `None` si pas de fix** (jamais de
  position inventée) ; lève si gpsd est injoignable alors qu'on l'a demandé.
- `GpsFix` (+ `fix_from_tpv`, `fix_to_geolocation`) — normalisation d'un TPV :
  `has_fix` (2D+, lat *et* lon), `is_3d` (altitude fiable). Évite le « piège
  GPSD » : ordre `[lon, lat, alt]` du GeoJSON respecté, pas de dump brut.
- `reader` injectable → conversion testée sans démon.

### 4.7 `producer/capture_config.py` — le schéma de config (pydantic)
- `_StrictModel` — `extra="forbid"` : **toute clé inconnue est rejetée**
  (attrape les fautes de frappe à la validation, pas au runtime).
- **`CaptureConfig`** — la requête complète : quoi (`signal_type`,
  `center_freq`, `sample_rate`, `duration_s`), comment (`source`, union
  discriminée par `type` → `SyntheticSourceConfig` | `SoapySourceConfig`),
  descriptif (`author`, `description`, `license`, `operator`), où
  (`LocationConfig`), plus `AnnotationConfig` et `AntennaConfig` optionnels.
  `source_params()` traduit le bloc source en objet orchestrateur.
- Validations croisées à connaître : `location.gps` **exclusif** avec la
  géoloc manuelle ; `freq_lower_edge`/`freq_upper_edge` **par paire** (règle
  SigMF) ; `GeolocationConfig.to_geojson()` émet l'ordre **[lon, lat, alt]**.
- Les valeurs **calculées** (datatype, version, datetime, sha512…) ne sont
  PAS dans la config : l'encodeur les remplit à la capture.

### 4.8 `producer/config_loader.py` — TOML/JSON → config validée
- **`load_capture_config(path)`** → `CaptureConfig`. Choix du parseur par
  l'extension : `.toml` → `tomllib` (stdlib), sinon JSON. Trois familles
  d'échec (fichier absent, syntaxe invalide, schéma violé) → **une** exception
  lisible : **`ConfigError`** (le CLI l'affiche sans traceback, exit 2).

### 4.9 `producer/sigmf_writer.py` — l'encodage SigMF
- **`encode(signal, *, author, recorder, hardware, signal_type, …)`** →
  **`SigMFCapture(data_bytes, meta_bytes)`**. Écrit le Global SigMF complet :
  `core:datatype=cf32_le`, `core:version` (tirée de `sigmf.__specification__`,
  pas codée en dur), `core:sample_rate`, **`core:sha512`** (intégrité),
  `core:num_channels`, `core:offset`, auteur/description/licence, les champs
  `aerolake:*` (signal_type, operator, location, mobile, hardware_info,
  overflows), la **géolocalisation** dans le segment captures, l'**annotation**
  unique (label/commentaire/bords de bande + pointage antenne) et l'extension
  **`antenna:`** (champs scalaires ; le pointage — polarization/azimuth/
  elevation — va dans l'annotation, conformément à la spec).
- `EncodableSignal` (Protocol) — le contrat minimal d'une source :
  `samples`, `sample_rate`, `center_freq`, `description`. C'est ce qui rend
  l'encodeur **agnostique de la source**.
- `AnnotationFields` / `AntennaFields` (TypedDict) — les dictionnaires aplatis
  que l'encodeur accepte.

### 4.10 `producer/orchestrator.py` — le chef d'orchestre (457 l.)
- **`prepare_capture(*, signal_type, duration_s, sample_rate, center_freq, source, …, rich)`**
  → **`PreparedCapture`**. Enchaîne : résolution de la source (type de
  `source` → synthétique ou SDR) → acquisition → `encode()` → construction
  des clés (disposition du bucket, cf. §3), des en-têtes `x-amz-meta-*`
  (sample-rate, center-freq, session-id, datatype, sample-count) et des
  **tags** (signal-type, operator, mobile, recorder, hardware, + provenance
  SDR : sdr-serial/sdr-gain/sdr-antenna, + location et antenna-model promus).
  **Rien n'est stocké.** `operator` par défaut = login système.
- **`push_capture(prepared, storage_client=None, *, with_preview=False)`** →
  `CaptureResult`. Upload méta **puis** data ; si `with_preview`,
  `_upload_preview()` rend le PNG et le range à côté — **best-effort** (un
  échec d'aperçu ne fait jamais échouer la capture).
- `save_capture_locally(prepared, root="captures")` — même arborescence que le
  bucket, sur disque (la branche « garder en local »).
- `capture_and_upload(…)` — les deux d'un coup (utilisé par les tests).
- `RichMetadata` — le paquet descriptif optionnel (author, description,
  license, geolocation, annotation, antenna) que la CLI/le GUI construisent
  depuis la config ; l'orchestrateur ne connaît pas `CaptureConfig`.

### 4.11 `producer/ingest.py` — entrer un enregistrement existant
- **`ingest_files(*, file_paths, signal_type, sample_rate, center_freq, datatype="cf32", …)`**
  → `IngestResult`. Ingestion **en flux** : lit le(s) fichier(s) par chunks,
  convertit `cu8`/`cs16`/`cs32` → **cf32 normalisé**, calcule le sha512 au fil
  de l'eau, pousse via `upload_multipart` (RAM bornée quelle que soit la
  taille), puis écrit le `.sigmf-meta`. Multi-fichiers = **un seul** capture
  continu (cas RFSoC `RX0_pkt_*.bin`, concaténés en ordre numérique).
- `ingest_file(…)` — enveloppe mono-fichier.
- C'est **le pont d'entrée GNU Radio → lakehouse** (ADR-019).

### 4.12 `producer/preview.py` — l'aperçu visuel
- **`render_spectrum_png(samples, sample_rate, center_freq)`** → octets PNG :
  PSD (spectre) en haut, spectrogramme (waterfall) en bas. Import matplotlib
  **paresseux** + backend Agg (pas d'écran requis). Sous-échantillonne au-delà
  de ~2 M d'échantillons pour rester rapide.

### 4.13 `consumer/reader.py` — relire le lakehouse
- **`CaptureReader`** :
  - `list_captures(prefix)` — toutes les captures **complètes** (paire
    data+meta), triées ; les orphelins sont ignorés ;
  - `inspect(data_key)` → `CaptureInfo` — métadonnées + tags **sans
    télécharger un octet** de signal (HEAD + GetObjectTagging) ;
  - `read(data_key)` → `CaptureContent` — tout le signal décodé (le
    `core:datatype` du méta choisit le dtype numpy) ;
  - **`read_segment(data_key, start_s, duration_s)`** — LA lecture partielle
    (ADR-009) : secondes → échantillons → octets, puis `download_range` ; une
    fenêtre hors bornes est tronquée proprement (array vide si au-delà).

### 4.14 `consumer/player.py` — replay logiciel cadencé (ADR-007 n.1)
- `iter_frames(samples, frame_size)` — découpage pur en trames.
- **`CapturePlayer.play(data_key, *, frame_size=4096, realtime=True, start_s, duration_s, on_frame)`**
  → `PlaybackStats`. Émet les trames **au rythme du sample rate d'origine**
  (`frame_size / sample_rate` s entre trames) ; `realtime=False` pour aller
  au plus vite ; `on_frame(index, frame)` = le point de branchement (c'est là
  que se greffe le publisher ZeroMQ). L'horloge (`sleep`) est **injectée** →
  les tests vérifient la cadence sans attendre réellement.

### 4.15 `consumer/stream.py` — le bus ZeroMQ (ADR-008)
- `encode_frame(topic, header, samples)` / `decode_frame(parts)` — le format
  filaire **pur** (3 parties : topic, header JSON, octets complex64) ; testé
  sans réseau.
- **`FramePublisher.bind("tcp://*:5555", topic, …)`** — PUB ; `publish(index,
  frame)` a la signature de `on_frame` → se branche directement sur le player.
- **`FrameSubscriber.connect(address, topic)`** — SUB ; `recv()` → trame
  décodée. Sockets injectables.

### 4.16 `consumer/collection.py` — les SigMF Collections (ADR-014)
- **`CollectionBuilder`** : `scan(prefix)` (paires + orphelins **signalés**),
  `build(*, prefix, name, description, author)` → **`CollectionPlan`** (le
  document assemblé, streams nommés en **relatif** au préfixe, hash sha512 du
  méta de chaque Recording — rien d'écrit : c'est le `--dry-run` naturel),
  `write(plan)` (upload du `.sigmf-collection` à la racine du préfixe).

### 4.17 `gui/app.py` — l'interface web (Streamlit)
Façade **sans logique de capture propre**. À savoir pour la maintenir :
- Streamlit **ré-exécute tout le script à chaque interaction** ; ce qui doit
  survivre (la capture préparée) vit dans `st.session_state`.
- Onglet **Capture** : `_load_uploaded` (fichier uploadé → fichier temp →
  `load_capture_config`, même chemin de validation que la CLI) →
  `_location_picker` (carte folium ; un clic renvoie un Point GeoJSON qui
  **remplace** la géoloc de la config ; se dégrade en silence hors-ligne) →
  `_do_capture` (réutilise `_resolve_geolocation` + `_build_rich_metadata` de
  la CLI puis `prepare_capture`) → `_render_result` (métriques, spectre via
  `_spectrum_png`, boutons Pousser/Garder/Jeter).
- Onglet **Playback** : `_render_playback` — `CaptureReader.list_captures` →
  `inspect` → aperçu PNG stocké → scrub (`read_segment` + `render_spectrum_png`)
  → commande ZeroMQ affichée → export `.sigmf-meta`/`.sigmf-data` (gros
  fichiers : renvoi vers la console MinIO au-delà de 100 Mo).
- Esthétique : CSS injecté (`_CSS`) + fond WebGL **ColorBends** (three.js dans
  un `st.iframe` épinglé plein écran par le sélecteur `iframe[srcdoc]`).
  Le thème de base vit dans `.streamlit/config.toml`.
- `run()` = point d'entrée `aerolake-gui` (sert sur `0.0.0.0`).

### 4.18 `scripts/` — les 8 CLIs
Tous : `configure_logging()` d'abord, sortie `rich`, **codes de sortie
documentés** (0 ok / 1 stockage / 2 config / 3 capture-inattendu), et une
dépendance injectable pour les tests.
- **`capture.py`** — `--config x.toml` : charge/valide, récap, résout la
  géoloc (`_resolve_geolocation` : gpsd si `gps=true`, sinon point manuel,
  sinon rien), aplatit (`_build_rich_metadata` — y répartit le pointage
  antenne vers l'annotation, règle SigMF), capture, récap post-capture, puis
  **confirmation** Pousser / Garder local / Jeter.
- **`healthcheck.py`** — `.env` + MinIO joignable + bucket accessible ;
  `--json` pour scripter.
- **`ingest.py`** — fichier **ou dossier** (tri « naturel » des `RX0_pkt_N` via
  `_natural_key`) → `ingest_files`.
- **`catalog.py`** (`aerolake-list`) — liste/filtre par tags (`--signal-type`,
  `--hardware`, `--tag k=v`) en requêtes HEAD uniquement.
- **`collection.py`** — grouper un préfixe ; `--dry-run` ; sortie JSON stable.
- **`play.py`** — `--key` ou `--prefix` (prend la plus récente) ; `--start/
  --duration` (lecture partielle) ; `--no-realtime`.
- **`stream.py`** — player + `FramePublisher` ; `--bind tcp://*:5555`,
  `--topic`.
- **`subscribe.py`** — s'abonne, affiche l'en-tête et le RMS dBFS de chaque
  trame (`_rms_dbfs` : « y a-t-il du signal ? » en un chiffre).

## 5. Les tests (27 fichiers, ~210 tests)

- **moto** simule S3 : `tests/conftest.py` fournit `test_settings`
  (`s3_endpoint=""` → moto intercepte ; valeurs passées en kwargs pour être
  isolé du `.env` du développeur), `mock_s3` (bucket pré-créé) et
  `storage_client` (un `StorageClient` branché dessus). **Injecter ces
  fixtures**, ne jamais taper un vrai backend dans les tests unitaires.
- Le matériel est simulé par l'injection : faux `device_opener` (SoapySDR),
  faux `reader` (gpsd), faux `sleep` (cadence du player), faux sockets (ZMQ),
  stubs `prepare/push` (CLIs).
- `tests/test_examples_valid.py` **globe** `examples/*.toml|json` : un modèle
  qui dérive du schéma casse la CI.
- `tests/gui/` : smoke test Streamlit **AppTest** (sauté sans l'extra gui).
- `tests/integration/` (marqueur `integration`, opt-in
  `AEROLAKE_RUN_INTEGRATION=1`) : aller-retour **réel** multipart + Range +
  tagging — sert aussi de **test de conformité** pour tout stockage S3
  candidat (c'est lui qui a validé SeaweedFS, ADR-020).

## 6. La CI (`.github/workflows/ci.yml`)

Deux jobs : **lint + types + tests** (`ruff check`, `mypy src`,
`pytest -m "not integration"`, deps gelées par `uv sync --frozen`) et
**intégration** (conteneur MinIO réel + `pytest -m integration`).

## 7. Pour aller plus loin

- `docs/carte-du-code.md` — le chemin d'une capture en 6 fichiers (commencer là).
- `docs/adr/001…020` — chaque décision structurante, datée et argumentée.
- `docs/manuel-utilisateur.md` — le mode d'emploi côté utilisateur.
- `HANDOFF.md` — installer un poste, migrer vers FAST/GitLab, reprendre le projet.
