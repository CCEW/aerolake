"""Quality metrics for RF captures.

This module contains PURE FUNCTIONS that compute objective quality
indicators from IQ samples. By "pure" we mean: they take inputs,
return outputs, and have no side effects (no logging, no file I/O,
no decisions). This makes them trivially testable and reusable.

Each metric tells us something specific about the recorded signal:
  - Is it clipped (saturated)?
  - Is the average power level reasonable?
  - Are there invalid samples (NaN/Inf)?

The QualityChecker class (in checker.py) will call these functions
and aggregate them into a verdict.

Mathematical conventions used in this module
--------------------------------------------
IQ samples are stored as np.complex64, meaning each sample is a
complex number I + j*Q where:
  - I (in-phase) is the real part (float32)
  - Q (quadrature) is the imaginary part (float32)

The MAGNITUDE of a sample is sqrt(I^2 + Q^2), which represents the
instantaneous amplitude of the RF signal. In SigMF cf32_le format,
samples are typically normalized so that magnitudes fit in [0, 1],
with 1.0 being the maximum the ADC can represent (full scale).
"""

from __future__ import annotations

import numpy as np


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default threshold above which we consider a sample "clipped".
# We pick 0.99 (not 1.0) because real ADCs introduce a tiny bit of
# noise, so a perfectly-saturated signal won't always reach exactly 1.0.
# Using 0.99 catches "near-saturation" too, which is what we want — the
# signal is already degraded by the time it gets that close to the ceiling.
CLIPPING_THRESHOLD_DEFAULT: float = 0.99

# Full-scale reference for dBFS calculation. For normalized cf32_le
# samples (the SigMF convention), the max magnitude is 1.0.
# "dBFS" = decibels relative to Full Scale.
#   -  0 dBFS = signal at the maximum the ADC can encode
#   - -6 dBFS = signal at half that magnitude (factor 2)
#   - -20 dBFS = signal at 1/10 of full scale
#   - -60 dBFS = signal at 1/1000 (very quiet, mostly noise)
FULL_SCALE_AMPLITUDE: float = 1.0


# ---------------------------------------------------------------------------
# Metric 1 — Clipping ratio
# ---------------------------------------------------------------------------

def compute_clipping_ratio(
    samples: np.ndarray,
    threshold: float = CLIPPING_THRESHOLD_DEFAULT,
) -> float:
    """Return the fraction of samples whose magnitude exceeds ``threshold``.

    Why this metric matters
    -----------------------
    Clipping (also called saturation) happens when the input signal is
    too strong for the ADC to encode. The ADC outputs its maximum value
    and any information above that ceiling is irreversibly lost — the
    waveform is "flat-topped" instead of being faithfully reproduced.

    A clipped signal looks fine in amplitude (the levels are high) but
    is harmonically distorted: a pure tone becomes a square-ish wave,
    and any demodulator downstream will produce garbage.

    Rule of thumb:
      - clipping_ratio < 0.001  -> excellent (less than 0.1% clipped)
      - clipping_ratio < 0.01   -> acceptable
      - clipping_ratio > 0.01   -> the recording is degraded

    Parameters
    ----------
    samples
        1-D numpy array of complex64 IQ samples.
    threshold
        Magnitude above which a sample is considered clipped, in the
        same scale as ``samples`` (default 0.99 for normalized cf32_le).

    Returns
    -------
    float
        Ratio of clipped samples, in the range [0.0, 1.0].
    """
    # Step 1 — Compute the magnitude (envelope) of every sample.
    # For a complex number z = I + jQ, np.abs(z) returns sqrt(I^2 + Q^2).
    # numpy handles this in a single vectorized operation, much faster
    # than a Python loop.
    magnitudes = np.abs(samples)

    # Step 2 — Build a boolean mask: True where magnitude >= threshold,
    # False elsewhere. This is a vectorized comparison: numpy compares
    # the threshold to every element at once.
    is_clipped = magnitudes >= threshold

    # Step 3 — Count the True values. In numpy, summing a boolean
    # array converts True->1 and False->0, so np.sum gives us the count
    # of clipped samples directly.
    n_clipped = np.sum(is_clipped)

    # Step 4 — Divide by total samples to get the ratio.
    # We cast to float() because numpy operations return numpy scalars
    # (np.float64), and we prefer plain Python floats in our public API
    # for easier JSON serialization later.
    return float(n_clipped / len(samples))


# ---------------------------------------------------------------------------
# Metric 2 — RMS power in dBFS
# ---------------------------------------------------------------------------

