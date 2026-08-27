# AeroLake — Complete code documentation

> **Goal**: understand and be able to modify **every part** of the code. This
> document goes down to class and function level. For the one-page overview,
> read `code-map.md` first; for the *why* behind the choices, `docs/adr/`.

---

## 1. Overview

The pipeline is **Producer → MinIO → Consumer**, with a web interface on top:

```
                    ┌── synthetic.py (test signal)
  CONFIG (.toml) ──►│── soapy_source.py (real SDR)        PRODUCER
                    └── ingest.py (existing IQ file)
                              │  IQ samples (complex64)
                              ▼
                    sigmf_writer.py  → .sigmf-data + .sigmf-meta
                              │
                    orchestrator.py  → keys, HTTP metadata, S3 tags
                              ▼
                    storage.py ───────► MinIO  (THE single access point)
                              ▲
                    reader.py (list / inspect / read / read_segment)   CONSUMER
                              │
            ┌─────────────────┼──────────────────┐
        player.py         stream.py         collection.py
     (paced replay)     (ZeroMQ bus)     (.sigmf-collection)

        gui/app.py = a Streamlit facade over exactly these bricks
```

**Design principles (respect them when modifying the code)**

1. **One single S3 access point**: everything goes through `StorageClient`
   (ADR-001). Never boto3 anywhere else.
2. **Dependency injection everywhere**: the SDR (`device_opener`), gpsd
   (`reader`), the player's clock (`sleep`), the ZeroMQ sockets, the CLIs'
   `storage_client` — everything is injectable, hence testable without hardware
   or a server.
3. **Prepare ≠ store**: `prepare_capture()` builds everything in memory,
   `push_capture()` uploads. In between, the human decides.
4. **Errors bubble up typed**: `StorageError`, `ConfigError` — the CLIs catch
   them and exit with documented codes (0 ok, 1 storage, 2 config,
   3 capture/unexpected).
5. **Pedagogical code**: the comment density is intentional.

## 2. Repository layout

```
aerolake/
├── src/aerolake/
│   ├── common/       config, logging, storage  (shared infra)
│   ├── producer/     acquisition → SigMF → upload preparation
│   ├── consumer/     MinIO reads → replay / streaming / collections
│   ├── gui/          Streamlit web interface (optional [gui] extra)
│   └── scripts/      the 8 CLIs (pyproject [project.scripts] entries)
├── tests/            mirror of src/ (moto = simulated S3) + tests/integration/
├── examples/         TOML/JSON config templates (validated by the tests)
├── gnuradio/         record.grc / playback.grc (system GNU Radio, outside venv)
├── docker/           local dev MinIO (docker-compose)
├── docs/             ADRs + guides (including this document)
├── acquire.sh        all-in-one capture (USB + Soapy + healthcheck + capture)
├── launch-gui.bat/.vbs   terminal-free Windows launcher
└── setup-soapy.sh    SoapySDR system → venv bridge (rerun after uv sync)
```

## 3. Cross-cutting conventions (the project's invariants)

- **Bucket layout**:
  `{signal_type}/{YYYY-MM-DD}/{YYYY-MM-DD_HHhMMmSS}_{source}_{id8}/capture.*`
  — parent date in UTC (stable sorting), leaf folder in local time (readable),
  `id8` = 8 random hex chars (anti-collision). A capture is "complete" if both
  `.sigmf-data` **and** `.sigmf-meta` exist; orphans are ignored by
  `list_captures`.
- **Metadata vs tags (ADR-003)**: technical/continuous values → HTTP
  `x-amz-meta-*` headers (readable by HEAD without downloading);
  categorical/searchable values → **S3 tags** (`signal-type`, `operator`,
  `hardware`, `location`…). Both **only on the `.sigmf-data`**.
- **Upload order**: `.sigmf-meta` **before** `.sigmf-data` — a reader arriving
  between the two sees interpretable JSON, not orphan bytes.
- **`update_tags` is a full REPLACE** (S3 API): to change ONE tag, read →
  merge → rewrite, otherwise the others are wiped.
- **Datatype**: everything is normalised to **`cf32_le`** (complex64
  little-endian, 8 bytes/sample). Also what GNU Radio reads natively.
