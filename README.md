# AeroLake

End-to-end Python pipeline for recording, storing and extracting RF environments — a LASSENA project.

AeroLake captures radio-frequency signals, stores them in the SigMF format inside a MinIO data lakehouse (with native metadata and tags for discovery), then serves them back over a ZeroMQ Pub/Sub bus. The core goal: any member of the laboratory can find and replay any capture, thanks to standardised metadata.

## Scope

This repository follows the project mandate (docs/LASSENA-Project_AeroLake.pdf): an RX (receive) pipeline in four sprints.

    Producer (capture -> SigMF)  ->  MinIO (lakehouse)  ->  Consumer (extraction -> ZeroMQ)

- Producer — generates/encodes IQ samples in the SigMF format and pushes them to MinIO (multipart upload).
- Lakehouse — MinIO (S3-compatible): stores the .sigmf-data + .sigmf-meta pair, with object metadata (x-amz-meta-*) and tags for fast discovery and lifecycle.
- Consumer — reads captures back through HTTP Range Requests and publishes them on a ZeroMQ Pub/Sub bus, ready to feed software decoders or, in a future phase, an SDR transmitter.

### Out of scope (future phases, archived)

The following components were built and then archived to refocus the project on the mandate. They are preserved in full on the archive/explorations-v1 branch and remain recoverable:

- The former Streamlit/Plotly *visualisation* interface (ADR-006) — not to be confused with the **new capture/playback web interface**, which is part of this repository (see "Web interface" below)
- Decoded .h5 data analysis module — Doppler/IMU/GPS (ADR-011)
- RF transmission / TX — BladeRF flowgraph + MinIO->file bridge (ADR-012)
- Parquet / Apache Iceberg analytical evolution

See ADR-013 for the details of that refocus.

## Requirements

- WSL2 + Ubuntu 22.04+ (Windows) or native Linux / macOS
- Docker Desktop with WSL2 integration
- Python 3.12+ via uv (https://github.com/astral-sh/uv)
- Git

## Quick start

    git clone <url>
    cd aerolake
    cp .env.example .env
    uv sync
    cd docker && docker compose up -d

## Main commands

    uv run aerolake-healthcheck
    uv run aerolake-capture --config examples/capture.example.toml   # TOML (recommended) or JSON
    uv run aerolake-ingest capture.sigmf-data --signal-type gnss_l1 --sample-rate 2e6 --center-freq 1575.42e6
    uv run aerolake-ingest capture.sigmf-data                         # existing capture.sigmf-meta beside it
    uv run aerolake-list --signal-type gnss_l1
    uv run aerolake-collection --prefix gnss_l1/2026-06-17/ --name "campaign" --description "..."
    uv run aerolake-play --prefix gnss_l1/
    uv run aerolake-stream --prefix gnss_l1/
    uv run aerolake-subscribe --address tcp://localhost:5555

See `docs/cli-reference.md` for command details, especially the
`aerolake-ingest` variants: generated metadata, existing SigMF pairs,
and Iridium annotation. For metadata structure and required field shapes,
use `examples/iqengine-metadata-schema-example.json` as the field-level reference.

In both ingest modes, AeroLake completes a pre-upload checklist. Raw source
formats are validated and converted to normalized `cf32_le`. Existing SigMF
pairs are parsed and validated, checked for sample alignment, verified against
any existing `core:sha512`, enriched with a missing hash, and converted from
`ci16_le` when necessary. The hash in uploaded metadata always describes the
stored bytes; local source files are not modified.

Capture config files are written in **TOML (recommended — comments allowed) or
JSON**, selected by the file extension; commented templates live in
`examples/` (see `examples/README.md`).

## Web interface (GUI)

A Streamlit interface to capture and replay **without a terminal** (optional
extra). Two tabs: **Capture** (drop a TOML/JSON config, point the antenna on a
map, capture, review the spectrum, push / keep / discard) and **Playback**
(browse the lakehouse, view any time window's spectrum via HTTP Range,
ready-to-run ZeroMQ command, SigMF export). Binds `0.0.0.0:8501`, so colleagues
reach it at `http://<this-pc>:8501`.

**From a terminal:**

    uv sync --extra gui
    uv run aerolake-gui

**No terminal (Windows + WSL):** double-click **`launch-gui.vbs`** to start
(background, opens the browser) and **`stop-gui.vbs`** to stop — the GUI has no
window to close. Use the `.bat` versions to see console output. For auto-start
at boot, put a `launch-gui.vbs` shortcut in `shell:startup` (Win+R).

The `.vbs` → `.bat` → `.sh` chain uses **no hardcoded paths** (it derives the
repo's WSL path via `wslpath`). Per-PC setup:

- **One-time in WSL** — make the scripts runnable and strip Windows CRLF endings
  (they live on the Windows filesystem, which otherwise breaks bash):

      chmod +x launch-gui.sh stop-gui.sh
      sed -i 's/\r$//' launch-gui.sh stop-gui.sh

- **WSL2 + Ubuntu** installed; distro named `Ubuntu` (else set
  `AEROLAKE_WSL_DISTRO` to your name from `wsl -l -q`).
- **`uv` in WSL** — found on PATH or in `~/.local/bin`, `~/radioconda/bin`,
  `~/miniconda3/bin`, `/usr/local/bin`.
- Extra synced once (`uv sync --extra gui`); the first launch may take a minute.

If nothing opens, run `launch-gui.bat` directly (the `.vbs` hides errors) — it
prints the failure reason and the log tail
(`\\wsl.localhost\Ubuntu\tmp\aerolake-gui.log`). To stop from WSL:
`./stop-gui.sh` or `pkill -f "streamlit run src/aerolake/gui/app.py"`.

## Quality / tests

    uv run ruff check .
    uv run ruff format .
    uv run mypy src
    uv run pytest

An optional integration test (tests/integration/) runs against a real MinIO: AEROLAKE_RUN_INTEGRATION=1 uv run pytest -m integration

## Project layout

    aerolake/
    ├── src/aerolake/
    │   ├── common/     Configuration, storage (S3 chokepoint), logging
    │   ├── producer/   Capture/ingestion -> SigMF -> MinIO
    │   ├── consumer/   MinIO extraction (HTTP Range) -> ZeroMQ
    │   ├── gui/        Streamlit web interface (optional [gui] extra)
    │   └── scripts/    CLI entry points
    ├── tests/          pytest tests (moto)
    ├── docker/         local MinIO (docker-compose)
    ├── gnuradio/       Record / Playback flowgraphs
    └── docs/           Documentation and ADRs

## Documentation

The docs/adr/ folder holds the Architectural Decision Records — the written trace of every design decision. ADR-013 documents the refocus on the mandate; the ADRs of archived components (006, 011, 012) are kept there and marked as such.

Start here:

- **docs/operator-cli-guide.md** — CLI overview for people using AeroLake as a system; the short route for operators who are not reading the code.
- **docs/cli-reference.md** — the full command reference and advanced CLI behavior.
- **docs/handoff-document.md** — the complete handoff: concepts from zero, install, the whole code, the decisions and the roadmap. Start here if you are taking the project over.
- **docs/code-map.md** — the whole codebase in one page.
- **docs/user-manual.md** — daily use, no terminal needed.
- **docs/code-documentation.md** — the class-by-class code reference.
- **HANDOFF.md** — the operational cheat-sheet: set up a station, migrate the storage, transfer the repository.

The whole repository — code, comments, documentation and ADRs — is written in English.

## Authors

Théo Schmitt — LASSENA

Camila Nino Francia — Contributor
