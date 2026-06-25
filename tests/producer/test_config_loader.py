"""Tests for the TOML/JSON capture-config loader.

Cover the valid paths (TOML and JSON) and the failure modes the loader must
turn into a clean ConfigError: missing file, malformed TOML/JSON, and schema
violation. TOML and JSON must produce an identical CaptureConfig.
"""

from __future__ import annotations

import json

import pytest

from aerolake.producer.capture_config import CaptureConfig
from aerolake.producer.config_loader import ConfigError, load_capture_config

GNSS_L1 = 1_575_420_000.0


def _valid_dict() -> dict[str, object]:
    return {
        "signal_type": "gnss_l1",
        "center_freq": GNSS_L1,
        "sample_rate": 2_000_000.0,
        "duration_s": 10.0,
        "source": {"type": "soapy", "driver": "bladerf"},
    }


def test_loads_a_valid_config(tmp_path) -> None:
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(_valid_dict()), encoding="utf-8")

    config = load_capture_config(path)

    assert isinstance(config, CaptureConfig)
    assert config.signal_type == "gnss_l1"
    assert config.center_freq == GNSS_L1


def test_missing_file_raises_config_error(tmp_path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_capture_config(tmp_path / "does_not_exist.json")


def test_malformed_json_raises_config_error(tmp_path) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"signal_type": "gnss_l1",,}', encoding="utf-8")

    with pytest.raises(ConfigError, match="Invalid JSON"):
        load_capture_config(path)


def test_non_object_top_level_raises_config_error(tmp_path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(ConfigError, match="object at the top"):
        load_capture_config(path)


def test_schema_violation_raises_config_error(tmp_path) -> None:
    bad = _valid_dict()
    del bad["signal_type"]  # required field missing
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(bad), encoding="utf-8")

    with pytest.raises(ConfigError, match="not a valid capture request"):
        load_capture_config(path)


def test_unknown_field_raises_config_error(tmp_path) -> None:
    bad = _valid_dict()
    bad["smaple_rate"] = 1.0  # typo -> extra="forbid" rejects it
    path = tmp_path / "typo.json"
    path.write_text(json.dumps(bad), encoding="utf-8")

    with pytest.raises(ConfigError):
        load_capture_config(path)


# ---------------------------------------------------------------------------
# TOML support (recommended format; chosen by the .toml extension)
# ---------------------------------------------------------------------------

_VALID_TOML = """
# a comment, which JSON could never carry
signal_type = "gnss_l1"
center_freq = 1_575_420_000   # Hz
sample_rate = 2_000_000
duration_s  = 10

[source]
type   = "soapy"
driver = "bladerf"
"""


def test_loads_a_valid_toml_config(tmp_path) -> None:
    path = tmp_path / "capture.toml"
    path.write_text(_VALID_TOML, encoding="utf-8")

    config = load_capture_config(path)

    assert isinstance(config, CaptureConfig)
    assert config.signal_type == "gnss_l1"
    assert config.center_freq == GNSS_L1
    assert config.source.driver == "bladerf"


def test_toml_and_json_describe_the_same_config(tmp_path) -> None:
    # The same capture, written in each format, must validate identically.
    jpath = tmp_path / "c.json"
    jpath.write_text(json.dumps(_valid_dict()), encoding="utf-8")
    tpath = tmp_path / "c.toml"
    tpath.write_text(_VALID_TOML, encoding="utf-8")

    assert load_capture_config(tpath).model_dump() == load_capture_config(jpath).model_dump()


def test_malformed_toml_raises_config_error(tmp_path) -> None:
    path = tmp_path / "broken.toml"
    path.write_text("signal_type = ", encoding="utf-8")  # value missing

    with pytest.raises(ConfigError, match="Invalid TOML"):
        load_capture_config(path)


def test_toml_schema_violation_raises_config_error(tmp_path) -> None:
    path = tmp_path / "invalid.toml"
    # center_freq missing -> schema violation surfaced as ConfigError
    path.write_text('signal_type = "x"\nsample_rate = 1.0\nduration_s = 1.0\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="not a valid capture request"):
        load_capture_config(path)