- **Endpoint switch (ADR-001/020)**: empty `s3_endpoint` → real AWS (what moto
  intercepts in tests); set → MinIO/S3-compatible. Migrating storage = changing
  the `.env`, zero code.

---

## 4. Module-by-module reference

### 4.1 `common/config.py` — the settings
- **`Settings(BaseSettings)`** — fields `s3_access_key`, `s3_secret_key`
  (**`SecretStr`**: never in clear in logs), `s3_endpoint`, `s3_bucket`,
  `s3_region`, `s3_verify_ssl` (bool), `s3_ca_bundle` (internal-CA path).
  Loaded from `AEROLAKE_*` env vars and the `.env` (pydantic-settings); real
  env vars override the file.
- **`get_settings()`** — **cached** access (`lru_cache`): the `.env` is parsed
  once per process. Always go through it.

### 4.2 `common/logging.py` — clean logs
- **`configure_logging(level)`** — structlog → **stderr**, leaving stdout to
  results (`--json`, tables). Every CLI calls it first.
- `_StderrLogger` — resolves stderr **at call time** (not at import), so test
  redirections work.

### 4.3 `common/storage.py` — THE S3 access point (~430 lines)
- `_safe_tag_value(value)` / `_tagging_header(tags)` — sanitise tags (S3 only
  accepts letters/digits/` +-=._:/@`; the rest becomes `_`) and build the
  URL-encoded `Tagging` header. *(Born from a real bug: a comma in `location`
  made the whole upload fail.)*
- **`StorageError`** — the storage layer's single exception.
- **`StorageClient`** — methods:
  - `health_check()` — bucket reachable and accessible;
  - `object_exists(key)`, `object_size(key)` (HEAD, zero body bytes),
    `get_object_metadata(key)`, `get_object_tags(key)`, `update_tags(key, tags)`;
  - `upload_bytes(key, data, content_type, *, metadata, tags)` — small object
    in one PUT;
  - **`upload_multipart(key, chunks, …, part_size=8 MiB)`** (ADR-010) — upload
    of a **stream** of chunks without loading everything in RAM; aggregates up
    to `part_size` then sends; returns total bytes; cleans up (abort) on
    failure;
  - `download_bytes(key)`, **`download_range(key, start, end)`** (ADR-009) —
    the partial access that makes "seek" possible on multi-GB captures;
  - `list_objects(prefix)` (paginated), `delete_object(key)`.

### 4.4 `producer/synthetic.py` — the test signal
- **`generate_tone(duration_s, sample_rate, center_freq, tone_offset_hz,
  tone_amplitude, snr_db, seed)`** → **`SyntheticSignal`** (complex64 samples,
  sample_rate, center_freq, description). Complex sine offset by
  `tone_offset_hz` + AWGN noise dosed by `snr_db`; `seed` makes the capture
  **reproducible**.
- `SyntheticParams` — the "synthetic source" block on the orchestrator side.

### 4.5 `producer/soapy_source.py` — the real SDR (~590 lines, ADR-015)
- `list_devices()` — enumerates the SDRs visible to SoapySDR.
- **`SdrRecorder`** — the object owning the device's **full lifecycle**:
  `open()` → `configure(sample_rate, center_freq)` → `start()` → `read(n)` →
  `stop()` → `close()`, usable as a `with` context manager. Key points:
  - **injectable `device_opener`**: tests provide a fake device — the whole
    recorder is tested without hardware;
  - `configure()` **re-reads the effective values** (hardware rounds: we store
    what was actually applied, not what was requested);
  - `read()` counts **overflows** (lost samples) — surfaced all the way to the
    metadata;
  - provenance properties: `serial`, `hardware_info`, `effective_*`;
  - `capture(duration_s, sample_rate, center_freq)` → **`SdrCapture`**
    (samples + full provenance: driver, serial, gain, antenna, overflows).
- `capture_from_sdr(…)` — backward-compatible functional shim on top of the
  recorder; this is what the orchestrator calls.
- `SoapyParams(driver, agc, antenna)` — the "SDR source" block.

### 4.6 `producer/gps.py` — live position via gpsd (ADR-016)
- **`read_geolocation(reader=None)`** — reads ONE gpsd TPV report and returns
  a `core:geolocation` GeoJSON Point **or `None` if no fix** (never an
  invented position); raises if gpsd is unreachable when requested.
