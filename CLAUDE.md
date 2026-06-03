# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AeroLake is an RF data-lakehouse pipeline (LASSENA project). It captures radio-frequency
signals, encodes them as [SigMF](https://github.com/sigmf/SigMF) (a `.sigmf-data` binary blob +
a `.sigmf-meta` JSON sidecar), stores them in a MinIO bucket, and reads/validates them back.
Stored captures can be replayed at their recorded cadence and streamed over a ZeroMQ Pub/Sub bus
(ADR-007/008). Real SDR capture and GNU Radio Record/Playback are on the roadmap but **not yet
built** — today the producer generates **synthetic** signals and ingestion is batch.

## Commands

This project uses [uv](https://github.com/astral-sh/uv). Python 3.12+. `src/` layout
(`module-root = src`, so the importable package is `aerolake`, living at `src/aerolake/`).

```bash
uv sync                          # install deps (incl. dev group)

# Start local storage (run from docker/; reads ../.env)
cd docker && docker compose up -d   # MinIO API :9000, console :9001, auto-creates the bucket

# Entry points (defined in pyproject [project.scripts])
uv run aerolake-healthcheck          # verify .env + MinIO reachable + bucket accessible
uv run aerolake-producer --preset gnss-l1 --duration 1.0   # generate+upload a synthetic capture
uv run aerolake-ingest capture.sigmf-data --signal-type gnss_l1 --sample-rate 2e6 --center-freq 1575.42e6  # ingest a REAL IQ file
uv run aerolake-validate --prefix gnss_l1/ --dry-run       # batch-validate a prefix (read-only preview)
uv run aerolake-validate --prefix gnss_l1/ --expected-duration 1.0  # curate: promote quality tags + write reports
uv run aerolake-list --quality validated     # list/filter captures by tag (no byte download)
uv run aerolake-play --prefix gnss_l1/ --start 200 --duration 10   # partial read: t=200s, 10s (HTTP Range)
uv run aerolake-stream --prefix gnss_l1/      # publish a capture's frames over ZeroMQ Pub/Sub
uv run aerolake-subscribe --address tcp://localhost:5555   # subscribe to a ZeroMQ stream (the receiving half, any device)
uv run aerolake-fetch --key gnss_l1/…/capture.sigmf-data --out /tmp/capture.sigmf-data   # MinIO→local cf32 file bridge for GNU Radio (ADR-012)

# Visualization GUI (Streamlit + Plotly; optional `gui` dependency group)
uv sync --group gui                  # install the GUI runtime (streamlit + plotly)
uv run --group gui aerolake-gui      # launch the web app (browser at localhost:8501)
uv run --group gui aerolake-analysis # BONUS: viewer for decoded GPS/IMU/Iridium .h5 tables (NOT IQ)

# Quality / linting / tests
uv run ruff check .              # lint  (ruff config in pyproject; line-length 100, E501 ignored)
uv run ruff format .             # format
uv run mypy src                  # type-check
uv run pytest                    # full suite (verbose + short tracebacks via pyproject addopts)
uv run pytest tests/quality/test_metrics.py            # one file
uv run pytest tests/quality/test_metrics.py::test_name # one test
uv run pytest -k clipping        # tests matching an expression
```

`aerolake-validate` orchestrates `CaptureReader.validate()` over a whole prefix to curate the
bucket (promote `quality` tags, write `quality_report.json` artifacts). `--dry-run` previews
verdicts without mutating anything. `aerolake-list` is the read-only catalog: it lists captures
and filters them by tag (`--signal-type`, `--quality`, `--hardware`, or generic `--tag k=v`)
using only HEAD-class requests (no sample bytes downloaded), per the ADR-003 discovery pattern.

## Configuration

Settings come from environment variables prefixed `AEROLAKE_`, loaded via
`aerolake.common.config.Settings` (pydantic-settings). The `.env` at the project root feeds local
dev; real env vars override it. Always read settings through `get_settings()` (it is
`lru_cache`d — `.env` is parsed once per process). Copy `.env.example` to `.env` to start.
`s3_secret_key` is a `SecretStr` so it never leaks into logs/tracebacks.

## Architecture

The pipeline is **Producer → MinIO → Consumer**, with a **Quality** layer that gates what becomes
a "curated" capture. Four packages under `src/aerolake/`:

- **`common/`** — shared infra. `config.py` (Settings). `storage.py` (`StorageClient`, the *single*
  chokepoint for all S3 access; every read/write goes through it — incl. `upload_multipart`
  (streaming upload, ADR-010) and `download_range` (partial reads, ADR-009)).
- **`producer/`** — `synthetic.py` generates IQ samples (`generate_tone`), `sigmf_writer.py`
  encodes them to SigMF bytes (`encode`), `orchestrator.py` (`capture_and_upload`) ties
  generate → encode → upload together. `ingest.py` (`ingest_file`/`ingest_files`,
  `aerolake-ingest`) is the **real-data** entry point: take an existing IQ file **or a directory
  of packet files** (RFSoC `RX0_pkt_*.bin`, concatenated in numeric order; `cf32/cu8/cs16/cs32`
  → normalised cf32), write the `.sigmf-meta`, and stream into MinIO via `upload_multipart`.
- **`consumer/`** — `reader.py` (`CaptureReader`): list/inspect/read captures, `read_segment()`
  for **partial/seeked reads** (HTTP Range — fetch only a `start_s`/`duration_s` window, ADR-009),
  plus `validate()` which runs the quality layer and promotes the capture's quality tag. `player.py`
  (`CapturePlayer`, ADR-007) replays a capture's samples in frames paced at the recorded sample
  rate (injectable clock for tests) — the software half of "playback". `stream.py`
  (`FramePublisher`/`FrameSubscriber`, ADR-008) publishes those frames over a ZeroMQ PUB/SUB bus
  (pure `encode_frame`/`decode_frame` wire format; injectable socket for tests).
- **`quality/`** — `metrics.py` is **pure functions** (no I/O, no logging, no decisions:
  clipping ratio, RMS dBFS, invalid samples, DC offset, completeness, SigMF metadata validity).
  `checker.py` (`QualityChecker`/`QualityReport`) applies configurable `QualityThresholds` to
  those metrics and produces a pass/fail verdict.
- **`gui/`** (ADR-006, optional `gui` dep group) — Streamlit web app. Same pure-vs-glue split:
  `plots.py` is **pure DSP functions** (Welch spectrum, STFT spectrogram, constellation → Plotly
  figures, unit-tested), `theme.py` is the aerospace dark styling, `app.py` is thin Streamlit glue
  that reads via `CaptureReader` (never S3 directly), loading a **time window** via `read_segment`
  (partial read — multi-GB captures open instantly and you can seek), with a **whole-capture
  overview** mode (full-duration waterfall built from ~240 strided Range reads), `launch.py` is the
  `aerolake-gui` entry point.
- **`analysis/`** (ADR-011, BONUS, optional `gui` deps + `h5py`) — *separate from the IQ core*.
  Multi-modal viewer for **decoded** `.h5` tables (GR-Iridium Toolkit + ublox/VN100 output:
  `GPS_Analysis`/`IMU_Analysis`/`Iridium_Analysis` groups — **not** raw IQ, never enters MinIO).
  `tables.py` = pure loader (`load_table`/`list_datasets`, kind detection) + per-modality Plotly
  figures (`figures_for`, tested); `app.py` = Streamlit app `aerolake-analysis` (pick file → run →
  type-aware plots: GPS **OpenStreetMap map** (`go.Scattermap`, no token) + X/Y track + altitude,
  IMU orientation/accel/gyro, Iridium SNR/frequency).

`scripts/` holds the CLI entry points (`healthcheck.py`, `producer.py`, `ingest.py`, `validate.py`,
`catalog.py`, `play.py`, `stream.py`, `subscribe.py`, `fetch.py`), all using `rich` for output and documented exit codes (0 ok / 1 storage failure /
2 config-or-unexpected). All CLIs call `aerolake.common.logging.configure_logging` first so
structlog logs go to stderr, keeping stdout clean for results (`--json`, tables). `fetch.py`
(`aerolake-fetch`, ADR-012) is the **MinIO→local-file bridge** for GNU Radio: it reads a capture
(whole, or a window via `read_segment`/Range) through `CaptureReader` and writes the raw `cf32_le`
bytes + a `.sigmf-meta` sidecar to disk, printing the `samp_rate`/`freq` to paste into a flowgraph.

### GNU Radio flowgraphs (`gnuradio/`, ADR-007 layer 2/3, ADR-012)

`gnuradio/` holds `record.grc` / `record_sdr.grc` / `playback.grc` / `transmit_sdr.grc` —
**separate from the uv project**: they need a system GNU Radio (`sudo apt install gnuradio`, 3.10+)
and run with the *system* Python that ships its bindings, not `.venv`. The bridge to the rest of
AeroLake is the **`.sigmf-data` file itself**: it is raw `cf32_le`, which GNU Radio's File
Source/Sink read/write natively as *complex* — no SigMF block needed. Get a capture onto local disk
with **`aerolake-fetch`** (ADR-012). Validate a `.grc` headlessly with `grcc -o /tmp
gnuradio/playback.grc`; the generated `.py` is gitignored. **Real RF transmit** —
`transmit_sdr.grc` (File Source → amplitude backoff → Soapy Custom **Sink**, ADR-012) — needs the
**BladeRF** (the RTL-SDR is RX-only). ⚠️ Emitting on GNSS/Iridium bands over the air is illegal and
jams real receivers: use a shielded cable + attenuator / dummy load / Faraday enclosure.

### Conventions that span multiple files

These are the load-bearing decisions; read the referenced ADR before changing them.

- **Bucket key layout** (`orchestrator.py`): `{signal_type}/{YYYY-MM-DD}/{session_id}/capture.sigmf-data`
  and `…/capture.sigmf-meta`. `session_id` is 8 hex chars. A capture is "complete" only when both
  objects exist; `CaptureReader.list_captures` skips orphans. Quality reports are written as
  `…/{session}/quality_report.json`.

- **Metadata vs. tags split** (ADR-003, `docs/adr/003-…`): continuous/technical values go in
  `x-amz-meta-*` headers (cheap to read via HEAD, no body transfer); categorical/enumerable values
  go in **S3 tags** (`signal-type`, `recorder`, `hardware`, `quality`) which are indexable and drive
  lifecycle. **Both are attached only to the `.sigmf-data` object** — the `.sigmf-meta` JSON carries
  no headers/tags because its body *is* the description.

- **Upload order matters** (`orchestrator.py`): `.sigmf-meta` is uploaded **before** `.sigmf-data`,
  so a consumer racing between the two puts sees interpretable JSON rather than orphan bytes.

- **`StorageClient.update_tags` is a full REPLACE, not a merge.** The S3 `PutObjectTagging` API
  overwrites the entire tag set. To change one tag (e.g. quality promotion), you MUST read existing
  tags, merge, then write — `CaptureReader.validate` does exactly this. Forgetting the merge wipes
  `signal-type`/`hardware`/`recorder`.

- **Quality lifecycle** (ADR-003 + ADR-004): tag starts at `quality=raw` (set by the producer).
  `validate()` promotes it to `validated` or `rejected` based on the report verdict. `archived` is
  manual. There is no automated retention/lifecycle policy (dropped from scope per ADR-004).

- **boto3 endpoint switch** (ADR-001, `storage.py`): when `s3_endpoint` is empty, boto3 talks to
  real AWS — which is also what **moto** intercepts in tests; when set, it talks to MinIO. MinIO
  needs `signature_version="s3v4"` + path-style addressing (already configured). boto3 (not
  minio-py) was chosen for portability and moto support.

### Project direction (read ADR-004 first)

ADR-004 reprioritized the project after a call with the project lead: **input data quality and a
curated dataset are the priority**, not real-time delivery. So the **quality layer is the current
focus**; the **streaming pipeline (multipart upload, HTTP Range Requests, ZeroMQ Pub/Sub) is
deferred**, and real SDR capture (SoapySDR) is still future work. The README's "Architecture cible"
describes the eventual end state, not what exists today — trust the ADRs and the code for current
status.

## Project context & history

`docs/context/historique-discussions.md` (committed) distills the two desktop-app design
discussions (21–29 May 2026) that predate this repo: the project goal, the 3 demos
(GNSS/Iridium/Starlink), the people (Abdu = project lead, Malek = tutor, Wissem/Ahmad =
receiver owners, Pierre/Lucien = NeSIVA predecessors), the hardware (BladeRF + RTL-SDR,
remote MinIO at fast.etsmtl.ca), and the roadmap Théo wants (finish infra → visualization
GUI → GNU Radio Record/Playback → test on real data). Read it for the *why* behind the
code. Raw transcripts live under `docs/context/transcripts/` (gitignored).

Note: Théo prefers **heavily commented, pedagogical code** — match the existing comment
density in `src/`, it is intentional (a learning aid), not clutter.

## Decision records

`docs/adr/` holds the Architectural Decision Records. They are the authoritative record of *why*
the code is shaped the way it is — consult them before reversing a design choice, and add a new ADR
(don't silently edit an accepted one) when making a decision of comparable weight:

- ADR-001 — boto3 over the MinIO SDK
- ADR-002 — batch upload now, streaming later
- ADR-003 — metadata vs. tagging convention (the key layout + lifecycle)
- ADR-004 — prioritize data quality over streaming (reorders the roadmap)
- ADR-005 — consumer-side quality tag promotion lifecycle (raw → validated/rejected)
- ADR-006 — visualization GUI: Streamlit + Plotly web app
- ADR-007 — playback strategy (software cadence replay now; GNU Radio + SDR re-emission later)
- ADR-008 — ZeroMQ Pub/Sub streaming of capture frames (reactivates ADR-002's streaming half)
- ADR-009 — partial/seeked reads via HTTP Range Requests (Python `read_segment` + GNU Radio offset/length)
- ADR-010 — streaming multipart upload to bypass RAM (`StorageClient.upload_multipart`)
- ADR-011 — analysis viewer for decoded `.h5` tables (GPS/IMU/Iridium; bonus, separate from the IQ core)
- ADR-012 — RF re-emission: BladeRF TX flowgraph (`transmit_sdr.grc`) + the `aerolake-fetch` MinIO→file bridge

## Testing notes

Tests use **moto** to mock S3 (no real MinIO needed). `tests/conftest.py` provides `test_settings`
(isolated from the developer's `.env` by passing values as kwargs, with `s3_endpoint=""` so moto
intercepts), `mock_s3` (a moto-backed client with the test bucket pre-created), and `storage_client`
(a `StorageClient` wired to the mock). Inject these rather than hitting a live backend.

An opt-in **integration test** (`tests/integration/`, marker `integration`) exercises a *real*
MinIO end-to-end (multipart + Range + tagging). It's skipped unless `AEROLAKE_RUN_INTEGRATION=1`;
the CI `integration` job spins up a MinIO container (Docker) and runs `pytest -m integration`.
The producer/ingest declare `core:version` from `sigmf.__specification__` (see
`sigmf_writer.SIGMF_VERSION`), not a hard-coded string.
