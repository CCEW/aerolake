"""Ingest an EXISTING IQ recording into the lakehouse.

The producer's :mod:`orchestrator` *generates* a synthetic capture and uploads
it. This module does the other half: take a **file already on disk** (e.g. a
``.sigmf-data`` recorded by GNU Radio, or a raw IQ dump from an SDR tool) and
push it into MinIO as a proper SigMF capture — writing the ``.sigmf-meta`` and
attaching the metadata/tags, following the exact same key layout and
conventions as the synthetic producer (ADR-003).

Two things make this the "real data" entry point:

1. **Datatype conversion.** SDRs dump different raw types — RTL-SDR gives
   ``cu8`` (unsigned 8-bit), many tools give ``cs16`` (signed 16-bit), GNU
   Radio's File Sink gives ``cf32`` (complex float32). We normalise everything
   to **cf32 in [-1, 1]** on the way in, so the whole lake is homogeneous and
   the quality metrics (which assume normalised cf32) just work.

2. **Streaming multipart upload.** We read the file and upload it **in chunks**
   via :meth:`StorageClient.upload_multipart`, so a capture larger than RAM is
   never fully loaded — the ADR-010 path, finally used by a real caller.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import structlog
from sigmf import SigMFFile

from aerolake.common.storage import StorageClient
from aerolake.producer.iqengine import (
    render_iqengine_minimap_from_files,
    render_iqengine_thumbnail_jpeg_from_files,
)
from aerolake.producer.preview import render_spectrum_jpeg
from aerolake.producer.sigmf_writer import SIGMF_VERSION

logger = structlog.get_logger(__name__)

# Supported source datatypes -> (numpy dtype of the raw file, bytes per complex
# sample). Each raw complex sample is two interleaved scalars (I, Q).
_SOURCE_DTYPES: dict[str, tuple[np.dtype, int]] = {
    "cf32": (np.dtype("<c8"), 8),  # complex float32 LE — already our target
    "cu8": (np.dtype("u1"), 2),  # unsigned 8-bit (RTL-SDR), I,Q interleaved
    "cs16": (np.dtype("<i2"), 4),  # signed 16-bit LE, I,Q interleaved
    "ci16_le": (np.dtype("<i2"), 4),  # signed int16 LE, I,Q interleaved
    "cs32": (np.dtype("<i4"), 8),  # signed 32-bit LE (e.g. RFSoC), I,Q interleaved
}

# cf32 datatype string we store everywhere (SigMF spec).
_TARGET_DATATYPE = "cf32_le"
_IQENGINE_PREVIEW_SAMPLES = 2_000_000
_LOCAL_TZ = ZoneInfo("America/Montreal")
_IQENGINE_SIDECARS = (
    (".jpg", "image/jpeg"),
    (".preview.jpg", "image/jpeg"),
    (".minimap", "application/octet-stream"),
)


@dataclass(frozen=True)
class IngestResult:
    """Outcome of ingesting one file."""

    session_id: str
    data_key: str
    meta_key: str
    sidecar_keys: tuple[str, ...]
    sample_count: int
    bytes_uploaded: int


def _iter_cf32_chunks(file_path: str, datatype: str, chunk_samples: int) -> Iterator[bytes]:
    """Yield the file as **cf32 byte chunks**, converting from the source type.

    Reads ``chunk_samples`` complex samples at a time so memory stays bounded.
    For ``cf32`` the bytes pass through untouched; for ``cu8``/``cs16`` each
    chunk is rescaled to the normalised [-1, 1] complex64 range.
    """
    src_dtype, bytes_per_sample = _SOURCE_DTYPES[datatype]
    read_size = chunk_samples * bytes_per_sample

    with open(file_path, "rb") as f:
        while True:
            raw = f.read(read_size)
            if not raw:
                break
            if datatype == "cf32":
                # Already complex float32 LE — nothing to convert.
                yield raw
                continue
            # Integer types: interleaved scalars -> normalised complex64 [-1, 1).
            scalars = np.frombuffer(raw, dtype=src_dtype)
            if datatype == "cu8":
                # 0..255, midpoint 127.5 -> map to roughly [-1, 1].
                floats = (scalars.astype(np.float32) - 127.5) / 127.5
            elif datatype in {"cs16", "ci16_le"}:
                floats = scalars.astype(np.float32) / 32768.0  # 2**15
            else:  # cs32
                floats = scalars.astype(np.float32) / 2147483648.0  # 2**31
            iq = (floats[0::2] + 1j * floats[1::2]).astype(np.complex64)
            yield iq.tobytes()


def _iter_cf32_files(file_paths: list[str], datatype: str, chunk_samples: int) -> Iterator[bytes]:
    """Stream several files in order as one continuous cf32 byte stream.

    Used to ingest a capture split into many packet files (e.g. the RFSoC's
    ``RX0_pkt_*.bin``): concatenated in the given order, they form one recording.
    """
    for path in file_paths:
        yield from _iter_cf32_chunks(path, datatype, chunk_samples)


def _load_preview_samples(
    file_paths: list[str],
    datatype: str,
    max_samples: int = _IQENGINE_PREVIEW_SAMPLES,
) -> np.ndarray:
    """Load a bounded cf32 preview slice from one or more source files."""
    chunks: list[np.ndarray] = []
    remaining = max_samples
    for chunk in _iter_cf32_files(file_paths, datatype, chunk_samples=min(remaining, 1_000_000)):
        samples = np.frombuffer(chunk, dtype="<c8")
        if len(samples) > remaining:
            samples = samples[:remaining]
        chunks.append(samples)
        remaining -= len(samples)
        if remaining <= 0:
            break
    if not chunks:
        return np.array([], dtype=np.complex64)
    return np.concatenate(chunks).astype(np.complex64, copy=False)


def _local_sidecar_base(file_paths: list[str]) -> Path | None:
    """Return the local base path used for sidecar reuse, only for one input file."""
    if len(file_paths) != 1:
        return None
    path = Path(file_paths[0])
    for suffix in (".sigmf-data", ".sigmf-meta"):
        if str(path).endswith(suffix):
            return Path(str(path)[: -len(suffix)])
    return path.with_suffix("")


def _generate_iqengine_artifacts(
    *,
    file_paths: list[str],
    datatype: str,
    sample_rate: float,
    center_freq: float,
) -> dict[str, bytes]:
    samples = _load_preview_samples(file_paths, datatype)
    return {
        ".jpg": render_iqengine_thumbnail_jpeg_from_files(file_paths, datatype),
        ".preview.jpg": render_spectrum_jpeg(samples, sample_rate, center_freq),
        ".minimap": render_iqengine_minimap_from_files(file_paths, datatype),
    }


def _load_or_generate_iqengine_artifacts(
    *,
    file_paths: list[str],
    datatype: str,
    sample_rate: float,
    center_freq: float,
    mode: str,
) -> dict[str, bytes]:
    local_base = _local_sidecar_base(file_paths)
    artifacts: dict[str, bytes] = {}
    generated: dict[str, bytes] | None = None

    for suffix, _ in _IQENGINE_SIDECARS:
        local_path = local_base.with_suffix(suffix) if local_base else None
        if mode == "reuse" and local_path and local_path.exists():
            artifacts[suffix] = local_path.read_bytes()
            continue

        if generated is None:
            generated = _generate_iqengine_artifacts(
                file_paths=file_paths,
                datatype=datatype,
                sample_rate=sample_rate,
                center_freq=center_freq,
            )
        artifacts[suffix] = generated[suffix]
        if local_path:
            local_path.write_bytes(generated[suffix])

    return artifacts


def _iqengine_mode(iqengine: bool | str) -> str | None:
    if not iqengine:
        return None
    if iqengine is True:
        return "reuse"
    if iqengine in {"reuse", "redo"}:
        return str(iqengine)
    raise ValueError("iqengine must be False, True, 'reuse', or 'redo'")


def _upload_iqengine_artifacts(
    client: StorageClient,
    *,
    base_key: str,
    file_paths: list[str],
    datatype: str,
    sample_rate: float,
    center_freq: float,
    mode: str,
) -> tuple[tuple[str, ...], int]:
    """Generate and upload IQEngine artifacts next to the SigMF pair.

    Returns the uploaded keys and the number of artifact bytes uploaded.
    """
    artifacts = _load_or_generate_iqengine_artifacts(
        file_paths=file_paths,
        datatype=datatype,
        sample_rate=sample_rate,
        center_freq=center_freq,
        mode=mode,
    )
    uploaded: list[tuple[str, bytes]] = []
    for suffix, content_type in _IQENGINE_SIDECARS:
        key = f"{base_key}{suffix}"
        data = artifacts[suffix]
        client.upload_bytes(
            key,
            data,
            content_type=content_type,
            metadata={"role": "iqengine-artifact"},
        )
        uploaded.append((key, data))
    return tuple(key for key, _ in uploaded), sum(len(data) for _, data in uploaded)


def ingest_file(
    *,
    file_path: str,
    **kwargs: Any,
) -> IngestResult:
    """Ingest a single raw IQ file (thin wrapper over :func:`ingest_files`)."""
    return ingest_files(file_paths=[file_path], **kwargs)


def ingest_files(
    *,
    file_paths: list[str],
    signal_type: str,
    sample_rate: float,
    center_freq: float,
    datatype: str = "cf32",
    hardware: str = "unknown",
    recorder: str = "aerolake-ingest",
    description: str | None = None,
    iqengine: bool | str = False,
    storage_client: StorageClient | None = None,
    chunk_samples: int = 1_000_000,
) -> IngestResult:
    """Ingest one or more raw IQ files into MinIO as a single SigMF capture.

    Several files are concatenated **in the given order** into one continuous
    recording — the way the RFSoC packet dumps (``RX0_pkt_*.bin``) form one
    capture. The data is streamed via multipart upload, so total size is not
    bounded by RAM.

    Parameters
    ----------
    file_paths
        Ordered list of raw IQ files (a single-element list for one file).
    signal_type
        Bucket prefix / tag (e.g. ``gnss_l1``, ``iridium``).
    sample_rate, center_freq
        Acquisition parameters (Hz) — go into the SigMF metadata.
    datatype
        Source datatype: ``cf32`` (default), ``cu8``, ``cs16`` or ``cs32``.
        Anything but cf32 is converted to normalised cf32 on the way in.
    hardware
        Goes into the ``hardware`` tag + SigMF ``core:hw`` (e.g. ``rfsoc``).
    iqengine
        Generate and upload ``capture.jpg`` and ``capture.minimap`` beside the
        SigMF pair.
    storage_client
        Injected for tests; defaults to a fresh client.
    chunk_samples
        How many complex samples to read/upload per chunk (bounds memory).
    """
    if datatype not in _SOURCE_DTYPES:
        raise ValueError(
            f"Unsupported source datatype {datatype!r}. Supported: {sorted(_SOURCE_DTYPES)}"
        )
    if not file_paths:
        raise ValueError("ingest_files: file_paths is empty")

    client = storage_client or StorageClient()
    _, bytes_per_sample = _SOURCE_DTYPES[datatype]

    # Total complex samples = sum of file sizes / bytes-per-sample.
    total_size = sum(os.path.getsize(p) for p in file_paths)
    sample_count = total_size // bytes_per_sample

    # --- Key layout (same human-readable folder style as capture) --------
    session_id = uuid.uuid4().hex[:8]
    now_utc = datetime.now(UTC)
    now_local = now_utc.astimezone(_LOCAL_TZ)
    date_str = now_utc.strftime("%Y-%m-%d")
    stamp = now_local.strftime("%Y-%m-%d_%Hh%Mm%S")
    hardware_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", hardware).strip("_") or "unknown"
    folder = f"{stamp}_{hardware_label}_{session_id}"
    base_key = f"{signal_type}/{date_str}/{folder}/capture"
    data_key = f"{base_key}.sigmf-data"
    meta_key = f"{base_key}.sigmf-meta"

    log = logger.bind(
        n_files=len(file_paths),
        signal_type=signal_type,
        session_id=session_id,
        datatype=datatype,
        sample_count=sample_count,
    )
    log.info("ingest.start", total_size=total_size)

    # --- Build + validate the SigMF metadata (always cf32_le on our side) --
    if description is None:
        description = (
            f"Ingested {datatype} capture at {sample_rate / 1e6:.3f} MS/s, "
            f"{center_freq / 1e6:.3f} MHz"
        )
    global_meta: dict[str, object] = {
        "core:datatype": _TARGET_DATATYPE,
        "core:sample_rate": float(sample_rate),
        "core:author": "AeroLake",
        "core:description": description,
        "core:recorder": recorder,
        "core:hw": hardware,
        "core:version": SIGMF_VERSION,
        "core:num_channels": 1,
        "core:offset": 0,
    }
    metadata = {
        "global": global_meta,
        "captures": [
            {
                "core:sample_start": 0,
                "core:frequency": float(center_freq),
                "core:datetime": datetime.now(UTC).isoformat(),
            }
        ],
        "annotations": [],
    }
    # Fail fast if the metadata is malformed (before any upload).
    SigMFFile(metadata=metadata).validate()
    meta_bytes = json.dumps(metadata, indent=2, sort_keys=True).encode("utf-8")

    # --- Upload: meta first, then data (streamed via multipart) -----------
    # Same ordering rule as the producer: a consumer racing between the two
    # objects sees interpretable JSON, not orphan bytes.
    client.upload_bytes(meta_key, meta_bytes, content_type="application/json")

    data_metadata = {
        "sample-rate": str(int(sample_rate)),
        "center-freq": str(int(center_freq)),
        "session-id": session_id,
        "datatype": _TARGET_DATATYPE,
        "sample-count": str(sample_count),
    }
    data_tags = {
        "signal-type": signal_type,
        "recorder": recorder,
        "hardware": hardware,
    }

    # Hash the .sigmf-data WHILE it streams, so a multi-GB capture is hashed in
    # the same single pass it is uploaded (no extra read of the files).
    hasher = hashlib.sha512()

    def _hashed_chunks() -> Iterator[bytes]:
        for chunk in _iter_cf32_files(file_paths, datatype, chunk_samples):
            hasher.update(chunk)
            yield chunk

    data_bytes_uploaded = client.upload_multipart(
        data_key,
        _hashed_chunks(),
        content_type="application/octet-stream",
        metadata=data_metadata,
        tags=data_tags,
    )

    # Fold the computed hash into the metadata (core:sha512) and rewrite the
    # small meta object. The meta still lands before the data; this only
    # enriches it once the full hash is known.
    global_meta["core:sha512"] = hasher.hexdigest()
    meta_bytes = json.dumps(metadata, indent=2, sort_keys=True).encode("utf-8")
    client.upload_bytes(meta_key, meta_bytes, content_type="application/json")

    sidecar_keys: tuple[str, ...] = ()
    sidecar_bytes = 0
    iqengine_mode = _iqengine_mode(iqengine)
    if iqengine_mode:
        sidecar_keys, sidecar_bytes = _upload_iqengine_artifacts(
            client,
            base_key=base_key,
            file_paths=file_paths,
            datatype=datatype,
            sample_rate=sample_rate,
            center_freq=center_freq,
            mode=iqengine_mode,
        )

    log.info(
        "ingest.done",
        data_key=data_key,
        bytes=data_bytes_uploaded,
        sidecars=len(sidecar_keys),
    )
    return IngestResult(
        session_id=session_id,
        data_key=data_key,
        meta_key=meta_key,
        sidecar_keys=sidecar_keys,
        sample_count=sample_count,
        bytes_uploaded=len(meta_bytes) + data_bytes_uploaded + sidecar_bytes,
    )
