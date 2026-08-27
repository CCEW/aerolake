# Capture configuration files

`aerolake-capture --config <file>` records a capture described entirely by a
config file. Two formats are accepted, chosen by the file extension:

- **`.toml` — recommended.** TOML allows *comments*, so each field can be
  documented inline — much friendlier when someone else reads or edits a capture.
- **`.json` — still supported**, for backward compatibility.

Copy one of the templates here, fill in your values, and run it.

```bash
cp examples/capture.example.toml my_capture.toml
# edit my_capture.toml  (comments explain every field)
uv run aerolake-capture --config my_capture.toml
```

After the capture, a summary is shown and you are asked whether to push it to
MinIO. If you decline, you can keep it on disk (under `captures/`) instead.

Templates provided:

- **`capture.example.toml`** — minimal, commented (start here).
- **`capture.full.toml`** — **every supported field**, commented and marked
  `(obligatoire)`/`(optionnel)`: keep what you need, delete the rest. This is the
  "fill-or-delete" reference.
- **`test-complet.toml`** — a real RTL-SDR bench capture, ready to run.
- **`capture.example.json` / `capture.full.json`** — the JSON equivalents
  (`capture.full.json` shows every supported field at once).
- **`capture.sigmf-meta.example.json`** — a final, manually editable SigMF
  metadata file for an existing `.sigmf-data` recording. The data and metadata
  must have the same basename.
- **`short_24lines.ingest.sigmf-meta.example.json`** — an example of the
  metadata produced when the supplied `short_24lines.sigmf-data` is ingested
  with the values from its original metadata.

The TOML templates document each field inline; the same fields are also tabulated
below (they apply to both formats).

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

## SigMF metadata and defaults

The capture path and the ingest path use the same canonical SigMF metadata
schema. Capture fills it automatically; raw-file ingest fills the same fields
with defaults and then adds the SHA-512 hash after the data has been streamed.
The final metadata file is readable in
`capture.sigmf-meta.example.json`.

These fields are always saved in a canonical capture:

| Field | Default / source |
|---|---|
| `core:datatype` | `cf32_le` (the normalized stored IQ format) |
| `core:sample_rate` | Capture setting |
| `core:author` | `AeroLake` |
| `core:description` | Capture description or generated ingest description |
| `core:recorder` | Capture recorder or `aerolake-ingest` |
| `core:hw` | SDR name, or `unknown` |
| `core:version` | Current SigMF library specification version |
| `core:num_channels` | `1` |
| `core:offset` | `0` |
| `core:sha512` | Computed from the exact stored `.sigmf-data` bytes |
| `core:extensions` | `aerolake` extension, plus `antenna` when used |
| `aerolake:signal_type` | Capture setting; required for an existing pair |
| `aerolake:operator` | Same value as `core:author` |
| `aerolake:mobile` | `false` unless the receiver is moving |
| `aerolake:duration_s` | Derived from sample count and sample rate |
| `aerolake:sample_count` | Derived from the data size |
| `captures[0].core:sample_start` | `0` |
| `captures[0].core:frequency` | Center-frequency setting |
| `captures[0].core:datetime` | Capture start time in UTC |
| `annotations` | `[]`, or generated analysis annotations |

The following descriptive fields are optional, but are written when supplied:

These go into the SigMF `global` object.

| Field | Type | SigMF | Meaning |
|---|---|---|---|
| `signal_type_detail` | string | `aerolake:signal_type_detail` | Free-text detail, useful when `signal_type` is `other`. |
| `author` | string | `core:author` | Who recorded it (name, handle, email…). |
| `description` | string | `core:description` | Free-text description of the recording. |
| `license` | string (URL) | `core:license` | License URL the recording is offered under. |
| `operator` | derived | `aerolake:operator` | Always copied from `author`; it identifies the recording owner/operator. |
| `location.name` | string | `aerolake:location` | Human-readable recording location. |
| `location.mobile` | boolean | `aerolake:mobile` | Whether the receiver was moving. Defaults to `false`. |
| `hardware_info` | object | `aerolake:hardware_info` | Device details reported by the SDR. |
| `overflow_count` | integer | `aerolake:overflow_count` | Dropped samples reported by the SDR. |

## Ingesting a file manually

For a GNU Radio `.sigmf-data` file, either create the matching metadata by
copying `capture.sigmf-meta.example.json`, or let raw ingest create it:

```bash
uv run aerolake-ingest capture.sigmf-data \
  --signal-type gnss_l1 --sample-rate 2e6 \
  --center-freq 1575.42e6 --hardware bladerf
```

For the supplied `short_24lines.sigmf-data`, the equivalent generated-meta
command would be:

```bash
uv run aerolake-ingest /mnt/d/sigmf/short_24lines.sigmf-data \
  --signal-type iridium --sample-rate 10e6 \
  --center-freq 1622e6 --datatype ci16_le --hardware rfsoc
```

This converts the source `ci16_le` samples to stored `cf32_le`. The resulting
metadata shape is shown in `short_24lines.ingest.sigmf-meta.example.json`.
The sample count, duration, and SHA-512 are calculated from the data; the
capture timestamp is the ingest time. The CLI does not prompt for missing
values: `--signal-type`, `--sample-rate`, and `--center-freq` are required, and
`--datatype`/`--hardware` use defaults when omitted. Other generated values use
the canonical defaults listed above.

Because `short_24lines.sigmf-meta` already exists beside the data, running the
path with no metadata flags selects existing-pair mode instead. That mode does
not infer or prompt for fields; it validates the companion metadata and reports
all missing canonical fields before upload. To use the existing pair, complete
those fields first, or use the generated-meta command above when the original
metadata is only a partial record.

If a matching `.sigmf-meta` already exists, ingest it as a pair. It must contain
all canonical fields listed above, except `core:sha512`, which AeroLake can
calculate. If fields are missing, ingest stops before upload and prints the
complete list, for example:

```text
Existing SigMF metadata is missing required canonical fields: global.core:author, global.aerolake:operator
```

Edit the generated local metadata file and run ingest again. `antenna:*` fields
and manual annotations are optional. For supported analysis workflows,
annotations can also be generated during ingest (for example with
`--iridium-annotate`).

## Location (optional)

```json
"location": {
  "name": "LASSENA rooftop",
  "mobile": false,
  "geolocation": { "latitude": 45.4946, "longitude": -73.5623, "altitude": 50.0 }
}
```

Or let the recorder fix its own position live from `gpsd`:

```json
"location": { "name": "field test", "mobile": true, "gps": true }
```

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Human-readable place; also promoted to a searchable MinIO tag. |
| `mobile` | boolean | `true` if the receiver was moving during the capture. |
| `gps` | boolean | `true` reads the recorder's position **live from gpsd** at capture time. Mutually exclusive with `geolocation`. |
| `geolocation.latitude` | number | Degrees, −90…90. |
| `geolocation.longitude` | number | Degrees, −180…180. |
| `geolocation.altitude` | number | Optional, meters above the WGS84 ellipsoid. |

The geolocation is the **recorder's** position, written as a standard GeoJSON
Point in the SigMF `captures` segment. A manual point is entered here as
latitude/longitude for readability and converted to the spec's
`[longitude, latitude, altitude]` order automatically. With `"gps": true`, the
position is instead read live from `gpsd` and mapped to the same conformant
`core:geolocation` (ADR-016); if there is no fix, the capture simply carries no
geolocation.
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
