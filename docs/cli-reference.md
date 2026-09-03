# AeroLake CLI Reference

> Start here for exact commands and ingest behavior.
> Read the central index first: [README.md](./README.md)
> Recommended order: [operator-cli-guide.md](./operator-cli-guide.md) → [cli-reference.md](./cli-reference.md) → [IQENGINE-User-Manual.md](./IQENGINE-User-Manual.md) → [user-manual.md](./user-manual.md)

This is the full CLI reference for AeroLake. If you are a system user and want the short overview first, read **`docs/operator-cli-guide.md`**. It explains the common CLI workflows in plain language and points to this page only when you need the detailed command variations and edge cases.

This page lists the everyday commands and the important variations. Run commands
from the repository root after `uv sync` and after starting MinIO with
`docker compose up -d` from `docker/`.

## 1. Quick overview

The most important pattern in AeroLake is the SigMF pair:

- `capture.sigmf-data` = raw IQ sample data
- `capture.sigmf-meta` = metadata JSON describing the capture

The canonical metadata template is `examples/capture.sigmf-meta.example.json`, the practical example is `examples/short_24lines.ingest.sigmf-meta.example.json`, and the full field catalogue is `examples/iqengine-metadata-schema-example.json`. The last one is especially useful when generating a new `.sigmf-meta` file from raw data, because it shows the fields that commonly appear in a fully populated SigMF document after ingest and IQEngine sync.

Use the CLI in these common modes:

- `aerolake-healthcheck` for environment readiness
- `aerolake-capture` to record a new capture from config
- `aerolake-list` to browse catalog entries and filters
- `aerolake-ingest` to add raw files or validate an existing SigMF pair
- `aerolake-play` / `aerolake-stream` / `aerolake-subscribe` for replay and playback

---

## 2. Health Check

```bash
uv run aerolake-healthcheck
```

Checks that the configured S3/MinIO bucket is reachable.

## 3. Capture From Config

```bash
uv run aerolake-capture --config examples/capture.example.toml
```

Use this when AeroLake is doing the capture itself from a TOML or JSON config.
Templates are in `examples/`.

## 4. Ingest An IQ File

`aerolake-ingest` has two modes for handling missing metadata.

### 4.1 Step-by-step ingest examples

These examples show the operational flow from the CLI point of view.

#### A. Ingest when the `.sigmf-meta` file already exists

```bash
ls -l capture.sigmf-data capture.sigmf-meta
uv run aerolake-ingest capture.sigmf-data
```

Expected flow:

1. AeroLake locates `capture.sigmf-data` and `capture.sigmf-meta` next to each other.
2. It validates that the JSON is readable SigMF metadata and uploads it.
3. It checks that the `.sigmf-data` bytes are aligned to the declared datatype.
4. It verifies `global.core:sha512` if one already exists.
5. If the hash is missing, it computes one and prepares to upload the final meta.
6. If the declared datatype is `ci16_le`, it normalizes to stored `cf32_le` before upload.
7. It validates the final metadata and uploads metadata first, then the IQ data.

Typical output looks like:

```text
[ingest] found SigMF pair: capture.sigmf-data, capture.sigmf-meta, uploading...
[ingest] validating metadata schema
[ingest] datatype ok: ci16_le -> normalized cf32_le
[ingest] hash missing; computing core:sha512
[ingest] metadata valid
[ingest] upload metadata object
[ingest] upload data object
[ingest] ingest complete
```

#### B. Ingest when the `.sigmf-meta` file is missing

```bash
ls -l capture.sigmf-data
uv run aerolake-ingest capture.sigmf-data \
  --signal-type iridium \
  --sample-rate 10e6 \
  --center-freq 1622e6 \
  --datatype ci16_le \
  --hardware bladerf
```

Expected flow:

1. AeroLake notices there is no local `.sigmf-meta` sidecar.
2. It creates a same-basename `.sigmf-meta` file containing the CLI values, derived defaults, and `<missing:...>` placeholders only for values the CLI cannot know.
3. It stops before upload so you can enter the missing operator, location, and other capture-specific values.
4. You complete and save the metadata using the SigMF examples and schema reference.
5. You rerun `uv run aerolake-ingest capture.sigmf-data` without metadata flags.
6. AeroLake validates the completed pair, normalizes the stored bytes to `cf32_le`, computes `core:sha512`, and uploads metadata first, then data.

Typical output looks like:

```text
[ingest] source file detected: capture.sigmf-data
[ingest] no local sigmf-meta found; creating editable metadata template
[ingest] created capture.sigmf-meta
[ingest] fill <missing:...> values and rerun without metadata flags
[ingest] nothing uploaded
```

> Important: in this mode, placeholders such as `REPLACE_WITH_*`, `TODO`, or `<missing:...>` should not be left in the uploaded metadata. The values must be filled with real SigMF-compatible data. Use `examples/capture.sigmf-meta.example.json` and `examples/iqengine-metadata-schema-example.json` as the reference for the expected field layout and value shapes.

After completing the template:

```bash
uv run aerolake-ingest capture.sigmf-data
```

### 4.2 Generate The SigMF Meta From CLI Flags

