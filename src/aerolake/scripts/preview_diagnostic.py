"""Render the current AeroLake preview for a local IQ file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from rich.console import Console
from rich.table import Table

from aerolake.common.logging import configure_logging
from aerolake.producer.ingest import _SOURCE_DTYPES, _iter_cf32_chunks
from aerolake.producer.preview import render_spectrum_png

_PREVIEW_SAMPLES = 2_000_000


def _load_samples(path: Path, datatype: str, max_samples: int = _PREVIEW_SAMPLES) -> np.ndarray:
    chunks: list[np.ndarray] = []
    remaining = max_samples
    for chunk in _iter_cf32_chunks(str(path), datatype, chunk_samples=min(remaining, 1_000_000)):
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aerolake-preview-diagnostic",
        description="Render the current AeroLake preview for one IQ file.",
    )
    parser.add_argument("path", help="Local raw IQ file to inspect.")
    parser.add_argument("--sample-rate", type=float, required=True, help="Sample rate in Hz.")
    parser.add_argument("--center-freq", type=float, required=True, help="Center frequency in Hz.")
    parser.add_argument(
        "--datatype",
        choices=sorted(_SOURCE_DTYPES),
        default="cf32",
        help="Source datatype, matching aerolake-ingest.",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Directory for rendered PNGs. Defaults to the IQ file directory.",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="Output filename prefix. Defaults to the IQ file stem.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    configure_logging()
    console = Console()

    path = Path(args.path)
    if not path.is_file():
        console.print(f"[bold red]x Missing IQ file:[/] {path}")
        return 2

    out_dir = Path(args.out_dir) if args.out_dir else path.parent
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        samples = _load_samples(path, args.datatype)
        if len(samples) == 0:
            raise ValueError("No complete IQ samples were read.")

        prefix = args.prefix or path.stem
        aerolake_path = out_dir / f"{prefix}.aerolake-preview.png"
        aerolake_path.write_bytes(render_spectrum_png(samples, args.sample_rate, args.center_freq))
    except (OSError, ValueError) as exc:
        console.print(f"[bold red]x Failed:[/] {exc}")
        return 2

    table = Table(show_header=False, box=None)
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("Samples rendered", f"{len(samples):,}")
    table.add_row("AeroLake preview", str(aerolake_path))
    console.print("[bold green]Preview diagnostics generated[/]")
    console.print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
