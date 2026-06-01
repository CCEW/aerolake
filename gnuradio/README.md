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

## Files (added once GNU Radio is installed)

- `record.grc` — capture → `.sigmf-data` file.
- `playback.grc` — `.sigmf-data` file → throttle → spectrum sink (→ SDR TX later).

Each `.grc` is the GNU Radio Companion source; GRC generates a runnable `.py`
from it. Generated `.py` files are not committed (see `.gitignore`).

## Usage

```bash
# Open / edit / run graphically:
gnuradio-companion gnuradio/playback.grc

# Or compile a .grc to a runnable script headlessly (also validates it):
grcc -o /tmp gnuradio/playback.grc && python3 /tmp/playback.py
```
