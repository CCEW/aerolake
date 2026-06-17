# Capture configuration files

`aerolake-capture --config <file>.json` records a capture described entirely by
a JSON file. Copy one of the templates here, fill in your values, and run it.

```bash
cp examples/capture.example.json my_capture.json
# edit my_capture.json
uv run aerolake-capture --config my_capture.json
```

After the capture, a summary is shown and you are asked whether to push it to
MinIO. If you decline, you can keep it on disk (under `captures/`) instead.

Two templates are provided:

- **`capture.example.json`** — the minimal set to get started.
- **`capture.full.json`** — every supported field, with example values. Delete
  what you don't need; anything omitted is simply left out of the metadata.

JSON does not allow comments, so each field is documented below instead.

## Required fields

| Field | Type | Meaning |
|---|---|---|
| `signal_type` | string | Short signal identifier, used as the top-level folder in MinIO (e.g. `gnss_l1`, `iridium`, `starlink`, `other`). |
| `center_freq` | number | Center frequency in Hz (e.g. `1575420000` for GPS L1). |
| `sample_rate` | number | Sample rate in Hz / samples per second (e.g. `2000000`). |
| `duration_s` | number | Capture duration in seconds. |

## Source (how to capture)

The `source` block selects where samples come from. Its `type` is either
`soapy` (real SDR) or `synthetic` (generated signal, no hardware).

Real SDR:

```json
"source": { "type": "soapy", "driver": "bladerf", "agc": true, "antenna": "RX2" }
```

| Field | Type | Meaning |
|---|---|---|
| `driver` | string | SoapySDR driver key: `rtlsdr`, `bladerf`, `hackrf`, … |
| `agc` | boolean | Automatic gain control. `true` lets the front-end gain track the signal; the effective gain is read back into the metadata. |
| `antenna` | string | Optional antenna port to select. Omit to keep the device default. |

Synthetic (for testing without hardware):

```json
"source": { "type": "synthetic", "tone_offset_hz": 100000, "snr_db": 20 }
```

If `source` is omitted entirely, a synthetic source is used by default.

## Descriptive metadata (optional)

These go into the SigMF `global` object.

| Field | Type | SigMF | Meaning |
|---|---|---|---|
| `signal_type_detail` | string | `aerolake:signal_type_detail` | Free-text detail, useful when `signal_type` is `other`. |
| `author` | string | `core:author` | Who recorded it (name, handle, email…). |
| `description` | string | `core:description` | Free-text description of the recording. |
| `license` | string (URL) | `core:license` | License URL the recording is offered under. |
| `operator` | string | `aerolake:operator` | Operator id. If omitted, defaults to your login. |

## Location (optional)

```json
"location": {
  "name": "LASSENA rooftop",
  "mobile": false,
  "geolocation": { "latitude": 45.4946, "longitude": -73.5623, "altitude": 50.0 }
}
```

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Human-readable place; also promoted to a searchable MinIO tag. |
| `mobile` | boolean | `true` if the receiver was moving during the capture. |
| `geolocation.latitude` | number | Degrees, −90…90. |
| `geolocation.longitude` | number | Degrees, −180…180. |
| `geolocation.altitude` | number | Optional, meters above the WGS84 ellipsoid. |

The geolocation is written as a standard GeoJSON Point in the SigMF `captures`
segment. Coordinates are entered here as latitude/longitude for readability and
converted to the spec's `[longitude, latitude, altitude]` order automatically.
Coordinates are typed by hand here; automatic GPS read-out is not done yet.

## Annotation (optional)

A single annotation describing the whole capture, written to the SigMF
`annotations` array.

| Field | Type | Meaning |
|---|---|---|
| `label` | string | Short label (≤ 20 chars recommended). |
| `comment` | string | Longer free-text comment. |
| `freq_lower_edge` | number | Lower edge of the feature, in Hz. |
| `freq_upper_edge` | number | Upper edge of the feature, in Hz. |

`freq_lower_edge` and `freq_upper_edge` must be supplied together or not at all
(SigMF requires the pair).

## Antenna (optional)

Scalar fields of the SigMF `antenna:` extension. If you supply an antenna block,
`model` is required; everything else is optional. The 360-value gain-pattern
arrays from the spec are intentionally not exposed here.

| Field | Type | Unit |
|---|---|---|
| `model` | string | Make/model (required if the block is present). |
| `type` | string | e.g. dipole, monopole, patch. |
| `low_frequency` / `high_frequency` | number | Operational range, Hz. |
| `gain` | number | dBi. |
| `horizontal_beam_width` / `vertical_beam_width` | number | Degrees. |
| `cross_polar_discrimination` | number | — |
| `voltage_standing_wave_ratio` | number | Volts. |
| `cable_loss` | number | dB. |
| `steerable` | boolean | Whether the antenna is steerable. |
| `mobile` | boolean | Whether the antenna is mobile. |
| `hagl` | number | Phase-center height above ground level, meters. |
| `polarization` | string | e.g. "right-hand circular". |
| `azimuth_angle` | number | Degrees from North. |
| `elevation_angle` | number | Degrees from horizontal. |

`polarization`, `azimuth_angle` and `elevation_angle` describe antenna pointing;
per the SigMF spec they are written into the annotation rather than the global
antenna block. You still enter them here in the antenna block — the placement is
handled for you.
