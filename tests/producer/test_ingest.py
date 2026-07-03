"""Tests for ingesting existing IQ files (producer.ingest + the CLI).

We write a small raw file to a tmp path, ingest it into moto-backed storage,
then read it back through the consumer to prove the round trip and the cf32
conversion.
"""

from __future__ import annotations

import hashlib
import json
import os

import numpy as np
import pytest

from aerolake.common.storage import StorageClient
from aerolake.consumer.reader import CaptureReader
from aerolake.producer.ingest import ingest_file, ingest_files
from aerolake.scripts.ingest import _resolve_files, main


def test_ingest_cf32_file_roundtrips(storage_client: StorageClient, tmp_path) -> None:
    # A cf32 file (what GNU Radio's File Sink writes).
    samples = (np.arange(1000) + 1j * np.arange(1000)).astype(np.complex64)
    path = tmp_path / "capture.sigmf-data"
    path.write_bytes(samples.tobytes())

    result = ingest_file(
        file_path=str(path),
        signal_type="gnss_l1",
        sample_rate=2_000_000,
        center_freq=1_575_420_000,
        hardware="bladerf",
        storage_client=storage_client,
    )

    assert result.sample_count == 1000
    # Read it back through the normal consumer path.
    reader = CaptureReader(storage_client)
    content = reader.read(result.data_key)
    np.testing.assert_array_equal(content.samples, samples)
    # Metadata + tags landed correctly.
    info = reader.inspect(result.data_key)
    assert info.tags["signal-type"] == "gnss_l1"
    assert info.tags["hardware"] == "bladerf"
    assert info.metadata["sample-rate"] == "2000000"
    g = content.sigmf_meta["global"]
    assert g["core:datatype"] == "cf32_le"
    assert g["core:num_channels"] == 1
    assert g["core:offset"] == 0
    assert g["core:sha512"] == hashlib.sha512(samples.tobytes()).hexdigest()


def test_ingest_cu8_is_converted_to_normalised_cf32(
    storage_client: StorageClient, tmp_path
) -> None:
    # RTL-SDR style: unsigned 8-bit interleaved I,Q. 255 -> ~+1, 0 -> ~-1.
    raw = np.array([255, 0, 127, 128], dtype=np.uint8)  # one I=+1,Q=-1 ; one ~0
    path = tmp_path / "dump.iq"
    path.write_bytes(raw.tobytes())

    result = ingest_file(
        file_path=str(path),
        signal_type="iridium",
        sample_rate=2_000_000,
        center_freq=1_626_000_000,
        datatype="cu8",
        hardware="rtlsdr",
        storage_client=storage_client,
    )

    assert result.sample_count == 2  # 4 uint8 = 2 complex samples
    content = CaptureReader(storage_client).read(result.data_key)
    assert content.samples.dtype == np.complex64
    # First sample: I=(255-127.5)/127.5=+1.0, Q=(0-127.5)/127.5=-1.0
    assert content.samples[0].real == 1.0
    assert content.samples[0].imag == -1.0
    # Second sample is near zero.
    assert abs(content.samples[1]) < 0.02


def test_ingest_meta_uploaded_before_data_is_complete(
    storage_client: StorageClient, tmp_path
) -> None:
    """Both objects must exist and form a complete capture."""
    path = tmp_path / "capture.sigmf-data"
    path.write_bytes(np.zeros(16, dtype=np.complex64).tobytes())

    result = ingest_file(
        file_path=str(path),
        signal_type="gnss_l1",
        sample_rate=2_000_000,
        center_freq=1_575_420_000,
        storage_client=storage_client,
    )

    assert storage_client.object_exists(result.meta_key)
    assert storage_client.object_exists(result.data_key)
    # The meta is valid JSON with the expected fields.
    meta = json.loads(storage_client.download_bytes(result.meta_key))
    assert meta["captures"][0]["core:frequency"] == 1_575_420_000.0


