"""Tests for the aerolake-fetch CLI (the MinIO → local file bridge, ADR-012).

We ingest a small capture into moto-backed storage, then fetch it back to a tmp
file and prove: (a) the bytes round-trip exactly (raw cf32_le), (b) a windowed
fetch only writes that window, (c) a .sigmf-meta sidecar is written, and (d) the
documented exit codes hold.
"""

from __future__ import annotations

import json

import numpy as np

from aerolake.common.storage import StorageClient
from aerolake.consumer.reader import CaptureReader
from aerolake.producer.ingest import ingest_file
from aerolake.scripts.fetch import _meta_path, main


def _ingest_ramp(storage_client: StorageClient, tmp_path, n: int = 1000):
    """Ingest n cf32 samples (a ramp) and return (data_key, samples)."""
    samples = (np.arange(n) + 1j * np.arange(n)).astype(np.complex64)
    src = tmp_path / "src.sigmf-data"
    src.write_bytes(samples.tobytes())
    result = ingest_file(
        file_path=str(src),
        signal_type="iridium",
        sample_rate=1000.0,
        center_freq=1_626_271_000.0,
        hardware="rfsoc",
        storage_client=storage_client,
    )
    return result.data_key, samples


def test_meta_path_derivation() -> None:
    assert _meta_path("/tmp/capture.sigmf-data") == "/tmp/capture.sigmf-meta"
    assert _meta_path("/tmp/raw.iq") == "/tmp/raw.iq.sigmf-meta"


def test_fetch_whole_capture_roundtrips(storage_client, tmp_path, capsys) -> None:
    data_key, samples = _ingest_ramp(storage_client, tmp_path)
    out = tmp_path / "out.sigmf-data"

    code = main(
        ["--key", data_key, "--out", str(out)],
        reader=CaptureReader(storage_client),
    )

    assert code == 0
    # The raw bytes must match exactly (cf32_le, GNU Radio "complex").
    written = np.frombuffer(out.read_bytes(), dtype=np.complex64)
    np.testing.assert_array_equal(written, samples)
    # A .sigmf-meta sidecar was written and is valid JSON with the params.
    meta = json.loads((tmp_path / "out.sigmf-meta").read_text())
    assert meta["global"]["core:sample_rate"] == 1000.0
    assert "fetched" in capsys.readouterr().out.lower()


def test_fetch_window_writes_only_that_window(storage_client, tmp_path) -> None:
    data_key, samples = _ingest_ramp(storage_client, tmp_path)
    out = tmp_path / "win.sigmf-data"

    # sample_rate=1000 → start 0.1s = sample 100, duration 0.2s = 200 samples.
    code = main(
        ["--key", data_key, "--out", str(out), "--start", "0.1", "--duration", "0.2"],
        reader=CaptureReader(storage_client),
    )

    assert code == 0
    written = np.frombuffer(out.read_bytes(), dtype=np.complex64)
    assert len(written) == 200
    np.testing.assert_array_equal(written, samples[100:300])


def test_fetch_prefix_picks_a_capture(storage_client, tmp_path) -> None:
    _data_key, samples = _ingest_ramp(storage_client, tmp_path)
    out = tmp_path / "byprefix.sigmf-data"

    code = main(
        ["--prefix", "iridium/", "--out", str(out)],
        reader=CaptureReader(storage_client),
    )

    assert code == 0
    assert len(np.frombuffer(out.read_bytes(), dtype=np.complex64)) == len(samples)


def test_fetch_empty_prefix_returns_zero(storage_client, tmp_path, capsys) -> None:
    code = main(
        ["--prefix", "nothing-here/", "--out", str(tmp_path / "x.sigmf-data")],
        reader=CaptureReader(storage_client),
    )
    assert code == 0
    assert "no captures" in capsys.readouterr().out.lower()
