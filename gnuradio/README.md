# GNU Radio flowgraphs (Record / Playback)

This folder holds the GNU Radio side of AeroLake (ADR-007). It is **separate from
the Python/uv project**: GNU Radio is a system package (`sudo apt install
gnuradio`), and its flowgraphs run with the *system* Python that ships the
`gnuradio` bindings — not inside our `.venv`.

## The bridge: the `.sigmf-data` file

Our captures store samples as **complex float32, little-endian** (`cf32_le`).
That is byte-for-byte what GNU Radio's **File Source / File Sink** read and write
when their type is *complex* — so the two worlds meet at the `.sigmf-data` file
on disk, with **no special SigMF block required**. The companion `.sigmf-meta`
JSON carries the `core:sample_rate` you set in the flowgraph.

Typical loop:

- **Record:** a source → **File Sink** → `capture.sigmf-data`, then ingest it
  into MinIO with `uv run aerolake-ingest <file> --signal-type … --sample-rate …
  --center-freq …` (writes the `.sigmf-meta` and uploads).
- **Playback:** put a `capture.sigmf-data` on local disk, **File Source** →
  **Throttle** (at the recorded sample rate) → a Qt GUI sink (spectrum/time).

## Files

- `record.grc` — **synthetic** source → Head → File Sink (`.sigmf-data`). No
  hardware; useful to test the chain without an SDR.
- `playback.grc` — `.sigmf-data` File Source → Throttle → spectrum + waterfall.
  Supports partial reading (`start_s` / `duration_s` → File Source
  offset/length, ADR-009). Software-only view (no transmit).

Each `.grc` is the GNU Radio Companion source; GRC generates a runnable `.py`
from it. Generated `.py` files are not committed (see `.gitignore`).

## Usage

```bash
# Open / edit / run graphically:
gnuradio-companion gnuradio/playback.grc

# Or compile a .grc to a runnable script headlessly (also validates it):
grcc -o /tmp gnuradio/playback.grc && python3 /tmp/playback.py
```

## Archived (not in this folder)

The **real-SDR capture** flowgraph (`record_sdr.grc`), the **RF transmit**
flowgraph (`transmit_sdr.grc`) and the **MinIO→file** bridge CLI
(`aerolake-fetch`) were archived (ADR-013) and live on the
`archive/explorations-v1` branch. RF transmit is a future phase.

> ⚠️ **Legal & safety:** transmitting on the GNSS L1 (1575.42 MHz) or Iridium
> (~1626 MHz) bands **over the air is illegal and jams safety-of-life
> receivers**. Re-emit only through a **shielded RF cable + attenuator**, a
> **dummy load**, or a **Faraday enclosure** — never a bare antenna.
