"""Quality checker for RF captures.

This module orchestrates the pure metrics functions from metrics.py
into a complete quality assessment. It:

  1. Computes all 6 quality metrics on a capture
  2. Compares each metric against configurable thresholds
  3. Aggregates everything into a QualityReport with a clear verdict
  4. Returns the report so the caller can decide what to do
     (promote quality=raw -> validated, log to MinIO, reject, etc.)

The thresholds are configurable per-call, because what counts as "good"
depends on the application: a SETI search needs much cleaner signal than
a GNSS recording where some noise is acceptable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import structlog

from aerolake.quality.metrics import (
    compute_clipping_ratio,
    compute_dc_offset_iq,
    compute_rms_power_dbfs,
    compute_sample_completeness,
    count_invalid_samples,
    validate_sigmf_metadata,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Thresholds dataclass — what counts as "good enough"
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QualityThresholds:
    """Configurable thresholds for the quality verdict.

    These defaults are chosen to be reasonable for a synthetic / clean
    recording. For field data captured with cheap hardware, you may
    want to relax some of them (especially the DC offset bound).

    Use ``QualityThresholds(max_dc_offset=0.05)`` to override only the
    field you care about; all other defaults are kept.
    """

    # --- Clipping --------------------------------------------------------
    # Above 1% clipped samples we consider the recording degraded.
    # Tunable per use case: a SDR-based passive radar would want 0.001
    # (0.1%), while a low-priority logger might accept 0.05 (5%).
    max_clipping_ratio: float = 0.01

    # --- Power levels ---------------------------------------------------
    # The "sweet spot" for IQ recordings is between -40 and -3 dBFS.
    # Below -50 dBFS, the signal is mostly noise.
    # Above -3 dBFS, you're flirting with clipping.
    min_rms_dbfs: float = -50.0
    max_rms_dbfs: float = -3.0

    # --- Invalid samples ------------------------------------------------
    # Zero tolerance. Any NaN or Inf is a sign of corruption.
    # In a healthy pipeline, this should always be 0.
    max_invalid_count: int = 0

    # --- DC offset -------------------------------------------------------
    # 0.01 means "less than 1% of full scale". Tight enough to flag
    # broken hardware, loose enough to accept normal SDR imperfections.
    # We apply the same bound to I and Q separately.
    max_dc_offset: float = 0.01

    # --- Completeness ----------------------------------------------------
    # 0.99 means we tolerate up to 1% sample drops.
    # Above 1.0 means we got more samples than expected — always a bug.
    min_completeness: float = 0.99
    max_completeness: float = 1.001  # allow tiny float overshoot


# ---------------------------------------------------------------------------
# Quality report dataclass — the full picture for one capture
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QualityReport:
    """Outcome of running QualityChecker on a single capture.

    Contains every raw metric (so the caller can do its own analysis)
    AND a final verdict (so the common case is one boolean check).

    Use ``report.to_dict()`` to serialize as JSON for storage in MinIO
    metadata, Confluence reports, or anywhere else.
    """

    # --- Raw metrics (what we measured) ----------------------------------
    clipping_ratio: float
    rms_power_dbfs: float
    invalid_count: int
    dc_offset_i: float
    dc_offset_q: float
    sample_completeness: float
    metadata_valid: bool
    metadata_issues: list[str] = field(default_factory=list)

    # --- Verdict (what we conclude) -------------------------------------
    # True if EVERY threshold check passed. False otherwise.
    is_valid: bool = False

    # Human-readable list of checks that failed, e.g.
    #   ["clipping too high (0.05 > 0.01)", "DC offset on Q channel too large"]
    # Empty list if all checks passed.
    failed_checks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dict suitable for JSON serialization.

        We use dataclasses.asdict() which recursively converts nested
        dataclasses and standard containers. The lists become JSON arrays,
        floats become JSON numbers, etc.
        """
        return asdict(self)


# ---------------------------------------------------------------------------
# The QualityChecker class — orchestration
# ---------------------------------------------------------------------------

