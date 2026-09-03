# AeroLake CLI Guide for System Users

> Start here for the operator workflow.
> Read the central index first: [README.md](./README.md)
> Recommended order: [operator-cli-guide.md](./operator-cli-guide.md) → [cli-reference.md](./cli-reference.md) → [IQENGINE-User-Manual.md](./IQENGINE-User-Manual.md) → [user-manual.md](./user-manual.md)

This is the short, operator-focused guide for people who use AeroLake as a system, not as a developer working on the Python code.

If you only need the essentials, read this page first. If you need the exact command variants, edge cases, or advanced behavior, continue to the deeper reference in [cli-reference.md](./cli-reference.md).

For access to FAST home directory, MinIO, and IQEngine, see **https://fast.etsmtl.ca/**

---
## 1. What AeroLake is doing

AeroLake records RF signals and stores them as SigMF captures:

- one raw IQ file: `capture.sigmf-data`
- one metadata file: `capture.sigmf-meta`

Preview sidecars are not created by AeroLake ingest itself. If IQEngine is configured, it creates preview/thumbnail assets after a capture is refreshed and opened in the IQEngine UI. AeroLake's responsibility is the SigMF pair and the validated metadata.

The metadata file is the key to understanding what a capture is. Use [../examples/capture.sigmf-meta.example.json](../examples/capture.sigmf-meta.example.json) as the canonical layout reference and [../examples/iqengine-metadata-schema-example.json](../examples/iqengine-metadata-schema-example.json) as the field-level reference for the kinds of values that can legitimately appear after ingestion and sync. Use [../examples/short_24lines.ingest.sigmf-meta.example.json](../examples/short_24lines.ingest.sigmf-meta.example.json) as a real, populated example of a short capture.

### The metadata structure at a glance

The SigMF metadata is organized in three main blocks:

1. `global`
   - describes the recording itself
   - includes the author, sample rate, datatype, frequency, hardware, description, signal type, and integrity fields such as `core:sha512`
   - this is the most important section for cataloging and searching captures

2. `captures`
   - gives the capture timing and center frequency for the recording
   - usually contains a single capture entry with `core:datetime`, `core:frequency`, and `core:sample_start`

3. `annotations`
   - optional per-segment or per-event notes
   - often empty for a simple capture, but it is still part of the SigMF structure

In other words: `global` tells you what the recording is, `captures` tells you when and at what frequency it was taken, and `annotations` adds extra notes if needed.

---

## 2. The most common CLI workflows

These are the usual tasks for an operator.

### A. Check that the system is healthy

```bash
uv run aerolake-healthcheck
```

Use this to confirm that the MinIO storage and the configured AeroLake environment are reachable.

### B. Capture a signal from a configuration file

```bash
uv run aerolake-capture --config examples/capture.example.toml
```

This is the normal capture workflow when the system is recording directly from a known configuration.

### C. List existing captures

To initialize the catalog queries, run:
```bash
bash init-sync.sh # From wsl/linux to sync catalog query OR double-click on init-sync.bat for Windows
```
CLI examples:
```bash
uv run aerolake-list
uv run aerolake-list --signal-type iridium
uv run aerolake-list --prefix iridium/
```

This lets you browse or filter the lakehouse without downloading the full IQ data.

To use the same filter concepts as IQEngine's Query Recordings panel, query the
IQEngine catalog explicitly. The CLI test must include `--catalog iqengine` and
at least one filter:

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

Use the datetime format accepted by the configured IQEngine API. These metadata
filters require `--catalog iqengine`; direct MinIO queries support signal type,
hardware, and generic tags.

Do not use `--catalog auto` when you need to verify IQEngine specifically.
`auto` falls back to MinIO when IQEngine is not configured or unavailable. With
no filter, that fallback lists every complete MinIO capture, so the output can
show `"catalog": "minio"` even though IQEngine is part of the deployment.

### D. Ingest an existing IQ file

```bash
uv run aerolake-ingest capture.sigmf-data \
  --signal-type iridium \
  --sample-rate 10e6 \
  --center-freq 1622e6 \
  --datatype ci16_le \
  --hardware bladerf
```

This is used when a raw file exists and you want AeroLake to create a matching SigMF metadata file and upload the result.

> **Important**: when no `.sigmf-meta` file exists, you are creating the capture identity card from scratch. Fill the placeholder metadata fields with real values that match the SigMF format and the AeroLake conventions. Do not leave `REPLACE_WITH_*`, `TODO`, or other placeholders in a file that is being uploaded. The IQEngine schema example in [../examples/iqengine-metadata-schema-example.json](../examples/iqengine-metadata-schema-example.json) is the best reference for which fields may exist and which value shapes they expect.

### E. Ingest an existing SigMF pair

```bash
uv run aerolake-ingest capture.sigmf-data
```

Use this when both `capture.sigmf-data` and `capture.sigmf-meta` already exist. AeroLake validates the metadata, checks the sample alignment, and prepares the upload. If the meta file is missing, rerun with the metadata flags shown in section D; AeroLake will create an editable `.sigmf-meta` template with `<missing:...>` values and stop before upload. Complete that file, then rerun this command.

The metadata must also contain at least one annotation before an existing pair
can be uploaded. An empty `annotations: []` array is reported as incomplete.
For an Iridium capture, let the annotation tools populate it:

```bash
uv run aerolake-ingest capture.sigmf-data --iridium-annotate
```

This command uses the existing `capture.sigmf-meta`, adds the detected Iridium
annotations, validates the completed pair, and then uploads it. If the capture
is not Iridium, add an appropriate SigMF annotation object manually before
rerunning the ingest command. Do not use `--signal-type`, `--sample-rate`, or
`--center-freq` in existing-pair mode.

### F. Replay or stream a capture

```bash
uv run aerolake-play --prefix iridium/
uv run aerolake-stream --prefix iridium/
uv run aerolake-subscribe --address tcp://localhost:5555
```

These commands let you replay the data or push it onto a ZeroMQ stream for downstream tools.

---

## 3. The quick decision guide

| If you want to... | Use this command |
|---|---|
| confirm storage and config health | `uv run aerolake-healthcheck` |
| record a signal directly from a config | `uv run aerolake-capture --config ...` |
| scan the catalog | `uv run aerolake-list` |
| add a raw IQ file to the lakehouse | `uv run aerolake-ingest ...` |
| validate and upload an existing SigMF pair | `uv run aerolake-ingest capture.sigmf-data` |
| replay a capture | `uv run aerolake-play --prefix ...` |
| stream to another tool | `uv run aerolake-stream --prefix ...` |

---

## 4. The most important idea to remember

Every capture should be treated as a standard SigMF pair, not as a loose file plus random notes.

The pair is usually:

```text
capture.sigmf-data
capture.sigmf-meta
```

The `.sigmf-meta` file is the machine-readable identity card of the capture. The `.sigmf-data` file contains the raw IQ samples. The CLI validates and normalizes those files before upload, and it always keeps the integrity of the stored data by checking the hash.

---

## 5. Need more detail?

This page is the summary. For the full command reference, validation rules, edge cases, generated metadata mode, existing-pair mode, and Iridium annotation options, see:

- [cli-reference.md](./cli-reference.md)
- [user-manual.md](./user-manual.md)
- [code-map.md](./code-map.md)

If you are not a developer and you mainly need the system workflow, start with this page and only jump to the CLI reference when you need the exact option list or advanced behavior.
