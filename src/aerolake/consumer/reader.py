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

    # --- Partial / seeked read --------------------------------------------

    def read_segment(
        self,
        data_key: str,
        start_s: float = 0.0,
        duration_s: float | None = None,
    ) -> CaptureContent:
        """Read only a time window of a capture, via an HTTP Range request.

        This is the **partial-read** feature: to "start at t = 200 s" of a
        one-hour recording, we compute the byte offset and fetch *only* that
        slice from MinIO — the rest of the (potentially multi-GB) capture is
        never downloaded. The maths:

            bytes_per_sample = 8           (cf32: 4-byte I + 4-byte Q)
            start_byte = floor(start_s * sample_rate) * bytes_per_sample
            n_samples  = floor(duration_s * sample_rate)   (or to the end)

        Parameters
        ----------
        data_key
            Key of the ``.sigmf-data`` object.
        start_s
            Offset from the beginning, in seconds (default 0.0).
        duration_s
            Window length in seconds. None (default) reads to the end.

        Returns
        -------
        CaptureContent
            Same shape as :meth:`read`, but ``samples`` holds only the window.
            Out-of-range requests are clamped (an empty array if start_s is past
            the end).
        """
        log = logger.bind(data_key=data_key, start_s=start_s, duration_s=duration_s)
        meta_key = data_key[: -len(".sigmf-data")] + ".sigmf-meta"

        # We still need the metadata (datatype + sample rate) to interpret bytes
        # and to convert seconds -> samples -> bytes. The .sigmf-meta is tiny.
        info = self.inspect(data_key)
        sigmf_meta = json.loads(self._storage.download_bytes(meta_key).decode("utf-8"))

        datatype = sigmf_meta.get("global", {}).get("core:datatype")
        if datatype not in _SIGMF_DTYPE_TO_NUMPY:
            raise ValueError(
                f"Unsupported SigMF datatype: {datatype!r}. "
                f"Supported: {sorted(_SIGMF_DTYPE_TO_NUMPY)}"
            )
        np_dtype = _SIGMF_DTYPE_TO_NUMPY[datatype]
        bytes_per_sample = np_dtype.itemsize  # 8 for complex64

        sample_rate = float(sigmf_meta.get("global", {}).get("core:sample_rate", 0.0))
        if sample_rate <= 0:
            raise ValueError(f"Cannot seek without a valid core:sample_rate in {data_key!r}")

        # How many samples exist in total (size on disk / bytes per sample)?
        total_samples = self._storage.object_size(data_key) // bytes_per_sample

        # Convert the time window to a sample window, clamped to what exists.
        start_sample = min(max(0, int(start_s * sample_rate)), total_samples)
        if duration_s is None:
            n_samples = total_samples - start_sample
        else:
            n_samples = min(max(0, int(duration_s * sample_rate)), total_samples - start_sample)

        if n_samples <= 0:
            # Window is empty (e.g. start past the end) — return no samples.
            samples = np.empty(0, dtype=np_dtype)
        else:
            start_byte = start_sample * bytes_per_sample
            end_byte = start_byte + n_samples * bytes_per_sample - 1  # inclusive
            data_bytes = self._storage.download_range(data_key, start_byte, end_byte)
            samples = np.frombuffer(data_bytes, dtype=np_dtype)

        log.info(
            "consumer.read_segment.ok",
            start_sample=start_sample,
            n_samples=len(samples),
            total_samples=total_samples,
        )
        return CaptureContent(samples=samples, sigmf_meta=sigmf_meta, info=info)
