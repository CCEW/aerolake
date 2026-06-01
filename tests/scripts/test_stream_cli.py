"""Unit tests for the aerolake-stream CLI.

main() takes an injectable CapturePlayer (moto-backed, no-op sleep) and an
injectable publisher (a fake recording frames), so no real ZeroMQ socket or
MinIO is needed.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from aerolake.common.storage import StorageClient
from aerolake.consumer.player import CapturePlayer
from aerolake.consumer.reader import CaptureReader
from aerolake.scripts.stream import main


def _seed(storage_client: StorageClient, data_key: str, *, signal_type: str = "gnss_l1") -> None:
    meta = {
        "global": {"core:datatype": "cf32_le", "core:sample_rate": 2_000_000.0,
                   "core:version": "1.2.6"},
        "captures": [{"core:sample_start": 0, "core:frequency": 1_575_420_000.0}],
        "annotations": [],
    }
    meta_key = data_key[: -len(".sigmf-data")] + ".sigmf-meta"
    storage_client.upload_bytes(meta_key, json.dumps(meta).encode("utf-8"))
    storage_client.upload_bytes(
        data_key, np.zeros(8192, dtype=np.complex64).tobytes(),
        tags={"signal-type": signal_type, "quality": "raw"},
    )


def _player(storage_client: StorageClient) -> CapturePlayer:
    return CapturePlayer(CaptureReader(storage_client), sleep=lambda _d: None)


class _FakePublisher:
    """Records published frame indices; matches FramePublisher's interface."""

    def __init__(self) -> None:
        self.indices: list[int] = []
        self.closed = False

    def publish(self, index: int, frame: np.ndarray) -> None:
        self.indices.append(index)

    def close(self) -> None:
        self.closed = True


def test_stream_by_key_publishes_all_frames(storage_client, capsys) -> None:
    _seed(storage_client, "gnss_l1/A/capture.sigmf-data")
    pub = _FakePublisher()

    code = main(
        ["--key", "gnss_l1/A/capture.sigmf-data", "--no-realtime", "--frame-size", "2048"],
        player=_player(storage_client),
        publisher=pub,
    )

    assert code == 0
    # 8192 samples / 2048 = 4 frames, indexed 0..3.
    assert pub.indices == [0, 1, 2, 3]
    assert "stream complete" in capsys.readouterr().out.lower()


def test_stream_by_prefix_picks_most_recent(storage_client, capsys) -> None:
    _seed(storage_client, "gnss_l1/2026-05-01/aa/capture.sigmf-data")
    _seed(storage_client, "gnss_l1/2026-05-29/bb/capture.sigmf-data")
    pub = _FakePublisher()

    code = main(
        ["--prefix", "gnss_l1/", "--no-realtime"],
        player=_player(storage_client),
        publisher=pub,
    )

    assert code == 0
    assert "2026-05-29/bb" in capsys.readouterr().out


def test_stream_empty_prefix_returns_zero(storage_client, capsys) -> None:
    code = main(
        ["--prefix", "nothing/"], player=_player(storage_client), publisher=_FakePublisher()
    )
    assert code == 0
    assert "no captures found" in capsys.readouterr().out.lower()


def test_stream_requires_key_or_prefix(storage_client) -> None:
    with pytest.raises(SystemExit):
        main([], player=_player(storage_client), publisher=_FakePublisher())
