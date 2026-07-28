# AeroLake code map — the bare essentials

> Purpose of this document: **take over AeroLake in one read.** Only ~17 files
> really matter (the "core"). The rest is optional and flagged further down.
> If you read only one page, read this one.

---

## 1. What AeroLake does, in one sentence
**Record a radio signal → write it in the SigMF format → store it in MinIO with
as much metadata as possible.** That is all. Everything else (streaming,
replay, collections) is a bonus, not the heart of the mandate.

One stored capture = 3 objects side by side in MinIO:
```
{signal_type}/{date}/{session}/capture.sigmf-data   ← the raw IQ samples
                              /capture.sigmf-meta    ← the SigMF JSON (description)
                              /capture-preview.png   ← the spectrum preview (auto)
```

---

## 2. The path of a capture (the happy path)

When you run `aerolake-capture --config my_capture.toml`, here are the files
traversed, **in order**. This is THE sequence to understand:

```
  my_capture.toml
        │
        ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ 1. scripts/capture.py        CLI entry point.                     │
 │                              Reads the arguments, drives the flow.│
 └─────────────────────────────────────────────────────────────────┘
        │ loads the TOML/JSON
        ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ 2. producer/config_loader.py + capture_config.py                 │
 │    file  ──►  validated CaptureConfig object (pydantic).         │
 │    (frequency, sample rate, duration, source, antenna, geoloc…)  │
 └─────────────────────────────────────────────────────────────────┘
        │ + optional geolocation
        ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ 3. producer/gps.py        (optional) reads ONE gpsd fix          │
 │                           → SigMF core:geolocation, else nothing.│
 └─────────────────────────────────────────────────────────────────┘
        │
        ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ 4. producer/orchestrator.py  ──  prepare_capture()               │
 │    THE conductor. It chains:                                     │
 │      a) generates/acquires the IQ samples from the SOURCE:       │
 │           • synthetic.py    → test signal (no hardware)          │
 │           • soapy_source.py → real SDR (RTL-SDR / BladeRF)       │
 │           • ingest.py       → already-recorded RFSoC files       │
 │      b) sigmf_writer.py  ──  encode()                            │
 │           samples + metadata  ►  .sigmf-data/-meta bytes         │
 │           (+ sha512, num_channels, SigMF version…)               │
 └─────────────────────────────────────────────────────────────────┘
        │  "prepared" = bytes + keys + tags, ready
        ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ 5. (the user confirms) orchestrator.py ── push_capture()         │
 │      a) preview.py  → renders the spectrum preview PNG           │
 │      b) everything goes to MinIO through the ONLY exit door:     │
 └─────────────────────────────────────────────────────────────────┘
        │
        ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ 6. common/storage.py  ──  StorageClient                          │
 │    THE single access point to MinIO/S3. EVERY read/write goes    │
 │    through here (upload_multipart, download_range, tags…).       │
 │    Upload order: .sigmf-meta BEFORE .sigmf-data (no orphans)     │
 └─────────────────────────────────────────────────────────────────┘
        │
        ▼
     MinIO (on your PC in dev, on the lab server in production)
```

**If you remember 3 files:** `orchestrator.py` (the sequencing),
`sigmf_writer.py` (the format), `storage.py` (the MinIO access). Everything
else orbits around them.

---

## 3. Reading / browsing the lakehouse
- `consumer/reader.py` — list, inspect, read a capture (or just a time window,
  through HTTP Range).
- `scripts/catalog.py` (`aerolake-list`) — the catalogue: list/filter by tag,
  **without downloading the bytes** (HEAD-class requests only).

---

## 4. The shared infrastructure (2 small files, read everywhere)
- `common/config.py` — every setting comes from here (`AEROLAKE_*` variables
  + `.env`). **To point at the lab server you change ONLY the `.env`**, not a
  single line of code.
- `common/logging.py` — structured logs to stderr (stdout stays clean for
  results).

---

## 5. The 4 everyday commands
| Command | File | Role |
|---|---|---|
| `aerolake-healthcheck` | scripts/healthcheck.py | Checks .env + MinIO reachable + bucket OK |
| `aerolake-capture --config x.toml` | scripts/capture.py | **The capture** (synthetic or SDR) → MinIO |
| `aerolake-ingest file --signal-type …` | scripts/ingest.py | Ingest an existing **real** IQ file |
| `aerolake-list --signal-type …` | scripts/catalog.py | Browse/filter the catalogue |

---

## 5b. The web interface (optional, but it is what colleagues see)
- `gui/app.py` — the **Streamlit** app (`uv sync --extra gui` then
  `uv run aerolake-gui`). **No capture logic of its own**: it is a *clickable
  facade* over the same functions as the CLI (`load_capture_config` →
  `prepare_capture` → `push_capture`, and `CaptureReader` for the Playback tab).
  If you understand the happy path in §2, you understand the GUI. Theme lives
  in `.streamlit/config.toml`.

---

## 6. What you can IGNORE to run the lakehouse (the periphery)
These files are a **bonus** (useful some day, not required for the "record →
SigMF → MinIO" mandate). If you are taking the project over, you can leave them
aside at first:

- `consumer/player.py` + `scripts/play.py` — replay a capture at its recorded
  pace (ADR-007).
- `consumer/stream.py` + `scripts/stream.py` + `scripts/subscribe.py` — publish
  the frames on a ZeroMQ Pub/Sub bus (ADR-008).
- `consumer/collection.py` + `scripts/collection.py` — group several captures
  into a `.sigmf-collection` (ADR-014).

> They have their tests and their ADR; nothing is broken. They are simply not on
> the critical path of the mandate.

---

## 7. Where to go next
- **The "why" behind every choice**: `docs/adr/` (ADR-001 → 020).
- **The complete handoff document**: `docs/passation-en.md`.
- **Take over / deploy / migrate to the lab server**: `HANDOFF.md`.
- **Run a real end-to-end acquisition**: `./acquire.sh examples/<config>.toml`.

```
                 Master 6 files → you master AeroLake.
        config.py · capture_config.py · orchestrator.py
        sigmf_writer.py · storage.py · reader.py
```
