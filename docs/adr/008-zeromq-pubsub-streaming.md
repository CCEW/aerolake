# ADR-008 — ZeroMQ Pub/Sub streaming of capture frames

- **Status:** Accepted
- **Date:** 2026-06-01
- **Author:** Théo Schmitt
- **Supersedes:** N/A (partially reactivates ADR-002; builds on ADR-007)

## Context

The original architecture ends with **`Consumer → ZeroMQ Pub/Sub`**: captures
read back from the lake are republished on a high-performance bus for live
subscribers (decoders, future SDR transmitters). ADR-002 deferred this, and
ADR-004 confirmed real-time delivery wasn't an immediate need.

With the software cadence player in place (ADR-007 layer 1), the missing piece
is small and purely software: turn the frames the player already emits into a
**network stream**. This is the last buildable-without-hardware step; doing it
now completes the software pipeline before the project moves to GNU Radio and
real SDR hardware.

## Decision

**Build a ZeroMQ PUB/SUB layer on top of `CapturePlayer`.** The player's
``on_frame(index, frame)`` hook feeds a `FramePublisher` that sends each frame
on a ZeroMQ **PUB** socket; subscribers use **SUB** sockets to receive them.

**Wire format** — one multipart message per frame, three parts:

1. ``topic`` (UTF-8) — the routing key SUB sockets filter on. Defaults to the
   capture's ``signal-type`` tag, so a subscriber can ask for just `gnss_l1`.
2. ``header`` (JSON) — ``{"index", "n", "dtype"}``.
3. ``payload`` — raw IQ bytes (``ndarray.tobytes()``).

`encode_frame`/`decode_frame` are pure functions (format unit-tested with no
sockets); `FramePublisher` takes an injectable socket (behaviour unit-tested
with a fake socket — no flaky real PUB/SUB round-trip needed). A new
`aerolake-stream` CLI wires player + publisher together.

## Rationale

- **PUB/SUB fits the use case**: one producer of frames, N independent consumers,
  no back-pressure coupling — exactly what a "live replay bus" wants.
- **Reuses the player** (ADR-007): pacing/cadence already solved; streaming is
  just "where the frames go". The ``on_frame`` hook was designed for this.
- **Topic = signal-type** gives cheap, meaningful filtering for free.
- **Pure encode/decode + injectable socket** keep it fully unit-testable in CI
  without networking flakiness (ZeroMQ's "slow joiner" makes real PUB/SUB
  round-trips timing-dependent).

## Consequences

### Positive

- Completes the `Producer → MinIO → Consumer → ZeroMQ` software pipeline.
- A subscriber (decoder, visualiser, or a future GNU Radio TX sink) can consume
  live frames at the recorded cadence.

### Negative / open

- ZeroMQ PUB/SUB drops messages for slow/late subscribers (no replay/queue);
  acceptable for a live stream, but not a reliable transport. A future ADR could
  add PUSH/PULL or a broker if guaranteed delivery is needed.
- The **other half of ADR-002's streaming** (multipart *upload* + HTTP Range
  *reads* for chunked ingest) remains deferred: today the player reads a whole
  capture from MinIO, then streams it. Chunked end-to-end streaming is future
  work.

## Alternatives considered

- **PUSH/PULL** instead of PUB/SUB: reliable, load-balanced, but point-to-point
  (one consumer gets each message) — wrong for "many subscribers see every
  frame". Revisit if guaranteed delivery to a single sink is needed.
- **Raw TCP / WebSocket**: more plumbing for no gain; ZeroMQ gives framing +
  patterns out of the box and is the tool named in the mandate.

## References

- ADR-002 (batch vs streaming — this reactivates the ZeroMQ half)
- ADR-007 (CapturePlayer — the frame source feeding this)
- `src/aerolake/consumer/stream.py`, `src/aerolake/scripts/stream.py`
