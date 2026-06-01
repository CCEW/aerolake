# ADR-010 — Streaming multipart upload to bypass RAM

- **Status:** Accepted
- **Date:** 2026-06-01
- **Author:** Théo Schmitt
- **Supersedes:** N/A (reactivates the "multipart upload" half of ADR-002)

## Context

The mandate's Producer zone "streams [samples] continuously into the lakehouse
using **multipart uploads to bypass local RAM constraints**". For a 25 MHz
Starlink capture (200 MB/s in cf32), even a few seconds is gigabytes — holding a
whole capture in memory before a single `put_object` is not viable.

ADR-002 deferred multipart upload; ADR-008 and ADR-009 already reactivated the
streaming (ZeroMQ) and partial-read (Range) halves of that deferral. This ADR
reactivates the remaining half: the **streaming upload** path.

## Decision

**Add `StorageClient.upload_multipart(key, chunks, …)`**, which consumes an
*iterator of byte chunks* and ships them via the S3 multipart protocol
(`create_multipart_upload` → N × `upload_part` → `complete_multipart_upload`),
never holding the whole object in RAM.

Key behaviours:
- **Coalescing**: incoming chunks of any size are buffered into parts of
  `part_size` (default 8 MiB). S3 requires every part *except the last* to be
  ≥ 5 MiB, so `part_size` stays ≥ 5 MiB.
- **Metadata + tags** are attached at `create_multipart_upload` time (same
  `x-amz-meta-*` / tagging convention as `upload_bytes`, ADR-003).
- **Abort on failure**: any error (including one raised by the chunk iterator)
  triggers `abort_multipart_upload`, so no orphaned partial object is left.
- Returns the total bytes uploaded.

It complements `upload_bytes` rather than replacing it: small, already-in-memory
objects (the `.sigmf-meta`, quality reports) keep using `upload_bytes`; the
large, streamed `.sigmf-data` from a live capture will use `upload_multipart`.

## Rationale

- **Bounded memory**: the producer/ingest only ever holds `part_size` bytes,
  independent of capture length — the explicit mandate requirement.
- **Same conventions**: metadata/tags/key-layout are unchanged; only the upload
  mechanism differs, so the consumer side is unaffected.
- **Crash-safe**: aborting on failure avoids dangling multipart uploads (which
  otherwise accumulate and incur storage cost on real S3/MinIO).
- **Tested both ways**: unit-tested under moto (coalescing, metadata/tags,
  abort) and smoke-tested live against MinIO (2 parts, byte-identical round
  trip).

## Consequences

### Positive

- Captures larger than RAM can be ingested as they stream in.
- The producer/live-capture path (SoapySDR → SigMF → MinIO) now has its upload
  primitive ready.

### Negative / open

- Not yet wired into a caller: the synthetic producer still holds its array in
  memory and uses `upload_bytes` (no RAM benefit there). The primitive will be
  used by the live SDR capture/ingest path (future work).
- `part_size` must stay ≥ 5 MiB (S3 rule); the code documents this but does not
  hard-validate it — a misconfigured tiny part_size would fail at
  `complete` time on real S3.

## Alternatives considered

- **boto3 `TransferManager` / `upload_fileobj`**: convenient for files on disk,
  but our source is an in-memory *stream of numpy chunks*, not a file handle;
  the explicit multipart API fits the chunk iterator cleanly and keeps the
  memory bound obvious.

## References

- Scoping PDF (Producer zone: multipart upload to bypass RAM)
- ADR-002 (deferred streaming — upload half reactivated here), ADR-008 (ZeroMQ),
  ADR-009 (Range reads)
- `src/aerolake/common/storage.py` (`upload_multipart`)
