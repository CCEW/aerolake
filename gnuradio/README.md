# GNU Radio flowgraphs (Record / Playback)

This folder holds the GNU Radio side of AeroLake (ADR-007, layer 2). It is
**separate from the Python/uv project**: GNU Radio is a system package (install
with `sudo apt install gnuradio`), and its flowgraphs run with the *system*
Python that ships the `gnuradio` bindings — not inside our `.venv`.

## The bridge: the `.sigmf-data` file

Our captures store samples as **complex float32, little-endian** (`cf32_le`).
That is byte-for-byte what GNU Radio's **File Source / File Sink** read and write
when their type is *complex* — so the two worlds meet at the `.sigmf-data` file
on disk, with **no special SigMF block required**. The companion `.sigmf-meta`
JSON carries the `core:sample_rate` you set in the flowgraph (Throttle / source
rate) and on the receiver.

Typical loop:

- **Record:** SDR (or synthetic) source → **File Sink** → `capture.sigmf-data`,
  then add the `.sigmf-meta` and upload with the AeroLake tooling.
- **Playback:** download a `capture.sigmf-data`, **File Source** → **Throttle**
  (at the recorded sample rate) → a Qt GUI sink (spectrum/time) and, later, an
  **SDR sink** to transmit on real hardware.

## Hardware note (ADR-007)

Real RF re-emission (transmitting a capture over the air) needs a **TX-capable
SDR — the BladeRF**. The **RTL-SDR is receive-only** and cannot be used for the
playback/transmit demos.

## Files

- `record.grc` — **synthetic** source → Head → File Sink (`.sigmf-data`). No
  hardware; useful to test the chain without an SDR.
- **`record_sdr.grc`** — **real SDR** capture via a **Soapy Custom Source** →
  Head → File Sink. One flowgraph for *any* SoapySDR device: set the
  `sdr_driver` variable to `"rtlsdr"`, `"bladerf"`, `"hackrf"`, … (plus `freq`,
  `samp_rate`, `gain`, `agc`, `n_samples`). The drivers for BladeRF and RTL-SDR
  are already installed (they came with GNU Radio).
- `playback.grc` — `.sigmf-data` File Source → Throttle → spectrum + waterfall.
  Supports partial reading (`start_s` / `duration_s` → File Source
  offset/length, ADR-009). An SDR **sink** for real TX comes later (BladeRF —
  the RTL-SDR is RX-only).

### Per-device caveats (record_sdr.grc)

- **RTL-SDR**: max sample rate ~2.4 MS/s (so fine for GNSS/Iridium at 2 MHz,
  **not** for Starlink 25 MHz). RX only.
- **BladeRF**: up to 61 MS/s (needed for the 25 MHz Starlink target); TX-capable.

Each `.grc` is the GNU Radio Companion source; GRC generates a runnable `.py`
from it. Generated `.py` files are not committed (see `.gitignore`).

## Usage

```bash
# Open / edit / run graphically:
gnuradio-companion gnuradio/playback.grc

# Or compile a .grc to a runnable script headlessly (also validates it):
grcc -o /tmp gnuradio/playback.grc && python3 /tmp/playback.py
```
