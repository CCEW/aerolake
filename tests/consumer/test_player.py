"""Unit tests for aerolake.consumer.player.

The pure frame-splitting (iter_frames) is tested directly. Playback pacing is
tested with an **injected fake clock** that records the requested sleep
durations, so we verify the cadence maths without any real-time waiting.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from aerolake.common.storage import StorageClient
from aerolake.consumer.player import CapturePlayer, PlaybackStats, iter_frames
from aerolake.consumer.reader import CaptureReader
from aerolake.producer.synthetic import generate_tone

# --- iter_frames (pure) --------------------------------------------------


def test_iter_frames_splits_into_full_and_remainder() -> None:
    samples = np.arange(10, dtype=np.complex64)
    frames = list(iter_frames(samples, 4))
    assert [len(f) for f in frames] == [4, 4, 2]
    # Concatenating the frames must reproduce the original signal exactly.
    np.testing.assert_array_equal(np.concatenate(frames), samples)


def test_iter_frames_empty_yields_nothing() -> None:
    assert list(iter_frames(np.array([], dtype=np.complex64), 4)) == []


def test_iter_frames_rejects_nonpositive_size() -> None:
    with pytest.raises(ValueError, match="frame_size"):
        list(iter_frames(np.zeros(4, dtype=np.complex64), 0))


# --- CapturePlayer -------------------------------------------------------


def _seed_capture(
    storage_client: StorageClient,
    data_key: str,
    *,
    sample_rate: float = 2_000_000.0,
    duration_s: float = 0.01,
) -> int:
    """Seed a capture and return its sample count."""
    signal = generate_tone(
        duration_s=duration_s,
        sample_rate=sample_rate,
        center_freq=1_575_420_000.0,
        seed=42,
    )
    sigmf_meta = {
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": float(sample_rate),
            "core:version": "1.2.6",
        },
        "captures": [{"core:sample_start": 0, "core:frequency": 1_575_420_000.0}],
        "annotations": [],
    }
    meta_key = data_key[: -len(".sigmf-data")] + ".sigmf-meta"
    storage_client.upload_bytes(
        meta_key,
        json.dumps(sigmf_meta).encode("utf-8"),
        content_type="application/json",
    )
    storage_client.upload_bytes(
        data_key,
        signal.samples.tobytes(),
        content_type="application/octet-stream",
        tags={"signal-type": "gnss_l1", "quality": "raw"},
    )
    return len(signal.samples)


def test_play_paces_each_frame_at_recorded_rate(storage_client) -> None:
    """Real-time playback should sleep frame_size/sample_rate per full frame."""
    sample_rate = 2_000_000.0
    n = _seed_capture(storage_client, "gnss_l1/A/capture.sigmf-data", sample_rate=sample_rate)

    # Fake clock: record every requested sleep duration instead of waiting.
    slept: list[float] = []
    player = CapturePlayer(CaptureReader(storage_client), sleep=slept.append)

    frame_size = 4096
    stats = player.play("gnss_l1/A/capture.sigmf-data", frame_size=frame_size, realtime=True)

    # One sleep per emitted frame.
    expected_frames = (n + frame_size - 1) // frame_size
    assert stats.frames == expected_frames
    assert len(slept) == expected_frames
    # Full frames sleep frame_size/sample_rate; the total slept time equals the
    # capture's real duration (all samples / rate).
    assert slept[0] == pytest.approx(frame_size / sample_rate)
    assert sum(slept) == pytest.approx(n / sample_rate)
    assert stats.duration_s == pytest.approx(n / sample_rate)


def test_play_no_realtime_does_not_sleep(storage_client) -> None:
    """With realtime=False, no pacing sleeps happen but all frames still emit."""
    _seed_capture(storage_client, "gnss_l1/B/capture.sigmf-data")
    slept: list[float] = []
    player = CapturePlayer(CaptureReader(storage_client), sleep=slept.append)

    stats = player.play("gnss_l1/B/capture.sigmf-data", frame_size=1024, realtime=False)

    assert slept == []
    assert stats.frames > 0


def test_play_invokes_on_frame_for_every_frame(storage_client) -> None:
    """The on_frame hook (future ZMQ publisher) fires once per frame, in order."""
    _seed_capture(storage_client, "gnss_l1/C/capture.sigmf-data")
    player = CapturePlayer(CaptureReader(storage_client), sleep=lambda _d: None)

    seen: list[int] = []
    stats = player.play(
        "gnss_l1/C/capture.sigmf-data",
        frame_size=2048,
        realtime=False,
        on_frame=lambda i, _frame: seen.append(i),
    )

    assert seen == list(range(stats.frames))


def test_play_raises_without_sample_rate(storage_client) -> None:
    """A capture whose metadata lacks core:sample_rate can't be paced."""
    # Seed a capture with a zero sample rate in its metadata.
    meta = {
        "global": {"core:datatype": "cf32_le", "core:sample_rate": 0, "core:version": "1.2.6"},
        "captures": [{"core:sample_start": 0}],
        "annotations": [],
    }
    storage_client.upload_bytes(
        "bad/capture.sigmf-meta",
        json.dumps(meta).encode("utf-8"),
    )
    storage_client.upload_bytes(
        "bad/capture.sigmf-data",
        np.zeros(16, dtype=np.complex64).tobytes(),
    )
    player = CapturePlayer(CaptureReader(storage_client), sleep=lambda _d: None)

    with pytest.raises(ValueError, match="sample_rate"):
        player.play("bad/capture.sigmf-data")


def test_playback_stats_fields(storage_client) -> None:
    """The returned stats describe the run correctly."""
    n = _seed_capture(storage_client, "gnss_l1/D/capture.sigmf-data")
    player = CapturePlayer(CaptureReader(storage_client), sleep=lambda _d: None)

    stats = player.play("gnss_l1/D/capture.sigmf-data", realtime=False)

    assert isinstance(stats, PlaybackStats)
    assert stats.samples == n
    assert stats.data_key == "gnss_l1/D/capture.sigmf-data"