def compute_rms_power_dbfs(samples: np.ndarray) -> float:
    """Return the Root-Mean-Square power of the signal in dBFS.

    Why this metric matters
    -----------------------
    The RMS (Root Mean Square) is the standard way to measure the
    "average loudness" of a signal. Unlike a simple average (which can
    be misleading for signals that swing positive and negative), the
    RMS squares each value first, so it only captures magnitude.

    In dBFS (decibels Full Scale), 0 dBFS is the loudest a signal can
    be without clipping. Most well-recorded signals sit between
    -20 dBFS (loud and clear) and -40 dBFS (quiet but exploitable).
    Below -50 dBFS the signal is usually buried in noise.

    Rule of thumb:
      - rms > -3 dBFS    -> too loud, likely clipping somewhere
      - -20 to -10 dBFS  -> ideal recording level
      - -40 to -20 dBFS  -> usable, may need gain
      - < -50 dBFS       -> too quiet, mostly noise

    Mathematical detail
    -------------------
    For complex IQ samples:

        RMS = sqrt( mean( |sample|^2 ) )

    And in dBFS, relative to the full-scale amplitude 1.0:

        dBFS = 20 * log10( RMS / 1.0 ) = 20 * log10( RMS )

    Parameters
    ----------
    samples
        1-D numpy array of complex64 IQ samples.

    Returns
    -------
    float
        RMS power in dBFS. Returns -inf if the signal is identically zero
        (which would mean either a bug or a totally silent recording).
    """
    # Step 1 — Compute the instantaneous power of each sample.
    # Power is the squared magnitude: |z|^2 = I^2 + Q^2.
    # np.abs(samples) ** 2 gives a vector of real, non-negative values.
    instantaneous_power = np.abs(samples) ** 2

    # Step 2 — Average the power over all samples.
    # This is the MEAN of the squared magnitudes, which is what the
    # "MS" in RMS stands for ("Mean Square").
    mean_power = np.mean(instantaneous_power)

    # Step 3 — Take the square root to get RMS amplitude.
    # This is the standard "R" of "Root Mean Square".
    rms_amplitude = np.sqrt(mean_power)

    # Step 4 — Edge case: a perfectly silent signal has rms = 0, and
    # log10(0) = -infinity. We return -inf explicitly to avoid
    # numpy warnings polluting the logs, and to make the caller's
    # intent explicit: "this signal has no power at all".
    if rms_amplitude == 0:
        return float("-inf")

    # Step 5 — Convert linear amplitude to dBFS.
    # The factor 20 (not 10) is used because we're working with an
    # AMPLITUDE quantity. For a POWER quantity, the factor would be 10.
    # Both conventions encode the same information, just expressed
    # differently. dBFS = 20 * log10(amp / amp_max) = 20 * log10(rms),
    # since amp_max = 1.0 for normalized cf32_le samples.
    dbfs = 20.0 * np.log10(rms_amplitude / FULL_SCALE_AMPLITUDE)

    return float(dbfs)


# ---------------------------------------------------------------------------
# Metric 3 — Invalid sample count
# ---------------------------------------------------------------------------

def count_invalid_samples(samples: np.ndarray) -> int:
    """Return the number of samples containing NaN or Inf values.

    Why this metric matters
    -----------------------
    NaN (Not-a-Number) and Inf (infinity) are floating-point sentinel
    values that should NEVER appear in a healthy RF recording. Their
    presence almost always indicates one of:

      - A bug in the acquisition driver (rare but happens)
      - A division by zero somewhere in a preprocessing pipeline
      - Memory corruption (extremely rare)
      - A botched conversion from another datatype

    Any single NaN propagates: an FFT applied to a NaN-containing
    signal will produce all-NaN output. So even one bad sample can
    poison an entire downstream analysis.

    Verdict:
      - invalid_count == 0     -> healthy, expected
      - invalid_count > 0      -> investigate immediately; the recording
                                  is corrupted

    Parameters
    ----------
    samples
        1-D numpy array of complex64 IQ samples.

    Returns
    -------
    int
        Number of samples where I, Q, or both are NaN or Inf.
    """
    # Step 1 — Detect NaN on the I or Q channel.
    # For a complex number, np.isnan returns True if EITHER the real
    # or the imaginary part is NaN. So a single check covers both
    # channels at once.
    has_nan = np.isnan(samples)

    # Step 2 — Same thing for infinities.
    has_inf = np.isinf(samples)

    # Step 3 — Combine the two masks with logical OR.
    # A sample is "invalid" if it has NaN OR Inf.
    # Using the | operator is the vectorized form of OR for boolean
    # arrays (do NOT use Python's `or` keyword, which doesn't work
    # element-wise on arrays).
    invalid_mask = has_nan | has_inf

    # Step 4 — Count the True values and cast to plain int.
    # int() instead of float() here because counts are always integers.
    return int(np.sum(invalid_mask))

