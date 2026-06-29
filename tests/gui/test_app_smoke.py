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

_APP = Path(__file__).resolve().parents[2] / "src" / "aerolake" / "gui" / "app.py"


def test_app_initial_screen_runs_without_exception() -> None:
    at = AppTest.from_file(str(_APP))
    at.run(timeout=60)

    # No uncaught exception while executing the script (ElementList is empty).
    assert len(at.exception) == 0
    # The initial (no-file-uploaded) screen renders its header/markdown.
    assert len(at.markdown) > 0
