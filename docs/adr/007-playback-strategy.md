# ADR-007 — Playback strategy: software cadence replay now, GNU Radio + SDR re-emission later

- **Status:** Accepted
- **Date:** 2026-06-01
- **Author:** Théo Schmitt
- **Supersedes:** N/A (complements ADR-002, ADR-004)

## Context

The project mandate calls for a **Record / Playback** capability: the ultimate
goal of the three demos (GNSS, Iridium, Starlink) is to **replay stored RF
captures physically onto real receivers**. The project lead also explicitly
asked for a **GNU Radio "Record/Playback"** flowgraph (noted in
`docs/context/historique-discussions.md` and ADR-004), and ADR-004 states the
consumer must **respect the temporal cadence** of the data (replay samples at
their recorded rate) — throughput, not latency, being the real constraint.

The word **"playback" is overloaded**, and conflating its meanings has stalled
the planning. It actually spans three distinct layers with very different
dependencies:

| Layer | What it means | Needs |
|-------|---------------|-------|
| **1. Software cadence replay** | Stream a stored capture's samples out **at their recorded sample rate** (a paced generator / future ZeroMQ feed) | Nothing — pure software |
| **2. GNU Radio Record/Playback flowgraphs** | `.grc` graphs: SDR → file/MinIO (record), and file → sink (playback) | **GNU Radio installed** (not yet) |
| **3. Real RF re-emission** | Transmit a stored capture **over the air** via an SDR to a real receiver — the actual demos | **Hardware** (a TX-capable SDR) |

Two hard constraints shape this:

- **GNU Radio is not yet installed** on the dev machine.
- **Of the available SDRs, the RTL-SDR is receive-only — it cannot transmit.**
  Real RF re-emission (layer 3) therefore requires the **BladeRF** (which is
  TX-capable). This is easy to overlook and would block a demo if discovered
  late.

## Decision (proposed)

**Treat playback as the three layers above and build them in dependency order,
starting with the only one that needs no hardware.**

1. **Now — Layer 1 (software cadence replay).** Implement a `CapturePlayer` in
   the consumer that reads a capture via `CaptureReader` and yields fixed-size
   frames paced at the recorded sample rate (`frame_size / sample_rate` seconds
   between frames). The pacing clock is injectable so the logic is unit-tested
   without real-time sleeps. This directly satisfies ADR-004's "respect the
   cadence" requirement and is the natural seed for the deferred ZeroMQ pub/sub
   stream (ADR-002).

2. **When GNU Radio is installed — Layer 2 (flowgraphs).** Author the
   Record/Playback `.grc` graphs. Validate them in software first (file/null
   sinks, spectrum display) before involving any radio.

3. **At the hardware/demo phase — Layer 3 (RF re-emission).** Transmit via the
   **BladeRF** to a real receiver. Plan the demos around BladeRF for any TX;
   the RTL-SDR is reserved for capture (RX) only.

## Rationale

- **Unblocks immediate, testable progress** on a stated requirement (cadence
  replay) without waiting for GNU Radio or hardware.
- **Separates concerns cleanly**: software pacing, signal-processing graph, and
  physical transmission are independent problems with independent risks.
- **Surfaces the RTL-SDR TX limitation now**, while there's time to ensure a
  BladeRF is available for the demos, instead of discovering it at the end.
- **Builds on existing abstractions**: Layer 1 sits on `CaptureReader`, no new
  data path.

## Consequences

### Positive

- A working, tested cadence player advances the pipeline today and seeds the
  future streaming layer.
- The demo plan gains an explicit, correct hardware assumption (BladeRF for TX).

### Negative / open

- Layers 2 and 3 remain **inherently gated on GNU Radio install + hardware**, so
  "playback" is not fully "done" until the hardware phase — by design.
- Real-time pacing accuracy in pure Python is limited (OS scheduling); fine for
  the stated cadence use, but high-rate (Starlink 25 MHz) replay may later need
  the GNU Radio / hardware path rather than the software player.

## Alternatives considered

- **Jump straight to GNU Radio flowgraphs:** blocked — GNU Radio isn't installed
  and the graphs can't be meaningfully tested headless/in CI.
- **Wait for hardware and do it all at the end:** leaves a stated software
  requirement (cadence replay) unbuilt and risks discovering the RTL-SDR TX
  limitation too late.

## References

- ADR-002 (batch vs streaming — multipart/Range/ZeroMQ deferred)
- ADR-004 (data-quality priority; "respect the temporal cadence"; GNU Radio
  Record/Playback requested)
- `docs/context/historique-discussions.md` (mandate, SDRs: BladeRF + RTL-SDR)
- `src/aerolake/consumer/reader.py` (`CaptureReader`, the player's data source)
