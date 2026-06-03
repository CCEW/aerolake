"""Unit tests for the aerolake-subscribe CLI.

main() takes an injectable subscriber (a fake yielding canned frames), so no
real ZeroMQ socket or publisher is needed.
"""

from __future__ import annotations

import numpy as np
import pytest

from aerolake.scripts.subscribe import _rms_dbfs, main


class _FakeSubscriber:
    """Yields a fixed list of frames, then blocks forever (StopIteration→raise).

    Matches FrameSubscriber's interface (recv/close). Each recv() returns the
    next (topic, header, samples) triple.
    """

    def __init__(self, frames: list[tuple[str, dict, np.ndarray]]) -> None:
        self._frames = iter(frames)
        self.closed = False

    def recv(self):
        return next(self._frames)  # raises StopIteration when exhausted

    def close(self) -> None:
        self.closed = True


def _frame(index: int, n: int = 4096) -> tuple[str, dict, np.ndarray]:
    samples = np.full(n, 0.5 + 0.5j, dtype=np.complex64)
    return ("gnss_l1", {"index": index, "n": n, "dtype": "complex64"}, samples)


def test_rms_dbfs_of_silence_is_minus_inf() -> None:
    assert _rms_dbfs(np.zeros(10, dtype=np.complex64)) == float("-inf")
    assert _rms_dbfs(np.array([], dtype=np.complex64)) == float("-inf")


def test_rms_dbfs_of_known_amplitude() -> None:
    # |0.5+0.5j|^2 = 0.5 → 10*log10(0.5) ≈ -3.01 dBFS.
    val = _rms_dbfs(np.full(100, 0.5 + 0.5j, dtype=np.complex64))
    assert val == pytest.approx(-3.0103, abs=1e-3)


def test_subscribe_prints_n_frames_then_summarises(capsys) -> None:
    sub = _FakeSubscriber([_frame(0), _frame(1), _frame(2)])

    code = main(["--frames", "3"], subscriber=sub)

    assert code == 0
    out = capsys.readouterr().out.lower()
    assert "frames received" in out
    assert "3" in out
    # We did NOT inject-and-own the socket → main must not close it.
    assert sub.closed is False


def test_subscribe_stops_at_frame_count(capsys) -> None:
    # Provide more frames than requested; only --frames should be consumed.
    sub = _FakeSubscriber([_frame(i) for i in range(10)])

    code = main(["--frames", "2"], subscriber=sub)

    assert code == 0
    out = capsys.readouterr().out
    assert "#   0" in out and "#   1" in out
    assert "#   2" not in out  # stopped after 2


def test_subscribe_handles_recv_error(capsys) -> None:
    class _Boom:
        def recv(self):
            raise RuntimeError("socket exploded")

        def close(self) -> None:
            pass

    code = main(["--frames", "1"], subscriber=_Boom())
    assert code == 2
    assert "receive error" in capsys.readouterr().out.lower()
