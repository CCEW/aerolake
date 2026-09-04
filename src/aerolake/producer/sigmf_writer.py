"""SigMF encoding for AeroLake.

Takes raw IQ samples plus metadata and produces the two byte streams that
form a SigMF capture: the binary samples (``<name>.sigmf-data``) and the
JSON metadata (``<name>.sigmf-meta``).

These bytes are ready to be uploaded as separate S3 objects via the
StorageClient. We do not write temporary files to disk — everything
happens in memory, which keeps the pipeline stateless and efficient.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, TypedDict

import numpy as np
import sigmf
from sigmf import SigMFFile


class EncodableSignal(Protocol):
    """Minimal interface the encoder needs from any acquisition source.

    Both :class:`aerolake.producer.synthetic.SyntheticSignal` and
    :class:`aerolake.producer.soapy_source.SdrCapture` satisfy this — the
    encoder reads only these four attributes, so it stays agnostic to whether
    the samples are synthetic or captured from real hardware.
    """

    @property
    def samples(self) -> np.ndarray: ...
    @property
    def sample_rate(self) -> float: ...
    @property
    def center_freq(self) -> float: ...
    @property
    def description(self) -> str: ...


class AnnotationFields(TypedDict, total=False):
    """Flattened annotation fields the encoder writes into ``annotations``.

    All optional. The encoder emits an annotation segment covering the whole
    capture only if at least one of these is present. ``label``/``comment`` and
    the ``freq_*_edge`` pair are core SigMF; the ``antenna:*`` keys belong in
    Annotations per the spec (not the Global antenna block), so they ride here.
    """

    label: str
    comment: str
    freq_lower_edge: float
    freq_upper_edge: float
    polarization: str
    azimuth_angle: float
    elevation_angle: float


class AntennaFields(TypedDict, total=False):
    """Flattened scalar fields of the SigMF ``antenna:`` extension (Global).

    All optional except that, by construction upstream, ``model`` is always
    present when an antenna block exists (the spec's required field). The
    encoder maps each key to ``antenna:<key>`` in the Global Object and
    declares the ``antenna`` extension in ``core:extensions``.
    """

    model: str
    type: str
    low_frequency: float
    high_frequency: float
    gain: float
    horizontal_beam_width: float
    vertical_beam_width: float
    cross_polar_discrimination: float
    voltage_standing_wave_ratio: float
    cable_loss: float
    steerable: bool
    mobile: bool
    hagl: float


# SigMF datatype string for np.complex64.
# Format: complex float 32-bit little-endian. See SigMF spec, "Datatypes".
SIGMF_DATATYPE_CF32_LE = "cf32_le"

# SigMF *specification* version we declare in core:version. We derive it from
# the installed sigmf library (its __specification__) rather than hard-coding a
# string, so what we write always matches the spec the tooling implements.
SIGMF_VERSION: str = getattr(sigmf, "__specification__", "1.2.6")


@dataclass(frozen=True)
class SigMFCapture:
    """A SigMF capture ready to be uploaded as two S3 objects.

    Attributes
    ----------
    data_bytes
        Raw binary IQ samples, contents of the ``<name>.sigmf-data`` object.
    meta_bytes
        JSON-encoded metadata (UTF-8), contents of the ``<name>.sigmf-meta``
        object.
    """

    data_bytes: bytes
    meta_bytes: bytes


def build_metadata(
    *,
    sample_count: int,
    sample_rate: float,
    center_freq: float,
    capture_datetime: str,
    data_sha512: str | None = None,
    author: str = "AeroLake",
    description: str = "AeroLake IQ capture",
    recorder: str = "aerolake-ingest",
    hardware: str = "unknown",
    signal_type: str | None = None,
    operator: str | None = None,
    location: str | None = None,
    mobile: bool = False,
    signal_type_detail: str | None = None,
    hardware_info: dict[str, str] | None = None,
    overflow_count: int | None = None,
    license: str | None = None,
    geolocation: dict[str, object] | None = None,
    annotation: AnnotationFields | None = None,
    antenna: AntennaFields | None = None,
) -> dict[str, object]:
    """Build the canonical AeroLake SigMF metadata structure.

    Capture and ingest both use this function. The data hash is optional while
    a streamed upload is in progress and is added before the final meta upload.
    """
    global_block: dict[str, object] = {
        "core:datatype": SIGMF_DATATYPE_CF32_LE,
        "core:sample_rate": float(sample_rate),
        "core:author": author,
        "core:description": description,
        "core:recorder": recorder,
        "core:hw": hardware,
        "core:version": SIGMF_VERSION,
        "core:num_channels": 1,
        "core:offset": 0,
        "aerolake:duration_s": sample_count / sample_rate if sample_rate > 0 else 0.0,
        "aerolake:sample_count": sample_count,
        "aerolake:operator": author,
        "aerolake:mobile": bool(mobile),
    }
    if data_sha512 is not None:
        global_block["core:sha512"] = data_sha512
    if signal_type is not None:
        global_block["aerolake:signal_type"] = signal_type
    if signal_type_detail is not None:
        global_block["aerolake:signal_type_detail"] = signal_type_detail
    if location is not None:
        global_block["aerolake:location"] = location
    if hardware_info:
        global_block["aerolake:hardware_info"] = hardware_info
    if overflow_count is not None:
        global_block["aerolake:overflow_count"] = overflow_count
    if license is not None:
        global_block["core:license"] = license

    if antenna:
        for key, value in antenna.items():
            if value is not None:
                global_block[f"antenna:{key}"] = value

    antenna_in_annotation = annotation is not None and any(
        key in annotation for key in ("polarization", "azimuth_angle", "elevation_angle")
    )
    extensions: list[dict[str, object]] = [
        {"name": "aerolake", "version": "1.0.0", "optional": True}
    ]
    if antenna or antenna_in_annotation:
        extensions.append({"name": "antenna", "version": "1.0.0", "optional": True})
    global_block["core:extensions"] = extensions

    capture_segment: dict[str, object] = {
        "core:sample_start": 0,
        "core:frequency": float(center_freq),
        "core:datetime": capture_datetime,
    }
    if geolocation is not None:
        capture_segment["core:geolocation"] = geolocation

    annotations: list[dict[str, object]] = []
    if annotation:
        annotation_segment: dict[str, object] = {
            "core:sample_start": 0,
            "core:sample_count": sample_count,
        }
        if "label" in annotation:
            annotation_segment["core:label"] = annotation["label"]
        if "comment" in annotation:
            annotation_segment["core:comment"] = annotation["comment"]
        if "freq_lower_edge" in annotation and "freq_upper_edge" in annotation:
            annotation_segment["core:freq_lower_edge"] = annotation["freq_lower_edge"]
            annotation_segment["core:freq_upper_edge"] = annotation["freq_upper_edge"]
        for key in ("polarization", "azimuth_angle", "elevation_angle"):
            if key in annotation:
                annotation_segment[f"antenna:{key}"] = annotation[key]
        annotations.append(annotation_segment)

    metadata: dict[str, object] = {
        "global": global_block,
        "captures": [capture_segment],
        "annotations": annotations,
    }
    SigMFFile(metadata=metadata).validate()
    return metadata


def missing_canonical_fields(metadata: dict[str, object]) -> list[str]:
    """Return required canonical fields absent from an existing SigMF pair."""
    global_meta = metadata.get("global")
    captures = metadata.get("captures")
    first_capture = captures[0] if isinstance(captures, list) and captures else None
    missing: list[str] = []
    required_global = (
        "core:datatype",
        "core:sample_rate",
        "core:author",
        "core:description",
        "core:recorder",
        "core:hw",
        "core:version",
        "core:num_channels",
        "core:offset",
        "core:extensions",
        "aerolake:signal_type",
        "aerolake:operator",
        "aerolake:mobile",
        "aerolake:duration_s",
        "aerolake:sample_count",
    )
    if not isinstance(global_meta, dict):
        return [f"global.{field}" for field in required_global] + [
            "captures[0].core:sample_start",
            "captures[0].core:frequency",
            "captures[0].core:datetime",
        ]
    missing.extend(
        f"global.{field}"
        for field in required_global
        if field not in global_meta or _is_missing_placeholder(global_meta[field])
    )
    if not isinstance(first_capture, dict):
        missing.extend(
            [
                "captures[0].core:sample_start",
                "captures[0].core:frequency",
                "captures[0].core:datetime",
            ]
        )
    else:
        missing.extend(
            f"captures[0].{field}"
            for field in ("core:sample_start", "core:frequency", "core:datetime")
            if field not in first_capture or _is_missing_placeholder(first_capture[field])
        )
    annotations = metadata.get("annotations")
    if (
        "annotations" not in metadata
        or _is_missing_placeholder(annotations)
        or (isinstance(annotations, list) and not annotations)
    ):
        missing.append("annotations")
    return missing


def _is_missing_placeholder(value: object) -> bool:
    """Recognize placeholders written into an incomplete local metadata file."""
    return isinstance(value, str) and value.startswith("<missing:") and value.endswith(">")


def complete_canonical_metadata(
    metadata: dict[str, object],
    *,
    sample_count: int | None = None,
) -> list[str]:
    """Fill safe defaults and mark unresolved canonical fields for user editing."""
    global_meta = metadata.setdefault("global", {})
    if not isinstance(global_meta, dict):
        global_meta = {}
        metadata["global"] = global_meta
    captures = metadata.setdefault("captures", [])
    if not isinstance(captures, list):
        captures = []
        metadata["captures"] = captures
    if not captures or not isinstance(captures[0], dict):
        captures[:] = [{}]
    first_capture = captures[0]

    sample_rate = global_meta.get("core:sample_rate")
    center_freq = first_capture.get("core:frequency")
    description = "AeroLake IQ capture"
    if isinstance(sample_rate, (int, float)) and isinstance(center_freq, (int, float)):
        description = (
            f"Ingested capture at {float(sample_rate) / 1e6:.3f} MS/s, "
            f"{float(center_freq) / 1e6:.3f} MHz"
        )
    defaults: dict[str, object] = {
        "core:author": "AeroLake",
        "core:description": description,
        "core:recorder": "aerolake-ingest",
        "core:hw": "unknown",
        "core:version": SIGMF_VERSION,
        "core:num_channels": 1,
        "core:offset": 0,
        "core:extensions": [{"name": "aerolake", "version": "1.0.0", "optional": True}],
        "aerolake:operator": str(global_meta.get("core:author") or "AeroLake"),
        "aerolake:mobile": False,
        "aerolake:signal_type": "<missing:aerolake:signal_type>",
    }
    for field, value in defaults.items():
        if field not in global_meta or _is_missing_placeholder(global_meta[field]):
            global_meta[field] = value
    if _is_missing_placeholder(global_meta.get("core:description")):
        global_meta["core:description"] = description
    if _is_missing_placeholder(global_meta.get("aerolake:operator")):
        global_meta["aerolake:operator"] = str(global_meta.get("core:author") or "AeroLake")
    if sample_count is not None:
        if "aerolake:sample_count" not in global_meta or _is_missing_placeholder(
            global_meta["aerolake:sample_count"]
        ):
            global_meta["aerolake:sample_count"] = sample_count
        if isinstance(sample_rate, (int, float)) and sample_rate > 0:
            if "aerolake:duration_s" not in global_meta or _is_missing_placeholder(
                global_meta["aerolake:duration_s"]
            ):
                global_meta["aerolake:duration_s"] = sample_count / float(sample_rate)
        else:
            if "aerolake:duration_s" not in global_meta or _is_missing_placeholder(
                global_meta["aerolake:duration_s"]
            ):
                global_meta["aerolake:duration_s"] = "<missing:aerolake:duration_s>"
    else:
        global_meta.setdefault("aerolake:sample_count", "<missing:aerolake:sample_count>")
        global_meta.setdefault("aerolake:duration_s", "<missing:aerolake:duration_s>")

    first_capture.setdefault("core:sample_start", 0)
    # core:datetime is when the SIGNAL was recorded, which ingest cannot know:
    # it always runs after the fact, so "now" would be the ingest time, not the
    # capture time. Since this value also picks the bucket's date prefix, a
    # fabricated timestamp files the capture under the wrong day permanently —
    # so an unresolved value stays a placeholder for the operator to fill.
    first_capture.setdefault("core:datetime", "<missing:captures[0].core:datetime>")
    first_capture.setdefault("core:frequency", "<missing:captures[0].core:frequency>")
    metadata.setdefault("annotations", [])
    return missing_canonical_fields(metadata)


def encode(
    signal: EncodableSignal,
    *,
    author: str = "AeroLake",
    recorder: str = "aerolake-producer-synthetic",
    hardware: str = "synthetic",
    signal_type: str | None = None,
    signal_type_detail: str | None = None,
    operator: str | None = None,
    location: str | None = None,
    mobile: bool | None = None,
    hardware_info: dict[str, str] | None = None,
    overflow_count: int | None = None,
    description: str | None = None,
    license: str | None = None,
    geolocation: dict[str, object] | None = None,
    annotation: AnnotationFields | None = None,
    antenna: AntennaFields | None = None,
) -> SigMFCapture:
    """Encode a SyntheticSignal into SigMF byte streams.

    Validates the metadata against the SigMF schema before returning, so
    any structural mistake fails fast here rather than silently producing
    a non-compliant capture on MinIO.

    Parameters
    ----------
    signal
        Output of one of the generators in :mod:`aerolake.producer.synthetic`.
    author
        Goes into ``global.core:author`` (free text).
    recorder
        Goes into ``global.core:recorder`` (identifies the software).
    hardware
        Goes into ``global.core:hw`` (identifies the device).

    Returns
    -------
    SigMFCapture
        Two byte streams ready to upload, with validated metadata.
    """
    # --- 1. Pack the samples to raw bytes ----------------------------------
    # numpy stores complex64 in C order as alternating float32 I and Q.
    # tobytes() gives us exactly the bytes SigMF expects for cf32_le.
    data_bytes = signal.samples.tobytes()

    metadata = build_metadata(
        sample_count=len(signal.samples),
        sample_rate=signal.sample_rate,
        center_freq=signal.center_freq,
        capture_datetime=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        data_sha512=hashlib.sha512(data_bytes).hexdigest(),
        author=author,
        description=description if description is not None else signal.description,
        recorder=recorder,
        hardware=hardware,
        signal_type=signal_type,
        operator=operator if operator is not None else "unknown",
        location=location,
        mobile=mobile if mobile is not None else False,
        signal_type_detail=signal_type_detail,
        hardware_info=hardware_info,
        overflow_count=overflow_count,
        license=license,
        geolocation=geolocation,
        annotation=annotation,
        antenna=antenna,
    )

    # --- 4. Serialize metadata as human-readable JSON ----------------------
    # indent=2 + sort_keys=True makes the output diff-friendly and
    # readable when inspecting an object in the MinIO console.
    meta_bytes = json.dumps(
        metadata,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")

    return SigMFCapture(data_bytes=data_bytes, meta_bytes=meta_bytes)
