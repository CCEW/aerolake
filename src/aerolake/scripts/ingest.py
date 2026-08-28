"""CLI to ingest an existing IQ recording into the lakehouse.

With metadata flags, this command treats the input as raw IQ, creates a fresh
SigMF meta object, normalizes integer datatypes to cf32, and uploads both files.
With no metadata flags, it expects an existing same-basename .sigmf-meta,
checks the pair, adds a missing SHA-512, and normalizes ``ci16_le`` data to
the lake's canonical ``cf32_le`` format before uploading. ``--ensure-sha512``
is retained as a compatibility option; hashes are now always checked.

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
    # Generate meta, annotate it with iridium-toolkit, then upload:
    uv run aerolake-ingest test.sigmf-data --signal-type iridium \
        --sample-rate 10e6 --center-freq 1622e6 --datatype ci16_le \
        --iridium-annotate
    # An existing SigMF pair:
    uv run aerolake-ingest capture.sigmf-data --iqengine
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from datetime import datetime

from rich.console import Console
from rich.table import Table

from aerolake.common.logging import configure_logging
from aerolake.common.storage import StorageClient, StorageError
from aerolake.producer.ingest import IngestResult, ingest_files, ingest_sigmf_pair


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
    parser.add_argument("--signal-type", help="Signal type / prefix (e.g. gnss_l1).")
    parser.add_argument("--sample-rate", type=float, help="Sample rate in Hz.")
    parser.add_argument("--center-freq", type=float, help="Center frequency in Hz.")
    parser.add_argument(
        "--datatype",
        choices=["cf32", "cu8", "cs16", "ci16_le", "cs32"],
        default=None,
        help="Source datatype (default cf32; integer IQ datatypes are converted to cf32).",
    )
    parser.add_argument("--hardware", help="Hardware tag (e.g. bladerf, rfsoc).")
    parser.add_argument(
        "--iqengine",
        nargs="?",
        const="reuse",
        choices=["reuse", "redo"],
        help="Upload IQEngine artifacts beside the SigMF pair. With no value, reuse "
        "local .jpg/.preview.jpg/.minimap if present and generate missing files. "
        "Use '--iqengine redo' to regenerate local sidecars before upload.",
    )
    parser.add_argument(
        "--ensure-sha512",
        action="store_true",
        help="Compatibility option; existing-pair ingest always checks and adds "
        "global.core:sha512. The local .sigmf-meta file is not modified.",
    )
    parser.add_argument(
        "--iridium-annotate",
        action="store_true",
        help="Run iridium-extractor piped into iridium-toolkit's parser to append "
        "annotations before upload (generated or existing-pair mode).",
    )
    parser.add_argument(
        "--iridium-parser",
        default="~/iridium-toolkit/iridium-parser.py",
        help="Path to iridium-toolkit's iridium-parser.py "
        "(default ~/iridium-toolkit/iridium-parser.py).",
    )
    parser.add_argument(
        "--iridium-extractor",
        default="iridium-extractor",
        help="iridium-extractor command name/path (default iridium-extractor).",
    )
    parser.add_argument(
        "--pypy",
        default="pypy3",
        help="PyPy command name/path used to run iridium-parser.py (default pypy3).",
    )
    return parser


def main(argv: list[str] | None = None, *, storage_client: StorageClient | None = None) -> int:
    """Entry point. ``storage_client`` is injectable for testing (moto-backed)."""
    args = _build_parser().parse_args(argv)
    configure_logging()
    console = Console()

    files = _resolve_files(args.path, args.glob)
    if not files:
        console.print(f"[bold red]x No file(s) found at:[/] {args.path}")
        return 2

    metadata_flags = (
        args.signal_type is not None,
        args.sample_rate is not None,
        args.center_freq is not None,
        args.datatype is not None,
        args.hardware is not None,
    )
    generated_meta_mode = any(metadata_flags)
    if generated_meta_mode and args.ensure_sha512:
        console.print(
            "[bold red]x --ensure-sha512 is only needed with existing SigMF-pair ingest. "
            "Generated metadata already includes core:sha512.[/]"
        )
        return 2
    if generated_meta_mode and not all(metadata_flags[:3]):
        console.print(
            "[bold red]x --signal-type, --sample-rate, and --center-freq are required "
            "when generating metadata from CLI flags.[/]"
        )
        return 2
    if not generated_meta_mode and len(files) != 1:
        console.print(
            "[bold red]x Existing SigMF-pair ingest accepts one .sigmf-data file. "
            "Use metadata flags for directory/multi-file ingest.[/]"
        )
        return 2

    what = files[0] if len(files) == 1 else f"{len(files)} files in {args.path}"
    started_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    if generated_meta_mode:
        assert args.signal_type is not None
        assert args.sample_rate is not None
        assert args.center_freq is not None
        datatype = args.datatype or "cf32"
        hardware = args.hardware or "unknown"
        console.print(
            f"[dim][{started_at}][/dim] [bold cyan]>[/] Ingesting [bold]{what}[/] as "
            f"[bold]{args.signal_type}[/] ({datatype}, "
            f"{args.sample_rate / 1e6:.3f} MS/s @ {args.center_freq / 1e6:.3f} MHz)"
            + (f" [dim](annotations: {args.pypy})[/dim]" if args.iridium_annotate else "")
            + "..."
        )
    else:
        console.print(
            f"[dim][{started_at}][/dim] [bold cyan]>[/] Ingesting existing SigMF pair "
            f"[bold]{what}[/] using its .sigmf-meta..."
            + (f" [dim](annotations: {args.pypy})[/dim]" if args.iridium_annotate else "")
        )

    with console.status("[bold cyan]Ingesting...[/]", spinner="dots"):
        try:
            if generated_meta_mode:
                result: IngestResult = ingest_files(
                    file_paths=files,
                    signal_type=args.signal_type,
                    sample_rate=args.sample_rate,
                    center_freq=args.center_freq,
                    datatype=datatype,
                    hardware=hardware,
                    iqengine=args.iqengine,
                    iridium_annotate=args.iridium_annotate,
                    iridium_parser=args.iridium_parser,
                    iridium_extractor=args.iridium_extractor,
                    pypy=args.pypy,
                    storage_client=storage_client,
                )
            else:
                result = ingest_sigmf_pair(
                    file_path=files[0],
                    iqengine=args.iqengine,
                    ensure_sha512=args.ensure_sha512,
                    iridium_annotate=args.iridium_annotate,
                    iridium_parser=args.iridium_parser,
                    iridium_extractor=args.iridium_extractor,
                    pypy=args.pypy,
                    storage_client=storage_client,
                )
        except StorageError as exc:
            console.print(f"[bold red]x Storage error:[/] {exc}")
            return 1
        except (ValueError, OSError) as exc:
            console.print(f"[bold red]x {exc}[/]")
            return 2
        except Exception as exc:
            console.print(f"[bold red]x Unexpected error:[/] {exc}")
            return 2

    console.print("[bold green]Capture ingested[/]")
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("Session ID", result.session_id)
    table.add_row("Data key", result.data_key)
    table.add_row("Meta key", result.meta_key)
    if result.sidecar_keys:
        table.add_row("Sidecars", "\n".join(result.sidecar_keys))
    table.add_row("Samples", f"{result.sample_count:,}")
    table.add_row(
        "Uploaded",
        f"{result.bytes_uploaded:,} bytes ({result.bytes_uploaded / 1024 / 1024:.2f} MiB)",
    )
    console.print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
