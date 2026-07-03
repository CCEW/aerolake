"""Unit tests for aerolake.producer.gps (live position -> SigMF geolocation).

The conversion core (gpsd TPV -> GpsFix -> core:geolocation) is pure, so these
tests need no gpsd and no GPS hardware: we feed TPV-shaped dicts directly and
inject a fake reader for the end-to-end path. The three "GPSD trap" behaviours
(no-fix -> None, [lon, lat, alt] order, altitude only on a 3D fix) each get a
test.
"""

from __future__ import annotations

import pytest

from aerolake.producer.gps import (
    GpsFix,
    fix_from_tpv,
    fix_to_geolocation,
    read_geolocation,
)

# A realistic gpsd 3D TPV report.
_TPV_3D = {
    "class": "TPV",
    "mode": 3,
    "lat": 45.5017,
    "lon": -73.5673,
    "altHAE": 42.0,
    "time": "2026-06-18T12:00:00.000Z",
}


# --- fix_from_tpv --------------------------------------------------------


def test_fix_from_tpv_parses_all_fields() -> None:
    fix = fix_from_tpv(_TPV_3D)
    assert fix.mode == 3
    assert fix.latitude == 45.5017
    assert fix.longitude == -73.5673
    assert fix.altitude == 42.0
    assert fix.time == "2026-06-18T12:00:00.000Z"
    assert fix.has_fix is True
    assert fix.is_3d is True


def test_fix_from_tpv_altitude_falls_back_to_legacy_alt() -> None:
    fix = fix_from_tpv({"class": "TPV", "mode": 3, "lat": 1.0, "lon": 2.0, "alt": 10.0})
    assert fix.altitude == 10.0


def test_fix_from_tpv_no_fix_has_no_position() -> None:
    fix = fix_from_tpv({"class": "TPV", "mode": 1})
    assert fix.mode == 1
    assert fix.latitude is None
    assert fix.has_fix is False


def test_fix_from_tpv_rejects_non_tpv() -> None:
    with pytest.raises(ValueError, match="TPV"):
        fix_from_tpv({"class": "SKY"})


# --- fix_to_geolocation (the three traps) --------------------------------


def test_3d_fix_yields_point_in_lon_lat_alt_order() -> None:
    geo = fix_to_geolocation(fix_from_tpv(_TPV_3D))
    assert geo is not None
    assert geo["type"] == "Point"
    # Trap #2: longitude FIRST, then latitude, then altitude.
    assert geo["coordinates"] == [-73.5673, 45.5017, 42.0]


def test_2d_fix_drops_altitude() -> None:
    # Trap #3: a 2D fix has no reliable altitude, even if a value sneaks in.
    fix = GpsFix(mode=2, latitude=45.0, longitude=-73.0, altitude=999.0)
    geo = fix_to_geolocation(fix)
    assert geo is not None
    assert geo["coordinates"] == [-73.0, 45.0]  # no altitude


def test_no_fix_yields_no_geolocation() -> None:
    # Trap #1: no usable fix -> we emit nothing rather than fabricate a position.
    assert fix_to_geolocation(GpsFix(mode=1)) is None
    assert fix_to_geolocation(GpsFix(mode=0)) is None


def test_corrupt_coordinates_are_rejected() -> None:
    # Out-of-range values from gpsd must raise (revalidated via GeolocationConfig),
    # not silently poison the metadata.
    with pytest.raises(ValueError):
        fix_to_geolocation(GpsFix(mode=3, latitude=200.0, longitude=0.0))


# --- read_geolocation (injected reader) ----------------------------------


def test_read_geolocation_with_injected_reader() -> None:
    geo = read_geolocation(reader=lambda: _TPV_3D)
    assert geo == {"type": "Point", "coordinates": [-73.5673, 45.5017, 42.0]}


def test_read_geolocation_returns_none_without_fix() -> None:
    geo = read_geolocation(reader=lambda: {"class": "TPV", "mode": 1})
    assert geo is None
