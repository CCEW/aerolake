"""CLI healthcheck for AeroLake.

Verifies that the storage layer is fully operational:
- Settings load correctly from .env
- StorageClient can be instantiated
- MinIO is reachable
- Configured bucket is accessible (head_bucket succeeds)

Exit codes
----------
0 : Healthcheck passed
1 : Storage layer failure (MinIO unreachable, bucket missing, etc.)
2 : Configuration or unexpected error

Usage
-----
    uv run aerolake-healthcheck
    uv run aerolake-healthcheck -v          # verbose
    uv run aerolake-healthcheck --json      # JSON output for scripting
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from rich.console import Console
from rich.table import Table

from aerolake.common.config import Settings, get_settings
from aerolake.common.logging import configure_logging
from aerolake.common.storage import StorageClient, StorageError


def _build_report(
    settings: Settings,
    success: bool,
    latency_ms: float | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build a structured report dict, used for both pretty and JSON output."""
    return {
        "status": "ok" if success else "error",
        "endpoint": settings.s3_endpoint,
        "bucket": settings.s3_bucket,
        "region": settings.s3_region,
        "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
        "error": error,
    }


def _print_pretty(console: Console, report: dict[str, Any], verbose: bool) -> None:
    """Render the report in human-friendly format using rich."""
    if report["status"] == "ok":
        console.print("[bold green]✓ AeroLake healthcheck passed[/]")
    else:
        console.print("[bold red]✗ AeroLake healthcheck failed[/]")
        console.print(f"  [red]{report['error']}[/]")

    if verbose or report["status"] != "ok":
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="cyan")
        table.add_column()
        table.add_row("Endpoint", report["endpoint"])
        table.add_row("Bucket", report["bucket"])
        table.add_row("Region", report["region"])
        if report["latency_ms"] is not None:
            table.add_row("Latency", f"{report['latency_ms']} ms")
        console.print(table)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aerolake-healthcheck",
        description="Validate AeroLake storage layer connectivity.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print connection details even on success.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output a JSON report instead of human-readable text.",
    )
    args = parser.parse_args(argv)
    # Route logs to stderr so stdout stays clean (matters for --json).
    configure_logging()

    console = Console(stderr=False)

    # Step 1: load settings
    try:
        settings = get_settings()
    except Exception as exc:
        msg = f"Failed to load settings: {exc}"
        if args.json:
            print(json.dumps({"status": "error", "error": msg}))
        else:
            console.print(f"[bold red]✗ Configuration error:[/] {msg}")
        return 2

    # Step 2: instantiate client and run healthcheck (timed)
    start = time.perf_counter()
    try:
        client = StorageClient(settings)
        client.health_check()
    except StorageError as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        report = _build_report(settings, success=False, latency_ms=elapsed_ms, error=str(exc))
        if args.json:
            print(json.dumps(report))
        else:
            _print_pretty(console, report, verbose=True)
        return 1
    except Exception as exc:
        msg = f"Unexpected error: {exc}"
        if args.json:
            print(json.dumps({"status": "error", "error": msg}))
        else:
            console.print(f"[bold red]✗ {msg}[/]")
        return 2

    elapsed_ms = (time.perf_counter() - start) * 1000
    report = _build_report(settings, success=True, latency_ms=elapsed_ms)
    if args.json:
        print(json.dumps(report))
    else:
        _print_pretty(console, report, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
