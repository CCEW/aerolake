"""CLI to stream a capture's frames over ZeroMQ Pub/Sub (ADR-008).

Plays a stored capture at its recorded cadence (via CapturePlayer) and
publishes each frame on a ZeroMQ PUB socket, so live subscribers can consume
it. This is the software ``Consumer → ZeroMQ`` delivery step.

Exit codes
----------
0 : Streamed successfully (or nothing to stream under the prefix).
1 : Storage layer failure (MinIO unreachable, bucket missing, etc.).
2 : Configuration / unexpected error (incl. a capture with no sample rate).

Usage
-----
    uv run aerolake-stream --key gnss_l1/2026-05-29/ab/capture.sigmf-data
    uv run aerolake-stream --prefix gnss_l1/ --bind tcp://*:5555
    uv run aerolake-stream --prefix gnss_l1/ --no-realtime --topic gnss_l1
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
from aerolake.consumer.stream import FramePublisher


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aerolake-stream",
        description="Stream a capture's frames over ZeroMQ at recorded cadence.",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--key", help="Exact .sigmf-data key to stream.")
    target.add_argument(
        "--prefix", help="Stream the most recent complete capture under this prefix."
    )
    parser.add_argument(
        "--bind", default="tcp://*:5555", help="ZeroMQ bind address (default tcp://*:5555)."
    )
    parser.add_argument(
        "--topic",
        default=None,
        help="PUB topic (default: the capture's signal-type tag, else 'frames').",
    )
    parser.add_argument("--frame-size", type=int, default=4096, help="Samples per frame.")
    parser.add_argument(
        "--start",
        type=float,
        default=0.0,
        help="Start offset in seconds — partial read (default 0).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Window length in seconds (default: to the end).",
    )
    parser.add_argument(
        "--no-realtime",
        action="store_true",
        help="Publish frames back-to-back without pacing.",
    )
    return parser


def _resolve_key(reader: CaptureReader, args: argparse.Namespace) -> str | None:
    """Return the capture key to stream, or None if a prefix matched nothing."""
    if args.key:
        return args.key
    captures = reader.list_captures(prefix=args.prefix)
    return captures[-1] if captures else None


def main(
    argv: list[str] | None = None,
    *,
    player: CapturePlayer | None = None,
    publisher: FramePublisher | None = None,
) -> int:
    """Entry point. ``player``/``publisher`` are injectable for testing."""
    args = _build_parser().parse_args(argv)
    configure_logging()
    console = Console()

    if player is None:
        try:
            player = CapturePlayer(CaptureReader(StorageClient()))
        except Exception as exc:
            console.print(f"[bold red]✗ Configuration error:[/] {exc}")
            return 2

    # Resolve the capture to stream.
    try:
        key = _resolve_key(player.reader, args)
    except StorageError as exc:
        console.print(f"[bold red]✗ Storage error:[/] {exc}")
        return 1
    if key is None:
        console.print(f"[yellow]No captures found under prefix {args.prefix!r}.[/]")
        return 0

    # Topic defaults to the capture's signal-type tag (so subscribers can filter
    # by signal), falling back to a generic "frames".
    topic = args.topic
    if topic is None:
        try:
            topic = player.reader.inspect(key).tags.get("signal-type", "frames")
        except StorageError as exc:
            console.print(f"[bold red]✗ Storage error:[/] {exc}")
            return 1

    # Build the publisher unless one was injected (tests inject a fake).
    created_publisher = publisher is None
    if publisher is None:
        publisher = FramePublisher.bind(args.bind, topic)

    realtime = not args.no_realtime
    console.print(
        f"[bold cyan]>[/] Streaming [bold]{key}[/] on [bold]{args.bind}[/] "
        f"(topic '{topic}', {'real-time' if realtime else 'as fast as possible'})…"
    )

    try:
        stats = player.play(
            key,
            frame_size=args.frame_size,
            realtime=realtime,
            start_s=args.start,
            duration_s=args.duration,
            on_frame=publisher.publish,
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
    finally:
        # Only close a socket we opened ourselves (don't close an injected one).
        if created_publisher:
            publisher.close()

    console.print("[bold green]✓ Stream complete[/]")
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("Topic", topic)
    table.add_row("Frames published", f"{stats.frames:,}")
    table.add_row("Samples", f"{stats.samples:,}")
    table.add_row("Capture duration", f"{stats.duration_s:.3f} s")
    console.print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
