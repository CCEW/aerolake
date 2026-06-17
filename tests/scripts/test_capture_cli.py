"""Tests for the file-driven capture CLI (aerolake-capture).

We don't touch MinIO here: capture_and_upload is replaced with a stub that
records the kwargs it was called with, so we can assert the config maps onto
the engine correctly and the exit codes are right.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from aerolake.common.storage import StorageError
from aerolake.scripts import capture as capture_cli

GNSS_L1 = 1_575_420_000.0


@dataclass
class _FakeResult:
    session_id: str = "deadbeef"
    data_key: str = "gnss_l1/2026-06-17/x/capture.sigmf-data"
    meta_key: str = "gnss_l1/2026-06-17/x/capture.sigmf-meta"
    sample_count: int = 20_000_000
    bytes_uploaded: int = 160_000_000


def _write(tmp_path, payload: dict) -> str:
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _full_config() -> dict:
    return {
        "signal_type": "gnss_l1",
        "signal_type_detail": "L1 C/A",
        "center_freq": GNSS_L1,
        "sample_rate": 2_000_000.0,
        "duration_s": 10.0,
        "source": {"type": "soapy", "driver": "bladerf", "agc": True},
        "operator": "schmitt",
        "location": {"name": "LASSENA rooftop", "mobile": False},
    }


def test_capture_maps_config_onto_engine(tmp_path, monkeypatch) -> None:
    captured: dict = {}

    def fake_capture_and_upload(**kwargs):
        captured.update(kwargs)
        return _FakeResult()

    monkeypatch.setattr(capture_cli, "capture_and_upload", fake_capture_and_upload)

    code = capture_cli.main(["--config", _write(tmp_path, _full_config())])

    assert code == 0
    assert captured["signal_type"] == "gnss_l1"
    assert captured["signal_type_detail"] == "L1 C/A"
    assert captured["center_freq"] == GNSS_L1
    assert captured["sample_rate"] == 2_000_000.0
    assert captured["duration_s"] == 10.0
    assert captured["operator"] == "schmitt"
    assert captured["location"] == "LASSENA rooftop"
    assert captured["mobile"] is False
    # Source was translated to the concrete params object.
    from aerolake.producer.soapy_source import SoapyParams

    assert isinstance(captured["source"], SoapyParams)
    assert captured["source"].driver == "bladerf"


def test_capture_without_location_passes_none_and_false(tmp_path, monkeypatch) -> None:
    captured: dict = {}

    def fake_capture_and_upload(**kwargs):
        captured.update(kwargs)
        return _FakeResult()

    monkeypatch.setattr(capture_cli, "capture_and_upload", fake_capture_and_upload)

    cfg = _full_config()
    del cfg["location"]
    code = capture_cli.main(["--config", _write(tmp_path, cfg)])

    assert code == 0
    assert captured["location"] is None
    assert captured["mobile"] is False


def test_bad_config_path_exits_2(tmp_path, monkeypatch) -> None:
    # Engine must never be called when the config is unusable.
    def fail(**kwargs):  # pragma: no cover - must not run
        raise AssertionError("capture_and_upload should not be called")

    monkeypatch.setattr(capture_cli, "capture_and_upload", fail)

    code = capture_cli.main(["--config", str(tmp_path / "nope.json")])
    assert code == 2


def test_storage_error_exits_1(tmp_path, monkeypatch) -> None:
    def raise_storage(**kwargs):
        raise StorageError("MinIO unreachable")

    monkeypatch.setattr(capture_cli, "capture_and_upload", raise_storage)

    code = capture_cli.main(["--config", _write(tmp_path, _full_config())])
    assert code == 1


def test_unexpected_error_exits_3(tmp_path, monkeypatch) -> None:
    def boom(**kwargs):
        raise RuntimeError("something broke")

    monkeypatch.setattr(capture_cli, "capture_and_upload", boom)

    code = capture_cli.main(["--config", _write(tmp_path, _full_config())])
    assert code == 3


def test_missing_config_arg_errors() -> None:
    # argparse exits with SystemExit(2) when a required arg is absent.
    with pytest.raises(SystemExit):
        capture_cli.main([])


def test_build_rich_metadata_splits_antenna_pointing_into_annotation() -> None:
    # polarization/azimuth/elevation must ride in the annotation (SigMF spec),
    # not the Global antenna block; the other antenna scalars stay Global.
    from aerolake.producer.capture_config import CaptureConfig
    from aerolake.scripts.capture import _build_rich_metadata

    config = CaptureConfig.model_validate(
        {
            "signal_type": "gnss_l1",
            "center_freq": GNSS_L1,
            "sample_rate": 2_000_000.0,
            "duration_s": 1.0,
            "location": {
                "name": "LASSENA rooftop",
                "geolocation": {"latitude": 45.4946, "longitude": -73.5623},
            },
            "antenna": {
                "model": "Tallysman TW3742",
                "gain": 28.0,
                "polarization": "right-hand circular",
                "azimuth_angle": 90.0,
            },
        }
    )
    rich = _build_rich_metadata(config)

    # Global antenna keeps scalars, drops pointing fields.
    assert rich.antenna is not None
    assert rich.antenna["model"] == "Tallysman TW3742"
    assert rich.antenna["gain"] == 28.0
    assert "polarization" not in rich.antenna
    assert "azimuth_angle" not in rich.antenna

    # Pointing fields are in the annotation instead.
    assert rich.annotation is not None
    assert rich.annotation["polarization"] == "right-hand circular"
    assert rich.annotation["azimuth_angle"] == 90.0

    # Geolocation flattened to GeoJSON [lon, lat].
    assert rich.geolocation is not None
    assert rich.geolocation["coordinates"] == [-73.5623, 45.4946]


def test_build_rich_metadata_empty_when_nothing_supplied() -> None:
    from aerolake.producer.capture_config import CaptureConfig
    from aerolake.scripts.capture import _build_rich_metadata

    config = CaptureConfig.model_validate(
        {
            "signal_type": "gnss_l1",
            "center_freq": GNSS_L1,
            "sample_rate": 2_000_000.0,
            "duration_s": 1.0,
        }
    )
    rich = _build_rich_metadata(config)
    assert rich.antenna is None
    assert rich.annotation is None
    assert rich.geolocation is None