- `GpsFix` (+ `fix_from_tpv`, `fix_to_geolocation`) — TPV normalisation:
  `has_fix` (2D+, lat *and* lon), `is_3d` (reliable altitude). Avoids the
  "GPSD trap": GeoJSON's `[lon, lat, alt]` order respected, no raw dump.
- Injectable `reader` → conversion tested without a daemon.

### 4.7 `producer/capture_config.py` — the config schema (pydantic)
- `_StrictModel` — `extra="forbid"`: **any unknown key is rejected** (typos are
  caught at validation, not at runtime).
- **`CaptureConfig`** — the full request: what (`signal_type`, `center_freq`,
  `sample_rate`, `duration_s`), how (`source`, a union discriminated by `type`
  → `SyntheticSourceConfig` | `SoapySourceConfig`), descriptive (`author`,
  `description`, `license`, `operator`), where (`LocationConfig`), plus
  optional `AnnotationConfig` and `AntennaConfig`. `source_params()` translates
  the source block into an orchestrator object.
- Cross-validations to know: `location.gps` **exclusive** with manual
  geolocation; `freq_lower_edge`/`freq_upper_edge` **in pairs** (SigMF rule);
  `GeolocationConfig.to_geojson()` emits the **[lon, lat, alt]** order.
- **Computed** values (datatype, version, datetime, sha512…) are NOT in the
  config: the encoder fills them at capture time.

### 4.8 `producer/config_loader.py` — TOML/JSON → validated config
- **`load_capture_config(path)`** → `CaptureConfig`. Parser chosen by
  extension: `.toml` → `tomllib` (stdlib), otherwise JSON. Three failure
  families (missing file, invalid syntax, schema violation) → **one** readable
  exception: **`ConfigError`** (shown without traceback, exit 2).

### 4.9 `producer/sigmf_writer.py` — SigMF encoding
- **`encode(signal, *, author, recorder, hardware, signal_type, …)`** →
  **`SigMFCapture(data_bytes, meta_bytes)`**. Writes the full SigMF Global:
  `core:datatype=cf32_le`, `core:version` (taken from
  `sigmf.__specification__`, not hard-coded), `core:sample_rate`,
  **`core:sha512`** (integrity), `core:num_channels`, `core:offset`,
  author/description/licence, the `aerolake:*` fields (signal_type, operator,
  location, mobile, hardware_info, overflows), the **geolocation** in the
  captures segment, the single **annotation** (label/comment/band edges +
  antenna pointing) and the **`antenna:`** extension (scalar fields; the
  pointing — polarization/azimuth/elevation — goes into the annotation, per
  the spec).
- `EncodableSignal` (Protocol) — a source's minimal contract: `samples`,
  `sample_rate`, `center_freq`, `description`. This is what makes the encoder
  **source-agnostic**.
- `AnnotationFields` / `AntennaFields` (TypedDict) — the flattened dicts the
  encoder accepts.

### 4.10 `producer/orchestrator.py` — the conductor (~460 lines)
- **`prepare_capture(*, signal_type, duration_s, sample_rate, center_freq,
  source, …, rich)`** → **`PreparedCapture`**. Chains: source resolution
  (`source` type → synthetic or SDR) → acquisition → `encode()` → building of
  the keys (bucket layout, §3), the `x-amz-meta-*` headers (sample-rate,
  center-freq, session-id, datatype, sample-count) and the **tags**
  (signal-type, operator, mobile, recorder, hardware, + SDR provenance:
  sdr-serial/sdr-gain/sdr-antenna, + promoted location and antenna-model).
  **Nothing is stored.** Default `operator` = system login.
- **`push_capture(prepared, storage_client=None, *, with_preview=False)`** →
  `CaptureResult`. Uploads meta **then** data; if `with_preview`,
  `_upload_preview()` renders the PNG and stores it alongside —
  **best-effort** (a preview failure never fails the capture).
- `save_capture_locally(prepared, root="captures")` — same tree as the bucket,
  on disk (the "keep locally" branch).
- `capture_and_upload(…)` — both at once (used by tests).
- `RichMetadata` — the optional descriptive bundle (author, description,
  license, geolocation, annotation, antenna) that the CLI/GUI build from the
  config; the orchestrator doesn't know `CaptureConfig`.

