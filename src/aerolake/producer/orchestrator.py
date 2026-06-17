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
from zoneinfo import ZoneInfo

import structlog

from aerolake.common.storage import StorageClient
from aerolake.producer.sigmf_writer import (
    AnnotationFields,
    AntennaFields,
    EncodableSignal,
    encode,
)
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


@dataclass(frozen=True)
class RichMetadata:
    """Optional descriptive metadata threaded from a capture config to SigMF.

    Groups the fields that are not needed to *perform* a capture but enrich the
    stored ``.sigmf-meta`` (and, for a couple of them, the searchable S3 tags).
    Kept neutral on purpose: the orchestrator stays unaware of the CLI-level
    CaptureConfig: the CLI builds this object and hands it over. Every field is
    optional; absent ones are simply not written.

    Attributes
    ----------
    author, description, license
        SigMF ``core:`` Global fields (free text / URL).
    geolocation
        RFC 7946 GeoJSON Point, written to the capture segment.
    annotation
        Flattened annotation fields (label/comment/freq edges + antenna
        pointing) for the single whole-capture annotation segment.
    antenna
        Flattened scalar fields of the SigMF ``antenna:`` extension (Global).
    """

    author: str | None = None
    description: str | None = None
    license: str | None = None
    geolocation: dict[str, object] | None = None
    annotation: AnnotationFields | None = None
    antenna: AntennaFields | None = None


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
    rich: RichMetadata | None = None,
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

    # Resolve the source early so its label can go into the human-readable
    # folder name below (and so acquisition uses the same resolved object).
    if source is None:
        source = SyntheticParams()
    source_label = source.driver if isinstance(source, SoapyParams) else "synthetic"

    # --- Session identification ------------------------------------------
    # session_id stays an opaque unique id (used in logs, tags, CaptureResult).
    # The folder name is made human-readable: <HHMMSS>_<source>_<session_id>,
    # so listings sort chronologically and say at a glance what/when/which SDR.
    # 8 hex chars = ~4 billion possibilities; the id keeps folders collision-free.
    now_utc = datetime.now(UTC)
    now_local = now_utc.astimezone(ZoneInfo("America/Montreal"))
    session_id = uuid.uuid4().hex[:8]
    # Parent folder date stays UTC for stable, timezone-independent ordering.
    date_str = now_utc.strftime("%Y-%m-%d")
    # Leaf folder is fully self-describing and uses LOCAL time so it matches
    # the operator's wall clock: <YYYY-MM-DD>_<HHhMMmSS>_<source>_<id>.
    stamp = now_local.strftime("%Y-%m-%d_%Hh%Mm%S")
    folder = f"{stamp}_{source_label}_{session_id}"
    base_key = f"{signal_type}/{date_str}/{folder}/capture"
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
    if isinstance(source, SoapyParams):
        signal = capture_from_sdr(
            duration_s=duration_s,
            sample_rate=sample_rate,
            center_freq=center_freq,
            driver=source.driver,
            agc=source.agc,
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
    # Real captures carry full hardware provenance; synthetic ones don't.
    hardware_info = signal.hardware_info if isinstance(signal, SdrCapture) else None
    overflow_count = signal.overflow_count if isinstance(signal, SdrCapture) else None
    rich = rich or RichMetadata()
    capture = encode(
        signal,
        author=rich.author if rich.author is not None else "AeroLake",
        recorder=recorder,
        hardware=hardware,
        signal_type=signal_type,
        signal_type_detail=signal_type_detail,
        operator=operator,
        location=location,
        mobile=mobile,
        hardware_info=hardware_info,
        overflow_count=overflow_count,
        description=rich.description,
        license=rich.license,
        geolocation=rich.geolocation,
        annotation=rich.annotation,
        antenna=rich.antenna,
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

    # Promote the two richest search criteria to S3 tags so captures are
    # filterable without downloading the .sigmf-meta: where it was recorded,
    # and with which antenna. Everything else stays in the .sigmf-meta. S3
    # caps objects at 10 tags; these two keep us within budget.
    if location:
        data_tags["location"] = location
    if rich.antenna and rich.antenna.get("model"):
        data_tags["antenna-model"] = str(rich.antenna["model"])

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
