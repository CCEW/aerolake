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

# Captures are stored in the datatype they were recorded in (ADR-024), so the
# reader has to decode every format the lake accepts, not just cf32_le. Each
# entry maps a SigMF datatype to the numpy dtype of its *scalars* (I and Q are
# interleaved), the bytes one complex sample occupies, and the divisor that
# brings integer scalars back to the normalised [-1, 1) float range.
_SIGMF_INTERLEAVED: dict[str, tuple[np.dtype, int, float]] = {
    "ci8": (np.dtype("i1"), 2, 128.0),
    "ci8_le": (np.dtype("i1"), 2, 128.0),
    "cu8": (np.dtype("u1"), 2, 127.5),
    "cu8_le": (np.dtype("u1"), 2, 127.5),
    "ci16_le": (np.dtype("<i2"), 4, 32768.0),
    "ci16": (np.dtype("<i2"), 4, 32768.0),
    "cs16": (np.dtype("<i2"), 4, 32768.0),
    "ci32_le": (np.dtype("<i4"), 8, 2147483648.0),
    "ci32": (np.dtype("<i4"), 8, 2147483648.0),
    "cs32": (np.dtype("<i4"), 8, 2147483648.0),
    "cf64_le": (np.dtype("<f8"), 16, 1.0),
    "cf64": (np.dtype("<f8"), 16, 1.0),
}


def _bytes_per_sample(datatype: str) -> int:
    """Bytes occupied by one complex sample of ``datatype``."""
    if datatype in _SIGMF_DTYPE_TO_NUMPY:
        return int(_SIGMF_DTYPE_TO_NUMPY[datatype].itemsize)
    if datatype in _SIGMF_INTERLEAVED:
        return _SIGMF_INTERLEAVED[datatype][1]
    raise ValueError(
        f"Unsupported SigMF datatype: {datatype!r}. "
        f"Supported: {sorted(set(_SIGMF_DTYPE_TO_NUMPY) | set(_SIGMF_INTERLEAVED))}"
    )


def _decode_samples(data_bytes: bytes, datatype: str) -> np.ndarray:
    """Decode raw capture bytes into complex64 samples.

    ``cf32_le`` is already complex float32 and is reinterpreted in place. The
    interleaved formats are read as scalar pairs and rescaled to [-1, 1) so a
    caller sees the same normalised complex64 whatever the stored datatype is.
    """
    if datatype in _SIGMF_DTYPE_TO_NUMPY:
        return np.frombuffer(data_bytes, dtype=_SIGMF_DTYPE_TO_NUMPY[datatype])

    scalar_dtype, _, scale = _SIGMF_INTERLEAVED[datatype]
    scalars = np.frombuffer(data_bytes, dtype=scalar_dtype)
    # Drop a trailing half-sample rather than letting the reshape fail: a Range
    # request can land mid-sample at the very end of an object.
    scalars = scalars[: len(scalars) - (len(scalars) % 2)]
    floats = scalars.astype(np.float32)
    if datatype in {"cu8", "cu8_le"}:
        floats = floats - 127.5  # unsigned: midpoint is 127.5, not 0
    if scale != 1.0:
        floats = floats / scale
    return (floats[0::2] + 1j * floats[1::2]).astype(np.complex64)


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
        _bytes_per_sample(datatype)  # rejects unsupported datatypes early

        # Now the actual samples.
        data_bytes = self._storage.download_bytes(data_key)
        samples = _decode_samples(data_bytes, datatype)

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
        # 8 for cf32_le; 4 for ci16_le, 2 for cu8, ... — the byte maths below
        # works for any stored datatype as long as this is right.
        bytes_per_sample = _bytes_per_sample(datatype)

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
            samples = np.empty(0, dtype=np.complex64)
        else:
            start_byte = start_sample * bytes_per_sample
            end_byte = start_byte + n_samples * bytes_per_sample - 1  # inclusive
            data_bytes = self._storage.download_range(data_key, start_byte, end_byte)
            samples = _decode_samples(data_bytes, datatype)

        log.info(
            "consumer.read_segment.ok",
            start_sample=start_sample,
            n_samples=len(samples),
            total_samples=total_samples,
        )
        return CaptureContent(samples=samples, sigmf_meta=sigmf_meta, info=info)