# --- CLI -----------------------------------------------------------------


def test_ingest_cli_happy_path(storage_client: StorageClient, tmp_path, capsys) -> None:
    path = tmp_path / "capture.sigmf-data"
    path.write_bytes(np.zeros(100, dtype=np.complex64).tobytes())

    code = main(
        [
            str(path),
            "--signal-type",
            "gnss_l1",
            "--sample-rate",
            "2e6",
            "--center-freq",
            "1575.42e6",
            "--hardware",
            "bladerf",
        ],
        storage_client=storage_client,
    )

    assert code == 0
    assert "ingested" in capsys.readouterr().out.lower()


def test_ingest_cli_missing_file_returns_two(storage_client: StorageClient) -> None:
    code = main(
        [
            "/nope/does-not-exist.iq",
            "--signal-type",
            "x",
            "--sample-rate",
            "2e6",
            "--center-freq",
            "1e9",
        ],
        storage_client=storage_client,
    )
    assert code == 2


# --- cs32 (RFSoC int32) + multi-file ingestion ---------------------------


def test_ingest_cs32_is_normalised(storage_client: StorageClient, tmp_path) -> None:
    # int32 interleaved I,Q -> normalised by 2**31.
    raw = np.array([2**30, -(2**31), 0, 2**31 - 1], dtype="<i4")  # 2 complex
    path = tmp_path / "rfsoc.bin"
    path.write_bytes(raw.tobytes())

    result = ingest_file(
        file_path=str(path),
        signal_type="iridium",
        sample_rate=400e3,
        center_freq=1626.271e6,
        datatype="cs32",
        hardware="rfsoc",
        storage_client=storage_client,
    )

    assert result.sample_count == 2
    content = CaptureReader(storage_client).read(result.data_key)
    assert content.samples[0].real == pytest.approx(0.5)  # 2**30 / 2**31
    assert content.samples[0].imag == pytest.approx(-1.0)  # -2**31 / 2**31


def test_ingest_files_concatenates_in_order(storage_client: StorageClient, tmp_path) -> None:
    a = np.arange(50, dtype=np.complex64)
    b = np.arange(50, 90, dtype=np.complex64)
    (tmp_path / "a.bin").write_bytes(a.tobytes())
    (tmp_path / "b.bin").write_bytes(b.tobytes())

    result = ingest_files(
        file_paths=[str(tmp_path / "a.bin"), str(tmp_path / "b.bin")],
        signal_type="iridium",
        sample_rate=400e3,
        center_freq=1626e6,
        storage_client=storage_client,
    )

    content = CaptureReader(storage_client).read(result.data_key)
    np.testing.assert_array_equal(content.samples, np.concatenate([a, b]))


def test_resolve_files_sorts_packets_numerically(tmp_path) -> None:
    for n in (1, 2, 10):  # lexical sort would wrongly put pkt_10 before pkt_2
        (tmp_path / f"pkt_{n}.bin").write_bytes(b"x" * 8)
    files = _resolve_files(str(tmp_path), "pkt_*.bin")
    assert [os.path.basename(f) for f in files] == ["pkt_1.bin", "pkt_2.bin", "pkt_10.bin"]


def test_ingest_cli_directory_of_packets(storage_client: StorageClient, tmp_path, capsys) -> None:
    for n in (1, 2, 3):
        (tmp_path / f"pkt_{n}.bin").write_bytes(np.zeros(10, dtype=np.complex64).tobytes())
    code = main(
        [
            str(tmp_path),
            "--glob",
            "pkt_*.bin",
            "--signal-type",
            "iridium",
            "--sample-rate",
            "400e3",
            "--center-freq",
            "1626.271e6",
            "--datatype",
            "cf32",
            "--hardware",
            "rfsoc",
        ],
        storage_client=storage_client,
    )
    assert code == 0
    assert "ingested" in capsys.readouterr().out.lower()
