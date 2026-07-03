# AeroLake

Pipeline Python end-to-end pour l'enregistrement, le stockage et l'extraction d'environnements RF — projet LASSENA.

AeroLake capture des signaux radiofréquences, les stocke au format SigMF dans un data lakehouse MinIO (avec métadonnées et tags natifs pour la recherche), puis les ré-expose via un bus ZeroMQ Pub/Sub. L'objectif central : que n'importe quel membre du laboratoire puisse retrouver et rejouer n'importe quelle capture grâce à des métadonnées standardisées.

## Périmètre

Ce dépôt suit le mandat du projet (docs/LASSENA-Project_AeroLake.pdf) : un pipeline RX (réception) en quatre sprints.

    Producer (capture -> SigMF)  ->  MinIO (lakehouse)  ->  Consumer (extraction -> ZeroMQ)

- Producer — génère/encode des échantillons IQ au format SigMF et les pousse dans MinIO (multipart upload).
- Lakehouse — MinIO (S3-compatible) : stockage des .sigmf-data + .sigmf-meta, avec métadonnées d'objet (x-amz-meta-*) et tags pour la découverte rapide et le cycle de vie.
- Consumer — relit les captures par HTTP Range Requests et les publie sur un bus ZeroMQ Pub/Sub, prêt à alimenter décodeurs logiciels ou, en phase future, un émetteur SDR.

### Hors-périmètre (phases futures, archivé)

Les composants suivants ont été développés puis archivés pour recentrer le projet sur le mandat. Ils sont préservés intégralement sur la branche archive/explorations-v1 et restent récupérables :

- Ancienne interface de *visualisation* Streamlit/Plotly (ADR-006) — à ne pas confondre avec la **nouvelle interface web de capture/playback**, qui fait partie du dépôt (voir « Interface web » ci-dessous)
- Module d'analyse de données décodées .h5 — Doppler/IMU/GPS (ADR-011)
- Émission RF / TX — flowgraph BladeRF + pont MinIO->fichier (ADR-012)
- Évolution analytique Parquet / Apache Iceberg

Voir ADR-013 pour le détail de ce recadrage.

## Prérequis

- WSL2 + Ubuntu 22.04+ (Windows) ou Linux natif / macOS
- Docker Desktop avec intégration WSL2
- Python 3.12+ via uv (https://github.com/astral-sh/uv)
- Git

## Quick start

    git clone <url>
    cd aerolake
    cp .env.example .env
    uv sync
    cd docker && docker compose up -d

## Commandes principales

    uv run aerolake-healthcheck
    uv run aerolake-capture --config examples/capture.example.toml   # TOML (recommandé) ou JSON
    uv run aerolake-ingest capture.sigmf-data --signal-type gnss_l1 --sample-rate 2e6 --center-freq 1575.42e6
    uv run aerolake-list --signal-type gnss_l1
    uv run aerolake-collection --prefix gnss_l1/2026-06-17/ --name "campagne" --description "..."
    uv run aerolake-play --prefix gnss_l1/
    uv run aerolake-stream --prefix gnss_l1/
    uv run aerolake-subscribe --address tcp://localhost:5555

Les fichiers de config de capture sont en **TOML (recommandé — commentaires
autorisés) ou JSON**, choisis par l'extension ; modèles commentés dans
`examples/` (voir `examples/README.md`).

## Interface web (GUI)

Une interface Streamlit pour capturer et rejouer **sans terminal** (extra
optionnel, hors installation de base) :

    uv sync --extra gui
    uv run aerolake-gui        # sert sur le réseau (0.0.0.0:8501)

Deux onglets : **Capture** (déposer une config TOML/JSON, pointer l'antenne sur
une carte, capturer, revoir le spectre, pousser dans MinIO / garder / jeter) et
**Playback** (parcourir le lakehouse, visualiser le spectre de n'importe quelle
fenêtre via HTTP Range, commande ZeroMQ prête, export SigMF pour GNU Radio).

## Qualité / tests

    uv run ruff check .
    uv run ruff format .
    uv run mypy src
    uv run pytest

Un test d'intégration optionnel (tests/integration/) s'exécute contre un vrai MinIO : AEROLAKE_RUN_INTEGRATION=1 uv run pytest -m integration

## Structure du projet

    aerolake/
    ├── src/aerolake/
    │   ├── common/     Configuration, storage (chokepoint S3), logging
    │   ├── producer/   Capture/ingestion -> SigMF -> MinIO
    │   ├── consumer/   Extraction MinIO (HTTP Range) -> ZeroMQ
    │   ├── gui/        Interface web Streamlit (extra optionnel [gui])
    │   └── scripts/    Points d'entrée CLI
    ├── tests/          Tests pytest (moto)
    ├── docker/         MinIO local (docker-compose)
    ├── gnuradio/       Flowgraphs Record / Playback
    └── docs/           Documentation et ADR

## Documentation

Le dossier docs/adr/ contient les Architectural Decision Records — la trace des décisions de conception. ADR-013 documente le recadrage sur le mandat ; les ADR des composants archivés (006, 011, 012) y sont conservés et marqués comme tels.

## Auteur

Théo Schmitt — LASSENA
