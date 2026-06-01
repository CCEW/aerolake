"""Tests for partial / seeked reads (read_segment + player windowing).

We seed a capture whose samples are simply 0,1,2,…,N-1 (as complex64) so a
window is trivially checkable: the segment [a:b] must equal arange(a, b).
"""

from __future__ import annotations

import json

import numpy as np

from aerolake.common.storage import StorageClient
from aerolake.consumer.player import CapturePlayer
from aerolake.consumer.reader import CaptureReader


def _seed_arange(
    storage_client: StorageClient,
    data_key: str,
    *,
    n: int = 1000,
    sample_rate: float = 1000.0,
) -> np.ndarray:
    """Seed a capture of samples 0..n-1 at the given rate; return the samples."""
    samples = np.arange(n, dtype=np.complex64)
    meta = {
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": sample_rate,
            "core:version": "1.2.6",
        },
        "captures": [{"core:sample_start": 0, "core:frequency": 1.0}],
        "annotations": [],
    }
    meta_key = data_key[: -len(".sigmf-data")] + ".sigmf-meta"
    storage_client.upload_bytes(meta_key, json.dumps(meta).encode("utf-8"))
    storage_client.upload_bytes(
        data_key, samples.tobytes(),
        tags={"signal-type": "gnss_l1", "quality": "raw"},
    )
    return samples


# --- read_segment --------------------------------------------------------

def test_read_segment_returns_requested_window(storage_client) -> None:
    samples = _seed_arange(storage_client, "c/capture.sigmf-data", n=1000, sample_rate=1000.0)
    reader = CaptureReader(storage_client)

    # 0.2s .. 0.5s at 1000 Hz -> samples [200:500].
    content = reader.read_segment("c/capture.sigmf-data", start_s=0.2, duration_s=0.3)
    np.testing.assert_array_equal(content.samples, samples[200:500])


def test_read_segment_no_window_reads_whole(storage_client) -> None:
    samples = _seed_arange(storage_client, "c/capture.sigmf-data")
    reader = CaptureReader(storage_client)

    content = reader.read_segment("c/capture.sigmf-data")
    np.testing.assert_array_equal(content.samples, samples)


def test_read_segment_clamps_duration_to_end(storage_client) -> None:
    samples = _seed_arange(storage_client, "c/capture.sigmf-data", n=1000, sample_rate=1000.0)
    reader = CaptureReader(storage_client)

    # Ask for 5 s starting at 0.8 s — only 0.2 s exists → samples[800:].
    content = reader.read_segment("c/capture.sigmf-data", start_s=0.8, duration_s=5.0)
    np.testing.assert_array_equal(content.samples, samples[800:])


def test_read_segment_start_past_end_is_empty(storage_client) -> None:
    _seed_arange(storage_client, "c/capture.sigmf-data", n=1000, sample_rate=1000.0)
    reader = CaptureReader(storage_client)

    content = reader.read_segment("c/capture.sigmf-data", start_s=10.0)
    assert len(content.samples) == 0


# --- player windowing ----------------------------------------------------

def test_player_partial_window_emits_only_the_window(storage_client) -> None:
    _seed_arange(storage_client, "c/capture.sigmf-data", n=1000, sample_rate=1000.0)
    player = CapturePlayer(CaptureReader(storage_client), sleep=lambda _d: None)

    stats = player.play(
        "c/capture.sigmf-data",
        frame_size=100,
        realtime=False,
        start_s=0.2,
        duration_s=0.3,
    )

    # Window = 300 samples → 3 frames of 100.
    assert stats.samples == 300
    assert stats.frames == 3
