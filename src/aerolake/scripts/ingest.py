"""CLI to ingest an existing IQ recording into the lakehouse.

Takes a raw IQ file on disk (e.g. recorded by GNU Radio's record_sdr.grc, or a
raw SDR dump) and uploads it to MinIO as a SigMF capture — converting to cf32
if needed and streaming the data via multipart upload (ADR-010). This is the
"real data" entry point that complements the config-driven ``aerolake-capture``.

Exit codes
----------
0 : Ingested successfully.
1 : Storage layer failure (MinIO unreachable, bucket missing, etc.).
2 : Configuration / usage error (missing file, bad datatype, etc.).

Usage
-----
    # A cf32 file recorded by GNU Radio:
    uv run aerolake-ingest /tmp/capture.sigmf-data \
        --signal-type gnss_l1 --sample-rate 2e6 --center-freq 1575.42e6 \
        --hardware bladerf
    # A raw RTL-SDR dump (unsigned 8-bit):
    uv run aerolake-ingest dump.iq --signal-type iridium \
        --sample-rate 2e6 --center-freq 1626e6 --datatype cu8 --hardware rtlsdr
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys

from rich.console import Console
from rich.table import Table

from aerolake.common.logging import configure_logging
from aerolake.common.storage import StorageClient, StorageError
from aerolake.producer.ingest import IngestResult, ingest_files


def _natural_key(path: str) -> tuple[int, str]:
    """Sort key from the last integer in a filename (RX0_pkt_2 before _10)."""
    nums = re.findall(r"\d+", os.path.basename(path))
    return (int(nums[-1]) if nums else -1, path)


def _resolve_files(path: str, glob_pat: str) -> list[str]:
    """Return the list of files to ingest: one file, or a sorted directory."""
    if os.path.isdir(path):
        return sorted(glob.glob(os.path.join(path, glob_pat)), key=_natural_key)
    if os.path.isfile(path):
        return [path]
    return []


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aerolake-ingest",
        description="Ingest an existing IQ file into MinIO as a SigMF capture.",
    )
    parser.add_argument(
        "path",
        help="A raw IQ file, OR a directory of packet files (concatenated in "
        "numeric order; select them with --glob).",
    )
    parser.add_argument(
        "--glob",
        default="*.bin",
        help="When path is a directory, which files to ingest (default '*.bin').",
    )
    parser.add_argument(
        "--signal-type", required=True, help="Signal type / prefix (e.g. gnss_l1)."
    )
    parser.add_argument(
        "--sample-rate", type=float, required=True, help="Sample rate in Hz."
    )
    parser.add_argument(
        "--center-freq", type=float, required=True, help="Center frequency in Hz."
    )
    parser.add_argument(
        "--datatype",
        choices=["cf32", "cu8", "cs16", "cs32"],
        default="cf32",
        help="Source datatype (default cf32; cu8/cs16/cs32 are converted to cf32).",
    )
    parser.add_argument(
        "--hardware", default="unknown", help="Hardware tag (e.g. bladerf, rfsoc)."
    )
    return parser


def main(argv: list[str] | None = None, *, storage_client: StorageClient | None = None) -> int:
    """Entry point. ``storage_client`` is injectable for testing (moto-backed)."""
    args = _build_parser().parse_args(argv)
    configure_logging()
    console = Console()

    # Resolve the input (a single file, or a sorted directory of packets).
    files = _resolve_files(args.path, args.glob)
    if not files:
        console.print(f"[bold red]✗ No file(s) found at:[/] {args.path}")
        return 2

    what = files[0] if len(files) == 1 else f"{len(files)} files in {args.path}"
    console.print(
        f"[bold cyan]>[/] Ingesting [bold]{what}[/] as "
        f"[bold]{args.signal_type}[/] ({args.datatype}, "
        f"{args.sample_rate / 1e6:.3f} MS/s @ {args.center_freq / 1e6:.3f} MHz)…"
    )

    try:
        result: IngestResult = ingest_files(
            file_paths=files,
            signal_type=args.signal_type,
            sample_rate=args.sample_rate,
            center_freq=args.center_freq,
            datatype=args.datatype,
            hardware=args.hardware,
            storage_client=storage_client,
        )
    except StorageError as exc:
        console.print(f"[bold red]✗ Storage error:[/] {exc}")
        return 1
    except (ValueError, OSError) as exc:
        console.print(f"[bold red]✗ {exc}[/]")
        return 2
    except Exception as exc:
        console.print(f"[bold red]✗ Unexpected error:[/] {exc}")
        return 2

    console.print("[bold green]✓ Capture ingested[/]")
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("Session ID", result.session_id)
    table.add_row("Data key", result.data_key)
    table.add_row("Meta key", result.meta_key)
    table.add_row("Samples", f"{result.sample_count:,}")
    table.add_row(
        "Uploaded",
        f"{result.bytes_uploaded:,} bytes "
        f"({result.bytes_uploaded / 1024 / 1024:.2f} MiB)",
    )
    console.print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
