"""CLI for the AeroLake synthetic producer.

Generates a synthetic IQ capture and uploads it as SigMF to MinIO.

Exit codes
----------
0 : Capture uploaded successfully
1 : Storage layer failure (MinIO unreachable, bucket missing, etc.)
2 : Configuration or unexpected error

Usage
-----
    uv run aerolake-producer --preset gnss-l1 --duration 1.0
    uv run aerolake-producer --preset iridium --duration 0.5 --snr 15
    uv run aerolake-producer --signal-type custom --duration 0.5 \
        --sample-rate 2e6 --center-freq 433e6
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from aerolake.common.logging import configure_logging
from aerolake.common.storage import StorageError
from aerolake.producer.orchestrator import capture_and_upload
from aerolake.producer.synthetic import SyntheticParams


@dataclass(frozen=True)
class Preset:
    """A frequency/bandwidth preset for a known RF signal of interest."""

    signal_type: str
    center_freq: float
    sample_rate: float
    description: str


# Presets aligned with the AeroLake project mandate.
# Note: frequencies and sample rates are taken from the LASSENA cadrage
# document. The GNSS L1 frequency is 1575.42 MHz (the standard L1 carrier).
PRESETS: dict[str, Preset] = {
    "gnss-l1": Preset(
        signal_type="gnss_l1",
        center_freq=1_575_420_000,
        sample_rate=2_000_000,
        description="GNSS L1 carrier (2 MHz bandwidth)",
    ),
    "iridium": Preset(
        signal_type="iridium",
        center_freq=1_626_270_000,
        sample_rate=2_000_000,
        description="Iridium downlink band (2 MHz bandwidth)",
    ),
    "starlink": Preset(
        signal_type="starlink",
        center_freq=1_400_000_000,
        sample_rate=25_000_000,
        description="Starlink test band (25 MHz bandwidth, ~200 MB/s)",
    ),
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aerolake-producer",
        description="Generate a synthetic SigMF capture and upload it to MinIO.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Presets available:\n"
            + "\n".join(
                f"  {name:<10} {p.description}"
                for name, p in PRESETS.items()
            )
        ),
    )
    parser.add_argument(
        "--preset",
        choices=sorted(PRESETS.keys()),
        help="Use a predefined signal_type/center_freq/sample_rate combo.",
    )
    parser.add_argument(
        "--signal-type",
        help="Bucket prefix (e.g. gnss_l1). Ignored if --preset is set.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=1.0,
        help="Capture duration in seconds (default: 1.0).",
    )
    parser.add_argument(
        "--sample-rate",
        type=float,
        help="Sample rate in Hz. Ignored if --preset is set.",
    )
    parser.add_argument(
        "--center-freq",
        type=float,
        help="Center frequency in Hz. Ignored if --preset is set.",
    )
    parser.add_argument(
        "--tone-offset",
        type=float,
        default=100_000.0,
        help="Synthetic tone offset from center, in Hz (default: 100000).",
    )
    parser.add_argument(
        "--snr",
        type=float,
        default=20.0,
        help="Synthetic signal-to-noise ratio in dB (default: 20).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="RNG seed for reproducible noise. Omit for random.",
    )
    return parser


def _resolve_capture_params(args: argparse.Namespace) -> dict:
    """Combine preset and CLI args into the kwargs for capture_and_upload."""
    if args.preset:
        preset = PRESETS[args.preset]
        return {
            "signal_type": preset.signal_type,
            "sample_rate": preset.sample_rate,
            "center_freq": preset.center_freq,
        }
    # No preset: require all three custom fields
    missing = [
        name for name, value in (
            ("--signal-type", args.signal_type),
            ("--sample-rate", args.sample_rate),
            ("--center-freq", args.center_freq),
        ) if value is None
    ]
    if missing:
        raise SystemExit(
            f"Without --preset, you must specify: {', '.join(missing)}"
        )
    return {
        "signal_type": args.signal_type,
        "sample_rate": args.sample_rate,
        "center_freq": args.center_freq,
    }


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    # Route logs to stderr so stdout stays clean for the result summary.
    configure_logging()
    console = Console()

    try:
        params = _resolve_capture_params(args)
    except SystemExit as exc:
        console.print(f"[bold red]✗ {exc}[/]")
        return 2

    console.print(
        f"[bold cyan]>[/] Capturing [bold]{params['signal_type']}[/] "
        f"for {args.duration}s @ {params['sample_rate']/1e6:.1f} MS/s, "
        f"center {params['center_freq']/1e6:.3f} MHz..."
    )

    try:
        result = capture_and_upload(
            **params,
            duration_s=args.duration,
            source=SyntheticParams(
                tone_offset_hz=args.tone_offset,
                snr_db=args.snr,
                seed=args.seed,
            ),
        )
    except StorageError as exc:
        console.print(f"[bold red]✗ Storage error:[/] {exc}")
        return 1
    except Exception as exc:
        console.print(f"[bold red]✗ Unexpected error:[/] {exc}")
        return 2

    # Pretty summary
    console.print("[bold green]✓ Capture uploaded[/]")
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("Session ID", result.session_id)
    table.add_row("Data key", result.data_key)
    table.add_row("Meta key", result.meta_key)
    table.add_row("Samples", f"{result.sample_count:,}")
    table.add_row(
        "Uploaded",
        f"{result.bytes_uploaded:,} bytes ({result.bytes_uploaded/1024/1024:.2f} MiB)",
    )
    console.print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
