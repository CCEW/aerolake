# AeroLake

Pipeline Python end-to-end pour l'enregistrement, le stockage et l'extraction d'environnements RF.

AeroLake capture des signaux radiofréquences via des SDR (RTL-SDR, BladeRF), les stocke au format SigMF dans un data lakehouse MinIO, puis les ré-expose via un bus ZeroMQ Pub/Sub haute performance prêt à alimenter des décodeurs logiciels ou de futurs émetteurs SDR.

## Statut du projet

Phase 0 — Mise en place de l'infrastructure et du squelette projet. Le code applicatif sera livré dans les sprints suivants.

## Architecture cible

- **Producer** : capture RF via SoapySDR → format SigMF → upload multipart vers MinIO
- **Lakehouse** : MinIO (S3-compatible) avec métadonnées natives et tagging
- **Consumer** : extraction par HTTP Range Requests → publication ZeroMQ Pub/Sub
- **Évolution prévue** : Apache Parquet + Apache Iceberg pour la couche analytique

## Prérequis

- WSL2 + Ubuntu 22.04+ (Windows) ou Linux natif / macOS
- Docker Desktop avec intégration WSL2
- Python 3.12+ via [uv](https://github.com/astral-sh/uv)
- Git

## Quick start

```bash
# Cloner le repo
git clone <url-gitlab>
cd aerolake

# Configurer l'environnement
cp .env.example .env

# Installer les dépendances Python
uv sync

# Démarrer MinIO
cd docker && docker compose up -d
```

## Structure du projet
aerolake/
├── src/aerolake/      Code applicatif Python
│   ├── common/        Configuration, storage, logging
│   ├── producer/      Pipeline d'ingestion SDR → SigMF → MinIO
│   ├── consumer/      Pipeline d'extraction MinIO → ZeroMQ
│   └── scripts/       Utilitaires CLI (healthcheck, init)
├── tests/             Tests pytest (unit + integration)
├── docker/            Compose files et infra locale
├── docs/              Documentation et ADR
└── data/              Données locales (gitignored)

## Documentation

Voir le dossier `docs/` pour les Architectural Decision Records et la documentation détaillée.

## Auteurs

Théo Schmitt — LASSENA
