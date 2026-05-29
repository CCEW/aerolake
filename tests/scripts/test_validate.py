"""Unit tests for the aerolake-validate batch curation CLI.

We invoke main() directly with an injected, moto-backed CaptureReader (the
``reader=`` seam) so the tests are fast and need no real MinIO. Captures are
seeded with the real producer code (generate_tone) so the quality verdicts
are genuine, not faked.
"""

from __future__ import annotations

import json

from aerolake.common.storage import StorageClient
from aerolake.consumer.reader import CaptureReader
from aerolake.producer.synthetic import generate_tone
from aerolake.scripts.validate import main


def _upload_capture(
    storage_client: StorageClient,
    data_key: str,
    *,
    sample_rate: float = 2_000_000,
    duration_s: float = 0.001,
    tone_amplitude: float = 0.1,
    quality: str = "raw",
) -> None:
    """Seed one capture (data + meta) tagged quality=<quality>.

    tone_amplitude=0.1 (-20 dBFS) passes the default thresholds; pass
    tone_amplitude=1.5 to force a saturated, rejectable capture.
    """
    signal = generate_tone(
        duration_s=duration_s,
        sample_rate=sample_rate,
        center_freq=1_575_420_000,
        tone_amplitude=tone_amplitude,
        snr_db=20.0,
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
        meta_key, json.dumps(sigmf_meta).encode("utf-8"),
        content_type="application/json",
    )
    storage_client.upload_bytes(
        data_key, signal.samples.tobytes(),
        content_type="application/octet-stream",
        tags={"signal-type": "gnss_l1", "quality": quality},
    )


# --- Happy path ----------------------------------------------------------

def test_validate_promotes_all_clean_captures(storage_client, capsys) -> None:
    """Every clean capture under the prefix is promoted to validated."""
    _upload_capture(storage_client, "gnss_l1/A/capture.sigmf-data")
    _upload_capture(storage_client, "gnss_l1/B/capture.sigmf-data")
    reader = CaptureReader(storage_client)

    exit_code = main(
        ["--prefix", "gnss_l1/", "--expected-duration", "0.001"], reader=reader
    )

    assert exit_code == 0
    assert storage_client.get_object_tags(
        "gnss_l1/A/capture.sigmf-data"
    )["quality"] == "validated"
    assert storage_client.get_object_tags(
        "gnss_l1/B/capture.sigmf-data"
    )["quality"] == "validated"


def test_validate_mixed_batch_reports_counts(storage_client, capsys) -> None:
    """A mix of clean and saturated captures yields the right verdicts/counts."""
    _upload_capture(storage_client, "gnss_l1/good/capture.sigmf-data")
    _upload_capture(
        storage_client, "gnss_l1/bad/capture.sigmf-data", tone_amplitude=1.5
    )
    reader = CaptureReader(storage_client)

    exit_code = main(["--prefix", "gnss_l1/", "--json"], reader=reader)

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out.strip())
    assert report["total"] == 2
    assert report["validated"] == 1
    assert report["rejected"] == 1
    assert report["errors"] == 0
    # And the tags reflect the verdicts.
    assert storage_client.get_object_tags(
        "gnss_l1/bad/capture.sigmf-data"
    )["quality"] == "rejected"


# --- Dry run -------------------------------------------------------------

def test_dry_run_does_not_promote_or_store(storage_client, capsys) -> None:
    """--dry-run computes verdicts but mutates nothing in the bucket."""
    _upload_capture(storage_client, "gnss_l1/C/capture.sigmf-data", quality="raw")
    reader = CaptureReader(storage_client)

    exit_code = main(
        ["--prefix", "gnss_l1/", "--expected-duration", "0.001", "--dry-run"],
        reader=reader,
    )

    assert exit_code == 0
    # Tag still 'raw' — not promoted.
    assert storage_client.get_object_tags(
        "gnss_l1/C/capture.sigmf-data"
    )["quality"] == "raw"
    # No report artifact written.
    assert not storage_client.object_exists("gnss_l1/C/quality_report.json")


# --- Edge cases ----------------------------------------------------------

def test_empty_prefix_returns_zero(storage_client, capsys) -> None:
    """No captures under the prefix is a clean no-op, not an error."""
    reader = CaptureReader(storage_client)

    exit_code = main(["--prefix", "nothing/"], reader=reader)

    assert exit_code == 0
    assert "no complete captures" in capsys.readouterr().out.lower()


def test_unsupported_datatype_is_recorded_not_fatal(storage_client, capsys) -> None:
    """A capture with a bad datatype is reported as an error but doesn't abort."""
    # One good capture...
    _upload_capture(storage_client, "gnss_l1/ok/capture.sigmf-data")
    # ...and one with an unsupported SigMF datatype.
    bad_meta = {
        "global": {"core:datatype": "ci8_le", "core:sample_rate": 1000.0,
                   "core:version": "1.2.6"},
        "captures": [{"core:sample_start": 0, "core:frequency": 1000.0}],
        "annotations": [],
    }
    storage_client.upload_bytes(
        "gnss_l1/broken/capture.sigmf-meta", json.dumps(bad_meta).encode("utf-8"),
    )
    storage_client.upload_bytes(
        "gnss_l1/broken/capture.sigmf-data", b"\x01\x02\x03\x04",
        tags={"signal-type": "gnss_l1", "quality": "raw"},
    )
    reader = CaptureReader(storage_client)

    exit_code = main(["--prefix", "gnss_l1/", "--json"], reader=reader)

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out.strip())
    assert report["total"] == 2
    assert report["validated"] == 1
    assert report["errors"] == 1
