"""Tests that the shipped example configs stay valid.

The templates in examples/ are what users copy to write their own captures. If
the CaptureConfig schema changes and a template falls out of sync, these tests
fail — so the examples can never silently lie about what a valid config is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aerolake.producer.capture_config import CaptureConfig
from aerolake.producer.config_loader import load_capture_config

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"

# Every shipped config, in both formats — so a TOML *or* JSON template can never
# silently fall out of sync with the schema.
EXAMPLE_FILES = sorted(
    [
        *EXAMPLES_DIR.glob("capture*.json"),
        *EXAMPLES_DIR.glob("capture*.toml"),
    ]
)
EXAMPLE_FILES = [path for path in EXAMPLE_FILES if ".sigmf-meta." not in path.name]


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: p.name)
def test_example_config_loads_and_validates(path: Path) -> None:
    config = load_capture_config(path)
    assert isinstance(config, CaptureConfig)
    assert config.signal_type
    assert config.center_freq > 0
    assert config.sample_rate > 0
    assert config.duration_s > 0


def test_full_example_exercises_every_optional_block() -> None:
    # The "full" template is meant to show all the optional blocks at once;
    # guard that it actually does, so it stays a useful reference.
    config = load_capture_config(EXAMPLES_DIR / "capture.full.json")
    assert config.location is not None
    assert config.location.geolocation is not None
    assert config.annotation is not None
    assert config.antenna is not None
    assert config.author is not None
    assert config.description is not None
    assert config.license is not None
