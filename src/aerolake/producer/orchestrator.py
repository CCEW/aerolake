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

import getpass
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from aerolake.common.storage import StorageClient
from aerolake.producer.sigmf_writer import EncodableSignal, encode
from aerolake.producer.soapy_source import (
    SdrCapture,
    SoapyParams,
    capture_from_sdr,
)
from aerolake.producer.synthetic import SyntheticParams, generate_tone

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
    source: SyntheticParams | SoapyParams | None = None,
    signal_type_detail: str | None = None,
    operator: str | None = None,
    location: str | None = None,
    mobile: bool = False,
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

    # Operator defaults to the session account. Each user has their own login,
    # so this identifies "who recorded" with no manual entry and no typos.
    if operator is None:
        try:
            operator = getpass.getuser()
        except Exception:
            operator = "unknown"

    # --- Session identification ------------------------------------------
    # 8 hex chars = ~4 billion possibilities, plenty for our usage.
    # We also embed the UTC date in the path so listing per-day is trivial.
    session_id = uuid.uuid4().hex[:8]
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
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
    # Acquisition source: the *type* of `source` selects the path. Both
    # generate_tone and capture_from_sdr return an object exposing the same
    # four attributes the encoder reads (samples, sample_rate, center_freq,
    # description), so everything downstream is source-agnostic.
    # Annotated as the Protocol so mypy accepts either concrete type
    # (SyntheticSignal or SdrCapture) assigned in the branches below.
    signal: EncodableSignal
    if source is None:
        source = SyntheticParams()

    if isinstance(source, SoapyParams):
        signal = capture_from_sdr(
            duration_s=duration_s,
            sample_rate=sample_rate,
            center_freq=center_freq,
            driver=source.driver,
            gain=source.gain,
            antenna=source.antenna,
        )
        recorder = "aerolake-producer-soapy"
        hardware = signal.driver
    else:
        signal = generate_tone(
            duration_s=duration_s,
            sample_rate=sample_rate,
            center_freq=center_freq,
            tone_offset_hz=source.tone_offset_hz,
            snr_db=source.snr_db,
            seed=source.seed,
        )
        recorder = "aerolake-producer-synthetic"
        hardware = "synthetic"
    log.info(
        "producer.capture.generated",
        sample_count=len(signal.samples),
        size_bytes=signal.samples.nbytes,
    )

    # --- 2. Encode -------------------------------------------------------
    capture = encode(
        signal,
        signal_type=signal_type,
        signal_type_detail=signal_type_detail,
        operator=operator,
        location=location,
        mobile=mobile,
    )
    log.info(
        "producer.capture.encoded",
        data_bytes=len(capture.data_bytes),
        meta_bytes=len(capture.meta_bytes),
    )

# --- 3. Build metadata and tags --------------------------------------
    # HTTP metadata: technical values needed by the consumer to interpret
    # the bytes. Accessible via HEAD without downloading anything.
    # Stored as x-amz-meta-* headers.
    data_metadata = {
        "sample-rate": str(int(signal.sample_rate)),
        "center-freq": str(int(signal.center_freq)),
        "session-id": session_id,
        "datatype": "cf32_le",
        "sample-count": str(len(signal.samples)),
    }

    # S3 tags: categorical attributes for search and lifecycle policies.
    # Quality starts as "raw"; the consumer-side validation step promotes it to
    # "validated" or "rejected" (see ADR-005 for the promotion lifecycle).
    data_tags = {
        "signal-type": signal_type,
        "operator": operator,
        "mobile": "true" if mobile else "false",
        "recorder": recorder,
        "hardware": hardware,
        "quality": "raw",
    }
    # Real captures carry extra hardware provenance for fine-grained search:
    # which physical device, what gain, which antenna port produced the data.
    if isinstance(signal, SdrCapture):
        data_tags["sdr-serial"] = signal.serial
        data_tags["sdr-gain"] = f"{signal.gain:.1f}"
        data_tags["sdr-antenna"] = signal.antenna

    # --- 4. Upload -------------------------------------------------------
    # Meta is uploaded first: if a consumer races between the two puts, it
    # sees the .sigmf-meta JSON (interpretable on its own) rather than
    # orphan bytes. The .sigmf-data carries both metadata and tags; the
    # .sigmf-meta does not (the JSON itself is the description).
    client.upload_bytes(
        meta_key,
        capture.meta_bytes,
        content_type="application/json",
    )
    client.upload_bytes(
        data_key,
        capture.data_bytes,
        content_type="application/octet-stream",
        metadata=data_metadata,
        tags=data_tags,
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
