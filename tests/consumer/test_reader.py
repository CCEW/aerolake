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
from aerolake.producer.synthetic import generate_tone
from aerolake.quality.checker import QualityChecker, QualityThresholds

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
    samples = (
        rng.normal(0, 1, sample_count)
        + 1j * rng.normal(0, 1, sample_count)
    ).astype(np.complex64)

    # Minimal valid SigMF metadata.
    sigmf_meta = {
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": float(sample_rate),
            "core:version": "1.2.6",
        },
        "captures": [
            {"core:sample_start": 0, "core:frequency": float(center_freq)}
        ],
        "annotations": [],
    }

    meta_key = data_key[: -len(".sigmf-data")] + ".sigmf-meta"
    storage_client.upload_bytes(
        meta_key, json.dumps(sigmf_meta).encode("utf-8"),
        content_type="application/json",
    )
    storage_client.upload_bytes(
        data_key, samples.tobytes(),
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

def test_read_returns_decoded_samples(
    reader: CaptureReader, storage_client: StorageClient
) -> None:
    """read should return samples byte-identical to the uploaded ones."""
    original_samples = _upload_synthetic_capture(
        storage_client, "test/capture.sigmf-data", sample_count=512,
    )

    content = reader.read("test/capture.sigmf-data")
    assert content.samples.dtype == np.complex64
    assert content.samples.shape == (512,)
    np.testing.assert_array_equal(content.samples, original_samples)


def test_read_parses_sigmf_metadata(
    reader: CaptureReader, storage_client: StorageClient
) -> None:
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
    # Build a fake capture with an unsupported datatype.
    meta = {
        "global": {"core:datatype": "ci8_le", "core:sample_rate": 1000.0,
                   "core:version": "1.2.6"},
        "captures": [{"core:sample_start": 0, "core:frequency": 1000.0}],
        "annotations": [],
    }
    storage_client.upload_bytes(
        "bad/capture.sigmf-meta", json.dumps(meta).encode("utf-8"),
    )
    storage_client.upload_bytes("bad/capture.sigmf-data", b"\x01\x02\x03\x04")

    with pytest.raises(ValueError, match="Unsupported SigMF datatype"):
        reader.read("bad/capture.sigmf-data")

def _upload_clean_capture(
    storage_client: StorageClient,
    data_key: str,
    *,
    duration_s: float = 0.001,
    sample_rate: float = 2_000_000,
    tone_amplitude: float = 0.1,
    quality: str = "raw",
) -> np.ndarray:
    """Upload a clean tone capture (good quality) tagged quality=<quality>.

    Unlike _upload_synthetic_capture (which generates pure Gaussian noise at
    ~full-scale RMS and would fail quality checks), this helper uses
    generate_tone at a realistic -20 dBFS level, so the capture passes the
    default quality thresholds. Pass tone_amplitude=1.5 to force a saturated,
    rejectable capture.
    """
    # Generate a clean tone via the real producer code (seed -> deterministic).
    signal = generate_tone(
        duration_s=duration_s,
        sample_rate=sample_rate,
        center_freq=1_575_420_000,
        tone_amplitude=tone_amplitude,
        snr_db=20.0,
        seed=42,
    )

    # Minimal valid SigMF metadata, matching the data we just generated.
    sigmf_meta = {
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": float(sample_rate),
            "core:version": "1.2.6",
        },
        "captures": [
            {"core:sample_start": 0, "core:frequency": 1_575_420_000.0}
        ],
        "annotations": [],
    }

    # Upload the .sigmf-meta sidecar.
    meta_key = data_key[: -len(".sigmf-data")] + ".sigmf-meta"
    storage_client.upload_bytes(
        meta_key,
        json.dumps(sigmf_meta).encode("utf-8"),
        content_type="application/json",
    )
    # Upload the .sigmf-data with an initial quality tag (default "raw"),
    # so we can verify the promotion to validated/rejected afterwards.
    storage_client.upload_bytes(
        data_key,
        signal.samples.tobytes(),
        content_type="application/octet-stream",
        tags={"signal-type": "gnss_l1", "quality": quality},
    )
    return signal.samples


# --- validate ------------------------------------------------------------

def test_validate_clean_capture_passes_and_promotes(
    reader: CaptureReader, storage_client: StorageClient
) -> None:
    """A clean capture should pass and have its quality tag promoted."""
    _upload_clean_capture(storage_client, "gnss_l1/X/capture.sigmf-data")

    report = reader.validate(
        "gnss_l1/X/capture.sigmf-data", expected_duration_s=0.001
    )

    # Verdict is positive, no failures.
    assert report.is_valid is True
    assert report.failed_checks == []

    # The quality tag was promoted raw -> validated...
    tags = storage_client.get_object_tags("gnss_l1/X/capture.sigmf-data")
    assert tags["quality"] == "validated"
    # ...and the other tags were preserved (read -> merge -> write worked).
    assert tags["signal-type"] == "gnss_l1"


def test_validate_stores_quality_report(
    reader: CaptureReader, storage_client: StorageClient
) -> None:
    """validate should write a quality_report.json next to the capture."""
    _upload_clean_capture(storage_client, "gnss_l1/Y/capture.sigmf-data")

    reader.validate("gnss_l1/Y/capture.sigmf-data", expected_duration_s=0.001)

    # The report artifact must exist and be valid JSON with the verdict.
    raw = storage_client.download_bytes("gnss_l1/Y/quality_report.json")
    parsed = json.loads(raw)
    assert parsed["is_valid"] is True
    assert parsed["clipping_ratio"] == 0.0


def test_validate_rejects_saturated_capture(
    reader: CaptureReader, storage_client: StorageClient
) -> None:
    """A saturated capture should fail and be tagged quality=rejected."""
    # tone_amplitude=1.5 -> every sample clips -> fails clipping + too loud.
    _upload_clean_capture(
        storage_client, "gnss_l1/Z/capture.sigmf-data", tone_amplitude=1.5
    )

    report = reader.validate(
        "gnss_l1/Z/capture.sigmf-data", expected_duration_s=0.001
    )

    assert report.is_valid is False
    tags = storage_client.get_object_tags("gnss_l1/Z/capture.sigmf-data")
    assert tags["quality"] == "rejected"


def test_validate_can_skip_report_storage(
    reader: CaptureReader, storage_client: StorageClient
) -> None:
    """With store_report=False, no quality_report.json should be written."""
    _upload_clean_capture(storage_client, "gnss_l1/W/capture.sigmf-data")

    reader.validate(
        "gnss_l1/W/capture.sigmf-data",
        expected_duration_s=0.001,
        store_report=False,
    )

    # The report must NOT exist.
    assert not storage_client.object_exists("gnss_l1/W/quality_report.json")


def test_validate_accepts_custom_checker(
    reader: CaptureReader, storage_client: StorageClient
) -> None:
    """An injected permissive checker can accept an otherwise-bad capture.

    This proves dependency injection works: the same saturated capture that
    would normally be rejected passes when we relax the thresholds.
    """
    _upload_clean_capture(
        storage_client, "gnss_l1/V/capture.sigmf-data", tone_amplitude=1.5
    )

    # Ultra-permissive thresholds: tolerate full clipping and loud signals.
    permissive = QualityChecker(
        QualityThresholds(max_clipping_ratio=1.0, max_rms_dbfs=50.0)
    )

    report = reader.validate(
        "gnss_l1/V/capture.sigmf-data",
        expected_duration_s=0.001,
        checker=permissive,
    )

    assert report.is_valid is True
    tags = storage_client.get_object_tags("gnss_l1/V/capture.sigmf-data")
    assert tags["quality"] == "validated"
