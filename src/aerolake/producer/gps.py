"""Live Recorder position from gpsd, mapped to SigMF-conformant geolocation.

The **"GPSD trap"**: ``gpsd`` (the Linux GPS daemon) speaks its own JSON
protocol, which is *not* SigMF. Dumping a raw gpsd report into the metadata —
or worse, whatever latitude/longitude happens to be cached when there is no
fix — produces non-standard, sometimes invalid position data. The standard,
value-adding way is to read **one position fix** from gpsd and turn it into a
spec-conformant ``core:geolocation`` GeoJSON Point (the same shape the manual
``GeolocationConfig`` path emits), or to emit **nothing** when there is no
usable fix.

This module is the pure, dependency-light core of that:

    reader() -> gpsd TPV dict  ->  fix_from_tpv  ->  GpsFix  ->  fix_to_geolocation  ->  core:geolocation | None

The gpsd *reader* is injected (default: a thin socket client to a local gpsd),
so the conversion logic — the part that actually matters for SigMF
conformance — is fully testable without a daemon or a GPS dongle. Same
dependency-injection seam the project uses for the SDR ``device_opener``, the
player clock, and moto.

Three traps handled explicitly here
-----------------------------------
1. **No fix → no geolocation.** A gpsd fix ``mode`` below 2 (0 = unknown,
   1 = no fix) means there is no usable position; we return ``None`` rather
   than fabricating coordinates.
2. **Coordinate order.** GeoJSON / SigMF require ``[longitude, latitude,
   altitude]`` — longitude first. (We reuse :class:`GeolocationConfig`, which
   already gets this right.)
3. **Altitude only on a 3D fix.** A 2D fix (``mode == 2``) has no reliable
   altitude; including one would invent data, so altitude is dropped unless the
   fix is 3D.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Callable
from dataclasses import dataclass

import structlog

from aerolake.producer.capture_config import GeolocationConfig

logger = structlog.get_logger(__name__)

# A "GPS reader" returns a single gpsd TPV (time-position-velocity) report as a
# dict. Production uses _read_gpsd_tpv (a local socket client); tests inject a
# fake returning a TPV-shaped dict, so the conversion is exercised hardware-free.
GpsReader = Callable[[], dict]

# gpsd listens here by default. Its wire protocol is line-delimited JSON.
_GPSD_HOST = "127.0.0.1"
_GPSD_PORT = 2947
_GPSD_WATCH = b'?WATCH={"enable":true,"json":true}\n'


@dataclass(frozen=True)
class GpsFix:
    """One position fix, normalized from a gpsd TPV report.

    Attributes
    ----------
    mode
        gpsd fix mode and the single source of truth for fix *quality*:
        0 = unknown, 1 = no fix, 2 = 2D fix, 3 = 3D fix.
    latitude, longitude
        Decimal degrees (WGS84). ``None`` when absent (e.g. no fix).
    altitude
        Height in metres (WGS84 ellipsoid when available), or ``None``. Only
        meaningful on a 3D fix.
    time
        ISO-8601 UTC timestamp of the fix as reported by gpsd, if present.
    """

    mode: int
    latitude: float | None = None
    longitude: float | None = None
    altitude: float | None = None
    time: str | None = None

    @property
    def has_fix(self) -> bool:
        """True when the fix is usable (2D or better, with lat *and* lon)."""
        return (
            self.mode >= 2
            and self.latitude is not None
            and self.longitude is not None
        )

    @property
    def is_3d(self) -> bool:
        """True when the fix carries a reliable altitude (3D fix)."""
        return self.mode >= 3 and self.altitude is not None


def _opt_float(value: float | int | str | None) -> float | None:
    """Coerce an optional gpsd numeric field to float (passing ``None`` through)."""
    return None if value is None else float(value)


def fix_from_tpv(tpv: dict) -> GpsFix:
    """Parse a gpsd **TPV** report into a :class:`GpsFix`.

    gpsd emits TPV objects such as::

        {"class": "TPV", "mode": 3, "lat": 45.5017, "lon": -73.5673,
         "altHAE": 42.0, "time": "2026-06-18T12:00:00.000Z"}

    Missing position fields are normal when there is no fix, so we never require
    them here — ``mode`` carries the fix quality. Altitude is read from
    ``altHAE`` (height above the WGS84 ellipsoid, which matches GeoJSON's datum)
    when present, falling back to the legacy ``alt`` then ``altMSL``.

    Raises
    ------
    ValueError
        If the report is not a TPV (so a caller can't accidentally feed in a
        SKY/VERSION/DEVICES object).
    """
    cls = tpv.get("class")
    if cls != "TPV":
        raise ValueError(f"Expected a gpsd TPV report, got class={cls!r}")
    altitude = tpv.get("altHAE", tpv.get("alt", tpv.get("altMSL")))
    return GpsFix(
        mode=int(tpv.get("mode", 0)),
        latitude=_opt_float(tpv.get("lat")),
        longitude=_opt_float(tpv.get("lon")),
        altitude=_opt_float(altitude),
        time=tpv.get("time"),
    )


def fix_to_geolocation(fix: GpsFix) -> dict[str, object] | None:
    """Map a :class:`GpsFix` to a SigMF ``core:geolocation`` Point, or ``None``.

    Returns ``None`` when there is no usable fix — the heart of dodging the GPSD
    trap: we never emit a position we do not actually have. On a valid fix the
    coordinates go through :class:`GeolocationConfig`, which **revalidates**
    latitude/longitude ranges (so a corrupt gpsd value raises rather than
    poisoning the metadata) and emits the GeoJSON ``[lon, lat, alt]`` order.
    Altitude is included only for a 3D fix.
    """
    if not fix.has_fix:
        return None
    # has_fix guarantees both are set; assert documents the invariant for mypy.
    assert fix.latitude is not None and fix.longitude is not None
    geo = GeolocationConfig(
        latitude=fix.latitude,
        longitude=fix.longitude,
        altitude=fix.altitude if fix.is_3d else None,
    )
    return geo.to_geojson()


def read_geolocation(reader: GpsReader | None = None) -> dict[str, object] | None:
    """Read one fix from gpsd and return SigMF geolocation (or ``None``).

    The end-to-end convenience: ``reader`` is the injection seam returning a
    single gpsd TPV dict (default: a local gpsd socket client). The result is a
    ready-to-store ``core:geolocation`` Point, or ``None`` when there is no fix.
    """
    reader = reader or _read_gpsd_tpv
    fix = fix_from_tpv(reader())
    geo = fix_to_geolocation(fix)
    logger.info("gps.read_geolocation", mode=fix.mode, has_geolocation=geo is not None)
    return geo


def _read_gpsd_tpv(
    *,
    host: str = _GPSD_HOST,
    port: int = _GPSD_PORT,
    timeout_s: float = 5.0,
    require_fix: bool = True,
) -> dict:
    """Read one TPV report from a live gpsd over its JSON socket.

    This is the **default, live** reader (the GPS counterpart to the real-SDR
    open path): it is exercised on the bench, not in unit tests, which inject a
    fake reader instead. It performs gpsd's minimal handshake — connect, enable
    JSON watching — then returns the first TPV seen (or the first TPV *with a
    fix* when ``require_fix`` is set), giving up after ``timeout_s``.

    Raises
    ------
    RuntimeError
        If gpsd is unreachable or no (qualifying) TPV arrives before the timeout.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout_s) as sock:
            sock.sendall(_GPSD_WATCH)
            buffer = b""
            sock.settimeout(timeout_s)
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                # gpsd frames each report as one JSON object per line.
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    report = json.loads(line)
                    if report.get("class") != "TPV":
                        continue
                    if require_fix and int(report.get("mode", 0)) < 2:
                        continue
                    return report
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Failed to read a fix from gpsd at {host}:{port}: {exc}"
        ) from exc
    raise RuntimeError(
        f"gpsd at {host}:{port} closed without sending a usable TPV fix"
    )