Use this when you have an IQ data file and want to supply capture values from
the command line. For a single file without a same-basename `.sigmf-meta`, the
first run creates an editable placeholder metadata file and stops before upload.
After completing it, rerun without metadata flags to validate and upload the
SigMF pair.

```bash
uv run aerolake-ingest capture.sigmf-data \
  --signal-type gnss_l1 \
  --sample-rate 2e6 \
  --center-freq 1575.42e6 \
  --hardware bladerf
```

For raw integer IQ, specify the source datatype:

```bash
uv run aerolake-ingest dump.iq \
  --signal-type iridium \
  --sample-rate 10e6 \
  --center-freq 1622e6 \
  --datatype ci16_le \
  --hardware bladerf
```

Supported source datatypes for generated-meta ingest:

```text
cf32, cu8, cs16, ci16_le, cs32
```

The generated-data checklist is: confirm the source datatype is supported,
confirm every file is aligned to a complete IQ sample, complete all metadata
placeholders with real SigMF-compatible values, normalize the source to stored
`cf32_le`, compute the hash over the stored bytes, validate the metadata, then
upload metadata followed by data.

> **Important**: do not leave `REPLACE_WITH_*`, `TODO`, or `<missing:...>` values
> in metadata that will be uploaded. Use `examples/capture.sigmf-meta.example.json`
> and `examples/iqengine-metadata-schema-example.json` as references for the
> expected field layout and value shapes.

### 4.3 Ingest An Existing SigMF Pair

Use this when you already have both files:

```text
capture.sigmf-data
capture.sigmf-meta
```

Run:

```bash
uv run aerolake-ingest capture.sigmf-data
```

Existing-pair ingest runs checks before uploading: the metadata must be valid,
the data must be aligned to its declared datatype, and an existing
`global.core:sha512` must match the local `.sigmf-data`. If the hash is missing,
AeroLake computes it and adds it to the uploaded meta automatically:

```bash
uv run aerolake-ingest capture.sigmf-data
```

If required canonical fields are missing, or the `annotations` array is empty,
ingest adds safe defaults and `<missing:...>` placeholders to the local
`.sigmf-meta`, reports the unresolved fields, and stops before upload. An empty
`annotations: []` array is therefore a required correction step, not an
optional warning. For an Iridium capture, rerun with `--iridium-annotate` to
generate the annotations:

```bash
uv run aerolake-ingest capture.sigmf-data --iridium-annotate
```

For another signal type, add an appropriate SigMF annotation object manually,
then rerun `uv run aerolake-ingest capture.sigmf-data`. Do not add metadata
flags when rerunning an existing pair.
After the file is completed, existing `ci16_le` data is
converted to normalized `cf32_le` before upload, and the metadata/hash describe
the converted bytes. Legacy `cf32` metadata is canonicalized to `cf32_le`.
The `--ensure-sha512` option remains accepted for compatibility but is no
longer required.

The existing-pair checklist is: locate both files, parse and validate SigMF,
check datatype support and byte alignment, verify any existing source hash,
normalize `ci16_le` when declared, canonicalize `cf32` to `cf32_le`, compute the
stored-byte hash, validate the final metadata, then upload metadata followed by
data. The source files are never modified. The currently accepted pair
datatypes are `cf32`, `cf32_le`, and `ci16_le`.

The signal type is required for existing-pair ingest so the capture can be
labeled and written under the correct bucket prefix. Add the AeroLake signal
field inside the meta `global` object before ingest:

```json
{
  "global": {
    "core:datatype": "ci16_le",
    "core:sample_rate": 10000000,
    "aerolake:signal_type": "iridium"
  }
}
```

Without `aerolake:signal_type`, ingest stops with a message asking you to
indicate the signal type. There is no fallback prefix. The local metadata file
is not modified, so add the field locally and run ingest again.

Do not pass `--signal-type` in this mode. Supplying metadata flags selects the
generated-meta mode.

### 4.4 Metadata placeholders and IQEngine reference schema

When a local `.sigmf-meta` file is missing, AeroLake is creating a new metadata record from scratch. This is the moment to fill the placeholders with the correct values for the recording, not to leave a half-empty document behind.

The schema example in `examples/iqengine-metadata-schema-example.json` shows the fields that commonly appear in a valid capture after AeroLake ingest and later IQEngine sync. It is a useful field catalogue for `global`, `captures`, and annotation conventions, and it helps distinguish values that are required, optional, or set by the catalog UI after refresh.

Preview/thumbnail sidecars are not produced by AeroLake ingest. They are created by IQEngine after clicking refresh and opening the capture in the IQEngine UI. AeroLake remains responsible for the SigMF pair and metadata validation.

### 4.5 Iridium Annotation During Generated-Meta Ingest

Use this when AeroLake is creating the meta and you want to apply annotations
from `iridium-toolkit` before uploading to MinIO.

Requirements:

- `iridium-extractor` available on `PATH`
- `pypy3` installed
- `iridium-toolkit` cloned, with `iridium-parser.py` available

Command:

