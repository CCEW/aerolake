"""Generate IQEngine sidecar artifacts for a local SigMF recording.

IQEngine's datasource browser expects optional sidecars named after the SigMF
base path:

    recording.jpg
    recording.minimap

The recording itself remains the standard SigMF pair:

    recording.sigmf-data
    recording.sigmf-meta
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from rich.console import Console
from rich.table import Table

from aerolake.common.logging import configure_logging
from aerolake.common.storage import StorageClient, StorageError
from aerolake.producer.ingest import _SOURCE_DTYPES, _iter_cf32_chunks
from aerolake.producer.iqengine import (
    render_iqengine_minimap_from_files,
    render_iqengine_thumbnail_jpeg_from_files,
    supported_iqengine_datatypes,
)
from aerolake.producer.preview import render_spectrum_jpeg

_JPEG_SAMPLES = 2_000_000


def _load_meta(base: Path) -> dict:
    meta_path = base.with_suffix(".sigmf-meta")
    return json.loads(meta_path.read_text())


def _load_preview_samples(data_path: Path, datatype: str) -> np.ndarray:
    preview_datatype = {
        "cf32_le": "cf32",
        "cu8_le": "cu8",
        "ci16": "ci16_le",
        "cs16": "ci16_le",
        "ci32": "cs32",
        "ci32_le": "cs32",
    }.get(datatype, datatype)
    if preview_datatype not in _SOURCE_DTYPES:
        return np.array([], dtype=np.complex64)
    chunks: list[np.ndarray] = []
    remaining = _JPEG_SAMPLES
    for chunk in _iter_cf32_chunks(
        str(data_path),
        preview_datatype,
        chunk_samples=min(remaining, 1_000_000),
    ):
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


def generate_artifacts(base: Path) -> tuple[Path, Path, Path]:
    meta = _load_meta(base)
    data_path = base.with_suffix(".sigmf-data")
    datatype = str(meta["global"]["core:datatype"])
    sample_rate = float(meta["global"].get("core:sample_rate") or 0)
    center_freq = float(meta.get("captures", [{}])[0].get("core:frequency") or 0)
    if datatype not in supported_iqengine_datatypes():
        supported = ", ".join(supported_iqengine_datatypes())
        raise ValueError(f"Unsupported datatype {datatype!r}. Supported: {supported}")

    jpg_path = base.with_suffix(".jpg")
    preview_path = base.with_suffix(".preview.jpg")
    minimap_path = base.with_suffix(".minimap")
    existing_jpg = jpg_path.read_bytes() if jpg_path.exists() else None

    jpg_path.write_bytes(render_iqengine_thumbnail_jpeg_from_files([data_path], datatype))
    if not preview_path.exists():
        if existing_jpg is not None:
            preview_path.write_bytes(existing_jpg)
        else:
            samples = _load_preview_samples(data_path, datatype)
            preview_path.write_bytes(render_spectrum_jpeg(samples, sample_rate, center_freq))
    minimap_path.write_bytes(render_iqengine_minimap_from_files([data_path], datatype))
    return jpg_path, preview_path, minimap_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aerolake-iqengine-artifacts",
        description="Generate IQEngine .jpg and .minimap sidecars for a SigMF pair.",
    )
    parser.add_argument(
        "base",
        help="SigMF base path, with or without .sigmf-data/.sigmf-meta suffix.",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload generated sidecars to the configured S3/MinIO bucket.",
    )
    parser.add_argument(
        "--key-prefix",
        default="",
        help="Optional bucket prefix for upload. For root use the default empty prefix.",
    )
    return parser


def _base_path(value: str) -> Path:
    path = Path(value)
    for suffix in (".sigmf-data", ".sigmf-meta", ".preview.jpg", ".jpg", ".minimap"):
        if str(path).endswith(suffix):
            return Path(str(path)[: -len(suffix)])
    return path


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    configure_logging()
    console = Console()
    base = _base_path(args.base)

    try:
        jpg_path, preview_path, minimap_path = generate_artifacts(base)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        console.print(f"[bold red]x Failed:[/] {exc}")
        return 2

    uploaded: list[str] = []
    if args.upload:
        client = StorageClient()
        prefix = args.key_prefix.strip("/")
        object_base = f"{prefix}/{base.name}" if prefix else base.name
        try:
            client.upload_bytes(f"{object_base}.jpg", jpg_path.read_bytes(), content_type="image/jpeg")
            client.upload_bytes(
                f"{object_base}.preview.jpg",
                preview_path.read_bytes(),
                content_type="image/jpeg",
            )
            client.upload_bytes(
                f"{object_base}.minimap",
                minimap_path.read_bytes(),
                content_type="application/octet-stream",
            )
        except StorageError as exc:
            console.print(f"[bold red]x Upload failed:[/] {exc}")
            return 1
        uploaded = [
            f"{object_base}.jpg",
            f"{object_base}.preview.jpg",
            f"{object_base}.minimap",
        ]

    table = Table(show_header=False, box=None)
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("IQEngine JPEG", str(jpg_path))
    table.add_row("Backup JPEG", str(preview_path))
    table.add_row("Minimap", str(minimap_path))
    if uploaded:
        table.add_row("Uploaded", "\n".join(uploaded))
    console.print("[bold green]Generated IQEngine artifacts[/]")
    console.print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
