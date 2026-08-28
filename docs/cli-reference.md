# AeroLake CLI Reference

This page lists the everyday commands and the important variations. Run commands
from the repository root after `uv sync` and after starting MinIO with
`docker compose up -d` from `docker/`.

## Health Check

```bash
uv run aerolake-healthcheck
```

Checks that the configured S3/MinIO bucket is reachable.

## Capture From Config

```bash
uv run aerolake-capture --config examples/capture.example.toml
```

Use this when AeroLake is doing the capture itself from a TOML or JSON config.
Templates are in `examples/`.

## Ingest An IQ File

`aerolake-ingest` has two modes.

### 1. Generate The SigMF Meta From CLI Flags

Use this when you have an IQ data file but no `.sigmf-meta`, or when you want
AeroLake to create a fresh metadata file.

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

In this mode, `--signal-type`, `--sample-rate`, and `--center-freq` are
required. AeroLake creates a new `.sigmf-meta`, uploads it to MinIO, streams the
data, computes `core:sha512`, then uploads the final meta.

The generated-data checklist is: confirm the source datatype is supported,
confirm every file is aligned to a complete IQ sample, convert the source to
normalized `cf32_le`, compute the hash over the converted bytes, validate the
generated SigMF metadata, then upload metadata followed by data.

### 2. Ingest An Existing SigMF Pair

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
`.sigmf-meta`, reports the unresolved fields, and stops before upload. For an
empty annotations array, rerun with `--iridium-annotate` to generate annotations.
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

### IQEngine Sidecars

Add `--iqengine` to either ingest mode:

```bash
uv run aerolake-ingest capture.sigmf-data --iqengine
```

With an existing SigMF pair, sidecars are generated from the source datatype
before any normalization and the uploaded metadata reflects the stored
`cf32_le` representation. It generates or reuses:

```text
capture.jpg
capture.preview.jpg
capture.minimap
```

To regenerate existing local sidecars:

```bash
uv run aerolake-ingest capture.sigmf-data --iqengine redo
```

### Iridium Annotation During Generated-Meta Ingest

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
  --iridium-annotate \
  --iridium-parser /path/to/iridium-toolkit/iridium-parser.py \
  --iridium-extractor /path/to/iridium-extractor \
  --pypy /path/to/pypy3
```

## List Captures

```bash
uv run aerolake-list
uv run aerolake-list --signal-type iridium
uv run aerolake-list --prefix iridium/
```

Listing reads MinIO object metadata and tags without downloading IQ data.

## Collections

```bash
uv run aerolake-collection \
  --prefix iridium/2026-07-20/ \
  --name "flight-test" \
  --description "Iridium flight test"
```

Creates a SigMF collection from complete `.sigmf-data` + `.sigmf-meta` pairs
under a prefix.

## Playback And Streaming

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
