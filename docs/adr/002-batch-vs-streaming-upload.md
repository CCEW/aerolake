# ADR-002 — Batch upload in Sprint 1, streaming in Sprint 2+

- **Status:** Accepted
- **Date:** 2026-05-27
- **Author:** Théo Schmitt
- **Supersedes:** N/A

## Context

The AeroLake project mandate (LASSENA-Project_AeroLake.pdf) describes a
producer that "streams [signals] continuously into the lakehouse using
multipart uploads" and a consumer that "uses highly optimized HTTP
Range Requests to pull specific .sigmf-data chunks at up to 200 MB/s".
This implies a streaming pipeline where producer and consumer run in
parallel: while the producer is still appending samples to an object,
the consumer is already reading the bytes that have arrived.

Building a streaming pipeline end-to-end requires four pieces in place
simultaneously:

1. A chunked acquisition path (SDR -> chunks of N milliseconds).
2. Multipart uploads in `StorageClient` to send chunks as they arrive.
3. A consumer that polls or listens for new data.
4. HTTP Range Requests to read partial objects.

In Sprint 1 we have neither a consumer nor a real SDR; the goal of
this sprint is to validate the SigMF encoding and the upload path end
to end. Implementing the four streaming pieces upfront would mean
debugging a system that nothing reads, with no easy way to validate
correctness.

## Decision

**Sprint 1 implements a batch upload pipeline.** The producer:

1. Generates (or captures) the full duration in memory as a
   `np.ndarray`.
2. Encodes the complete signal as a SigMF capture in memory.
3. Uploads `.sigmf-data` and `.sigmf-meta` as two complete S3 objects
   via `StorageClient.upload_bytes()`.

The streaming variant (chunked acquisition, multipart upload, partial
reads from the consumer) is **deferred to Sprint 2**, when the
consumer side of the pipeline starts to exist and gives us something
to read with.

## Rationale

- **Validation cycle.** A batch pipeline can be validated by a human
  opening the MinIO console and checking that two files exist with
  the right sizes and a valid SigMF metadata JSON. A streaming
  pipeline requires a working consumer just to confirm that bytes
  flow correctly.
- **No SDR yet.** With synthetic signals generated as a single numpy
  array, splitting them into chunks would be artificial complexity
  with no real benefit.
- **Forward compatibility.** Our `StorageClient` already abstracts
  boto3 behind a clean interface. Adding `upload_multipart()` later
  is additive; it does not require rewriting anything we have today.
- **Backpressure understanding.** Building the batch path first
  surfaces real-world questions (which content-type, which key
  layout, which metadata, which tags) that we resolve calmly. In
  streaming mode these decisions are harder to revisit because the
  system is in flight.

## Consequences

### Positive

- Sprint 1 deliverable is a working pipeline end-to-end with two
  visible artifacts in MinIO that can be downloaded and inspected.
- The chosen key layout
  (`{signal_type}/{date}/{session_id}/capture.sigmf-*`) is valid
  for both batch and streaming modes; no churn when we switch.
- Schema validation of SigMF metadata happens once, in memory,
  before any upload starts. Easier to debug than partial uploads.
- All structured logging events (`producer.capture.*`) work and
  can be reused unchanged in the streaming variant.

### Negative

- **No live playback possible today.** The consumer (when built)
  will have to wait for a capture to complete before reading. For
  a 60-second capture, that is 60 seconds of latency. Unacceptable
  for any real-time use case.
- **RAM usage scales with capture duration.** A 25 MHz Starlink
  capture for 10 seconds is 2 GB in RAM (25M samples * 8 bytes *
  10s). On a developer laptop this is fine; on the target
  hardware it may push limits. The project mandate calls out this
  exact concern, which is why multipart is needed eventually.
- **One-shot failure mode.** If the upload fails partway through,
  the partial object is invalid and must be retried entirely.
  Multipart uploads allow per-chunk retries with much smaller
  amounts to retransmit.

### Neutral

- The SigMF format itself does not constrain the choice. Both batch
  and streaming uploads produce identical `.sigmf-data` and
  `.sigmf-meta` files; only the way they are written changes.

## Migration plan to streaming (Sprint 2+)

When we attack the consumer in Sprint 2:

1. Add `StorageClient.upload_multipart(key, chunks_iterator)` that
   wraps boto3's `create_multipart_upload` / `upload_part` /
   `complete_multipart_upload`.
2. Add `StorageClient.read_range(key, start, end)` that wraps
   `get_object(Range=...)`.
3. Replace the producer's all-at-once acquisition with a generator
   of chunks (e.g. 100 ms each).
4. Build the consumer with a chunked-read loop that polls
   `head_object` or subscribes to MinIO bucket notifications to
   know when new bytes are available.
5. Re-validate end to end with both a synthetic streaming source
   and (eventually) a real SoapySDR source.

The latency target for the streaming variant is to be confirmed with
the project lead; current rough estimate is sub-second end-to-end
(SDR -> MinIO -> consumer -> ZMQ subscriber) for the GNSS L1 and
Iridium use cases, with Starlink 25 MHz requiring more careful
benchmarking.

## Alternatives considered

### Build streaming from day one

Considered but rejected because:
- Requires consumer, multipart, range reads, and notifications all
  in place to validate. Too many moving parts to debug at once.
- Sprint 1 deliverable becomes "nothing visible until everything
  works" instead of "tangible artifacts in MinIO after each commit".

### Keep batch indefinitely

Considered but rejected because:
- The project mandate explicitly requires multipart uploads and
  range requests. Staying batch would fail acceptance.
- RAM usage on long captures is genuinely problematic for the
  25 MHz Starlink target.

## References

- LASSENA-Project_AeroLake.pdf (project mandate), Sprint 1 and
  Sprint 2 deliverables
- `src/aerolake/producer/orchestrator.py` (current batch
  implementation)
- `src/aerolake/common/storage.py` (StorageClient, to be extended
  with multipart and range methods)
