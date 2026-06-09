# ADR-012 — RF re-emission: BladeRF TX flowgraph + a MinIO→file fetch bridge

> **Archivé — hors-périmètre phase 1 (voir ADR-013).** Ce composant a été retiré de `main` et préservé sur la branche `archive/explorations-v1`. Cet ADR est conservé comme trace de décision.

- **Status:** Accepted
- **Date:** 2026-06-03
- **Author:** Théo Schmitt
- **Complements:** ADR-007 (playback layers), ADR-009 (partial reads), ADR-001 (storage chokepoint)

## Context

ADR-007 split "playback" into three layers and built **layer 1** (software cadence
replay) and **layer 2** (the GNU Radio Record/Playback `.grc` graphs). It deferred
**layer 3 — real RF re-emission**: physically transmitting a stored capture over an
SDR so a real receiver sees it, which is the point of the three demos (GNSS,
Iridium, Starlink).

Two facts from ADR-007 still hold and shape this decision:

- **The RTL-SDR is receive-only.** Any transmit needs the **BladeRF** (TX-capable).
- **GNU Radio runs with the *system* Python**, not the uv `.venv` — the `gnuradio/`
  flowgraphs are deliberately separate from the Python package.

A capture lives in MinIO as a `.sigmf-data` object (raw `cf32_le`). A GNU Radio
**File Source** reads a *local* file as `complex` natively — so the missing pieces
to enable layer 3 are: (a) a **transmit flowgraph**, and (b) a **bridge** to land a
capture (or just a window of it) on local disk for that flowgraph to read.

There is also a **hard real-world constraint**: transmitting on the GNSS L1
(1575.42 MHz) or Iridium (~1626 MHz) bands **over the air is illegal and actively
jams safety-of-life receivers**. Re-emission must go through a **shielded RF cable +
attenuator**, a **dummy load**, or a **Faraday enclosure** — never a bare antenna.

## Decision

**Deliver layer 3 in its hardware-independent, validated form: a `transmit_sdr.grc`
flowgraph plus an `aerolake-fetch` CLI bridge — gated only on the BladeRF being
present to actually emit.**

1. **The bridge is the file** (same principle as `playback.grc`). Add
   **`aerolake-fetch`** (`scripts/fetch.py`): resolve a capture by `--key` or
   `--prefix`, read it through `CaptureReader` (whole capture, or a window via
   `read_segment` → HTTP Range, reusing ADR-009), and write the raw `cf32_le`
   bytes to a local `--out` file plus a `.sigmf-meta` JSON sidecar. It prints the
   acquisition parameters (`samp_rate`, `freq`) so they can be pasted straight into
   the flowgraph variables. All S3 access stays behind the `StorageClient`
   chokepoint (ADR-001).

2. **The flowgraph is `gnuradio/transmit_sdr.grc`.** File Source (`complex`, with
   `offset`/`length` for partial transmit, and a `repeat` toggle) → a digital
   **amplitude backoff** (`multiply_const`, default 0.8, to keep the DAC out of
   clipping) → **`soapy_custom_sink`** (driver-selectable, default `"bladerf"`,
   antenna `"TX"`, `tx_gain`). A `qtgui_freq_sink` taps the stream so the operator
   sees exactly what is being transmitted. **No Throttle** — with a real SDR the
   device clock paces the stream (a Throttle *and* an SDR sink fight each other).

3. **Two-stage level control** (`tx_amplitude` digital × `tx_gain` analog) and a
   conservative default gain, because over-driving a TX is both an RF-quality and a
   safety problem.

4. **Validate headlessly with `grcc`** (`grcc -o /tmp gnuradio/transmit_sdr.grc`),
   exactly as for the other graphs; actually emitting still requires the BladeRF.

## Rationale

- **Reuses everything**: the bridge is `CaptureReader` + Range reads (ADR-009), not
  a new data path; the flowgraph mirrors `record_sdr.grc`'s Soapy + driver-variable
  style, swapping Source→Sink and RX→TX.
- **Fetch a *window*, not 2.84 GB**: a 30-min capture need not hit disk/RAM whole —
  `--start/--duration` pulls only the slice you want to transmit (the Iridium demo
  fetched a 5 s window = 16 MB via one Range request).
- **Makes the safety constraint impossible to miss**: it is written into the
  flowgraph description, the block comments, the CLI, and the docs.
- **Keeps the hardware boundary clean**: the venv-side bridge is unit-tested (moto);
  the system-GNU-Radio side is validated by `grcc`. The only thing left un-exercised
  is the physical emission, which is inherently hardware-gated.

## Consequences

### Positive

- The full chain **store → fetch (whole or window) → transmit** now exists and is
  validated end-to-end except the final RF emission.
- `aerolake-fetch` is independently useful (export a capture for any external tool,
  not just GNU Radio).

### Negative / open

- Actual transmission remains **gated on a BladeRF** and a legal/shielded RF path —
  by design, not a gap to close in software.
- The operator must **manually match** `samp_rate`/`freq` in the flowgraph to the
  capture (the CLI prints them; the `.grc` can't read the sidecar itself).
- High-rate captures (Starlink 25 MHz) depend on BladeRF bandwidth/host throughput;
  out of scope here.

## Alternatives considered

- **Have the flowgraph read MinIO directly:** no clean SigMF/S3 source block exists
  for GNU Radio; the local `cf32` file is the simplest, already-used bridge.
- **A dedicated `soapy_bladerf_sink`:** locks the graph to one device. The
  `soapy_custom_sink` + `sdr_driver` variable keeps one graph usable for any
  TX-capable SoapySDR radio, matching `record_sdr.grc`.
- **Stream over ZeroMQ into a separate TX process:** more moving parts than a File
  Source; the file bridge is the same pattern as `playback.grc` and is restartable.

## References

- ADR-007 (playback layers; layer 3 = RF re-emission, BladeRF for TX)
- ADR-009 (partial/seeked reads via HTTP Range — reused by `aerolake-fetch`)
- ADR-001 (boto3 `StorageClient` as the single storage chokepoint)
- `gnuradio/transmit_sdr.grc`, `gnuradio/playback.grc`, `gnuradio/record_sdr.grc`
- `src/aerolake/scripts/fetch.py` (the bridge CLI), `tests/scripts/test_fetch.py`
- Technical Report (Lucien Millet) — cabled Iridium re-emission test methodology
