# ADR-009 — Partial / seeked reads via HTTP Range Requests

- **Status:** Accepted
- **Date:** 2026-06-01
- **Author:** Théo Schmitt
- **Supersedes:** N/A (reactivates the "Range Requests" half of ADR-002)

## Context

The project mandate (`docs/context/…`, scoping PDF) describes a consumer that
"uses highly optimized **HTTP Range Requests** to pull specific `.sigmf-data`
chunks". A call with the project lead reinforced this with a concrete need:
given a **one-hour recording**, the user must be able to **start playback at
t = 200 s**, not only at t = 0 — and this **partial reading must work both on
the Python side and in GNU Radio**.

ADR-002 deferred Range Requests; ADR-008 already reactivated the ZeroMQ half of
that deferral. This ADR reactivates the **Range** half. Without it, seeking into
a capture means downloading the whole (multi-GB) file first — unacceptable.

## Decision

**Add partial/seeked reads end to end, expressed in seconds, on both sides.**

### Python (the lakehouse path)

- `StorageClient.download_range(key, start, end)` — wraps the S3
  `Range: bytes=start-end` header (inclusive; open-ended if `end is None`).
- `StorageClient.object_size(key)` — a HEAD to know the total length (to clamp).
- `CaptureReader.read_segment(key, start_s, duration_s)` — converts the time
  window to a byte window and fetches **only** that slice:

  ```
  bytes_per_sample = dtype.itemsize        # 8 for cf32 (complex64)
  start_byte = floor(start_s * sample_rate) * bytes_per_sample
  n_samples  = floor(duration_s * sample_rate)   # or to the end
  ```

  Out-of-range windows are clamped (empty array if `start_s` is past the end).
- `CapturePlayer.play(..., start_s=, duration_s=)` uses `read_segment` when a
  window is given; the `aerolake-play` / `aerolake-stream` CLIs expose
  `--start` / `--duration` (seconds).

### GNU Radio (the flowgraph path)

`playback.grc` gains `start_s` / `duration_s` variables wired to the **File
Source** block's native `offset` / `length` parameters:
`offset = int(start_s * samp_rate)`, `length = int(duration_s * samp_rate)`
(`0` = whole file). So the same partial read is available graphically.

## Rationale

- **Directly satisfies the mandate + the lead's explicit request** ("start at
  t = 200 s"), on both required surfaces (Python and GNU Radio).
- **Bandwidth/latency**: fetch only the needed window instead of the whole
  capture — essential for hour-long or 25 MHz recordings.
- **Seconds, not bytes**, at the user-facing layer: the byte arithmetic is hidden
  in one place (`read_segment`); the File Source uses its built-in offset/length.
- **Reuses existing abstractions**: the storage chokepoint gains two thin methods;
  everything else composes.

## Consequences

### Positive

- Seeked playback/streaming without downloading whole files.
- Symmetry: the Python and GNU Radio paths take the same `start_s`/`duration_s`.

### Negative / open

- `read_segment` ignores SigMF `core:sample_start` / multi-segment captures —
  it treats the `.sigmf-data` as one contiguous stream from byte 0. Fine for our
  single-capture files; revisit if multi-capture SigMF is introduced.
- Sample-accurate seeking assumes a fixed `bytes_per_sample` (true for cf32);
  a future non-trivial datatype would need the same care.

## Alternatives considered

- **Download whole, slice in memory**: simple but defeats the purpose — it
  transfers (and buffers) the entire multi-GB capture to read 0.3 s of it.
- **Server-side query (Iceberg partition pruning)**: the mandate's long-term
  vision, but heavy; Range Requests deliver the needed capability now.

## References

- Scoping PDF (HTTP Range Requests; 200 MB/s extraction); lead call (start at t=200 s)
- ADR-002 (deferred streaming — Range half reactivated here), ADR-008 (ZeroMQ half)
- `src/aerolake/common/storage.py` (`download_range`, `object_size`),
  `src/aerolake/consumer/reader.py` (`read_segment`), `gnuradio/playback.grc`
