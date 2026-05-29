"""Unit tests for aerolake.producer.synthetic.

These tests focus on the signal-level guarantees of the generator,
especially the tone_amplitude parameter we added to produce realistic
recording levels (around -20 dBFS) instead of full-scale signals that
would clip.
"""

from __future__ import annotations

import math

import numpy as np

from aerolake.producer.synthetic import generate_tone


def test_generate_tone_amplitude_sets_power_level() -> None:
    """tone_amplitude=0.1 should yield approximately -20 dBFS RMS power.

    The math: for a complex tone of amplitude A, every sample has magnitude
    A, so the RMS is A, and 20*log10(0.1) = -20 dBFS. We use a high SNR
    (40 dB) so the added noise barely shifts the RMS, keeping the assertion
    tight.
    """
    signal = generate_tone(
        duration_s=0.01,
        sample_rate=1_000_000,
        center_freq=1e9,
        tone_amplitude=0.1,
        snr_db=40.0,  # high SNR -> noise contribution is negligible
        seed=1,
    )
    # Compute the RMS power of the generated samples, then convert to dBFS.
    rms = np.sqrt(np.mean(np.abs(signal.samples) ** 2))
    dbfs = 20 * np.log10(rms)
    # Expect ~-20 dBFS, with a small tolerance for the residual noise.
    assert math.isclose(dbfs, -20.0, abs_tol=0.5)


def test_generate_tone_default_amplitude_avoids_clipping() -> None:
    """The default amplitude should keep every sample magnitude well below 1.0.

    This is the whole point of the fix: a default tone must not saturate.
    """
    signal = generate_tone(
        duration_s=0.01,
        sample_rate=1_000_000,
        center_freq=1e9,
        seed=1,
    )
    # The largest magnitude across all samples must stay far from full scale.
    max_magnitude = np.max(np.abs(signal.samples))
    # Default amplitude 0.1 + a little noise -> comfortably under 0.5.
    assert max_magnitude < 0.5


def test_generate_tone_sample_count_matches_duration() -> None:
    """The sample count must equal duration_s * sample_rate exactly."""
    signal = generate_tone(
        duration_s=0.01,
        sample_rate=2_000_000,
        center_freq=1e9,
        seed=1,
    )
    # 0.01 s * 2 MHz = 20 000 samples.
    assert len(signal.samples) == int(0.01 * 2_000_000)


def test_generate_tone_higher_amplitude_increases_power() -> None:
    """A larger tone_amplitude must produce a higher RMS power.

    A simple monotonicity check: doubling-ish the amplitude can only raise
    the measured power, never lower it.
    """
    low = generate_tone(
        duration_s=0.01, sample_rate=1_000_000, center_freq=1e9,
        tone_amplitude=0.1, snr_db=40.0, seed=1,
    )
    high = generate_tone(
        duration_s=0.01, sample_rate=1_000_000, center_freq=1e9,
        tone_amplitude=0.5, snr_db=40.0, seed=1,
    )
    rms_low = np.sqrt(np.mean(np.abs(low.samples) ** 2))
    rms_high = np.sqrt(np.mean(np.abs(high.samples) ** 2))
    assert rms_high > rms_low


def test_generate_tone_description_includes_dbfs() -> None:
    """The description should mention the dBFS level, for traceability.

    We embed the level in core:description so a future reader of the SigMF
    metadata can see at what amplitude the capture was generated.
    """
    signal = generate_tone(
        duration_s=0.01, sample_rate=1_000_000, center_freq=1e9,
        tone_amplitude=0.1, seed=1,
    )
    assert "dBFS" in signal.description
