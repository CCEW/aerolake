"""Tests for the declarative CaptureConfig model.

Pure validation/translation tests -- no I/O, no hardware. They lock in the
JSON contract: required fields, defaults, the discriminated source union, the
GeoJSON [lon, lat, alt] conversion, the SigMF freq-edge pairing rule, the
antenna ``model`` requirement, and strict rejection of unknown keys.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aerolake.producer.capture_config import (
    AntennaConfig,
    CaptureConfig,
    GeolocationConfig,
)
from aerolake.producer.soapy_source import SoapyParams
from aerolake.producer.synthetic import SyntheticParams

GNSS_L1 = 1_575_420_000.0


def _minimal() -> dict[str, object]:
    return {
        "signal_type": "gnss_l1",
        "center_freq": GNSS_L1,
        "sample_rate": 2_000_000.0,
        "duration_s": 10.0,
    }


def test_minimal_config_is_valid_and_defaults_to_synthetic() -> None:
    cfg = CaptureConfig.model_validate(_minimal())
    assert cfg.signal_type == "gnss_l1"
    # Source defaults to synthetic when omitted.
    params = cfg.source_params()
    assert isinstance(params, SyntheticParams)


def test_synthetic_source_translates_to_params() -> None:
    cfg = CaptureConfig.model_validate(
        {
            **_minimal(),
            "source": {"type": "synthetic", "tone_offset_hz": 250.0, "snr_db": 5.0},
        }
    )
    params = cfg.source_params()
    assert isinstance(params, SyntheticParams)
    assert params.tone_offset_hz == 250.0
    assert params.snr_db == 5.0


def test_soapy_source_translates_to_params() -> None:
    cfg = CaptureConfig.model_validate(
        {
            **_minimal(),
            "source": {"type": "soapy", "driver": "bladerf", "agc": False},
        }
    )
    params = cfg.source_params()
    assert isinstance(params, SoapyParams)
    assert params.driver == "bladerf"
    assert params.agc is False


def test_unknown_source_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CaptureConfig.model_validate(
            {**_minimal(), "source": {"type": "usrp"}}
        )


def test_geolocation_to_geojson_lon_lat_alt_order() -> None:
    geo = GeolocationConfig(latitude=45.4946, longitude=-73.5623, altitude=50.0)
    gj = geo.to_geojson()
    assert gj["type"] == "Point"
    # RFC 7946 order: longitude first, then latitude, then altitude.
    assert gj["coordinates"] == [-73.5623, 45.4946, 50.0]


def test_geolocation_omits_altitude_when_absent() -> None:
    geo = GeolocationConfig(latitude=45.0, longitude=-73.0)
    assert geo.to_geojson()["coordinates"] == [-73.0, 45.0]


def test_geolocation_rejects_out_of_range() -> None:
    with pytest.raises(ValidationError):
        GeolocationConfig(latitude=200.0, longitude=0.0)


def test_annotation_requires_both_freq_edges() -> None:
    with pytest.raises(ValidationError):
        CaptureConfig.model_validate(
            {**_minimal(), "annotation": {"freq_lower_edge": GNSS_L1}}
        )


def test_annotation_rejects_inverted_edges() -> None:
    with pytest.raises(ValidationError):
        CaptureConfig.model_validate(
            {
                **_minimal(),
                "annotation": {
                    "freq_lower_edge": GNSS_L1 + 1_000.0,
                    "freq_upper_edge": GNSS_L1,
                },
            }
        )


def test_annotation_accepts_both_edges() -> None:
    cfg = CaptureConfig.model_validate(
        {
            **_minimal(),
            "annotation": {
                "label": "L1 C/A",
                "freq_lower_edge": GNSS_L1 - 1_000_000.0,
                "freq_upper_edge": GNSS_L1 + 1_000_000.0,
            },
        }
    )
    assert cfg.annotation is not None
    assert cfg.annotation.label == "L1 C/A"


def test_antenna_requires_model() -> None:
    with pytest.raises(ValidationError):
        AntennaConfig.model_validate({"gain": 28.0})


def test_antenna_minimal_with_model_only() -> None:
    ant = AntennaConfig.model_validate({"model": "Tallysman TW3742"})
    assert ant.model == "Tallysman TW3742"
    assert ant.gain is None


def test_unknown_top_level_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CaptureConfig.model_validate({**_minimal(), "smaple_rate": 1.0})


def test_sample_rate_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        CaptureConfig.model_validate({**_minimal(), "sample_rate": 0.0})


def test_full_config_parses() -> None:
    cfg = CaptureConfig.model_validate(
        {
            "signal_type": "gnss_l1",
            "signal_type_detail": "L1 C/A",
            "center_freq": GNSS_L1,
            "sample_rate": 2_000_000.0,
            "duration_s": 10.0,
            "source": {"type": "soapy", "driver": "bladerf", "agc": True},
            "author": "Theo Schmitt",
            "description": "Capture GPS L1 LASSENA rooftop",
            "license": "https://creativecommons.org/licenses/by-sa/4.0/",
            "operator": "schmitt",
            "location": {
                "name": "LASSENA rooftop",
                "mobile": False,
                "geolocation": {
                    "latitude": 45.4946,
                    "longitude": -73.5623,
                    "altitude": 50.0,
                },
            },
            "annotation": {
                "label": "GPS L1 C/A",
                "comment": "active antenna, clear sky",
                "freq_lower_edge": GNSS_L1 - 1_000_000.0,
                "freq_upper_edge": GNSS_L1 + 1_000_000.0,
            },
            "antenna": {
                "model": "Tallysman TW3742",
                "type": "active GNSS patch",
                "gain": 28.0,
                "cable_loss": 2.0,
                "hagl": 1.5,
                "polarization": "right-hand circular",
            },
        }
    )
    assert cfg.location is not None
    assert cfg.location.geolocation is not None
    assert cfg.location.geolocation.to_geojson()["coordinates"] == [
        -73.5623,
        45.4946,
        50.0,
    ]
    assert isinstance(cfg.source_params(), SoapyParams)
