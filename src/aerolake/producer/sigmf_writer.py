"""SigMF encoding for AeroLake.

Takes raw IQ samples plus metadata and produces the two byte streams that
form a SigMF capture: the binary samples (``<name>.sigmf-data``) and the
JSON metadata (``<name>.sigmf-meta``).

These bytes are ready to be uploaded as separate S3 objects via the
StorageClient. We do not write temporary files to disk — everything
happens in memory, which keeps the pipeline stateless and efficient.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

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

    # --- 2. Build the metadata dict per SigMF v1.0.0 spec ------------------
    # AeroLake namespace: lab-specific, discovery-oriented fields. SigMF leaves
    # domain fields open; we prefix ours with "aerolake:" so they coexist with
    # core: without clashing. Duration is always derived (never hand-entered).
    # Optional fields are omitted when absent, so "no location" is a real
    # absence rather than an empty string.
    sample_rate = float(signal.sample_rate)
    duration_s = len(signal.samples) / sample_rate if sample_rate > 0 else 0.0
    global_block: dict[str, object] = {
        "core:datatype": SIGMF_DATATYPE_CF32_LE,
        "core:sample_rate": sample_rate,
        "core:author": author,
        "core:description": signal.description,
        "core:recorder": recorder,
        "core:hw": hardware,
        "core:version": SIGMF_VERSION,
        "aerolake:duration_s": duration_s,
        "aerolake:sample_count": len(signal.samples),
    }
    if signal_type is not None:
        global_block["aerolake:signal_type"] = signal_type
    if signal_type_detail is not None:
        global_block["aerolake:signal_type_detail"] = signal_type_detail
    if operator is not None:
        global_block["aerolake:operator"] = operator
    if location is not None:
        global_block["aerolake:location"] = location
    if mobile is not None:
        global_block["aerolake:mobile"] = bool(mobile)

    # Declare the aerolake namespace as a SigMF extension. The spec requires
    # any custom "<name>:" field to be declared here; without this, current
    # SigMF warns and future versions reject the file. optional=True: readers
    # that don't know the extension can still decode the IQ.
    global_block["core:extensions"] = [
        {"name": "aerolake", "version": "1.0.0", "optional": True}
    ]

    metadata = {
        "global": global_block,
        "captures": [
            {
                "core:sample_start": 0,
                "core:frequency": float(signal.center_freq),
                "core:datetime": datetime.now(UTC).isoformat(),
            }
        ],
        "annotations": [],
    }

    # --- 3. Validate against the SigMF schema ------------------------------
    # The library raises if the structure or required fields are wrong.
    # Catching mistakes here means we never write a non-compliant capture
    # to MinIO.
    sigmf_file = SigMFFile(metadata=metadata)
    sigmf_file.validate()

    # --- 4. Serialize metadata as human-readable JSON ----------------------
    # indent=2 + sort_keys=True makes the output diff-friendly and
    # readable when inspecting an object in the MinIO console.
    meta_bytes = json.dumps(
        metadata,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")

    return SigMFCapture(data_bytes=data_bytes, meta_bytes=meta_bytes)