```bash
uv run aerolake-ingest test.sigmf-data \
  --signal-type iridium \
  --sample-rate 10e6 \
  --center-freq 1622e6 \
  --datatype ci16_le \
  --hardware bladerf \
  --iridium-annotate \
  --iqengine
```

By default this runs the equivalent of:

```bash
iridium-extractor /path/to/test.sigmf-data | \
  pypy3 ~/iridium-toolkit/iridium-parser.py --sigmf-annotate=/path/to/test.sigmf-meta -
```

Override tool paths if needed:

```bash
uv run aerolake-ingest test.sigmf-data \
  --signal-type iridium \
  --sample-rate 10e6 \
  --center-freq 1622e6 \
  --datatype ci16_le \
  --iridium-annotate
```
### 4.6 Stopping an ingest safely

**Press Ctrl+C to stop the ingest.** This is the supported stop action: AeroLake
stops the process and deletes the metadata, data, and sidecar objects created by
that unsuccessful attempt.

Other ways of stopping the command do **not** guarantee cleanup:

- **Ctrl+Z** only pauses the process (`SIGSTOP`); it does not delete anything.
  To stop and clean up afterward, run `fg` and then press Ctrl+C.
- Closing the terminal, killing the shell, or using an uncatchable kill can
  leave already-uploaded objects behind because AeroLake does not get a chance
  to run its cleanup handler.

Use Ctrl+C when you want an interrupted ingest to be removed from storage.

## 5. Query Recordings (List Captures)

Use `aerolake-list --catalog iqengine` with at least one filter to run the CLI
equivalent of IQEngine's Query Recordings panel. 

To initialize the catalog queries, run:
```bash
bash init-sync.sh # From wsl/linux to sync catalog query OR double-click on init-sync.bat for Windows
```
Also, configure IQEngine in the repository's local `.env` file (do not commit credentials):

```dotenv
AEROLAKE_IQENGINE_URL=https://<iqengine-host>
AEROLAKE_IQENGINE_ACCOUNT=<datasource-account>
AEROLAKE_IQENGINE_CONTAINER=<datasource-container>
```

These variables are listed in `.env.example`. Always include both `--catalog iqengine` and
at least one filter when testing IQEngine:

```bash
uv run aerolake-list --catalog iqengine \
  --min-frequency 1621000000 \
  --max-frequency 1623000000 \
  --signal-type iridium \
  --hardware bladerf \
  --min-datetime "2026-09-01T00:00:00Z" \
  --max-datetime "2026-09-02T23:59:59Z" \
  --author "Camila Nino Francia" \
  --location "Montreal" \
  --text "newflight" \
  --operator "Camila Nino Francia" \
  --recorder "aerolake-ingest" \
  --json
```

Use the value format accepted by the configured IQEngine API for frequency and
datetime bounds. For a simple smoke test:

```bash
uv run aerolake-list --catalog iqengine --signal-type iridium --json
```

The CLI returns JSON containing the selected catalog, freshness state, filters,
match count, and capture keys. A successful IQEngine response should contain
`"catalog": "iqengine"`.

`--catalog iqengine` is strict: if `AEROLAKE_IQENGINE_URL` is not configured,
the command reports an IQEngine configuration error instead of querying MinIO.
Use `--json` when testing so the selected catalog is visible in the result;
the human-readable table does not display the catalog source.

For a direct MinIO query, use only tag filters:

```bash
uv run aerolake-list --catalog minio --signal-type iridium --hardware bladerf --json
```

The IQEngine endpoint supports account and container selectors, frequency and
datetime bounds, author, location, signal type, hardware, operator, recorder,
and text search. Repeat `--account` or `--container` to query multiple values.
MinIO does not provide the MongoDB metadata query layer, so these metadata
filters require IQEngine.

To test IQEngine specifically, use the explicit catalog and a filter shown
above. If it fails, check the URL, token/account/container settings, and
whether the capture has been refreshed in IQEngine. `--catalog auto` uses
IQEngine only when configured and available; otherwise it falls back to MinIO.
With no filters, that fallback lists every complete MinIO capture.

IQEngine searches are read-only API calls. AeroLake never accesses IQEngine's
MongoDB. When the configured freshness interval has elapsed, a single
background sync is triggered and the current result is returned with stale
status; the JSON output includes `catalog`, `stale`, `sync_in_flight`, and
`sync_error`. Set `AEROLAKE_IQENGINE_SYNC_STATE_PATH` to persist the last sync
outcome across CLI processes. MinIO remains the degraded fallback and direct
capture reading/replay continues to use AeroLake's existing storage paths.

Listing either source avoids downloading IQ data.

## 6. Collections

```bash
uv run aerolake-collection \
  --prefix iridium/2026-07-20/ \
  --name "flight-test" \
  --description "Iridium flight test"
```

Creates a SigMF collection from complete `.sigmf-data` + `.sigmf-meta` pairs
under a prefix.

## 7. Playback And Streaming

Preview/play a stored capture:

```bash
uv run aerolake-play --prefix iridium/
```

Serve frames over ZeroMQ:

```bash
uv run aerolake-stream --prefix iridium/
```

Subscribe to the ZeroMQ stream:

```bash
uv run aerolake-subscribe --address tcp://localhost:5555
```
