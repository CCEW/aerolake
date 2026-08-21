"""Smoke test for the Streamlit GUI (optional ``gui`` extra).

Streamlit ships ``AppTest``, which runs the app script headlessly in a simulated
session — enough to catch a broken Streamlit API call or an import error without
a browser. Skipped automatically when Streamlit isn't installed (the GUI is an
optional extra, absent from the default dev environment).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="GUI extra not installed (uv sync --extra gui)")

from streamlit.testing.v1 import AppTest

from aerolake.gui.app import _find_preview_key, _preview_key_candidates

_APP = Path(__file__).resolve().parents[2] / "src" / "aerolake" / "gui" / "app.py"


class _FakeStorage:
    def __init__(self, existing_keys: set[str] | None = None) -> None:
        self._existing_keys = existing_keys or set()

    def object_exists(self, key: str) -> bool:
        return key in self._existing_keys


def test_app_initial_screen_runs_without_exception() -> None:
    at = AppTest.from_file(str(_APP))
    at.run(timeout=60)

    # No uncaught exception while executing the script (ElementList is empty).
    assert len(at.exception) == 0
    # The initial (no-file-uploaded) screen renders its header/markdown.
    assert len(at.markdown) > 0


def test_preview_key_candidates_include_capture_and_ingest_names() -> None:
    assert _preview_key_candidates("iridium/2026/session/capture.sigmf-data") == (
        "iridium/2026/session/capture-preview.png",
        "iridium/2026/session/capture.preview.jpg",
        "iridium/2026/session/capture-preview.jpg",
        "iridium/2026/session/capture.jpg",
    )


def test_find_preview_key_falls_back_to_ingest_preview_jpeg() -> None:
    storage = _FakeStorage(
        existing_keys={"iridium/2026/session/capture.preview.jpg"},
    )

    assert (
        _find_preview_key(storage, "iridium/2026/session/capture.sigmf-data")
        == "iridium/2026/session/capture.preview.jpg"
    )
