"""Unit tests for aerolake.quality.metrics.

We test each pure function with carefully crafted inputs. Since the
functions are pure (no I/O, no state), tests are trivially fast and
deterministic.

Strategy
--------
For each function we test:
  - The "happy path" with known-good input where we can predict the output
  - At least one edge case (empty, all zeros, all NaN, etc.)
  - At least one boundary case (the threshold itself)

We use numpy's `default_rng(seed=...)` everywhere to keep noise
reproducible, never the legacy `np.random.*` global API.
"""

from __future__ import annotations

import math

import numpy as np

from aerolake.quality.metrics import (
    compute_clipping_ratio,
    compute_dc_offset_iq,
    compute_rms_power_dbfs,
    compute_sample_completeness,
    count_invalid_samples,
    validate_sigmf_metadata,
)

# ===========================================================================
# Helpers — small factories to keep individual tests focused on behavior
# ===========================================================================

def _make_constant_amplitude_signal(amplitude: float, n: int = 1000) -> np.ndarray:
    """Build a complex64 signal where every sample has the given magnitude.

    We use a rotating phase so that I and Q both vary (more realistic
    than e.g. a pure real signal). Each sample has magnitude = amplitude
    by construction, since |amplitude * exp(j*phi)| = amplitude for any phi.
    """
    phases = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return (amplitude * np.exp(1j * phases)).astype(np.complex64)


# ===========================================================================
# compute_clipping_ratio
# ===========================================================================

def test_clipping_ratio_zero_for_quiet_signal() -> None:
    """A signal well below the threshold should report no clipping."""
    signal = _make_constant_amplitude_signal(amplitude=0.5)
    # All samples have magnitude 0.5, far below default threshold 0.99
    assert compute_clipping_ratio(signal) == 0.0


def test_clipping_ratio_one_for_saturated_signal() -> None:
    """A signal with all samples above the threshold reports full clipping."""
    signal = _make_constant_amplitude_signal(amplitude=1.5)
    # All samples have magnitude 1.5, well above default threshold 0.99
    assert compute_clipping_ratio(signal) == 1.0


def test_clipping_ratio_proportional_for_mixed_signal() -> None:
    """When half the samples are clipped, the ratio should be 0.5."""
    clean = _make_constant_amplitude_signal(amplitude=0.5, n=500)
    saturated = _make_constant_amplitude_signal(amplitude=1.5, n=500)
    mixed = np.concatenate([clean, saturated])
    # 500 of 1000 clipped -> ratio should be 0.5 exactly
    assert compute_clipping_ratio(mixed) == 0.5


def test_clipping_ratio_respects_custom_threshold() -> None:
    """A custom threshold should change which samples count as clipped."""
    signal = _make_constant_amplitude_signal(amplitude=0.7)
    # With default threshold 0.99, no sample is clipped
    assert compute_clipping_ratio(signal) == 0.0
    # With a low threshold of 0.5, every sample is clipped
    assert compute_clipping_ratio(signal, threshold=0.5) == 1.0


# ===========================================================================
# compute_rms_power_dbfs
# ===========================================================================

def test_rms_dbfs_matches_known_amplitude() -> None:
    """A constant-amplitude signal of 0.1 should yield -20 dBFS exactly."""
    signal = _make_constant_amplitude_signal(amplitude=0.1)
    # 20 * log10(0.1) = -20 exactly
    assert math.isclose(compute_rms_power_dbfs(signal), -20.0, abs_tol=0.01)


def test_rms_dbfs_zero_for_full_scale() -> None:
    """A constant-amplitude signal of 1.0 should yield 0 dBFS exactly."""
    signal = _make_constant_amplitude_signal(amplitude=1.0)
    # 20 * log10(1.0) = 0
    assert math.isclose(compute_rms_power_dbfs(signal), 0.0, abs_tol=0.01)


def test_rms_dbfs_returns_minus_infinity_for_silence() -> None:
    """A strictly-zero signal should return -inf (avoiding log10(0))."""
    silence = np.zeros(1000, dtype=np.complex64)
    assert compute_rms_power_dbfs(silence) == float("-inf")


# ===========================================================================
# count_invalid_samples
# ===========================================================================

