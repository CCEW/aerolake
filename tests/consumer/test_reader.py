"""Unit tests for aerolake.consumer.reader.CaptureReader.

Tests cover the three public methods (list_captures, inspect, read)
plus error handling for unsupported datatypes.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from aerolake.common.storage import StorageClient
from aerolake.consumer.reader import CaptureReader


@pytest.fixture
def reader(storage_client: StorageClient) -> CaptureReader:
    """A CaptureReader using the moto-mocked storage_client fixture."""
    return CaptureReader(storage_client=storage_client)


def _upload_synthetic_capture(
    storage_client: StorageClient,
    data_key: str,
    *,
    sample_count: int = 1024,
    sample_rate: float = 2_000_000,
    center_freq: float = 1_575_420_000,
    with_metadata: bool = True,
    with_tags: bool = True,
) -> np.ndarray:
    """Upload a SigMF-shaped pair (data + meta) for testing.

    Returns the original samples so the caller can verify round-trip.
    """
    # Build random complex64 samples (deterministic via seed).
    rng = np.random.default_rng(seed=42)
    samples = (rng.normal(0, 1, sample_count) + 1j * rng.normal(0, 1, sample_count)).astype(
        np.complex64
    )

    # Minimal valid SigMF metadata.
    sigmf_meta = {
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": float(sample_rate),
            "core:version": "1.2.6",
        },
        "captures": [{"core:sample_start": 0, "core:frequency": float(center_freq)}],
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
        samples.tobytes(),
        content_type="application/octet-stream",
        metadata={"sample-rate": str(int(sample_rate))} if with_metadata else None,
        tags={"signal-type": "gnss_l1"} if with_tags else None,
    )
    return samples


# --- list_captures -------------------------------------------------------


def test_list_captures_returns_only_complete_pairs(
    reader: CaptureReader, storage_client: StorageClient
) -> None:
    """A data file without its meta should be filtered out."""
    _upload_synthetic_capture(storage_client, "complete/capture.sigmf-data")

    # Orphan: data only, no meta
    storage_client.upload_bytes("orphan/capture.sigmf-data", b"x")

    captures = reader.list_captures()
    assert "complete/capture.sigmf-data" in captures
    assert "orphan/capture.sigmf-data" not in captures


def test_list_captures_filters_by_prefix(
    reader: CaptureReader, storage_client: StorageClient
) -> None:
    """Only objects under the given prefix should be returned."""
    _upload_synthetic_capture(storage_client, "gnss_l1/A/capture.sigmf-data")
    _upload_synthetic_capture(storage_client, "iridium/B/capture.sigmf-data")

    gnss = reader.list_captures(prefix="gnss_l1/")
    assert gnss == ["gnss_l1/A/capture.sigmf-data"]


def test_list_captures_empty_bucket_returns_empty_list(
    reader: CaptureReader,
) -> None:
    """Listing an empty bucket should return an empty list, not raise."""
    assert reader.list_captures() == []


# --- inspect -------------------------------------------------------------


def test_inspect_returns_metadata_and_tags(
    reader: CaptureReader, storage_client: StorageClient
) -> None:
    """inspect returns the metadata and tags attached at upload time."""
    _upload_synthetic_capture(storage_client, "test/capture.sigmf-data")

    info = reader.inspect("test/capture.sigmf-data")
    assert info.data_key == "test/capture.sigmf-data"
    assert info.metadata.get("sample-rate") == "2000000"
    assert info.tags.get("signal-type") == "gnss_l1"


def test_inspect_returns_empty_dicts_when_none_attached(
    reader: CaptureReader, storage_client: StorageClient
) -> None:
    """A capture uploaded without metadata/tags returns empty dicts."""
    _upload_synthetic_capture(
        storage_client,
        "bare/capture.sigmf-data",
        with_metadata=False,
        with_tags=False,
    )
    info = reader.inspect("bare/capture.sigmf-data")
    assert info.metadata == {}
    assert info.tags == {}


# --- read ----------------------------------------------------------------


def test_read_returns_decoded_samples(reader: CaptureReader, storage_client: StorageClient) -> None:
    """read should return samples byte-identical to the uploaded ones."""
    original_samples = _upload_synthetic_capture(
        storage_client,
        "test/capture.sigmf-data",
        sample_count=512,
    )

    content = reader.read("test/capture.sigmf-data")
    assert content.samples.dtype == np.complex64
    assert content.samples.shape == (512,)
    np.testing.assert_array_equal(content.samples, original_samples)


def test_read_parses_sigmf_metadata(reader: CaptureReader, storage_client: StorageClient) -> None:
    """read should parse the .sigmf-meta JSON correctly."""
    _upload_synthetic_capture(storage_client, "test/capture.sigmf-data")

    content = reader.read("test/capture.sigmf-data")
    assert content.sigmf_meta["global"]["core:datatype"] == "cf32_le"
    assert content.sigmf_meta["global"]["core:sample_rate"] == 2_000_000.0
    assert content.sigmf_meta["captures"][0]["core:frequency"] == 1_575_420_000.0


def test_read_raises_on_unsupported_datatype(
    reader: CaptureReader, storage_client: StorageClient
) -> None:
    """read should raise ValueError if the SigMF datatype is unknown."""
    # Build a fake capture with an unsupported datatype. Captures are stored in
    # their recorded format (ADR-024), so the reader accepts every datatype the
    # lake can hold — this one is a real-valued SigMF type it cannot decode.
    meta = {
        "global": {"core:datatype": "rf32_le", "core:sample_rate": 1000.0, "core:version": "1.2.6"},
        "captures": [{"core:sample_start": 0, "core:frequency": 1000.0}],
        "annotations": [],
    }
    storage_client.upload_bytes(
        "bad/capture.sigmf-meta",
        json.dumps(meta).encode("utf-8"),
    )
    storage_client.upload_bytes("bad/capture.sigmf-data", b"\x01\x02\x03\x04")

    with pytest.raises(ValueError, match="Unsupported SigMF datatype"):
        reader.read("bad/capture.sigmf-data")


def test_read_decodes_ci16_le_stored_in_its_recorded_datatype(
    reader: CaptureReader, storage_client: StorageClient
) -> None:
    """A capture stored as ci16_le decodes to normalised complex64 (ADR-024)."""
    scalars = np.array([32767, -32768, 0, 16384], dtype="<i2")  # 2 complex samples
    meta = {
        "global": {
            "core:datatype": "ci16_le",
            "core:sample_rate": 1000.0,
            "core:version": "1.2.6",
        },
        "captures": [{"core:sample_start": 0, "core:frequency": 1000.0}],
        "annotations": [],
    }
    storage_client.upload_bytes("i16/capture.sigmf-meta", json.dumps(meta).encode("utf-8"))
    storage_client.upload_bytes("i16/capture.sigmf-data", scalars.tobytes())

    content = reader.read("i16/capture.sigmf-data")

    assert content.samples.dtype == np.complex64
    np.testing.assert_allclose(
        content.samples,
        np.array([32767 / 32768 - 1j, 0 + 0.5j], dtype=np.complex64),
        rtol=1e-6,
    )


def test_read_segment_seeks_using_the_stored_datatype_width(
    reader: CaptureReader, storage_client: StorageClient
) -> None:
    """Range maths must use the stored datatype's width, not a fixed 8 bytes."""
    # 100 complex ci16_le samples (4 bytes each): I = n, Q = 0.
    scalars = np.zeros(200, dtype="<i2")
    scalars[0::2] = np.arange(100, dtype="<i2")
    meta = {
        "global": {
            "core:datatype": "ci16_le",
            "core:sample_rate": 100.0,
            "core:version": "1.2.6",
        },
        "captures": [{"core:sample_start": 0, "core:frequency": 1000.0}],
        "annotations": [],
    }
    storage_client.upload_bytes("seek/capture.sigmf-meta", json.dumps(meta).encode("utf-8"))
    storage_client.upload_bytes("seek/capture.sigmf-data", scalars.tobytes())

    # 100 samples at 100 S/s = 1 s total; take the window [0.5 s, 0.7 s).
    content = reader.read_segment("seek/capture.sigmf-data", start_s=0.5, duration_s=0.2)

    assert len(content.samples) == 20
    # First sample of the window is source sample 50 -> I = 50/32768.
    assert content.samples[0].real == pytest.approx(50 / 32768.0, rel=1e-6)
