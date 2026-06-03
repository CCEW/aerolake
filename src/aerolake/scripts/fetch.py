"""CLI to fetch a stored capture down to a LOCAL .sigmf-data file (ADR-007/012).

This is the **bridge** between the lakehouse and GNU Radio: the transmit/playback
flowgraphs read a *local* file (raw cf32_le), not S3. ``aerolake-fetch`` pulls a
capture (whole, or just a time window via an HTTP Range read) out of MinIO and
writes it to disk as ``<out>.sigmf-data`` (+ a ``.sigmf-meta`` JSON sidecar so a
human can read the acquisition parameters).

Typical flow for real RF re-emission (ADR-012):

    uv run aerolake-fetch --key iridium/2026-06-02/1198dcdf/capture.sigmf-data \\
        --out /tmp/capture.sigmf-data --duration 30
    # then, with system GNU Radio + a BladeRF:
    #   set capture_file/samp_rate/freq in gnuradio/transmit_sdr.grc and run it.

Exit codes
----------
0 : Fetched successfully (or nothing matched the prefix).
1 : Storage layer failure (MinIO unreachable, bucket missing, key absent…).
2 : Configuration / unexpected error.

Usage
-----
    uv run aerolake-fetch --key <data_key> --out /tmp/capture.sigmf-data
    uv run aerolake-fetch --prefix iridium/ --out /tmp/cap.sigmf-data --start 200 --duration 30
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
from rich.console import Console
from rich.table import Table

from aerolake.common.logging import configure_logging
from aerolake.common.storage import StorageClient, StorageError
from aerolake.consumer.reader import CaptureReader


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aerolake-fetch",
        description="Download a capture (whole or a window) to a local .sigmf-data file.",
    )
    # Either an explicit key, or a prefix (whose most recent capture we fetch).
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--key", help="Exact .sigmf-data key to fetch.")
    target.add_argument(
        "--prefix", help="Fetch the most recent complete capture under this prefix."
    )
    parser.add_argument(
        "--out",
        default="/tmp/capture.sigmf-data",
        help="Local output path for the raw cf32_le data (default /tmp/capture.sigmf-data).",
    )
    parser.add_argument(
        "--start",
        type=float,
        default=0.0,
        help="Start offset in seconds — partial read, e.g. --start 200 (default 0).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Window length in seconds (default: to the end — the WHOLE capture).",
    )
    return parser


def _resolve_key(reader: CaptureReader, args: argparse.Namespace) -> str | None:
    """Return the capture key to fetch, or None if a prefix matched nothing."""
    if args.key:
        return args.key
    captures = reader.list_captures(prefix=args.prefix)
    # Keys sort by {signal_type}/{YYYY-MM-DD}/… so the last is the most recent.
    return captures[-1] if captures else None


def _meta_path(out_path: str) -> str:
    """Derive the sidecar .sigmf-meta path from the data output path."""
    if out_path.endswith(".sigmf-data"):
        return out_path[: -len(".sigmf-data")] + ".sigmf-meta"
    return out_path + ".sigmf-meta"


def main(argv: list[str] | None = None, *, reader: CaptureReader | None = None) -> int:
    """Entry point. ``reader`` is injectable for testing (moto-backed)."""
    args = _build_parser().parse_args(argv)
    configure_logging()  # logs to stderr; stdout stays clean
    console = Console()

    # Build the reader (and its StorageClient) unless one was injected.
    if reader is None:
        try:
            reader = CaptureReader(StorageClient())
        except Exception as exc:
            console.print(f"[bold red]✗ Configuration error:[/] {exc}")
            return 2

    # Figure out which capture to fetch.
    try:
        key = _resolve_key(reader, args)
    except StorageError as exc:
        console.print(f"[bold red]✗ Storage error:[/] {exc}")
        return 1
    if key is None:
        console.print(f"[yellow]No captures found under prefix {args.prefix!r}.[/]")
        return 0

    # Whole capture (read) vs a time window (read_segment, HTTP Range). We only
    # take the partial path when the user actually narrows the window, so the
    # common "give me everything" case stays a plain read.
    windowed = args.start > 0.0 or args.duration is not None
    console.print(
        f"[bold cyan]>[/] Fetching [bold]{key}[/] "
        f"({'window ' + f'{args.start:g}s+{args.duration:g}s' if windowed else 'whole capture'}) "
        f"→ [bold]{args.out}[/]…"
    )

    try:
        if windowed:
            content = reader.read_segment(key, start_s=args.start, duration_s=args.duration)
        else:
            content = reader.read(key)
    except StorageError as exc:
        console.print(f"[bold red]✗ Storage error:[/] {exc}")
        return 1
    except (ValueError, KeyError) as exc:
        console.print(f"[bold red]✗ {exc}[/]")
        return 2
    except Exception as exc:
        console.print(f"[bold red]✗ Unexpected error:[/] {exc}")
        return 2

    # Write the samples as raw cf32_le — exactly what GNU Radio's File Source
    # reads as "complex". ascontiguousarray guards against a strided/odd view.
    data_bytes = np.ascontiguousarray(content.samples, dtype="<c8").tobytes()
    try:
        with open(args.out, "wb") as f:
            f.write(data_bytes)
        meta_out = _meta_path(args.out)
        with open(meta_out, "w", encoding="utf-8") as f:
            json.dump(content.sigmf_meta, f, indent=2)
    except OSError as exc:
        console.print(f"[bold red]✗ Could not write output:[/] {exc}")
        return 2

    # Pull the acquisition parameters back out so the user can paste them into
    # the flowgraph variables (samp_rate / freq).
    glob = content.sigmf_meta.get("global", {})
    captures = content.sigmf_meta.get("captures", [{}])
    sample_rate = float(glob.get("core:sample_rate", 0.0))
    center_freq = float(captures[0].get("core:frequency", 0.0)) if captures else 0.0

    console.print("[bold green]✓ Capture fetched[/]")
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("Data file", args.out)
    table.add_row("Meta file", meta_out)
    table.add_row("Samples", f"{len(content.samples):,}")
    table.add_row("Bytes", f"{len(data_bytes):,} ({len(data_bytes) / 1024 / 1024:.2f} MiB)")
    table.add_row("Sample rate", f"{sample_rate / 1e6:.6f} MS/s  (samp_rate = {sample_rate:g})")
    table.add_row("Center freq", f"{center_freq / 1e6:.6f} MHz  (freq = {center_freq:g})")
    console.print(table)
    console.print(
        "[dim]Next: set capture_file / samp_rate / freq in "
        "gnuradio/transmit_sdr.grc (or playback.grc) and run it with system GNU Radio.[/]"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
