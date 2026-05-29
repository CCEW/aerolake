"""Consumer-side reader for AeroLake captures.

Reads SigMF captures from MinIO using the metadata + tagging convention
defined in ADR-003. Two access patterns are supported:

1. Cheap inspection via :meth:`CaptureReader.inspect`: returns metadata
   and tags without downloading the sample bytes. Use this to decide
   whether a capture is worth reading.

2. Full read via :meth:`CaptureReader.read`: downloads both objects
   and returns the decoded samples plus the parsed SigMF metadata.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import structlog

from aerolake.common.storage import StorageClient, StorageError

from aerolake.quality.checker import QualityChecker, QualityReport

logger = structlog.get_logger(__name__)


# Map SigMF datatype strings -> numpy dtypes.
# Extend this when we support more SigMF datatypes.
_SIGMF_DTYPE_TO_NUMPY: dict[str, np.dtype] = {
    "cf32_le": np.dtype("<c8"),  # complex64 little-endian
}


@dataclass(frozen=True)
class CaptureInfo:
    """Lightweight description of a capture, obtained without downloading bytes.

    Attributes
    ----------
    data_key
        Key of the ``.sigmf-data`` object in the bucket.
    metadata
        ``x-amz-meta-*`` headers as a flat dict (keys lowercased,
        prefix stripped). Empty dict if none.
    tags
        S3 tags as a flat dict. Empty dict if none.
    """

    data_key: str
    metadata: dict[str, str]
    tags: dict[str, str]


@dataclass(frozen=True)
class CaptureContent:
    """Fully decoded capture: samples and SigMF metadata.

    Attributes
    ----------
    samples
        Decoded IQ samples as a ``np.ndarray``. Dtype depends on the
        ``core:datatype`` field of the SigMF metadata (cf32_le -> complex64).
    sigmf_meta
        The parsed ``.sigmf-meta`` JSON as a dict.
    info
        The :class:`CaptureInfo` (metadata + tags) for the capture.
    """

    samples: np.ndarray
    sigmf_meta: dict
    info: CaptureInfo


class CaptureReader:
    """High-level reader for SigMF captures stored in MinIO."""

    def __init__(self, storage_client: StorageClient | None = None) -> None:
        self._storage = storage_client or StorageClient()

    # --- Listing ----------------------------------------------------------

    def list_captures(self, prefix: str = "") -> list[str]:
        """Return the data_key of every complete capture under the prefix.

        A capture is considered "complete" if both its ``.sigmf-data`` and
        its ``.sigmf-meta`` are present in the bucket. Orphan files (one
        without the other) are skipped silently.
        """
        log = logger.bind(prefix=prefix)
        all_keys = set(self._storage.list_objects(prefix=prefix))
        data_keys = [k for k in all_keys if k.endswith(".sigmf-data")]
        complete: list[str] = []
        for data_key in data_keys:
            meta_key = data_key[: -len(".sigmf-data")] + ".sigmf-meta"
            if meta_key in all_keys:
                complete.append(data_key)
        log.info(
            "consumer.list_captures",
            total_data=len(data_keys),
            complete=len(complete),
        )
        return sorted(complete)

    # --- Cheap inspection -------------------------------------------------

    def inspect(self, data_key: str) -> CaptureInfo:
        """Return metadata and tags for a capture without downloading bytes.

        Two API calls: head_object (for metadata) + get_object_tagging
        (for tags). Body of the .sigmf-data is never transferred.
        """
        log = logger.bind(data_key=data_key)
        try:
            metadata = self._storage.get_object_metadata(data_key)
            tags = self._storage.get_object_tags(data_key)
        except StorageError:
            log.error("consumer.inspect.failed")
            raise
        log.info("consumer.inspect.ok", n_meta=len(metadata), n_tags=len(tags))
        return CaptureInfo(data_key=data_key, metadata=metadata, tags=tags)

    # --- Full read --------------------------------------------------------

    def read(self, data_key: str) -> CaptureContent:
        """Download and decode a full capture.

        Returns
        -------
        CaptureContent
            Samples as ``np.ndarray`` + parsed SigMF metadata dict + info.

        Raises
        ------
        StorageError
            If either object is missing or unreadable.
        ValueError
            If the SigMF datatype is not supported by this reader.
        """
        log = logger.bind(data_key=data_key)
        meta_key = data_key[: -len(".sigmf-data")] + ".sigmf-meta"

        info = self.inspect(data_key)

        # Download metadata first; parsing it gives us the datatype we
        # need to decode the samples.
        meta_bytes = self._storage.download_bytes(meta_key)
        sigmf_meta = json.loads(meta_bytes.decode("utf-8"))

        datatype = sigmf_meta.get("global", {}).get("core:datatype")
        if datatype not in _SIGMF_DTYPE_TO_NUMPY:
            raise ValueError(
                f"Unsupported SigMF datatype: {datatype!r}. "
                f"Supported: {sorted(_SIGMF_DTYPE_TO_NUMPY)}"
            )
        np_dtype = _SIGMF_DTYPE_TO_NUMPY[datatype]

        # Now the actual samples.
        data_bytes = self._storage.download_bytes(data_key)
        samples = np.frombuffer(data_bytes, dtype=np_dtype)

        log.info(
            "consumer.read.ok",
            sample_count=len(samples),
            datatype=datatype,
            data_bytes=len(data_bytes),
        )
        return CaptureContent(samples=samples, sigmf_meta=sigmf_meta, info=info)

# --- Quality validation -----------------------------------------------

    def validate(
        self,
        data_key: str,
        expected_duration_s: float | None = None,
        checker: QualityChecker | None = None,
        store_report: bool = True,
        promote_tag: bool = True,
    ) -> QualityReport:
        """Read a capture, assess its quality, and promote its quality tag.

        This is the orchestration method that turns a raw capture into a
        curated-dataset decision. The full flow is:

          1. Download + decode the capture (via self.read)
          2. Run every quality metric on it (via QualityChecker)
          3. Optionally store a quality_report.json next to the capture
          4. Promote the MinIO 'quality' tag based on the verdict:
                - report.is_valid is True  -> quality = "validated"
                - report.is_valid is False -> quality = "rejected"
          5. Return the full QualityReport so the caller knows what happened

        The 'quality' tag promotion extends the lifecycle convention from
        ADR-003 (raw -> validated -> archived) with a 'rejected' state, so
        that bad captures are explicitly marked rather than silently left
        as 'raw'. This is what lets us later filter the bucket down to a
        clean, curated subset.

        Parameters
        ----------
        data_key
            Key of the .sigmf-data object to validate.
        expected_duration_s
            The capture duration the producer INTENDED, in seconds. Used to
            compute sample completeness (did we drop samples?). If None, we
            fall back to deriving it from the data itself, which makes the
            completeness check trivially pass — so pass the real intended
            duration whenever you have it for a meaningful check.
        checker
            A pre-configured QualityChecker (with custom thresholds, say).
            If None, a default QualityChecker is created. This is dependency
            injection: it lets tests and callers control the thresholds
            without this method having to know about them.
        store_report
            If True (default), write a quality_report.json artifact next to
            the capture in MinIO. Set to False to skip the upload (e.g. for
            a dry-run validation where you only want the verdict).
        promote_tag
            If True (default), write the verdict back to the capture's MinIO
            'quality' tag (validated/rejected). Set to False to leave the tag
            untouched — combined with store_report=False this makes validate()
            a read-only dry run that computes and returns the verdict without
            mutating anything in the bucket.

        Returns
        -------
        QualityReport
            The full report, including raw metrics and the pass/fail verdict.

        Raises
        ------
        StorageError
            If the capture can't be read or the tag update fails.
        ValueError
            If the SigMF datatype is unsupported (propagated from read()).
        """
        # Bind the data_key to every log line in this method, so a reader
        # of the logs can follow the whole validation for one capture.
        log = logger.bind(data_key=data_key)

        # --- Step 1 — Read and decode the capture --------------------------
        # We reuse our own read() method. This downloads both the .sigmf-data
        # and the .sigmf-meta, and returns a CaptureContent with the decoded
        # samples, the parsed SigMF metadata, and the CaptureInfo.
        content = self.read(data_key)

        # --- Step 2 — Determine the expected duration ----------------------
        # The completeness metric needs to know how many samples we EXPECTED.
        # The honest source for that is the producer's intent, passed in as
        # expected_duration_s. If the caller didn't provide it, we derive a
        # duration from the data itself (sample_count / sample_rate). Note
        # this makes the completeness check always report ~1.0, because we're
        # comparing the data against itself — it can't detect dropouts in
        # that mode. That's why passing the real intended duration matters.
        if expected_duration_s is None:
            # Pull the sample rate from the SigMF global section.
            sample_rate = content.sigmf_meta.get("global", {}).get(
                "core:sample_rate", 0
            )
            if sample_rate > 0:
                # Derive duration from the actual sample count. This is the
                # "trust the data" fallback.
                expected_duration_s = len(content.samples) / sample_rate
            else:
                # No usable sample rate — set 0.0 so the completeness check
                # will fail loudly, and the metadata check will also flag the
                # missing core:sample_rate field.
                expected_duration_s = 0.0

        # --- Step 3 — Run the quality assessment ---------------------------
        # Use the injected checker, or build a default one with standard
        # thresholds. The "or" trick works because QualityChecker() with no
        # args returns a ready-to-use instance.
        checker = checker or QualityChecker()
        report = checker.check(
            samples=content.samples,
            sigmf_meta=content.sigmf_meta,
            expected_duration_s=expected_duration_s,
        )

        # --- Step 4 — Optionally store the report as a JSON artifact -------
        if store_report:
            # Build the report's key: same "folder" as the capture, but a
            # fixed filename. rsplit("/", 1) splits off the last path
            # segment (the "capture.sigmf-data" filename), giving us the
            # session prefix, to which we append our report filename.
            session_prefix = data_key.rsplit("/", 1)[0] + "/"
            report_key = session_prefix + "quality_report.json"

            # Serialize the report dict to pretty-printed JSON bytes.
            # NOTE: if a metric is -inf or NaN (e.g. RMS of a silent or
            # corrupted signal), json.dumps writes it as "Infinity"/"NaN",
            # which is valid for Python's json but not strict JSON. We accept
            # that for now; a future improvement could sanitize those values.
            report_bytes = json.dumps(report.to_dict(), indent=2).encode("utf-8")

            # Upload as a standalone object. content_type tells any future
            # reader (browser, MinIO console) that this is JSON.
            self._storage.upload_bytes(
                report_key,
                report_bytes,
                content_type="application/json",
            )
            log.info("consumer.validate.report_stored", report_key=report_key)

        # The verdict drives the new quality value, regardless of whether we
        # actually write it back below — we compute it here so it can be both
        # logged and (optionally) persisted.
        new_quality = "validated" if report.is_valid else "rejected"

        # --- Step 5 — Promote the quality tag (unless this is a dry run) ----
        if promote_tag:
            # We follow the read -> merge -> write pattern (as validated in our
            # smoke test). update_tags REPLACES the whole tag set, so we must
            # read the existing tags first and merge our change in, otherwise
            # we'd wipe signal-type, hardware, recorder, etc.
            current_tags = self._storage.get_object_tags(data_key)
            merged_tags = dict(current_tags)  # defensive copy before mutating
            merged_tags["quality"] = new_quality
            # Write the merged tag set back to the .sigmf-data object.
            self._storage.update_tags(data_key, merged_tags)

        # --- Step 6 — Log a summary and return -----------------------------
        log.info(
            "consumer.validate.done",
            is_valid=report.is_valid,
            # Make it explicit in the logs when the tag was left untouched, so
            # a dry run can't be mistaken for a real promotion.
            new_quality=new_quality if promote_tag else "(dry-run, tag unchanged)",
            n_failed=len(report.failed_checks),
        )

        return report
