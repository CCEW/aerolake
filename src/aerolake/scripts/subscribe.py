"""CLI to subscribe to a ZeroMQ capture stream and inspect frames (ADR-008).

The receiving half of ``aerolake-stream``: connect a ZeroMQ SUB socket to a
publisher and print one line per frame as it arrives (index, sample count,
dtype, and a quick RMS power so you can tell signal from silence). This is what
runs on "any device" that wants to consume a live stream.

ZeroMQ "slow joiner": a SUB that connects *after* the publisher has started may
miss the first frames — start the subscriber first (or before the stream you
care about), it is harmless for a continuous feed.

Exit codes
----------
0 : Received frames cleanly (or stopped with Ctrl-C).
2 : Configuration / unexpected error (bad address, ZeroMQ failure…).

Usage
-----
    uv run aerolake-subscribe                              # tcp://localhost:5555, all topics
    uv run aerolake-subscribe --address tcp://192.168.1.42:5555 --topic iridium
    uv run aerolake-subscribe --frames 10                  # stop after 10 frames (demo)
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
from rich.console import Console
from rich.table import Table

from aerolake.common.logging import configure_logging
from aerolake.consumer.stream import FrameSubscriber


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aerolake-subscribe",
        description="Subscribe to a ZeroMQ capture stream and print frames as they arrive.",
    )
    parser.add_argument(
        "--address",
        default="tcp://localhost:5555",
        help="Publisher address to connect to (default tcp://localhost:5555). "
        "From another machine, use the publisher's IP, e.g. tcp://192.168.1.42:5555.",
    )
    parser.add_argument(
        "--topic",
        default="",
        help="Topic prefix to subscribe to (default '' = every topic).",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=None,
        help="Stop after N frames (default: run until Ctrl-C).",
    )
    return parser


def _rms_dbfs(samples: np.ndarray) -> float:
    """Quick RMS power in dBFS — a one-number 'is there signal?' indicator."""
    if samples.size == 0:
        return float("-inf")
    power = float(np.mean(np.abs(samples.astype(np.complex64)) ** 2))
    return 10.0 * np.log10(power) if power > 0 else float("-inf")


def main(argv: list[str] | None = None, *, subscriber: FrameSubscriber | None = None) -> int:
    """Entry point. ``subscriber`` is injectable for testing (a fake socket)."""
    args = _build_parser().parse_args(argv)
    configure_logging()  # logs to stderr; stdout stays clean
    console = Console()

    # Build the subscriber unless one was injected (tests inject a fake).
    created = subscriber is None
    if subscriber is None:
        try:
            subscriber = FrameSubscriber.connect(args.address, args.topic)
        except Exception as exc:
            console.print(f"[bold red]✗ Could not connect:[/] {exc}")
            return 2

    topic_label = repr(args.topic) if args.topic else "ALL"
    waiting = f", waiting for {args.frames} frame(s)…" if args.frames else ", Ctrl-C to stop…"
    console.print(
        f"[bold cyan]>[/] Subscribed to [bold]{args.address}[/] (topic {topic_label}){waiting}"
    )

    frames = 0
    samples_total = 0
    try:
        while args.frames is None or frames < args.frames:
            topic, header, samples = subscriber.recv()  # blocks until next frame
            frames += 1
            samples_total += len(samples)
            console.print(
                f"[dim]#{header.get('index', '?'):>4}[/] "
                f"[bold]{topic or '—'}[/]  {len(samples):>5} {samples.dtype}  "
                f"RMS {_rms_dbfs(samples):6.1f} dBFS"
            )
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/]")
    except Exception as exc:
        console.print(f"[bold red]✗ Receive error:[/] {exc}")
        return 2
    finally:
        # Only close a socket we opened ourselves (don't close an injected one).
        if created:
            subscriber.close()

    console.print("[bold green]✓ Done[/]")
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("Frames received", f"{frames:,}")
    table.add_row("Samples", f"{samples_total:,}")
    console.print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