class QualityChecker:
    """Runs the full quality assessment pipeline on a capture.

    Usage::

        checker = QualityChecker()                     # default thresholds
        report = checker.check(samples, sigmf_meta, expected_duration_s=1.0)
        if report.is_valid:
            print("Capture passes quality checks")
        else:
            print(f"Issues: {report.failed_checks}")
    """

    def __init__(self, thresholds: QualityThresholds | None = None) -> None:
        # If no thresholds passed, fall back to defaults. The "or" trick
        # works because QualityThresholds() (no args) returns a fresh
        # instance with all defaults filled in.
        self._thresholds = thresholds or QualityThresholds()

    def check(
        self,
        samples: np.ndarray,
        sigmf_meta: dict,
        expected_duration_s: float,
    ) -> QualityReport:
        """Run all quality metrics and produce a verdict.

        Parameters
        ----------
        samples
            1-D numpy array of complex64 IQ samples to assess.
        sigmf_meta
            Parsed SigMF metadata dict (typically read from the
            corresponding .sigmf-meta object).
        expected_duration_s
            The duration we asked the producer to capture, in seconds.
            Used to compute sample completeness.

        Returns
        -------
        QualityReport
            Frozen dataclass holding every metric + the final verdict.
        """
        log = logger.bind(
            sample_count=len(samples),
            expected_duration_s=expected_duration_s,
        )

        # --- Step 1 — Compute all 6 raw metrics ------------------------
        # Each function is pure and independent, so we just call them
        # in sequence. Order doesn't matter; this is essentially a
        # data-collection step before any judgment is made.
        clipping = compute_clipping_ratio(samples)
        rms_dbfs = compute_rms_power_dbfs(samples)
        invalid = count_invalid_samples(samples)
        dc_i, dc_q = compute_dc_offset_iq(samples)

        # For completeness, we need the sample_rate from the metadata.
        # If the metadata is broken and missing core:sample_rate, we
        # can't compute completeness — we substitute 0.0 and flag it
        # via metadata_valid below.
        sample_rate = sigmf_meta.get("global", {}).get("core:sample_rate", 0)
        if sample_rate > 0:
            completeness = compute_sample_completeness(
                samples, sample_rate, expected_duration_s
            )
        else:
            # Can't compute without a valid sample rate. We return 0.0
            # which will fail the completeness check below, AND the
            # metadata check will catch the missing field.
            completeness = 0.0

        meta_valid, meta_issues = validate_sigmf_metadata(sigmf_meta)

        # --- Step 2 — Apply thresholds, accumulate failures ------------
        # We collect every failure rather than stopping at the first one,
        # so the user sees the complete picture in one shot.
        failed: list[str] = []
        t = self._thresholds  # short alias for readability below

        if clipping > t.max_clipping_ratio:
            failed.append(
                f"clipping ratio too high ({clipping:.4f} > {t.max_clipping_ratio})"
            )

        # The RMS check has two bounds (too quiet AND too loud).
        # -inf comes from a strictly-zero signal, which definitely
        # fails "too quiet" — the comparison still works because
        # -inf < any finite number.
        if rms_dbfs < t.min_rms_dbfs:
            failed.append(
                f"signal too weak ({rms_dbfs:.1f} dBFS < {t.min_rms_dbfs})"
            )
        if rms_dbfs > t.max_rms_dbfs:
            failed.append(
                f"signal too loud ({rms_dbfs:.1f} dBFS > {t.max_rms_dbfs})"
            )

        if invalid > t.max_invalid_count:
            failed.append(
                f"{invalid} invalid samples (max allowed: {t.max_invalid_count})"
            )

        # DC offset is checked on each channel independently — we report
        # specifically WHICH channel is bad so a hardware tech can target
        # the fix.
        if abs(dc_i) > t.max_dc_offset:
            failed.append(
                f"DC offset on I too large ({dc_i:+.4f}, |max| {t.max_dc_offset})"
            )
        if abs(dc_q) > t.max_dc_offset:
            failed.append(
                f"DC offset on Q too large ({dc_q:+.4f}, |max| {t.max_dc_offset})"
            )

        # Completeness has two bounds: under (dropouts) and over (bug).
        if completeness < t.min_completeness:
            failed.append(
                f"too few samples ({completeness:.4f} < {t.min_completeness})"
            )
        if completeness > t.max_completeness:
            failed.append(
                f"too many samples ({completeness:.4f} > {t.max_completeness})"
            )

        if not meta_valid:
            # We inline the meta_issues list into a single failure
            # message, so the caller can see what's specifically broken
            # without having to dig into the separate metadata_issues
            # field.
            failed.append(
                f"SigMF metadata invalid: {'; '.join(meta_issues)}"
            )

        # --- Step 3 — Build the report ---------------------------------
        # is_valid is True only if NO check failed.
        is_valid = (len(failed) == 0)

        report = QualityReport(
            clipping_ratio=clipping,
            rms_power_dbfs=rms_dbfs,
            invalid_count=invalid,
            dc_offset_i=dc_i,
            dc_offset_q=dc_q,
            sample_completeness=completeness,
            metadata_valid=meta_valid,
            metadata_issues=list(meta_issues),  # defensive copy
            is_valid=is_valid,
            failed_checks=failed,
        )

        # --- Step 4 — Structured log for observability -----------------
        # We log a summary, not the whole report, to keep log lines
        # readable. The caller has the full report in hand if it needs
        # to log more or store it.
        log.info(
            "quality.check.done",
            is_valid=is_valid,
            n_failed=len(failed),
            clipping=clipping,
            rms_dbfs=rms_dbfs,
            completeness=completeness,
        )

        return report