# ---------------------------------------------------------------------------
# Metric 4 — DC offset on I and Q channels
# ---------------------------------------------------------------------------

def compute_dc_offset_iq(samples: np.ndarray) -> tuple[float, float]:
    """Return the DC offset on the I and Q channels separately.

    Why this metric matters
    -----------------------
    A "DC offset" is a constant bias on the I or Q channel: instead of
    oscillating around zero, the signal oscillates around a non-zero
    baseline. In the frequency domain, this DC bias shows up as a sharp
    spurious spike right at the center frequency (called the "LO leakage"
    or "DC spike").

    DC offset is a hardware imperfection extremely common on cheap SDRs
    (RTL-SDR especially) and on uncalibrated equipment. It's not always
    fatal — many demodulators can tolerate it — but it pollutes any FFT
    or visual spectrum display, and it eats into the ADC's dynamic range.

    For an ideal SDR with no DC offset, both means should be ~0 to within
    measurement noise (typically 1e-3 or smaller for normalized cf32_le).

    Rule of thumb:
      - |DC| < 0.001 (≈ -60 dBFS)  -> excellent, hardware is clean
      - |DC| < 0.01  (≈ -40 dBFS)  -> acceptable
      - |DC| > 0.01                -> noticeable bias, may need correction

    Mathematical detail
    -------------------
    For a sequence of complex samples z[n] = I[n] + j*Q[n]:

        DC_I = mean( I[n] )
        DC_Q = mean( Q[n] )

    These are the DC components of each channel: they tell us, on
    average, where the signal sits along each axis.

    Parameters
    ----------
    samples
        1-D numpy array of complex64 IQ samples.

    Returns
    -------
    tuple[float, float]
        (dc_i, dc_q) — the mean of the I channel and the mean of the Q
        channel, in the same scale as the samples (normalized [-1, 1] for
        cf32_le).
    """
    # Step 1 — Extract the real part (I) and the imaginary part (Q).
    # numpy provides .real and .imag attributes on complex arrays that
    # return views (no copy) onto the underlying float32 storage.
    # This is essentially free — no computation, just reinterpretation.
    i_channel = samples.real
    q_channel = samples.imag

    # Step 2 — Compute the arithmetic mean of each channel.
    # If a channel oscillates symmetrically around zero (the ideal case),
    # the mean is approximately zero (within floating-point and finite-
    # window noise). Any persistent offset shows up here.
    dc_i = np.mean(i_channel)
    dc_q = np.mean(q_channel)

    # Step 3 — Return as a tuple of Python floats (not numpy scalars)
    # for clean serialization later.
    return (float(dc_i), float(dc_q))


# ---------------------------------------------------------------------------
# Metric 5 — Sample completeness
# ---------------------------------------------------------------------------

def compute_sample_completeness(
    samples: np.ndarray,
    sample_rate: float,
    expected_duration_s: float,
) -> float:
    """Return the ratio of actual samples to expected samples.

    Why this metric matters
    -----------------------
    During real-time acquisition from an SDR, samples can be DROPPED if
    the system can't keep up with the data rate. This happens when:

      - The USB bus is congested (frequent on cheap SDRs at high rates)
      - The CPU can't process the incoming stream fast enough
      - A scheduler hiccup briefly freezes the acquisition thread
      - The disk write rate is lower than the sample arrival rate

    Dropped samples are silent killers: the file size is smaller than
    expected, but if you trust the metadata you might not notice. A
    spectral analysis on a recording with dropouts will show artifacts
    that look like real RF events but are pure artifacts.

    The expected sample count is:  duration_s * sample_rate

    Verdict:
      - completeness == 1.0   -> all samples accounted for, perfect
      - completeness in [0.99, 1.0)  -> tiny dropouts, usually OK
      - completeness < 0.99   -> significant dropouts, recording suspect
      - completeness > 1.0    -> bug! we have MORE samples than expected

    Parameters
    ----------
    samples
        1-D numpy array of IQ samples.
    sample_rate
        Sample rate in Hz (from SigMF metadata: core:sample_rate).
    expected_duration_s
        Duration we asked the SDR to capture, in seconds. Comes from
        the producer's intent, not from the data itself.

    Returns
    -------
    float
        Ratio of actual samples to expected samples. 1.0 = perfect.
    """
    # Step 1 — Compute how many samples we EXPECTED.
    # This is the contract: capture for D seconds at rate R should yield
    # exactly D * R samples. Any deviation is suspect.
    # We round to an integer because sample counts are always whole.
    expected_count = int(round(expected_duration_s * sample_rate))

    # Step 2 — Guard against division by zero.
    # If somehow expected_count is 0 (would be a programming error,
    # since duration > 0 and rate > 0 in any real call), we can't
    # compute a meaningful ratio. Return 0 to indicate "totally broken".
    if expected_count == 0:
        return 0.0

    # Step 3 — Count how many samples we ACTUALLY got.
    actual_count = len(samples)

    # Step 4 — Compute the ratio.
    # A value < 1.0 indicates dropouts. Equal to 1.0 is perfect. A value
    # > 1.0 indicates a bug somewhere — we got more samples than asked.
    return float(actual_count / expected_count)


