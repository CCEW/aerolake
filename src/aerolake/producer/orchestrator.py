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
from pathlib import Path
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
class PreparedCapture:
    """A captured, encoded SigMF recording ready to push or save — not yet stored.

    This is the output of :func:`prepare_capture`: everything needed to either
    upload to MinIO (:func:`push_capture`) or write to disk
    (:func:`save_capture_locally`), plus a human-readable summary so a CLI can
    show the operator what was recorded *before* deciding to keep it. Splitting
    "prepare" from "push" is what makes a validate-before-upload confirmation
    possible: the bytes exist in memory, but nothing has been stored yet.

    Attributes
    ----------
    session_id
        Short unique identifier for this capture (8-char hex).
    data_key, meta_key
        The S3 keys the capture would occupy (also reused as the relative path
        when saving locally).
    data_bytes, meta_bytes
        The encoded ``.sigmf-data`` and ``.sigmf-meta`` payloads.
    data_metadata, data_tags
        HTTP metadata and S3 tags to attach to the data object on upload.
    sample_count
        Number of complex IQ samples captured.
    overflow_count
        Stream overflows during capture (0 = clean; None = synthetic / N/A).
    """

    session_id: str
    data_key: str
    meta_key: str
    data_bytes: bytes
    meta_bytes: bytes
    data_metadata: dict[str, str]
    data_tags: dict[str, str]
    sample_count: int
    overflow_count: int | None

    @property
    def size_bytes(self) -> int:
        """Total payload size (data + meta), for the pre-push summary."""
        return len(self.data_bytes) + len(self.meta_bytes)


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


def prepare_capture(
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
) -> PreparedCapture:
    """Acquire, encode, and assemble a capture — without storing anything.

    Runs acquisition (synthetic or real SDR), SigMF encoding, and metadata/tag
    assembly, then returns a :class:`PreparedCapture` holding the bytes and the
    keys they would occupy. Nothing is uploaded or written to disk here, so a
    caller can inspect the result and decide whether to keep it.

    Parameters mirror :func:`capture_and_upload` (minus the storage client).
    """
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

    # S3 tags: categorical attributes for search (ADR-003). Indexable on the
    # MinIO side without downloading the .sigmf-data body.
    data_tags = {
        "signal-type": signal_type,
        "operator": operator,
        "mobile": "true" if mobile else "false",
        "recorder": recorder,
        "hardware": hardware,
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

    return PreparedCapture(
        session_id=session_id,
        data_key=data_key,
        meta_key=meta_key,
        data_bytes=capture.data_bytes,
        meta_bytes=capture.meta_bytes,
        data_metadata=data_metadata,
        data_tags=data_tags,
        sample_count=len(signal.samples),
        overflow_count=overflow_count,
    )


def push_capture(
    prepared: PreparedCapture,
    storage_client: StorageClient | None = None,
) -> CaptureResult:
    """Upload an already-prepared capture to MinIO.

    The second half of the split: takes the bytes/keys/tags from
    :func:`prepare_capture` and performs the two uploads. Kept separate so a
    caller can interpose a confirmation step between preparing and pushing.
    """
    client = storage_client or StorageClient()

    # --- Upload ----------------------------------------------------------
    # Meta is uploaded first: if a consumer races between the two puts, it
    # sees the .sigmf-meta JSON (interpretable on its own) rather than
    # orphan bytes. The .sigmf-data carries both metadata and tags; the
    # .sigmf-meta does not (the JSON itself is the description).
    client.upload_bytes(
        prepared.meta_key,
        prepared.meta_bytes,
        content_type="application/json",
    )
    client.upload_bytes(
        prepared.data_key,
        prepared.data_bytes,
        content_type="application/octet-stream",
        metadata=prepared.data_metadata,
        tags=prepared.data_tags,
    )
    logger.info(
        "producer.capture.uploaded",
        data_key=prepared.data_key,
        meta_key=prepared.meta_key,
    )
    return CaptureResult(
        session_id=prepared.session_id,
        data_key=prepared.data_key,
        meta_key=prepared.meta_key,
        sample_count=prepared.sample_count,
        bytes_uploaded=prepared.size_bytes,
    )


def save_capture_locally(
    prepared: PreparedCapture,
    root: str | Path = "captures",
) -> Path:
    """Write a prepared capture to disk instead of (or before) uploading.

    Mirrors the MinIO key layout under ``root`` so a locally-kept capture has
    the same self-describing folder name as it would in the bucket. Returns the
    directory the two SigMF files were written to.

    Used as the "keep it on disk" branch when the operator declines the push:
    the capture (possibly a long one) is not lost, and can be ingested later.
    """
    # data_key looks like "<signal>/<date>/<folder>/capture.sigmf-data"; reuse
    # that relative path under the local root so layout matches the bucket.
    rel_dir = Path(prepared.data_key).parent
    out_dir = Path(root) / rel_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    data_path = out_dir / Path(prepared.data_key).name
    meta_path = out_dir / Path(prepared.meta_key).name
    data_path.write_bytes(prepared.data_bytes)
    meta_path.write_bytes(prepared.meta_bytes)

    logger.info("producer.capture.saved_local", path=str(out_dir))
    return out_dir


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
    """Capture and upload in one call (prepare + push).

    A convenience for callers that want the whole cycle at once (used by tests).
    The file-driven ``aerolake-capture`` uses :func:`prepare_capture` and
    :func:`push_capture` separately so it can ask for confirmation before storing.
    """
    prepared = prepare_capture(
        signal_type=signal_type,
        duration_s=duration_s,
        sample_rate=sample_rate,
        center_freq=center_freq,
        source=source,
        signal_type_detail=signal_type_detail,
        operator=operator,
        location=location,
        mobile=mobile,
        rich=rich,
    )
    return push_capture(prepared, storage_client)
