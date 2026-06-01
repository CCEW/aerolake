"""Software cadence playback for AeroLake captures (ADR-007, layer 1).

This is the *software* half of "playback": it reads a stored capture and emits
its IQ samples in fixed-size **frames, paced at the recorded sample rate** —
i.e. a 2 MS/s capture is replayed so that frames come out at the same speed they
were recorded. It does NOT touch any radio; transmitting over the air (ADR-007
layer 3) needs a TX-capable SDR (the BladeRF — the RTL-SDR is receive-only).

Why this matters (ADR-004): the consumer must *respect the temporal cadence* of
the data. This player is that mechanism, and the natural seed for the deferred
ZeroMQ pub/sub stream (ADR-002) — a future network publisher would simply send
each frame this player yields.

Design for testability
----------------------
Real-time pacing normally means calling ``time.sleep``, which would make tests
slow and flaky. So the **clock is injected**: ``CapturePlayer`` takes a
``sleep`` callable (defaulting to ``time.sleep``). Tests pass a fake that just
records the requested durations, so we can assert the pacing maths without any
real waiting. The frame-splitting itself is a separate **pure function**
(:func:`iter_frames`) that needs no clock at all.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import numpy as np
import structlog

from aerolake.consumer.reader import CaptureReader

logger = structlog.get_logger(__name__)


def iter_frames(samples: np.ndarray, frame_size: int) -> Iterator[np.ndarray]:
    """Yield consecutive, non-overlapping frames of ``frame_size`` samples.

    The final frame may be shorter than ``frame_size`` if the capture length
    isn't an exact multiple. Pure function — no timing, no I/O — so it's trivial
    to unit-test the frame boundaries.

    Parameters
    ----------
    samples
        1-D array of IQ samples.
    frame_size
        Number of samples per frame (must be > 0).
    """
    if frame_size <= 0:
        raise ValueError(f"frame_size must be > 0, got {frame_size}")
    for start in range(0, len(samples), frame_size):
        yield samples[start : start + frame_size]


@dataclass(frozen=True)
class PlaybackStats:
    """Summary of one playback run.

    Attributes
    ----------
    data_key
        Key of the capture that was played.
    frames
        Number of frames emitted.
    samples
        Total samples emitted (== the capture's sample count).
    sample_rate
        Sample rate used for pacing, in Hz.
    duration_s
        Wall-clock duration the capture represents (samples / sample_rate) —
        i.e. how long a real-time playback should take.
    """

    data_key: str
    frames: int
    samples: int
    sample_rate: float
    duration_s: float


class CapturePlayer:
    """Replays a stored capture's samples frame-by-frame at its recorded rate."""

    def __init__(
        self,
        reader: CaptureReader | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # Reuse the same reader the rest of the consumer uses (so playback goes
        # through the single storage chokepoint). Injectable for tests.
        self._reader = reader or CaptureReader()
        # The pacing clock. Default real sleep; tests inject a fake.
        self._sleep = sleep

    @property
    def reader(self) -> CaptureReader:
        """The CaptureReader this player reads through (e.g. to list captures)."""
        return self._reader

    def play(
        self,
        data_key: str,
        *,
        frame_size: int = 4096,
        realtime: bool = True,
        on_frame: Callable[[int, np.ndarray], None] | None = None,
    ) -> PlaybackStats:
        """Read a capture and emit it frame-by-frame, paced at its sample rate.

        For each frame we (optionally) call ``on_frame(index, frame)`` and then,
        in real-time mode, sleep for the frame's own duration
        (``len(frame) / sample_rate`` seconds) before moving on — so the stream
        leaves this player at the same cadence it was recorded at.

        Parameters
        ----------
        data_key
            Key of the ``.sigmf-data`` capture to play.
        frame_size
            Samples per frame. Smaller = smoother cadence but more overhead.
        realtime
            If True (default), pace the output at the recorded rate. If False,
            emit every frame back-to-back with no waiting (useful for quickly
            draining a capture, e.g. feeding a file as fast as possible).
        on_frame
            Optional callback invoked with ``(frame_index, frame_samples)`` for
            each frame — the hook a future ZeroMQ publisher would plug into.

        Returns
        -------
        PlaybackStats

        Raises
        ------
        ValueError
            If the SigMF metadata has no usable ``core:sample_rate`` (we can't
            pace without knowing the rate).
        """
        log = logger.bind(data_key=data_key, frame_size=frame_size)

        # Read + decode the capture through the normal reader path.
        content = self._reader.read(data_key)
        samples = content.samples

        sample_rate = float(
            content.sigmf_meta.get("global", {}).get("core:sample_rate", 0.0)
        )
        if sample_rate <= 0:
            raise ValueError(
                "Cannot pace playback: missing/invalid core:sample_rate in "
                f"the SigMF metadata of {data_key!r}"
            )

        log.info(
            "player.play.start",
            sample_count=len(samples),
            sample_rate=sample_rate,
            realtime=realtime,
        )

        n_frames = 0
        for index, frame in enumerate(iter_frames(samples, frame_size)):
            if on_frame is not None:
                on_frame(index, frame)
            if realtime:
                # Sleep for exactly this frame's worth of time, so the inter-
                # frame cadence matches the original recording. The last
                # (possibly short) frame sleeps proportionally less.
                self._sleep(len(frame) / sample_rate)
            n_frames += 1

        stats = PlaybackStats(
            data_key=data_key,
            frames=n_frames,
            samples=len(samples),
            sample_rate=sample_rate,
            duration_s=len(samples) / sample_rate,
        )
        log.info(
            "player.play.done",
            frames=stats.frames,
            duration_s=stats.duration_s,
        )
        return stats
