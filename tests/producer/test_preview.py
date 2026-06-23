"""Tests for the spectrum preview PNG and its upload next to a capture.

The preview is best-effort eye-candy for the lakehouse, but we still lock in:
its output is a real PNG, and `push_capture(with_preview=True)` stores it next
to the .sigmf-data/.sigmf-meta pair (`…-preview.png`).
"""

from __future__ import annotations

import numpy as np

from aerolake.producer.orchestrator import prepare_capture, push_capture
from aerolake.producer.preview import render_spectrum_png
from aerolake.producer.synthetic import SyntheticParams

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_render_spectrum_png_returns_png_bytes() -> None:
    sr = 1_000_000
    t = np.arange(50_000) / sr
    x = (0.3 * np.exp(2j * np.pi * 100_000 * t)).astype(np.complex64)
    png = render_spectrum_png(x, sr, 100_000_000)
    assert png[:8] == _PNG_MAGIC
    assert len(png) > 1000


def test_push_capture_with_preview_uploads_png(storage_client) -> None:
    prepared = prepare_capture(
        signal_type="test_synth",
        duration_s=0.05,
        sample_rate=1_000_000,
        center_freq=100_000_000,
        source=SyntheticParams(seed=1),
    )
    push_capture(prepared, storage_client, with_preview=True)

    preview_key = prepared.data_key[: -len(".sigmf-data")] + "-preview.png"
    assert storage_client.object_exists(preview_key)
    assert storage_client.download_bytes(preview_key)[:8] == _PNG_MAGIC


def test_push_capture_without_preview_writes_no_png(storage_client) -> None:
    prepared = prepare_capture(
        signal_type="test_synth",
        duration_s=0.05,
        sample_rate=1_000_000,
        center_freq=100_000_000,
        source=SyntheticParams(seed=1),
    )
    push_capture(prepared, storage_client)  # with_preview defaults to False

    preview_key = prepared.data_key[: -len(".sigmf-data")] + "-preview.png"
    assert not storage_client.object_exists(preview_key)
