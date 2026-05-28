"""Unit tests for aerolake.quality.checker.

We test the orchestrator's behavior: given a capture and thresholds,
does it produce the right verdict?

The individual metric calculations are tested in test_metrics.py — here
we only verify that the checker correctly wires metrics to thresholds
and accumulates failures.
"""

from __future__ import annotations

import numpy as np
import pytest

from aerolake.quality.checker import (
    QualityChecker,
    QualityReport,
    QualityThresholds,
)


# ===========================================================================
# Helpers
# ===========================================================================

def _good_meta(sample_rate: float = 2_000_000.0) -> dict:
    """A SigMF metadata dict that should pass validation."""
    return {
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": sample_rate,
            "core:version": "1.2.6",
        },
        "captures": [{"core:sample_start": 0, "core:frequency": 1.5e9}],
        "annotations": [],
    }


def _clean_signal(amplitude: float = 0.1, n: int = 2_000_000) -> np.ndarray:
    """A clean rotating-phase signal — should pass all default thresholds."""
    phases = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return (amplitude * np.exp(1j * phases)).astype(np.complex64)


# ===========================================================================
# Happy path — clean capture passes default thresholds
# ===========================================================================

def test_clean_capture_passes_all_checks() -> None:
    """A clean, well-formed capture should produce is_valid=True."""
    samples = _clean_signal()  # amplitude 0.1 -> -20 dBFS, no clipping
    report = QualityChecker().check(samples, _good_meta(), expected_duration_s=1.0)
    assert report.is_valid is True
    assert report.failed_checks == []


def test_clean_capture_returns_expected_metrics() -> None:
    """The numeric metrics on a clean capture should match expectations."""
    samples = _clean_signal()
    report = QualityChecker().check(samples, _good_meta(), expected_duration_s=1.0)
    # Spot-check a few values
    assert report.clipping_ratio == 0.0
    assert -20.5 < report.rms_power_dbfs < -19.5  # ~-20 dBFS
    assert report.invalid_count == 0
    assert report.sample_completeness == 1.0
    assert report.metadata_valid is True


# ===========================================================================
# Individual failure paths — each threshold check should trigger separately
# ===========================================================================

def test_failed_check_when_signal_clipped() -> None:
    """A saturated signal should fail the clipping check."""
    samples = _clean_signal(amplitude=1.5)  # full clipping
    report = QualityChecker().check(samples, _good_meta(), expected_duration_s=1.0)
    assert report.is_valid is False
    # The clipping failure should be mentioned in the human-readable list.
    assert any("clipping" in check.lower() for check in report.failed_checks)


def test_failed_check_when_signal_too_weak() -> None:
    """A near-zero signal should fail the 'too weak' check."""
    samples = _clean_signal(amplitude=1e-6)  # ~-120 dBFS
    report = QualityChecker().check(samples, _good_meta(), expected_duration_s=1.0)
    assert report.is_valid is False
    assert any("weak" in check.lower() for check in report.failed_checks)


def test_failed_check_when_invalid_samples_present() -> None:
    """Even a single NaN should fail the invalid-count check."""
    samples = _clean_signal().copy()
    samples[100] = np.nan + 0j
    report = QualityChecker().check(samples, _good_meta(), expected_duration_s=1.0)
    assert report.is_valid is False
    assert report.invalid_count == 1
    assert any("invalid" in check.lower() for check in report.failed_checks)


def test_failed_check_when_completeness_low() -> None:
    """If actual sample count is well below expected, the check fails."""
    # 1.5M samples but we'll claim we wanted 2M (1 second at 2 MHz).
    samples = _clean_signal(n=1_500_000)
    report = QualityChecker().check(samples, _good_meta(), expected_duration_s=1.0)
    assert report.is_valid is False
    assert any("samples" in check.lower() for check in report.failed_checks)


def test_failed_check_when_metadata_broken() -> None:
    """A SigMF metadata dict missing required fields should fail."""
    samples = _clean_signal()
    broken_meta = {"global": {"core:datatype": "cf32_le"}, "captures": []}
    report = QualityChecker().check(samples, broken_meta, expected_duration_s=1.0)
    assert report.is_valid is False
    assert report.metadata_valid is False
    assert any("metadata" in check.lower() for check in report.failed_checks)


# ===========================================================================
# Custom thresholds
# ===========================================================================

def test_custom_thresholds_can_relax_checks() -> None:
    """A saturated signal can be accepted by relaxing the clipping threshold."""
    samples = _clean_signal(amplitude=0.95)  # near-clipping but no NaN
    relaxed = QualityThresholds(max_clipping_ratio=1.0, max_rms_dbfs=10.0)
    report = QualityChecker(relaxed).check(samples, _good_meta(), expected_duration_s=1.0)
    # With ultra-permissive thresholds, this should now pass.
    assert report.is_valid is True


# ===========================================================================
# Report serialization
# ===========================================================================

def test_report_to_dict_returns_plain_python_types() -> None:
    """to_dict() output should be JSON-serializable without surprises."""
    import json
    samples = _clean_signal()
    report = QualityChecker().check(samples, _good_meta(), expected_duration_s=1.0)
    d = report.to_dict()
    # No exception means JSON-compatible.
    text = json.dumps(d)
    # And we should be able to round-trip back without losing structure.
    parsed = json.loads(text)
    assert parsed["is_valid"] is True
    assert parsed["clipping_ratio"] == 0.0