### 4.11 `producer/ingest.py` — bring in an existing recording
- **`ingest_files(*, file_paths, signal_type, sample_rate, center_freq,
  datatype="cf32", …)`** → `IngestResult`. **Streamed** ingestion: reads the
  file(s) in chunks, converts `cu8`/`cs16`/`ci16_le`/`cs32` → **normalised
  `cf32_le`**, computes the SHA-512 on the stored bytes, pushes via
  `upload_multipart` (bounded RAM whatever the size), then writes the
  `.sigmf-meta`. Multi-file = **one** continuous capture (RFSoC
  `RX0_pkt_*.bin` case, concatenated in numeric order).
- `ingest_file(…)` — single-file wrapper.
- **`ingest_sigmf_pair(file_path, …)`** — existing-pair path. Before upload it
  validates the SigMF metadata, checks byte alignment, verifies any existing
  source SHA-512, adds a missing hash, converts `ci16_le` data to normalized
  `cf32_le`, canonicalizes `cf32` metadata to `cf32_le`, and records the hash
  of the stored bytes. Accepted existing-pair datatypes are `cf32`, `cf32_le`,
  and `ci16_le`; source files are never modified.
- This is **the GNU Radio → lakehouse entry bridge** (ADR-019).

### 4.12 `producer/preview.py` — the visual preview
- **`render_spectrum_png(samples, sample_rate, center_freq)`** → PNG bytes:
  PSD (spectrum) on top, spectrogram (waterfall) below. **Lazy** matplotlib
  import + Agg backend (no display required). Subsamples beyond ~2 M samples
  to stay fast.

### 4.13 `consumer/reader.py` — re-reading the lakehouse
- **`CaptureReader`**:
  - `list_captures(prefix)` — all **complete** captures (data+meta pair),
    sorted; orphans are ignored;
  - `inspect(data_key)` → `CaptureInfo` — metadata + tags **without
    downloading a single signal byte** (HEAD + GetObjectTagging);
  - `read(data_key)` → `CaptureContent` — the whole decoded signal (the meta's
    `core:datatype` selects the numpy dtype);
  - **`read_segment(data_key, start_s, duration_s)`** — THE partial read
    (ADR-009): seconds → samples → bytes, then `download_range`; an
    out-of-bounds window is cleanly truncated (empty array if beyond).

### 4.14 `consumer/player.py` — paced software replay (ADR-007 #1)
- `iter_frames(samples, frame_size)` — pure framing.
- **`CapturePlayer.play(data_key, *, frame_size=4096, realtime=True, start_s,
  duration_s, on_frame)`** → `PlaybackStats`. Emits frames **at the original
  sample rate's pace** (`frame_size / sample_rate` s between frames);
  `realtime=False` to go as fast as possible; `on_frame(index, frame)` = the
  hook point (this is where the ZeroMQ publisher plugs in). The clock
  (`sleep`) is **injected** → tests verify the pacing without actually
  waiting.

### 4.15 `consumer/stream.py` — the ZeroMQ bus (ADR-008)
- `encode_frame(topic, header, samples)` / `decode_frame(parts)` — the
  **pure** wire format (3 parts: topic, JSON header, complex64 bytes); tested
  without a network.
- **`FramePublisher.bind("tcp://*:5555", topic, …)`** — PUB;
  `publish(index, frame)` has `on_frame`'s signature → plugs directly into the
  player.
- **`FrameSubscriber.connect(address, topic)`** — SUB; `recv()` → decoded
  frame. Injectable sockets.

