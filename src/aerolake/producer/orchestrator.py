"""High-level orchestration for AeroLake captures.

Wraps three steps into a single function:
  1. Signal acquisition (currently synthetic; later SoapySDR / real SDRs)
  2. SigMF encoding (samples + metadata -> bytes)
  3. Upload to MinIO via the StorageClient

Produces a deterministic key layout in the bucket:

    {signal_type}/{YYYY-MM-DD}/{session_id}/capture.sigmf-data
    {signal_type}/{YYYY-MM-DD}/{session_id}/capture.sigmf-meta

This module is the entry point used by the producer CLI.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import structlog

from aerolake.common.storage import StorageClient
from aerolake.producer.sigmf_writer import encode
from aerolake.producer.synthetic import generate_tone

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class CaptureResult:
    """Outcome of a single capture-and-upload cycle.

    Attributes
    ----------
    session_id
        Short unique identifier for this capture (8-char hex).
    data_key
        S3 key where the ``.sigmf-data`` blob was uploaded.
    meta_key
        S3 key where the ``.sigmf-meta`` JSON was uploaded.
    sample_count
        Number of complex IQ samples generated.
    bytes_uploaded
        Total bytes uploaded (data + meta).
    """

    session_id: str
    data_key: str
    meta_key: str
    sample_count: int
    bytes_uploaded: int


def capture_and_upload(
    *,
    signal_type: str,
    duration_s: float,
    sample_rate: float,
    center_freq: float,
    tone_offset_hz: float = 100_000.0,
    snr_db: float = 20.0,
    seed: int | None = None,
    storage_client: StorageClient | None = None,
) -> CaptureResult:
    """Generate a synthetic capture and upload it as SigMF in MinIO.

    Parameters
    ----------
    signal_type
        Short identifier used as the top-level prefix in the bucket
        (e.g. ``gnss_l1``, ``iridium``, ``starlink``).
    duration_s
        Capture duration in seconds.
    sample_rate
        Sample rate in Hz.
    center_freq
        Center frequency in Hz (used for SigMF metadata).
    tone_offset_hz, snr_db, seed
        Forwarded to :func:`generate_tone`.
    storage_client
        Optional injected StorageClient for tests; defaults to a fresh
        instance using the configured environment.

    Returns
    -------
    CaptureResult
    """
    client = storage_client or StorageClient()

    # --- Session identification ------------------------------------------
    # 8 hex chars = ~4 billion possibilities, plenty for our usage.
    # We also embed the UTC date in the path so listing per-day is trivial.
    session_id = uuid.uuid4().hex[:8]
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base_key = f"{signal_type}/{date_str}/{session_id}/capture"
    data_key = f"{base_key}.sigmf-data"
    meta_key = f"{base_key}.sigmf-meta"

    log = logger.bind(
        signal_type=signal_type,
        session_id=session_id,
        sample_rate=sample_rate,
        center_freq=center_freq,
    )
    log.info("producer.capture.start", duration_s=duration_s)

    # --- 1. Generate -----------------------------------------------------
    signal = generate_tone(
        duration_s=duration_s,
        sample_rate=sample_rate,
        center_freq=center_freq,
        tone_offset_hz=tone_offset_hz,
        snr_db=snr_db,
        seed=seed,
    )
    log.info(
        "producer.capture.generated",
        sample_count=len(signal.samples),
        size_bytes=signal.samples.nbytes,
    )

    # --- 2. Encode -------------------------------------------------------
    capture = encode(signal)
    log.info(
        "producer.capture.encoded",
        data_bytes=len(capture.data_bytes),
        meta_bytes=len(capture.meta_bytes),
    )

    # --- 3. Upload -------------------------------------------------------
    # We upload the metadata FIRST. If a race-condition consumer sees the
    # data, the meta is guaranteed to be there already and the capture is
    # interpretable. The reverse order would expose orphan bytes briefly.
    client.upload_bytes(
        meta_key,
        capture.meta_bytes,
        content_type="application/json",
    )
    client.upload_bytes(
        data_key,
        capture.data_bytes,
        content_type="application/octet-stream",
    )
    log.info(
        "producer.capture.uploaded",
        data_key=data_key,
        meta_key=meta_key,
    )

    return CaptureResult(
        session_id=session_id,
        data_key=data_key,
        meta_key=meta_key,
        sample_count=len(signal.samples),
        bytes_uploaded=len(capture.data_bytes) + len(capture.meta_bytes),
    )