# ---------------------------------------------------------------------------
# Metric 6 — SigMF metadata validity
# ---------------------------------------------------------------------------

# Required SigMF fields per the spec v1.0+. If any of these is missing or
# obviously wrong, the capture cannot be interpreted by a downstream tool.
# We deliberately keep this list short — only the fields that are
# essential for reading the .sigmf-data correctly.
_REQUIRED_GLOBAL_FIELDS = ("core:datatype", "core:sample_rate", "core:version")
_REQUIRED_CAPTURE_FIELDS = ("core:sample_start",)


def validate_sigmf_metadata(sigmf_meta: dict) -> tuple[bool, list[str]]:
    """Validate that the SigMF metadata dict has all required fields.

    Why this metric matters
    -----------------------
    A SigMF capture is only useful if its .sigmf-meta JSON is correctly
    structured: without core:datatype, you can't decode the bytes;
    without core:sample_rate, you can't reconstruct the time axis;
    without at least one capture entry with core:sample_start, the
    "where does the data start" question has no answer.

    A capture whose metadata is missing or malformed is, for all
    practical purposes, lost data — even if the .sigmf-data bytes are
    fine. This metric exists to detect such situations early, before
    they propagate to downstream analysis.

    Parameters
    ----------
    sigmf_meta
        Parsed SigMF metadata dict (the output of json.loads on a
        .sigmf-meta file).

    Returns
    -------
    tuple[bool, list[str]]
        (is_valid, list_of_issues). is_valid is True only if there are
        no issues. The list of issues is empty when valid, otherwise
        contains human-readable strings describing each problem.
    """
    # We accumulate issues in a list rather than returning at the first
    # failure. This way the caller gets a complete report of everything
    # wrong, not just the first thing — much more useful for debugging.
    issues: list[str] = []

    # --- Check the global section --------------------------------------
    # SigMF requires a top-level "global" object.
    global_section = sigmf_meta.get("global")
    if not isinstance(global_section, dict):
        issues.append("missing or invalid 'global' section")
        # If global is missing entirely, the rest of the check is moot
        # but we continue so we can still report on the captures section.
        global_section = {}

    # Verify each required field in global.
    for field in _REQUIRED_GLOBAL_FIELDS:
        if field not in global_section:
            issues.append(f"missing required field global.{field}")

    # --- Check the captures section ------------------------------------
    # SigMF requires a "captures" array with at least one entry.
    captures_section = sigmf_meta.get("captures")
    if not isinstance(captures_section, list):
        issues.append("missing or invalid 'captures' section (must be a list)")
    elif len(captures_section) == 0:
        issues.append("'captures' section is empty (need at least one entry)")
    else:
        # Validate the first capture entry. SigMF allows multiple
        # capture entries (for concatenated recordings), but at minimum
        # the first one must exist and be well-formed.
        first_capture = captures_section[0]
        if not isinstance(first_capture, dict):
            issues.append("first capture entry is not a dict")
        else:
            for field in _REQUIRED_CAPTURE_FIELDS:
                if field not in first_capture:
                    issues.append(f"missing required field captures[0].{field}")

    # --- Final verdict --------------------------------------------------
    # Valid if and only if no issue was raised during the checks above.
    is_valid = (len(issues) == 0)

    return (is_valid, issues)
