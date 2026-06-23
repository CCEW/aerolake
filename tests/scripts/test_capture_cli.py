"""Tests for the file-driven capture CLI (aerolake-capture).

No MinIO and no real capture here: prepare_capture / push_capture /
save_capture_locally are stubbed, and the rich Confirm prompts are scripted, so
we assert the prepare->confirm->push/save/discard flow and the exit codes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from aerolake.common.storage import StorageError
from aerolake.scripts import capture as capture_cli

GNSS_L1 = 1_575_420_000.0


@dataclass
class _FakePrepared:
    session_id: str = "deadbeef"
    data_key: str = "gnss_l1/2026-06-17/x/capture.sigmf-data"
    meta_key: str = "gnss_l1/2026-06-17/x/capture.sigmf-meta"
    data_bytes: bytes = b"\x00\x00\x00\x00"
    meta_bytes: bytes = b"{}"
    data_metadata: dict | None = None
    data_tags: dict | None = None
    sample_count: int = 20_000
    overflow_count: int | None = None

    @property
    def size_bytes(self) -> int:
        return len(self.data_bytes) + len(self.meta_bytes)


@dataclass
class _FakeResult:
    session_id: str = "deadbeef"
    data_key: str = "gnss_l1/2026-06-17/x/capture.sigmf-data"
    meta_key: str = "gnss_l1/2026-06-17/x/capture.sigmf-meta"
    sample_count: int = 20_000
    bytes_uploaded: int = 160_000


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


def _stub_prepare(monkeypatch, captured: dict | None = None) -> None:
    """Replace prepare_capture with a stub returning a fake prepared capture."""

    def fake_prepare(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        return _FakePrepared()

    monkeypatch.setattr(capture_cli, "prepare_capture", fake_prepare)


def _answers(monkeypatch, *responses: bool) -> None:
    """Script successive Confirm.ask() answers in order."""
    seq = iter(responses)
    monkeypatch.setattr(capture_cli.Confirm, "ask", lambda *a, **k: next(seq))


# ---------------------------------------------------------------------------
# Config -> prepare mapping
# ---------------------------------------------------------------------------


def test_prepare_receives_mapped_config(tmp_path, monkeypatch) -> None:
    captured: dict = {}
    _stub_prepare(monkeypatch, captured)
    monkeypatch.setattr(capture_cli, "push_capture", lambda prepared, **kw: _FakeResult())
    _answers(monkeypatch, True)

    code = capture_cli.main(["--config", _write(tmp_path, _full_config())])

    assert code == 0
    assert captured["signal_type"] == "gnss_l1"
    assert captured["signal_type_detail"] == "L1 C/A"
    assert captured["center_freq"] == GNSS_L1
    assert captured["operator"] == "schmitt"
    assert captured["location"] == "LASSENA rooftop"
    assert captured["mobile"] is False
    from aerolake.producer.soapy_source import SoapyParams

    assert isinstance(captured["source"], SoapyParams)
    assert captured["source"].driver == "bladerf"


def test_without_location_passes_none_and_false(tmp_path, monkeypatch) -> None:
    captured: dict = {}
    _stub_prepare(monkeypatch, captured)
    monkeypatch.setattr(capture_cli, "push_capture", lambda prepared, **kw: _FakeResult())
    _answers(monkeypatch, True)

    cfg = _full_config()
    del cfg["location"]
    code = capture_cli.main(["--config", _write(tmp_path, cfg)])

    assert code == 0
    assert captured["location"] is None
    assert captured["mobile"] is False


# ---------------------------------------------------------------------------
# Confirmation flow: push / keep local / discard
# ---------------------------------------------------------------------------


def test_yes_pushes_to_minio(tmp_path, monkeypatch) -> None:
    _stub_prepare(monkeypatch)
    pushed: dict = {}

    def fake_push(prepared, **kw):
        pushed["called"] = True
        return _FakeResult()

    monkeypatch.setattr(capture_cli, "push_capture", fake_push)
    monkeypatch.setattr(
        capture_cli,
        "save_capture_locally",
        lambda *a, **k: pytest.fail("save must not run when pushing"),
    )
    _answers(monkeypatch, True)  # "Push to MinIO?" -> yes

    code = capture_cli.main(["--config", _write(tmp_path, _full_config())])
    assert code == 0
    assert pushed.get("called") is True


def test_no_then_yes_saves_locally(tmp_path, monkeypatch) -> None:
    _stub_prepare(monkeypatch)
    saved: dict = {}

    def fake_save(prepared):
        saved["called"] = True
        return tmp_path / "captures" / "x"

    monkeypatch.setattr(
        capture_cli,
        "push_capture",
        lambda *a, **k: pytest.fail("push must not run when declined"),
    )
    monkeypatch.setattr(capture_cli, "save_capture_locally", fake_save)
    _answers(monkeypatch, False, True)  # push? no -> keep local? yes

    code = capture_cli.main(["--config", _write(tmp_path, _full_config())])
    assert code == 0
    assert saved.get("called") is True


def test_no_then_no_discards(tmp_path, monkeypatch) -> None:
    _stub_prepare(monkeypatch)
    monkeypatch.setattr(
        capture_cli,
        "push_capture",
        lambda *a, **k: pytest.fail("push must not run"),
    )
    monkeypatch.setattr(
        capture_cli,
        "save_capture_locally",
        lambda *a, **k: pytest.fail("save must not run when discarding"),
    )
    _answers(monkeypatch, False, False)  # push? no -> keep? no -> discard

    code = capture_cli.main(["--config", _write(tmp_path, _full_config())])
    assert code == 0


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_bad_config_path_exits_2(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        capture_cli,
        "prepare_capture",
        lambda **k: pytest.fail("prepare must not run on bad config"),
    )
    code = capture_cli.main(["--config", str(tmp_path / "nope.json")])
    assert code == 2


def test_storage_error_on_push_exits_1(tmp_path, monkeypatch) -> None:
    _stub_prepare(monkeypatch)

    def raise_storage(prepared, **kw):
        raise StorageError("MinIO unreachable")

    monkeypatch.setattr(capture_cli, "push_capture", raise_storage)
    _answers(monkeypatch, True)

    code = capture_cli.main(["--config", _write(tmp_path, _full_config())])
    assert code == 1


def test_capture_error_exits_3(tmp_path, monkeypatch) -> None:
    def boom(**kwargs):
        raise RuntimeError("SDR fell over")

    monkeypatch.setattr(capture_cli, "prepare_capture", boom)

    code = capture_cli.main(["--config", _write(tmp_path, _full_config())])
    assert code == 3


def test_missing_config_arg_errors() -> None:
    with pytest.raises(SystemExit):
        capture_cli.main([])


# ---------------------------------------------------------------------------
# _build_rich_metadata (unchanged logic, kept from Palier 3)
# ---------------------------------------------------------------------------


def test_build_rich_metadata_splits_antenna_pointing_into_annotation() -> None:
    from aerolake.producer.capture_config import CaptureConfig
    from aerolake.scripts.capture import _build_rich_metadata, _resolve_geolocation

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
    rich = _build_rich_metadata(config, _resolve_geolocation(config))

    assert rich.antenna is not None
    assert rich.antenna["model"] == "Tallysman TW3742"
    assert rich.antenna["gain"] == 28.0
    assert "polarization" not in rich.antenna
    assert "azimuth_angle" not in rich.antenna

    assert rich.annotation is not None
    assert rich.annotation["polarization"] == "right-hand circular"
    assert rich.annotation["azimuth_angle"] == 90.0

    assert rich.geolocation is not None
    assert rich.geolocation["coordinates"] == [-73.5623, 45.4946]


def test_build_rich_metadata_empty_when_nothing_supplied() -> None:
    from aerolake.producer.capture_config import CaptureConfig
    from aerolake.scripts.capture import _build_rich_metadata, _resolve_geolocation

    config = CaptureConfig.model_validate(
        {
            "signal_type": "gnss_l1",
            "center_freq": GNSS_L1,
            "sample_rate": 2_000_000.0,
            "duration_s": 1.0,
        }
    )
    rich = _build_rich_metadata(config, _resolve_geolocation(config))
    assert rich.antenna is None
    assert rich.annotation is None
    assert rich.geolocation is None


# ---------------------------------------------------------------------------
# _resolve_geolocation: live gpsd fix vs manual point vs none (ADR-016 wiring)
# ---------------------------------------------------------------------------


def _config_with_location(**location: object):
    from aerolake.producer.capture_config import CaptureConfig

    return CaptureConfig.model_validate(
        {
            "signal_type": "gnss_l1",
            "center_freq": GNSS_L1,
            "sample_rate": 2_000_000.0,
            "duration_s": 1.0,
            "location": {"name": "rooftop", **location},
        }
    )


def test_resolve_geolocation_live_gps_uses_injected_reader() -> None:
    from aerolake.scripts.capture import _resolve_geolocation

    config = _config_with_location(gps=True)
    tpv = {"class": "TPV", "mode": 3, "lat": 45.0, "lon": -73.0, "altHAE": 12.0}
    geo = _resolve_geolocation(config, gps_reader=lambda: tpv)
    assert geo == {"type": "Point", "coordinates": [-73.0, 45.0, 12.0]}


def test_resolve_geolocation_falls_back_to_manual_point() -> None:
    from aerolake.scripts.capture import _resolve_geolocation

    config = _config_with_location(
        geolocation={"latitude": 45.0, "longitude": -73.0}
    )
    assert _resolve_geolocation(config) == {
        "type": "Point",
        "coordinates": [-73.0, 45.0],
    }


def test_resolve_geolocation_none_without_location() -> None:
    from aerolake.producer.capture_config import CaptureConfig
    from aerolake.scripts.capture import _resolve_geolocation

    config = CaptureConfig.model_validate(
        {
            "signal_type": "gnss_l1",
            "center_freq": GNSS_L1,
            "sample_rate": 2_000_000.0,
            "duration_s": 1.0,
        }
    )
    assert _resolve_geolocation(config) is None


def test_resolve_geolocation_gps_without_fix_returns_none() -> None:
    from aerolake.scripts.capture import _resolve_geolocation

    config = _config_with_location(gps=True)
    geo = _resolve_geolocation(config, gps_reader=lambda: {"class": "TPV", "mode": 1})
    assert geo is None
