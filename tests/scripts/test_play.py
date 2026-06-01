"""Unit tests for the aerolake-play CLI.

main() takes an injectable CapturePlayer (wired to a moto-backed reader, with a
no-op sleep) so playback is instant and needs no real MinIO.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from aerolake.common.storage import StorageClient
from aerolake.consumer.player import CapturePlayer
from aerolake.consumer.reader import CaptureReader
from aerolake.scripts.play import main


def _seed(storage_client: StorageClient, data_key: str) -> None:
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
        tags={"signal-type": "gnss_l1", "quality": "raw"},
    )


def _player(storage_client: StorageClient) -> CapturePlayer:
    # No-op sleep so the test never actually waits in real time.
    return CapturePlayer(CaptureReader(storage_client), sleep=lambda _d: None)


def test_play_by_key(storage_client, capsys) -> None:
    _seed(storage_client, "gnss_l1/A/capture.sigmf-data")

    code = main(
        ["--key", "gnss_l1/A/capture.sigmf-data", "--no-realtime"],
        player=_player(storage_client),
    )

    assert code == 0
    assert "playback complete" in capsys.readouterr().out.lower()


def test_play_by_prefix_picks_most_recent(storage_client, capsys) -> None:
    _seed(storage_client, "gnss_l1/2026-05-01/aa/capture.sigmf-data")
    _seed(storage_client, "gnss_l1/2026-05-29/bb/capture.sigmf-data")

    code = main(
        ["--prefix", "gnss_l1/", "--no-realtime"], player=_player(storage_client)
    )

    assert code == 0
    # The most recent (latest-sorted) key should be the one played.
    assert "2026-05-29/bb" in capsys.readouterr().out


def test_play_empty_prefix_returns_zero(storage_client, capsys) -> None:
    code = main(["--prefix", "nothing/"], player=_player(storage_client))
    assert code == 0
    assert "no captures found" in capsys.readouterr().out.lower()


def test_requires_key_or_prefix(storage_client) -> None:
    # The mutually-exclusive group is required → argparse exits (code 2).
    with pytest.raises(SystemExit):
        main([], player=_player(storage_client))