### 4.16 `consumer/collection.py` — SigMF Collections (ADR-014)
- **`CollectionBuilder`**: `scan(prefix)` (pairs + **reported** orphans),
  `build(*, prefix, name, description, author)` → **`CollectionPlan`** (the
  assembled document, stream names **relative** to the prefix, sha512 hash of
  each Recording's meta — nothing written: the natural `--dry-run`),
  `write(plan)` (upload of the `.sigmf-collection` at the prefix root).

### 4.17 `gui/app.py` — the web interface (Streamlit)
A facade **with no capture logic of its own**. To maintain it, know:
- Streamlit **re-runs the whole script at every interaction**; what must
  survive (the prepared capture) lives in `st.session_state`.
- **Capture** tab: `_load_uploaded` (uploaded file → temp file →
  `load_capture_config`, same validation path as the CLI) →
  `_location_picker` (folium map; a click returns a GeoJSON Point that
  **replaces** the config's geolocation; degrades silently offline) →
  `_do_capture` (reuses the CLI's `_resolve_geolocation` +
  `_build_rich_metadata` then `prepare_capture`) → `_render_result` (metrics,
  spectrum via `_spectrum_png`, Push/Keep/Discard buttons).
- **Playback** tab: `_render_playback` — `CaptureReader.list_captures` →
  `inspect` → stored PNG preview → scrub (`read_segment` +
  `render_spectrum_png`) → ZeroMQ command shown → `.sigmf-meta`/`.sigmf-data`
  export (big files: redirect to the MinIO console beyond 100 MB).
- Aesthetics: injected CSS (`_CSS`) + **ColorBends** WebGL background
  (three.js in an `st.iframe` pinned full-screen by the `iframe[srcdoc]`
  selector). The base theme lives in `.streamlit/config.toml`.
- `run()` = the `aerolake-gui` entry point (serves on `0.0.0.0`).

### 4.18 `scripts/` — the 8 CLIs
All of them: `configure_logging()` first, `rich` output, **documented exit
codes** (0 ok / 1 storage / 2 config / 3 capture-unexpected), and an
injectable dependency for tests.
- **`capture.py`** — `--config x.toml`: loads/validates, summary, resolves the
  geolocation (`_resolve_geolocation`: gpsd if `gps=true`, else manual point,
  else nothing), flattens (`_build_rich_metadata` — routes the antenna
  pointing to the annotation, SigMF rule), captures, post-capture summary,
  then **confirmation** Push / Keep locally / Discard.
- **`healthcheck.py`** — `.env` + MinIO reachable + bucket accessible;
  `--json` for scripting.
- **`ingest.py`** — file **or folder** ("natural" sort of `RX0_pkt_N` via
  `_natural_key`) → `ingest_files`.
- **`catalog.py`** (`aerolake-list`) — lists/filters by tags
  (`--signal-type`, `--hardware`, `--tag k=v`) using HEAD-class requests only.
- **`collection.py`** — group a prefix; `--dry-run`; stable JSON output.
- **`play.py`** — `--key` or `--prefix` (takes the most recent); `--start/
  --duration` (partial read); `--no-realtime`.
- **`stream.py`** — player + `FramePublisher`; `--bind tcp://*:5555`,
  `--topic`.
- **`subscribe.py`** — subscribes, shows each frame's header and RMS dBFS
  (`_rms_dbfs`: "is there signal?" in one number).

## 5. The tests (27 files, ~210 tests)

- **moto** simulates S3: `tests/conftest.py` provides `test_settings`
  (`s3_endpoint=""` → moto intercepts; values passed as kwargs to be isolated
  from the developer's `.env`), `mock_s3` (bucket pre-created) and
  `storage_client` (a `StorageClient` wired to it). **Inject these fixtures**,
  never hit a real backend in unit tests.
- Hardware is simulated by injection: fake `device_opener` (SoapySDR), fake
  `reader` (gpsd), fake `sleep` (player pacing), fake sockets (ZMQ),
  `prepare/push` stubs (CLIs).
- `tests/test_examples_valid.py` **globs** `examples/*.toml|json`: a template
  that drifts from the schema breaks CI.
- `tests/gui/`: Streamlit **AppTest** smoke test (skipped without the gui
  extra).
- `tests/integration/` (marker `integration`, opt-in
  `AEROLAKE_RUN_INTEGRATION=1`): **real** round trip multipart + Range +
  tagging — also serves as the **conformance test** for any candidate S3
  storage (it validated SeaweedFS, ADR-020).

## 6. CI (`.github/workflows/ci.yml`)

Two jobs: **lint + types + tests** (`ruff check`, `mypy src`,
`pytest -m "not integration"`, dependencies frozen by `uv sync --frozen`) and
**integration** (real MinIO container + `pytest -m integration`).

## 7. Going further

- `docs/code-map.md` — the path of a capture through 6 files (start there).
- `docs/adr/001…020` — every structuring decision, dated and argued.
- `docs/user-manual.md` — the user-side guide.
- `HANDOFF.md` — set up a station, migrate to the lab server, take over the
  project.
