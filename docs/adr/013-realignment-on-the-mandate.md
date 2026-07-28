# ADR-013 — Realignment on the mandate's scope (refocus)

- **Status:** Accepted
- **Date:** 2026-06-08
- **Author:** Théo Schmitt
- **Supersedes:** reorients the prioritisation of ADR-004 (does not remove ADR-004; corrects its scope)

## Context

The project mandate (`docs/LASSENA-Project_AeroLake.pdf`) defines a clear scope,
organised in four sprints: an end-to-end RX pipeline — SDR capture → MinIO
storage in the SigMF format (with metadata and tags) → extraction through HTTP
Range Requests → ZeroMQ streaming bus — able to sustain 25 MHz, and **ready**
(but not required) to host a TX transmission in a future phase.

Two deviations from that scope had accumulated:

1. **A shift in priority (ADR-004).** Following a discussion with my supervisor,
   the priority had moved towards *data quality* and building a *curated
   dataset*, which led to **deferring the streaming path** (multipart upload,
   HTTP Range, ZeroMQ) — even though it sits at the heart of the mandate. The
   quality layer itself was legitimate; what made the project diverge from the
   expected order was putting it *first*, to the point of pushing back the core
   of the mandate.

2. **Unrequested explorations.** Beyond that shift, the repository had grown
   components absent from the mandate: a visualisation GUI (Streamlit/Plotly,
   ADR-006), a decoded-data analysis module (Doppler/IMU/GPS over `.h5` files,
   ADR-011), and a TX transmission path (BladeRF flowgraph + MinIO→file bridge,
   ADR-012). TX is explicitly labelled a "future phase" by the mandate; the GUI
   and the analysis module do not appear in it at all.

A recent meeting with my supervisor confirmed this: the project had drifted away
from what was asked. This ADR records the refocus.

## Decision

**We realign `main` on the mandate's scope. The RX → MinIO → extraction → ZeroMQ
pipeline becomes the priority again; everything else is kept but moved off the
main path.**

1. **The streaming path regains its priority.** Extraction through HTTP Range
   Requests and ZeroMQ Pub/Sub publication are no longer deferred: they are the
   core deliverable of the mandate (Sprints 2–3). The reverse prioritisation of
   ADR-004 (quality first, streaming later) is cancelled.

2. **The quality layer is kept as a support tool, not as the central axis.**
   `aerolake.quality` (metrics + checker) and `CaptureReader.validate()` stay in
   the project: they serve the mandate's sprint validation criteria (intact
   waterfall, `Samples In == Samples Out`, lossless profiling report). What
   changes is their *status*: a means of validating captures, not the purpose of
   the project.

3. **Out-of-scope explorations are archived, not deleted.** The GUI (ADR-006),
   the `.h5` analysis (ADR-011) and the TX path (ADR-012) are removed from
   `main` and preserved in full on the `archive/explorations-v1` branch. They
   remain recoverable at any time and may become relevant again in a later
   phase.

4. **The corresponding ADRs are not erased.** ADR-006, ADR-011 and ADR-012 stay
   in `docs/adr/`, marked "archived — out of phase-1 scope (see ADR-013)". The
   record of decisions is preserved; we do not rewrite history.

## Rationale

- **The mandate is authoritative.** The expected scope is written down in the
  project PDF; when a local initiative and the mandate diverge, the mandate
  wins.
- **Nothing is lost.** Archiving through a Git branch guarantees that the work
  done (good, but premature) stays available. The refocus is fully reversible.
- **The core is healthy.** After removing the out-of-scope components, the test
  suite passes (124 passed, 1 skipped — the integration test requiring a real
  MinIO), `ruff` and `mypy` report nothing: no part of the core depended on the
  removed surplus.
- **Quality stays useful without being central.** Keeping it as support rather
  than as the goal satisfies both the earlier instruction (measure capture
  quality) and the mandate (deliver the RX→ZMQ pipeline).

## Consequences

### Positive

- `main` now reflects the mandate: a reader (supervisor, newcomer) immediately
  understands the core of the project without drowning in peripheral
  components.
- The project is repositioned to resume the mandate's sprint order (extraction
  + ZeroMQ, then GNSS/Iridium/Starlink 25 MHz throughput tests).
- The dependency footprint is reduced (streamlit, plotly, h5py, skyfield and
  their transitive dependencies removed).

### Negative

- The archived features are no longer reachable from `main`; reactivating them
  will require reintegration work (and a new ADR at that point).

### Neutral

- The storage, metadata and tagging conventions (ADR-001, ADR-003) are
  unchanged.
- ADR-004 remains readable as the record of the past reorientation; this ADR
  only corrects its priority, without denying the discussion that motivated it.

## Realigned sprint plan

- **Sprint 1 — done.** Ingestion infrastructure: MinIO stack, `StorageClient`,
  synthetic producer, SigMF encoding, `CaptureReader`, metadata + tags
  convention, healthcheck CLI. (Real SDR capture through SoapySDR: still to
  come — the producer generates synthetic signals today.)
- **Sprint 2 — current priority.** Extraction through HTTP Range Requests +
  ZeroMQ Pub/Sub publication, with `Samples In == Samples Out` proof. The
  quality layer serves here as a validation tool.
- **Sprint 3 — next.** Throughput tests: GNSS (position lock via GNSS-SDR),
  Iridium (1 h continuity), Starlink 25 MHz (200 MB/s sustained).
- **Sprint 4 — next.** `.env`/CLI externalisation, Confluence documentation,
  on-prem migration guide.
- **Future phases (archived).** Visualisation (ADR-006), `.h5` analysis
  (ADR-011), TX transmission (ADR-012), Parquet/Iceberg analytical evolution.

## References

- `docs/LASSENA-Project_AeroLake.pdf` — the project mandate (authoritative scope)
- ADR-004 — the quality reorientation whose priority this ADR corrects
- ADR-002 — batch/streaming deferral (streaming regains its priority here)
- ADR-006, ADR-011, ADR-012 — decisions of the archived components
- Branch `archive/explorations-v1` — complete state preserved before the refocus
