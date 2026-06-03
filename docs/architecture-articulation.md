# Articulation du code AeroLake — comment tout s'enchaîne (A → Z)

> Ce document explique **comment les briques s'appellent entre elles** et les
> **parcours de données** réels, pour comprendre et défendre le projet à l'oral.
> Pour les *fondamentaux* (RF, IQ, SigMF, dBFS…), voir [`cours-aerolake.md`](cours-aerolake.md).

## 1. Vue d'ensemble

```
                ┌─────────────────────── AeroLake ───────────────────────┐
  RF / fichiers │  Producer ─┐                          ┌─ Consumer       │
  ──────────────┤            ├──►  MinIO (lac S3)  ──────┤   reader/player │──► IHM, ZeroMQ,
  SDR / RFSoC   │  Ingest ───┘   (.sigmf-data + meta     │   quality       │    playback,
                │                 + tags x-amz-meta)     └─ analysis(.h5)  │    GNU Radio
                └──────────────────────────────────────────────────────────┘
```

- **6 packages** sous `src/aerolake/` : `common` (infra), `producer`, `consumer`,
  `quality`, `gui`, `analysis`. + `scripts/` (les CLI) + `gnuradio/` (flowgraphs).
- **Le point de passage unique** : *tout* accès au stockage passe par
  **`common/storage.py:StorageClient`**. Personne d'autre ne parle à S3.
- **Principe « logique pure vs glue »** : la logique (qualité, DSP) est dans des
  **fonctions pures testables** ; les CLI/IHM sont une fine couche par-dessus.

## 2. Les parcours de données (qui appelle quoi)

### A. Produire une capture synthétique
```
aerolake-producer (scripts/producer.py)
  └─ producer/orchestrator.py : capture_and_upload()
       ├─ producer/synthetic.py : generate_tone()        # IQ = tone + bruit
       ├─ producer/sigmf_writer.py : encode()            # → octets data + meta JSON (validés SigMF)
       └─ common/storage.py : upload_bytes(meta)  PUIS  upload_bytes(data, metadata=…, tags=…)
```
Clé produite : `{signal_type}/{date}/{session}/capture.sigmf-{data,meta}`.

### B. Ingérer une vraie capture (RFSoC, fichiers/paquets)
```
aerolake-ingest (scripts/ingest.py)
  ├─ _resolve_files()                 # 1 fichier OU dossier de paquets (tri numérique)
  └─ producer/ingest.py : ingest_files()
       ├─ _iter_cf32_files()          # lit chaque fichier en chunks, convertit cs32/cu8/cs16 → cf32 normalisé
       ├─ common/storage.py : upload_bytes(meta)
       └─ common/storage.py : upload_multipart(data, …)  # streaming, RAM bornée (ADR-010)
```

### C. Cataloguer / filtrer (sans télécharger les octets)
```
aerolake-list (scripts/catalog.py)
  └─ consumer/reader.py : list_captures() + inspect()    # HEAD + get_object_tagging (ADR-003)
```

### D. Valider la qualité (curation)
```
aerolake-validate (scripts/validate.py)
  └─ consumer/reader.py : validate()
       ├─ read()                                   # télécharge + décode
       ├─ quality/checker.py : QualityChecker.check()
       │     └─ quality/metrics.py : 6 fonctions PURES (clipping, RMS, NaN, DC, complétude, SigMF)
       ├─ storage.upload_bytes(quality_report.json)
       └─ storage.get_object_tags() → merge → update_tags()   # lire-fusionner-écrire (ADR-005)
```

### E. Lecture partielle / seek (HTTP Range, ADR-009)
```
consumer/reader.py : read_segment(key, start_s, duration_s)
  ├─ object_size() (HEAD) + lecture du .sigmf-meta            # sample_rate, datatype
  └─ common/storage.py : download_range(byte_start, byte_end) # ne tire que la fenêtre
```

### F. Rejouer à la cadence (playback logiciel, ADR-007)
```
aerolake-play (scripts/play.py)
  └─ consumer/player.py : CapturePlayer.play(start_s, duration_s)
       ├─ reader.read() | read_segment()           # tout, ou une fenêtre (Range)
       ├─ iter_frames()                             # découpe en frames (pur)
       └─ pour chaque frame : on_frame(i, frame) puis sleep(frame/sample_rate)  # cadence
```

### G. Diffuser sur le réseau (ZeroMQ Pub/Sub, ADR-008)
```
aerolake-stream (scripts/stream.py)
  └─ player.play(on_frame = consumer/stream.py:FramePublisher.publish)
       └─ encode_frame() → socket ZMQ PUB           # [topic, header JSON, octets IQ]
   (un FrameSubscriber sur un autre appareil → recv() → decode_frame())
```

### H. Visualiser une capture IQ (IHM, ADR-006)
```
aerolake-gui (gui/launch.py → gui/app.py, Streamlit)
  ├─ _capture_overview() : inspect + object_size       # durée/metrics, sans charger d'échantillons
  ├─ fenêtre :   reader.read_segment()  → gui/plots.py : spectrum_figure / spectrogram_figure / constellation_figure
  └─ overview :  ~240 download_range() répartis        → plots.overview_spectrogram_figure()  # toute la durée, ~8 Mo
       (plots.py = DSP PUR : Welch, STFT, constellation → figures Plotly ; theme.py = style)
```

