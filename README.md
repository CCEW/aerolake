# AeroLake

AeroLake is an RF-recording pipeline for LASSENA. It records or ingests IQ samples, preserves them as a standard SigMF pair in S3-compatible storage, and makes them discoverable through MinIO tags or an optional IQEngine catalog.

```text
SDR / existing IQ -> AeroLake -> MinIO/S3 (authoritative SigMF files)
                                  |                 |
                                  |                 +-> replay / ZeroMQ / GUI
                                  +-> IQEngine sync -> browser search and analysis
```

The authoritative capture is always:

```text
capture.sigmf-data    raw IQ bytes
capture.sigmf-meta    SigMF metadata and integrity hash
```

When requested, AeroLake also stores IQEngine-compatible sidecars beside that pair: `capture.jpg`, `capture.preview.jpg`, and `capture.minimap`. They are derived convenience artifacts, not replacements for the SigMF recording.

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Docker Desktop (for the local MinIO stack), or credentials for the lab S3/MinIO service
- For real SDR capture: SoapySDR and the appropriate device driver
- WSL2 is supported on Windows; Linux and macOS work natively

## Quick start

```bash
git clone <repository-url>
cd aerolake
cp .env.example .env
uv sync
cd docker && docker compose up -d && cd ..
uv run aerolake-healthcheck
```

Configure the `AEROLAKE_S3_*` values in `.env` for a remote deployment. Never commit `.env` or real service-account credentials.

## Main workflows

```bash
# Record from a TOML or JSON configuration.
uv run aerolake-capture --config examples/capture.example.toml

# Ingest a raw file. The first run creates an editable metadata template if needed.
uv run aerolake-ingest capture.sigmf-data \
  --signal-type gnss_l1 --sample-rate 2e6 --center-freq 1575.42e6

# Validate and upload an existing SigMF pair; generate/upload IQEngine sidecars.
uv run aerolake-ingest capture.sigmf-data --iqengine

# Browse directly in MinIO, without downloading IQ samples.
uv run aerolake-list --catalog minio --signal-type gnss_l1

# Query the IQEngine catalog with its metadata filters.
uv run aerolake-list --catalog iqengine --signal-type iridium --json

# Generate sidecars locally (and optionally upload them).
uv run aerolake-iqengine-artifacts capture.sigmf-data --upload --key-prefix iridium/2026-09-14/session

# Group captures, replay one, or publish it over ZeroMQ.
uv run aerolake-collection --prefix gnss_l1/2026-06-17/ --name campaign --description "..."
uv run aerolake-play --prefix gnss_l1/
uv run aerolake-stream --prefix gnss_l1/
uv run aerolake-subscribe --address tcp://localhost:5555
```

`aerolake-ingest` preserves the recorded datatype and bytes. It validates sample alignment, verifies or adds `core:sha512`, and uploads metadata before the IQ object. Existing pairs require `aerolake:signal_type` in `global`; new single-file ingests stop after writing a template until all `<missing:...>` values are completed.

## IQEngine catalog and query CLI

IQEngine is optional and does not own the capture files. It indexes the SigMF metadata already held in MinIO/S3; AeroLake communicates only with IQEngine's supported integration API, never its MongoDB internals.

Set these local `.env` variables when IQEngine is available:

```dotenv
AEROLAKE_IQENGINE_URL=https://<iqengine-host>
AEROLAKE_IQENGINE_TOKEN=<dedicated-service-token>
AEROLAKE_IQENGINE_ACCOUNT=<datasource-account>
AEROLAKE_IQENGINE_CONTAINER=<datasource-container>
# AEROLAKE_IQENGINE_PREFIX=<optional-datasource-prefix>
```

Run `bash init-sync.sh` on Linux/WSL or `init-sync.bat` on Windows to request and wait for an initial datasource sync. Use `--catalog iqengine` when testing or requiring IQEngine: it fails rather than silently querying MinIO. `--catalog auto` uses IQEngine when configured and falls back to MinIO if it is unavailable.

The IQEngine query CLI accepts frequency and datetime bounds, text, author, location, operator, recorder, account/container selectors, signal type, and hardware. Use `--json` to see the selected catalog and freshness/sync state. See [docs/cli-reference.md](docs/cli-reference.md) for the complete syntax.

## Optional web interface

The Streamlit GUI offers capture and playback without using the CLI:

```bash
uv sync --extra gui
uv run aerolake-gui
```

On Windows + WSL, `launch-gui.vbs` starts it in the background and opens the browser; use the `.bat` script when console output is useful. The GUI-generated `-preview.png` is distinct from the IQEngine sidecars.

## Documentation

- [docs/README.md](docs/README.md) — documentation index and reading paths
- [docs/operator-cli-guide.md](docs/operator-cli-guide.md) — practical operator workflows
- [docs/cli-reference.md](docs/cli-reference.md) — complete CLI and IQEngine query reference
- [docs/IQENGINE-User-Manual.md](docs/IQENGINE-User-Manual.md) — IQEngine browser workflow
- [HANDOFF.md](HANDOFF.md) — concise deployment and ownership handoff
- [docs/handoff-document.md](docs/handoff-document.md) — detailed technical handoff
- [docs/adr/](docs/adr/) — architectural decisions, including IQEngine integration (ADR-021–023) and byte-preserving ingest (ADR-024)

Metadata references are in [examples/capture.sigmf-meta.example.json](examples/capture.sigmf-meta.example.json), [examples/short_24lines.ingest.sigmf-meta.example.json](examples/short_24lines.ingest.sigmf-meta.example.json), and [examples/iqengine-metadata-schema-example.json](examples/iqengine-metadata-schema-example.json).

## Quality checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Run the live MinIO integration tests only when a test service is intentionally available: `AEROLAKE_RUN_INTEGRATION=1 uv run pytest -m integration`.
