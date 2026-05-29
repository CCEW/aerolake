# ADR-004 — Prioritize data quality and curated dataset over streaming

- **Status:** Accepted
- **Date:** 2026-05-29
- **Author:** Théo Schmitt
- **Supersedes:** N/A (complements ADR-002, does not replace it)

## Context

ADR-002 deferred the streaming pipeline (multipart uploads, HTTP Range
Requests, ZeroMQ playback) to "Sprint 2+", and explicitly left the
end-to-end latency target as "to be confirmed with the project lead".

That confirmation happened during a phone call with the project lead
(Malek) on 2026-05-29. The guidance materially changes the project
priorities:

1. **Real-time is not an immediate requirement.** The consumer must
   respect the *temporal cadence* of the data (replay samples at their
   recorded rate), but not the *absolute latency*. The example given:
   the lakehouse could serve slow processes such as daily temperature
   variation, where sub-second delivery is meaningless.

2. **The real question is throughput, not latency.** Network latency is
   out of our control (the deployment runs over WiFi). The meaningful
   acceptance question is binary: "can the datalake keep up with the
   required cadence, yes or no?" — a throughput question, not a latency
   one.

3. **Archival is manual.** There will be no automated lifecycle policy
   (no automatic raw -> archived transition after N days). Archival is
   performed manually when needed.

4. **The priority is input data quality, not retention.** The explicit
   end goal stated by the project lead: *"build our own curated
   dataset"*. The value is in guaranteeing the quality of each capture
   that enters the lakehouse, not in managing how long captures are
   retained.

These points arrived after Sprint 1 (the batch producer/consumer
infrastructure) was complete, and they reorder what comes next.

## Decision

**We prioritize the data-quality axis and reorder the sprint plan.**

1. The **quality layer** becomes the immediate focus and is treated as
   the redefined Sprint 2 scope:
   - `aerolake.quality.metrics` — pure functions computing objective
     quality indicators (clipping, RMS power, invalid samples, DC
     offset, sample completeness, SigMF metadata validity).
   - `aerolake.quality.checker` — `QualityChecker` / `QualityReport`
     orchestration with configurable thresholds.
   - `CaptureReader.validate()` — read a capture, assess it, store a
     `quality_report.json` artifact next to it, and promote the MinIO
     `quality` tag (`raw -> validated` or `raw -> rejected`).
   - A batch validation CLI (planned) to curate whole prefixes at once.

2. The **streaming pipeline** (multipart upload, HTTP Range Requests,
   ZeroMQ Pub/Sub playback) — originally Sprint 2 — is **deferred to a
   later sprint**, because real-time delivery is no longer an immediate
   requirement.

3. The **archival / lifecycle automation** is **dropped from scope**.
   Archival is manual. The `quality` tag lifecycle is kept and extended
   with a `rejected` state, but no automated transition is implemented.

The `quality` tag lifecycle from ADR-003 is therefore extended:

    raw -> validated   (passed quality checks)
    raw -> rejected    (failed quality checks)
    validated -> archived  (manual, when needed)

## Rationale

- **Follows the project lead's explicit direction.** The end goal is a
  curated dataset; quality validation is the mechanism that produces it.
- **Unblocked work.** Quality validation does not depend on the latency
  target, the remote MinIO server, or real SDR hardware. It can proceed
  immediately and entirely on synthetic data.
- **ADR-002 remains valid, just longer.** Batch upload was already the
  Sprint 1 choice; with streaming deferred, the batch pipeline simply
  stays in use longer. Nothing built so far is invalidated.
- **Curating requires measurement.** Marking a capture `validated` only
  makes sense if the decision is backed by objective metrics rather than
  human guesswork. The quality layer provides exactly that evidence.

## Consequences

### Positive

- A capture's `quality` tag now reflects a measured verdict, enabling
  the bucket to be filtered down to a clean, curated subset
  (`quality=validated`).
- Each validated/rejected capture carries a `quality_report.json`
  artifact for traceability and debugging.
- The work is fully testable on synthetic signals; no dependency on
  hardware or remote infrastructure.
- The discovery that the default synthetic generator produced
  full-scale (clipping) signals was surfaced *by* the quality layer,
  and fixed (see the `tone_amplitude` change defaulting to -20 dBFS).

### Negative

- The documented sprint plan now diverges from the original mandate
  ordering. This ADR is the record reconciling the two; downstream docs
  (Confluence, weekly slides) must be updated to match.
- Streaming and ZeroMQ playback — required by the mandate for the final
  deliverable — are pushed further out. They remain in scope overall,
  just later in the timeline.

### Neutral

- The key layout, metadata, and tagging conventions (ADR-001, ADR-003)
  are unaffected. The quality layer consumes them; it does not change
  them.
- A separate GNU Radio deliverable ("Record / Playback" flowgraph,
  `.grc` format) was also requested by the project lead. It is noted
  here for traceability but is tracked as its own work item, not part
  of this decision.

## Revised sprint plan

- **Sprint 1 — done.** Batch producer/consumer infrastructure: MinIO
  stack, `StorageClient`, synthetic producer, SigMF encoding,
  `CaptureReader`, metadata + tagging convention, healthcheck CLI.
- **Sprint 2 — redefined, in progress.** Data quality + curated dataset:
  quality metrics, `QualityChecker`, `CaptureReader.validate()`, batch
  validation CLI.
- **Sprint 3+ — deferred.** Streaming pipeline (multipart upload, HTTP
  Range Requests), ZeroMQ Pub/Sub playback, real SDR integration via
  SoapySDR, Starlink 25 MHz throughput benchmarking.

## Alternatives considered

### Keep the original sprint order (streaming next)

Rejected: it would ignore the project lead's explicit reprioritization
toward data quality, and would invest effort in real-time delivery that
is no longer an immediate requirement.

### Implement automated lifecycle / retention now

Rejected: the project lead stated archival is manual. Automating it
would be speculative work against an explicit "not needed" signal.

## References

- Phone call with project lead (abdu), 2026-05-29 — reprioritization
  toward data quality and curated dataset
- ADR-002 (batch vs streaming) — this ADR confirms the deferred latency
  target it left open
- ADR-003 (metadata and tagging convention) — the `quality` tag
  lifecycle extended here with a `rejected` state
- `src/aerolake/quality/metrics.py`, `src/aerolake/quality/checker.py`
- `src/aerolake/consumer/reader.py` (`CaptureReader.validate`)
