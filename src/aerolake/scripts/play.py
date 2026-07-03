"""CLI for software cadence playback of a capture (ADR-007, layer 1).

Replays a stored capture's samples frame-by-frame, paced at the recorded
sample rate (so a 1-second capture takes ~1 second to play). This is the
software foundation for the eventual ZeroMQ stream — today it just paces and
summarises; it does not transmit over the air (that needs the BladeRF, ADR-007).

Exit codes
----------
0 : Played successfully (or nothing to play under the prefix).
1 : Storage layer failure (MinIO unreachable, bucket missing, etc.).
2 : Configuration / unexpected error (incl. a capture with no sample rate).

Usage
-----
    uv run aerolake-play --key gnss_l1/2026-05-29/356596ff/capture.sigmf-data
    uv run aerolake-play --prefix gnss_l1/            # plays the most recent
    uv run aerolake-play --prefix gnss_l1/ --no-realtime --frame-size 8192
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.table import Table

from aerolake.common.logging import configure_logging
from aerolake.common.storage import StorageClient, StorageError
from aerolake.consumer.player import CapturePlayer
from aerolake.consumer.reader import CaptureReader


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aerolake-play",
        description="Replay a stored capture's samples at their recorded cadence.",
    )
    # Either an explicit key or a prefix (whose most recent capture we play).
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--key", help="Exact .sigmf-data key to play.")
    target.add_argument("--prefix", help="Play the most recent complete capture under this prefix.")
    parser.add_argument(
        "--frame-size", type=int, default=4096, help="Samples per frame (default 4096)."
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
        help="Window length in seconds (default: play to the end).",
    )
    parser.add_argument(
        "--no-realtime",
        action="store_true",
        help="Emit frames back-to-back without pacing (don't wait in real time).",
    )
    return parser


def _resolve_key(reader: CaptureReader, args: argparse.Namespace) -> str | None:
    """Return the capture key to play, or None if a prefix matched nothing."""
    if args.key:
        return args.key
    captures = reader.list_captures(prefix=args.prefix)
    # list_captures returns sorted keys; the last is the most recent because the
    # key layout starts with {signal_type}/{YYYY-MM-DD}/… so it sorts by date.
    return captures[-1] if captures else None


def main(argv: list[str] | None = None, *, player: CapturePlayer | None = None) -> int:
    """Entry point. ``player`` is injectable for testing (moto-backed)."""
    args = _build_parser().parse_args(argv)
    configure_logging()  # logs to stderr; stdout stays clean
    console = Console()

    # Build the player (and its reader/StorageClient) unless one was injected.
    if player is None:
        try:
            player = CapturePlayer(CaptureReader(StorageClient()))
        except Exception as exc:
            # Any settings/client build failure is a configuration problem.
            console.print(f"[bold red]✗ Configuration error:[/] {exc}")
            return 2

    # Figure out which capture to play.
    try:
        key = _resolve_key(player.reader, args)
    except StorageError as exc:
        console.print(f"[bold red]✗ Storage error:[/] {exc}")
        return 1

    if key is None:
        console.print(f"[yellow]No captures found under prefix {args.prefix!r}.[/]")
        return 0

    realtime = not args.no_realtime
    console.print(
        f"[bold cyan]>[/] Playing [bold]{key}[/] "
        f"({'real-time' if realtime else 'as fast as possible'}, "
        f"frame {args.frame_size})…"
    )

    try:
        stats = player.play(
            key,
            frame_size=args.frame_size,
            realtime=realtime,
            start_s=args.start,
            duration_s=args.duration,
        )
    except StorageError as exc:
        console.print(f"[bold red]✗ Storage error:[/] {exc}")
        return 1
    except ValueError as exc:
        console.print(f"[bold red]✗ {exc}[/]")
        return 2
    except Exception as exc:
        console.print(f"[bold red]✗ Unexpected error:[/] {exc}")
        return 2

    console.print("[bold green]✓ Playback complete[/]")
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("Frames", f"{stats.frames:,}")
    table.add_row("Samples", f"{stats.samples:,}")
    table.add_row("Sample rate", f"{stats.sample_rate / 1e6:.3f} MS/s")
    table.add_row("Capture duration", f"{stats.duration_s:.3f} s")
    console.print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
