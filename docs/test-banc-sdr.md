# Runbook — Bench test: signal generator → SDR → lakehouse

Goal: capture a known signal (CW generator) with an **RTL-SDR** or a
**BladeRF**, described by a **config file**, and store it in the MinIO
lakehouse in the SigMF format.

---

## Quick method (a single call)

Once the hardware is connected (see §2) and your config is ready (see §3):

```bash
cd ~/code/lassena/aerolake
./acquire.sh examples/test-rtlsdr.json      # or your own config file
```

`acquire.sh` chains everything: SoapySDR bridge → MinIO → healthcheck →
capture. All you have to do is answer **y** to "Push this capture to MinIO?".

> The sections below detail every step (useful to understand or troubleshoot).
> If `acquire.sh` works, you can jump straight to §4 (verification).

---

## 1. Software requirements (what `acquire.sh` does)

```bash
cd ~/code/lassena/aerolake
bash setup-soapy.sh                  # SoapySDR bridge (RERUN after every `uv sync`)
cd docker && docker compose up -d && cd ..   # MinIO (the lakehouse)
uv run aerolake-healthcheck          # must be green
SoapySDRUtil --find                  # must list your SDR once connected
```

---

## 2. Hardware setup

1. **Coaxial cable**: generator output → SDR **RX** input (SMA).
   - RTL-SDR: the antenna port.  BladeRF: port **RX1** (or **RX**).
2. (!) **POWER — so you don't fry anything**:
   - Start **VERY LOW**: **−40 dBm** (or lower).
   - **Never exceed ~−10 dBm** on an RTL-SDR.
   - You will then raise it, little by little, until the tone appears.
3. **Frequency**: RTL-SDR 24 MHz → 1.7 GHz · BladeRF 47 MHz → 6 GHz.
4. (!) **Offset**: set the generator **~250 kHz ABOVE** the config's
   `center_freq`. The SDR has a spurious peak exactly at the centre (the "DC
   spike"); by offsetting, your tone appears at **+250 kHz**, clearly visible.

---

## 3. The config file

### Where to put it?
**Anywhere** — you simply pass its **path** as an argument:
```bash
./acquire.sh path/to/my-config.json
```
The simplest: keep it in **`examples/`** (next to the templates). Two configs
are **ready to use**, you can just edit them:
- `examples/test-rtlsdr.json`
- `examples/test-bladerf.json`

Or create your own, e.g. `examples/my-test.json`, then
`./acquire.sh examples/my-test.json`.

### How to fill it in?

**Minimal** (the strict minimum for a real SDR):
```json
{
  "signal_type": "test_banc",
  "center_freq": 100000000,
  "sample_rate": 2000000,
  "duration_s": 2.0,
  "source": { "type": "soapy", "driver": "rtlsdr", "agc": true }
}
```

**Complete** (as much metadata as possible):
```json
{
  "signal_type": "gnss_l1",
  "signal_type_detail": "L1 C/A",
  "center_freq": 1575420000,
  "sample_rate": 2000000,
  "duration_s": 5.0,

  "source": { "type": "soapy", "driver": "bladerf", "agc": false, "antenna": "RX1" },

  "author": "Theo Schmitt",
  "description": "GNSS L1 bench capture",
  "license": "https://creativecommons.org/licenses/by-sa/4.0/",
  "operator": "schmitt",

  "location": {
    "name": "LASSENA lab",
    "mobile": false,
    "geolocation": { "latitude": 45.4946, "longitude": -73.5623, "altitude": 50.0 }
  },

  "annotation": {
    "label": "carrier",
    "comment": "generator tone",
    "freq_lower_edge": 1575320000,
    "freq_upper_edge": 1575520000
  },

  "antenna": { "model": "Tallysman TW3742", "type": "active patch", "gain": 28.0 }
}
```

### The fields

| Field | Meaning |
|---|---|
| `signal_type` *(required)* | short identifier → MinIO folder + tag (`gnss_l1`, `test_banc`…) |
| `center_freq` *(required)* | centre frequency in Hz (= generator frequency − 250,000) |
| `sample_rate` *(required)* | sample rate in Hz (2000000 = 2 MS/s, fine for RTL-SDR and BladeRF) |
| `duration_s` *(required)* | duration in seconds (keep it short: 2 s) |
| `source.type` | `"soapy"` (real SDR) or `"synthetic"` (test without hardware) |
| `source.driver` | `rtlsdr` or `bladerf` |
| `source.agc` | `true` = automatic gain · `false` = fixed gain |
| `source.antenna` | port (BladeRF `RX1`) — optional, remove it if it errors |
| `author` / `description` / `license` / `operator` | descriptive metadata |
| `location.name` / `mobile` | place (becomes a tag) · moving? |
| `location.geolocation` | `latitude` / `longitude` / `altitude` — **or** `"gps": true` (reads gpsd) |
| `annotation` | `label`, `comment`, `freq_lower_edge` + `freq_upper_edge` (both together) |
| `antenna` | `model` (required if the block is present), `type`, `gain`, `polarization`… |

> Strict rule: an **unknown field is an error** (typo protection).
> Exhaustive reference: `examples/capture.full.json` and `examples/README.md`.

**Frequency pairing example** (RTL-SDR): generator at **100.25 MHz** →
`center_freq: 100000000` (100.00 MHz) → the tone shows up at **+250 kHz**.

---

## 4. Run + verify

```bash
./acquire.sh examples/test-rtlsdr.json     # capture, then answer "y" to push
uv run aerolake-list --signal-type test_banc   # does the capture appear?
```

(The `Avahi` / `RtApi` warnings at startup are SoapySDR noise; ignore them.)

### Looking at the content (optional)

`aerolake-list` confirms the capture **exists**; to look at the **spectrum**
(and confirm your tone is there, at the right level), open the `.sigmf-data`
in **GNU Radio** (`playback.grc`) or in **Inspectrum**.

---

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No SDR found for driver=...` | Device not connected, wrong `driver`, or USB permissions. Check `SoapySDRUtil --find`. Add udev rules or use `sudo` if needed. |
| `ModuleNotFoundError: SoapySDR` | Rerun `bash setup-soapy.sh` (the bridge breaks after a `uv sync`). |
| A single peak exactly at the centre (0 kHz) | That is the DC spike; offset the generator by +250 kHz. |
| Level ~0 dBFS / clipped | Lower the generator level. |
| No peak / very low level | Raise the generator; check cable + frequency + offset. |
| MinIO unreachable | `cd docker && docker compose up -d` then `uv run aerolake-healthcheck`. |
| BladeRF: antenna error | Remove the `"antenna"` field from the config. |

---

## Known limitation (gain)

The config does not expose the **gain**: it is the AGC when `"agc": true`,
otherwise a fixed 40 dB gain. For a generator test, **the generator's level is
your real adjustment knob**.
