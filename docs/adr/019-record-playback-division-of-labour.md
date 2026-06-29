# ADR-019 — Record/playback division of labour: GNU Radio owns the RF, AeroLake owns the lakehouse

- **Status:** Accepted
- **Date:** 2026-06-26
- **Author:** Théo Schmitt
- **Refines:** ADR-007 (playback layers), ADR-012 (RF re-emission), ADR-013 (mandate realignment)

## Context

"Record/Playback" is a core mandate requirement, and the question keeps coming
back: *what is the best, most optimised way to do it?* The honest answer is that
record/playback is **two very different problems wearing one name**:

| Concern | What it needs | Best tool |
|---|---|---|
| **Lakehouse** — store, describe, tag, catalogue, discover, serve, software/visual replay, network stream | object storage, metadata, HTTP Range, ZeroMQ, a GUI | **AeroLake (Python)** |
| **RF edge** — sustained high-rate capture to disk, and sample-accurate transmission over the air | real-time DSP, a TX-capable SDR, deterministic timing | **GNU Radio + SDR** |

Python is excellent for the first and structurally wrong for the second:
high-throughput continuous recording and sample-exact re-emission are not things
the OS scheduler + a numpy buffer do well — that is precisely what GNU Radio and
the SDR hardware exist for. AeroLake currently *also* captures from an SDR
(`soapy_source.py`), which overlaps GNU Radio's `record.grc` and risks
re-implementing DSP in Python.

There is also a **human** split: Théo owns the lakehouse; **Camelia owns the
GNU Radio / RF branch**. An architecture that matches the team is easier to run.

The bridge already exists and is format-free: the **`.sigmf-data` file** is raw
`cf32_le`, which GNU Radio's File Source/Sink read and write natively — no glue,
no custom format (see ADR-007, ADR-013).

## Decision

**Split record/playback by each tool's strength, with the `.sigmf-data` file as
the contract between them.**

- **Record.** GNU Radio (Camelia) is *the* heavy RF recorder: SDR →
  `.sigmf-data`/`.sigmf-meta` on disk, at full rate. AeroLake **ingests** those
  into MinIO (tags, metadata, preview, catalogue). AeroLake keeps its own
  `soapy_source` capture **only** for synthetic / light RTL-SDR / quick test
  captures — not as the high-throughput path.
- **Lakehouse.** AeroLake owns everything between: MinIO storage, the
  metadata/tag convention (ADR-003), partial/seeked reads (ADR-009), the
  catalogue, the auto-preview, and the GUI.
- **Playback — software / visualisation.** AeroLake: `CapturePlayer` paces
  frames at the recorded rate (ADR-007 layer 1), `FramePublisher` streams them
  over ZeroMQ (ADR-008), and the GUI shows/relays them. This is for inspection,
  monitoring and delivery — *not* RF-faithful.
- **Playback — real RF re-emission.** AeroLake **fetches** the `.sigmf-data` to
  a local file; GNU Radio (`playback.grc`) transmits it via a TX-capable SDR
  (BladeRF — the RTL-SDR is RX-only). Sample-exact timing lives in the hardware
  + GNU Radio, never in Python (ADR-007 layer 3, ADR-012).

### Optimisations this decision implies

1. **Prefer the native sample datatype in storage.** Forcing every capture to
   `cf32` (8 bytes/sample) is convenient but 2–4× larger than keeping native
   `cs16`/`cu8`. For volume, store the native `datatype` (SigMF records it) and
   convert only on read when needed.
2. **Reinstate a fetch→local-file bridge** (the archived `aerolake-fetch`,
   ADR-013) so GNU Radio playback reads a local file rather than streaming from
   MinIO.
3. **Do not duplicate the heavy recorder in Python.** Let GNU Radio be the RF
   recorder; AeroLake ingests.

## Rationale

- **Each tool does what it is best at** — no high-performance RF engine
  re-implemented in Python (which would be worse and unfinishable).
- **The architecture matches the team** (lakehouse = Théo, RF = Camelia), which
  is a strong signal the boundary is in the right place.
- **The contract is a standard file**, so the two halves evolve independently.
- **Leaves a finishable, hand-offable target** for the lakehouse owner: storage
  + catalogue + software/visual playback + the GNU Radio bridge.

## Consequences

### Positive

- Clear ownership and a clean seam; the GUI playback can ship its software/visual
  mode now and defer RF re-emission to GNU Radio without rework.
- Storage and bandwidth become tunable (native datatype) instead of fixed at cf32.

### Negative / open

- Two capture paths coexist for a while (AeroLake synthetic/light + GNU Radio
  heavy); acceptable, but document which to use when.
- Real RF re-emission stays gated on GNU Radio + TX hardware + Camelia — by
  design, not a gap to close in the Python codebase.
- Native-datatype storage and the fetch bridge are **not yet implemented**; this
  ADR records the direction, not done work.

## Alternatives considered

- **AeroLake does everything in Python (capture, TX pacing).** Rejected:
  re-implements GNU Radio badly, can't hit sample-accurate TX, and is not
  finishable before the author's departure.
- **GNU Radio does everything (incl. the lakehouse).** Rejected: GNU Radio is a
  DSP framework, not a storage/metadata/discovery system; the lakehouse is
  exactly AeroLake's value.

## References

- ADR-003 (metadata/tagging convention), ADR-007 (playback layers),
  ADR-008 (ZeroMQ streaming), ADR-009 (partial reads), ADR-012 (RF re-emission),
  ADR-013 (mandate realignment; `aerolake-fetch` archived)
- `gnuradio/record.grc`, `gnuradio/playback.grc` (the RF edges)
- `src/aerolake/producer/ingest.py` (the ingest path GNU Radio recordings feed)