### I. Visualiser des `.h5` décodés (bonus, ADR-011)
```
aerolake-analysis (analysis/launch.py → analysis/app.py)
  └─ analysis/tables.py : list_datasets() / load_table()   # détecte le type (GPS/IMU/Iridium)
       └─ figures_for()                                    # carte OSM, orientation IMU, SNR Iridium…
```
*(Hors lac IQ : ce sont des résultats décodés, jamais stockés dans MinIO.)*

### J. GNU Radio (matériel, ADR-007 couche 2/3)
```
gnuradio/record_sdr.grc : Soapy Source (RFSoC/BladeRF/RTL-SDR) → File Sink (.sigmf-data)
        →  aerolake-ingest  (le pont = le fichier cf32)
gnuradio/playback.grc   : File Source (.sigmf-data) → Throttle → spectre/waterfall (logiciel)
```

### K. Ré-émettre en VRAIE RF (matériel, ADR-012 — couche 3)
```
aerolake-fetch (scripts/fetch.py)                 # le PONT MinIO → fichier local
  └─ consumer/reader.py : read() | read_segment() # toute la capture, ou une fenêtre (Range)
       └─ écrit /tmp/capture.sigmf-data (cf32_le) + .sigmf-meta  + imprime samp_rate/freq
gnuradio/transmit_sdr.grc : File Source → multiply_const (tx_amplitude) → Soapy Sink (BladeRF, "TX")
  (pas de Throttle : l'horloge du SDR cadence) → ré-émission RF réelle
```
⚠️ Émettre sur GNSS/Iridium en l'air = illégal et brouille de vrais récepteurs :
**câble blindé + atténuateur / charge fictive / cage de Faraday** uniquement.

## 3. Carte des modules

| Module | Rôle | Dépend de |
|---|---|---|
| `common/config.py` | Settings (`.env`, env vars) | pydantic-settings |
| `common/storage.py` | **StorageClient** — tout S3 (upload/download/list/tags/multipart/range) | boto3 |
| `common/logging.py` | logs → stderr | structlog |
| `producer/synthetic.py` | génère l'IQ | numpy |
| `producer/sigmf_writer.py` | encode SigMF (valide le schéma) | sigmf |
| `producer/orchestrator.py` | générer→encoder→uploader | synthetic, sigmf_writer, storage |
| `producer/ingest.py` | ingérer fichiers réels (conversion + multipart) | storage, sigmf_writer |
| `consumer/reader.py` | list/inspect/read/**read_segment**/validate | storage, quality |
| `consumer/player.py` | playback à la cadence | reader |
| `consumer/stream.py` | ZeroMQ PUB/SUB | pyzmq |
| `quality/metrics.py` | 6 métriques **pures** | numpy |
| `quality/checker.py` | seuils + verdict | metrics |
| `gui/plots.py` | DSP pur → figures Plotly | numpy, plotly |
| `gui/app.py` | IHM Streamlit (glue) | reader, plots, theme |
| `analysis/tables.py` | charge/trace les `.h5` décodés | h5py, plotly |
| `scripts/fetch.py` | **aerolake-fetch** : MinIO → fichier `.sigmf-data` local (pont GNU Radio) | reader, storage |
| `scripts/*` | les 9 CLI (argparse + rich, exit codes 0/1/2) | les modules ci-dessus |

## 4. Les 5 patterns transverses (à citer à l'oral)

1. **Point de passage unique** : tout le stockage via `StorageClient` → changer
   de MinIO (local → labo → AWS) = changer une URL, pas le code (ADR-001).
2. **Fonctions pures vs glue** : qualité (`metrics.py`) et DSP (`plots.py`,
   `iter_frames`, `encode_frame`) sont pures → **testées sans réseau ni I/O**.
3. **Injection de dépendances** : on *passe* le `StorageClient`, le `reader`,
   l'horloge (`sleep`), la socket… → on injecte un faux en test (moto, fake socket).
4. **Fail-fast + erreurs maison** : config invalide = plante au démarrage ;
   toutes les erreurs stockage = une seule `StorageError` ; CLI = codes 0/1/2.
5. **Décisions tracées (ADR)** : 11 ADR dans `docs/adr/` documentent *pourquoi*
   chaque choix — on n'efface pas une décision, on en ajoute une.

## 5. Ce que j'ai construit (pour l'oral)

- **Pipeline complet** : Producer → MinIO → Consumer, avec une **couche qualité**
  qui curate le lac (raw → validated/rejected).
- **Vraie data** : ingestion de captures **RFSoC `cs32`** (17753 paquets → 1 capture
  de 2,84 Go), métadonnées + tags (source, type), validée et visualisée.
- **Optimisations du mandat** : **lecture partielle** (HTTP Range, seek), **upload
  multipart** (RAM bornée), **streaming ZeroMQ**, **playback** à la cadence.
- **Visualisation** : IHM IQ (spectre/spectrogramme/constellation + overview durée
  entière) et viewer `.h5` (GPS/IMU/Iridium, carte OSM).
- **GNU Radio** : flowgraphs Record/Playback (pont via le fichier cf32).
- **Qualité d'ingénierie** : ~140 tests (moto + test d'intégration MinIO réel),
  CI GitHub Actions (ruff + mypy + pytest), 11 ADR, 8 CLI.

> En une phrase : *« J'ai construit un data-lakehouse RF qui ingère des captures
> réelles (RFSoC), les stocke en SigMF avec métadonnées et qualité contrôlée dans
> MinIO, et permet de les retrouver, valider, rejouer à la cadence et visualiser —
> de n'importe où sur le réseau. »*
