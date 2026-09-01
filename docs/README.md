# AeroLake Documentation Index

This is the recommended reading order for operators using the CLI and IQEngine.

## Read in order

### 1. Operator CLI guide

Start here if you are using Aerolake as a system and not reading the Python code.

- [operator-cli-guide.md](./operator-cli-guide.md)
- purpose: overview of the main workflows, common commands, and the core SigMF structure

### 2. CLI reference

Use this when you need the exact commands and the ingest rules.

- [cli-reference.md](./cli-reference.md)
- purpose: detailed command reference, step-by-step ingest flows, validation, and metadata conventions

### 3. IQEngine user manual

Use this when you need to inspect, search, and refresh captures through the browser UI.

- [IQENGINE-User-Manual.md](./IQENGINE-User-Manual.md)
- purpose: how IQEngine indexes metadata, how data sources work, when to refresh, and how to open a capture

### 4. User manual

Use this for day-to-day operational use and troubleshooting.

- [user-manual.md](./user-manual.md)
- purpose: capture workflow, playback, setup, troubleshooting, and operational context

### 5. Handoff / station setup

Use this only when setting up a station or taking over the project.

- [handoff-document.md](./handoff-document.md)
- purpose: full installation, configuration, and technical handoff context

---

## By task

### I want to ingest a capture

- [operator-cli-guide.md](./operator-cli-guide.md)
- [cli-reference.md](./cli-reference.md)

### I want to find and inspect a capture in IQEngine

- [IQENGINE-User-Manual.md](./IQENGINE-User-Manual.md)
- [user-manual.md](./user-manual.md)

### I want to understand the capture structure

- [operator-cli-guide.md](./operator-cli-guide.md)
- [cli-reference.md](./cli-reference.md)
- [../examples/capture.sigmf-meta.example.json](../examples/capture.sigmf-meta.example.json)
- [../examples/short_24lines.ingest.sigmf-meta.example.json](../examples/short_24lines.ingest.sigmf-meta.example.json)
- [../examples/iqengine-metadata-schema-example.json](../examples/iqengine-metadata-schema-example.json)

### I want to set up the system or take over the station

- [handoff-document.md](./handoff-document.md)
- [user-manual.md](./user-manual.md)

---

## Developer docs

These are not the default operator path:

- [code-map.md](./code-map.md)
- [code-documentation.md](./code-documentation.md)
- [adr](./adr/)

Use those only when you need the implementation details or architecture decisions.
