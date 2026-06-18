# ADR-016 — SigMF-native Recorder geolocation from gpsd (avoid the "GPSD trap")

- **Status:** Accepted
- **Date:** 2026-06-18
- **Author:** Théo Schmitt
- **Supersedes:** N/A (complements the manual `GeolocationConfig` path)

## Context

AeroLake can already stamp a capture's position: `GeolocationConfig`
(lat/lon/alt, hand-typed in the JSON capture config) is converted to a
SigMF `core:geolocation` GeoJSON Point. But the Recorder usually *has* a GPS
receiver, and the supervisor (Abdu, 2026-06-18 call) flagged reading its **live
position** as a high-value, demo-worthy brick — with an explicit warning about
the **"GPSD trap"**: `gpsd` (the Linux GPS daemon) speaks its own JSON protocol,
which is *not* SigMF. Dumping a raw gpsd report into the metadata — or, worse,
whatever lat/lon is cached when there is no fix — yields non-standard, sometimes
invalid position data.

## Decision

**Add `producer/gps.py`: read one fix from gpsd and convert it to a
spec-conformant `core:geolocation`, going through the same validated
`GeolocationConfig`, or emit nothing when there is no fix.**

The pipeline is small and mostly pure:

    reader() -> gpsd TPV dict -> fix_from_tpv -> GpsFix -> fix_to_geolocation -> core:geolocation | None

- **Injected reader.** `read_geolocation(reader=...)` takes a `GpsReader`
  callable returning a single gpsd **TPV** report. The default
  (`_read_gpsd_tpv`) is a thin socket client to a local gpsd; tests inject a
  fake returning a TPV dict, so the conversion — the SigMF-relevant part — is
  tested with no daemon and no GPS dongle. Same dependency-injection seam as the
  SDR `device_opener` (ADR-015), the player clock, and moto.
- **Three traps handled explicitly:**
  1. **No fix → no geolocation.** A gpsd `mode < 2` (0 = unknown, 1 = no fix)
     means no usable position; we return `None` instead of fabricating one.
  2. **Coordinate order.** GeoJSON/SigMF require `[longitude, latitude,
     altitude]` — longitude first. We reuse `GeolocationConfig`, which already
     enforces this.
  3. **Altitude only on a 3D fix.** A 2D fix has no reliable altitude, so it is
     dropped unless `mode == 3`.
- **Revalidation, not raw trust.** Coordinates pass through `GeolocationConfig`
  (pydantic range checks), so a corrupt gpsd value raises rather than poisoning
  the metadata; the resulting Point is also covered by the encoder's full SigMF
  validation when the capture is written.
- **Dependency-light.** The default reader speaks gpsd's line-delimited JSON
  over a stdlib socket — no extra GPS library is added to the project.

### Whose position, and where it is written

The SigMF v1.2.6 schema is explicit that `core:geolocation` is **the location of
the Recording system** (the *recorder*), not the emitter — in both the Global
and the Captures scope. The spec marks the **Captures scope as preferred** ("the
location of the recording system at the start of this Captures segment… adding
it to Captures is preferred"; the Global field is kept "for backwards
compatibility" and for fixed systems). AeroLake already writes geolocation into
the **capture segment** (`sigmf_writer.encode`), so a live gpsd fix threaded
through `RichMetadata.geolocation` lands in the preferred scope automatically.
The Captures scope also means the point is the recorder's position *at the start
of that segment*, which is what enables per-segment position for a **mobile**
recorder later.

## Rationale

- **Directly answers the supervisor's brief** and is demoable on its own
  ("live GPS → standard SigMF geolocation").
- **Conformance over convenience**: the value is precisely in *not* dumping raw
  gpsd, but mapping it to the spec via the existing validated model.
- **Testable**: the conversion logic is pure and fully covered without hardware.

## Consequences

### Positive

- The Recorder's real position can be recorded automatically and correctly.
- Reuses `GeolocationConfig`, so the manual and live paths emit identical shapes.

### Negative / open

- **Not yet wired into the capture path.** This ADR delivers the conversion
  brick; a follow-up will let a capture opt into a live fix (e.g. a config flag
  / `aerolake-capture` option) and thread the result into
  `RichMetadata.geolocation`.
- The live `_read_gpsd_tpv` reader is bench-tested only (like the real-SDR open
  path): it is not exercised in CI, which injects a fake reader instead.
- Single-shot position (one fix per capture). Per-segment position tracks for a
  moving Recorder are out of scope here.

## Alternatives considered

- **Dump the raw gpsd report into the metadata**: the trap itself — non-standard
  and unvalidated; rejected.
- **Depend on a gpsd Python client library** (`gps`, `gpsd-py3`): heavier and an
  extra dependency for what is a few lines of line-delimited JSON over a socket.
- **A new SigMF extension for position**: unnecessary — `core:geolocation`
  (GeoJSON Point) is already the spec's native representation.

## References

- Supervisor call (Abdu, 2026-06-18): the "GPSD trap"; SigMF-native geolocation
- SigMF spec v1.2.6 — `core:geolocation` (RFC 7946 GeoJSON Point)
- `src/aerolake/producer/gps.py`, `src/aerolake/producer/capture_config.py`
  (`GeolocationConfig`), `tests/producer/test_gps.py`