def test_invalid_count_zero_for_clean_signal() -> None:
    """A healthy signal has zero NaN/Inf samples."""
    signal = _make_constant_amplitude_signal(amplitude=0.5)
    assert count_invalid_samples(signal) == 0


def test_invalid_count_detects_nan_and_inf() -> None:
    """Injecting NaN and Inf at specific positions is detected."""
    signal = _make_constant_amplitude_signal(amplitude=0.5).copy()
    signal[10] = np.nan + 0j  # NaN on I (and Q becomes 0 by construction)
    signal[20] = 0 + np.nan * 1j  # NaN on Q
    signal[30] = np.inf + 0j  # Inf on I
    # 3 distinct invalid samples
    assert count_invalid_samples(signal) == 3


# ===========================================================================
# compute_dc_offset_iq
# ===========================================================================

def test_dc_offset_near_zero_for_symmetric_signal() -> None:
    """A signal symmetric around zero has near-zero DC offset on both channels."""
    signal = _make_constant_amplitude_signal(amplitude=0.5, n=10000)
    dc_i, dc_q = compute_dc_offset_iq(signal)
    # Floating-point noise should be well under 1e-3
    assert abs(dc_i) < 1e-3
    assert abs(dc_q) < 1e-3


def test_dc_offset_matches_injected_bias() -> None:
    """Adding a known DC bias should be recovered by the estimator."""
    base = _make_constant_amplitude_signal(amplitude=0.1, n=10000)
    biased = (base + (0.05 + 0.02j)).astype(np.complex64)
    dc_i, dc_q = compute_dc_offset_iq(biased)
    # We expect ~(0.05, 0.02), within float32 precision
    assert math.isclose(dc_i, 0.05, abs_tol=1e-4)
    assert math.isclose(dc_q, 0.02, abs_tol=1e-4)


# ===========================================================================
# compute_sample_completeness
# ===========================================================================

def test_completeness_one_when_counts_match() -> None:
    """Perfect capture: actual sample count equals expected."""
    samples = np.zeros(2_000_000, dtype=np.complex64)
    # duration_s * rate = 1.0 * 2e6 = 2_000_000 expected
    assert compute_sample_completeness(samples, 2_000_000, 1.0) == 1.0


def test_completeness_below_one_for_dropouts() -> None:
    """Missing samples should yield a ratio below 1.0."""
    samples = np.zeros(1_900_000, dtype=np.complex64)
    # Expected 2e6, got 1.9e6 -> ratio 0.95
    result = compute_sample_completeness(samples, 2_000_000, 1.0)
    assert math.isclose(result, 0.95, abs_tol=1e-6)


def test_completeness_above_one_indicates_bug() -> None:
    """More samples than expected returns ratio > 1.0 (programming error)."""
    samples = np.zeros(2_100_000, dtype=np.complex64)
    result = compute_sample_completeness(samples, 2_000_000, 1.0)
    assert result > 1.0


# ===========================================================================
# validate_sigmf_metadata
# ===========================================================================

def test_metadata_validation_accepts_complete_dict() -> None:
    """A SigMF metadata dict with all required fields validates cleanly."""
    meta = {
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": 2_000_000.0,
            "core:version": "1.2.6",
        },
        "captures": [{"core:sample_start": 0, "core:frequency": 1.5e9}],
        "annotations": [],
    }
    is_valid, issues = validate_sigmf_metadata(meta)
    assert is_valid is True
    assert issues == []


def test_metadata_validation_lists_all_missing_fields() -> None:
    """Multiple missing fields should all appear in the issues list."""
    # Only datatype is present; missing sample_rate, version, captures.
    broken = {"global": {"core:datatype": "cf32_le"}, "captures": []}
    is_valid, issues = validate_sigmf_metadata(broken)
    assert is_valid is False
    # We expect specific failure messages — but tolerate variations in wording.
    joined = " ".join(issues)
    assert "core:sample_rate" in joined
    assert "core:version" in joined
    assert "captures" in joined


def test_metadata_validation_rejects_missing_global() -> None:
    """No 'global' section is a fatal error."""
    is_valid, issues = validate_sigmf_metadata({"captures": [{}]})
    assert is_valid is False
    assert any("global" in issue for issue in issues)
