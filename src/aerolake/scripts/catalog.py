"""CLI to list and filter AeroLake captures — the bucket "catalog".

Answers the discovery question from ADR-003: *"which captures are in the lake,
and which ones match what I'm looking for?"* — without downloading a single
sample byte. It pages the bucket, and for each complete capture reads only its
S3 tags and ``x-amz-meta-*`` metadata (two HEAD-class requests via
``CaptureReader.inspect``), then prints a table.

Filtering is done on **tags** (the categorical, enumerable attributes:
signal-type, quality, hardware — see ADR-003), combined with AND. Technical
metadata (sample rate, center frequency, …) is shown as columns but not
filtered on, matching the ADR-003 split.

Exit codes
----------
0 : Listing completed (zero matches is a normal result, not an error).
1 : Storage layer failure (MinIO unreachable, bucket missing, etc.).
2 : Configuration or unexpected error (incl. a malformed --tag).

Usage
-----
    uv run aerolake-list
    uv run aerolake-list --prefix gnss_l1/ --quality validated
    uv run aerolake-list --signal-type iridium --hardware synthetic
    uv run aerolake-list --tag project=nesiva --tag operator=theo --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from aerolake.common.logging import configure_logging
from aerolake.common.storage import StorageClient, StorageError
from aerolake.consumer.reader import CaptureReader


@dataclass(frozen=True)
class CaptureRow:
    """One catalog entry: a capture's key plus its (cheap) tags and metadata."""

    data_key: str
    tags: dict[str, str]
    metadata: dict[str, str]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aerolake-list",
        description=(
            "List captures in the bucket and filter them by tag, without "
            "downloading any sample data."
        ),
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="Bucket prefix to list, e.g. 'gnss_l1/' (default: whole bucket).",
    )
    # Named filters for the well-known tags defined in ADR-003. Each maps to an
    # exact-match constraint on the corresponding tag.
    parser.add_argument("--signal-type", help="Filter on the signal-type tag.")
    parser.add_argument(
        "--quality",
        choices=["raw", "validated", "rejected", "archived"],
        help="Filter on the quality tag.",
    )
    parser.add_argument("--hardware", help="Filter on the hardware tag.")
    # Generic escape hatch for any other tag, repeatable: --tag key=value.
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Filter on an arbitrary tag (repeatable). Example: --tag project=nesiva.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output a JSON array instead of a human-readable table.",
    )
    return parser


def _parse_filters(args: argparse.Namespace) -> dict[str, str]:
    """Build the tag filter dict from the named options and any --tag pairs.

    Raises ValueError on a malformed --tag (no '='), which main() turns into
    exit code 2.
    """
    filters: dict[str, str] = {}
    # Named filters first.
    if args.signal_type is not None:
        filters["signal-type"] = args.signal_type
    if args.quality is not None:
        filters["quality"] = args.quality
    if args.hardware is not None:
        filters["hardware"] = args.hardware
    # Then generic key=value pairs (these win if they repeat a named one).
    for raw in args.tag:
        if "=" not in raw:
            raise ValueError(
                f"--tag must be KEY=VALUE, got {raw!r} (missing '=')"
            )
        key, value = raw.split("=", 1)
        if not key:
            raise ValueError(f"--tag has an empty key: {raw!r}")
        filters[key] = value
    return filters


def _list_matching(
    reader: CaptureReader, *, prefix: str, filters: dict[str, str]
) -> list[CaptureRow]:
    """Return one CaptureRow per capture under ``prefix`` matching all filters.

    A capture matches when every filter key is present in its tags with the
    exact expected value (AND semantics). With no filters, every capture
    matches.
    """
    rows: list[CaptureRow] = []
    for data_key in reader.list_captures(prefix=prefix):
        info = reader.inspect(data_key)
        if all(info.tags.get(k) == v for k, v in filters.items()):
            rows.append(
                CaptureRow(
                    data_key=data_key, tags=info.tags, metadata=info.metadata
                )
            )
    return rows


def _fmt_hz(value: str | None, unit: str) -> str:
    """Format a Hz string (from metadata) as MHz/MS/s; '—' if absent/bad."""
    try:
        return f"{int(value) / 1e6:.3f} {unit}"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"


def _print_table(console: Console, rows: list[CaptureRow]) -> None:
    """Render the catalog as a table with a trailing count."""
    table = Table(title="AeroLake captures")
    table.add_column("Capture", overflow="fold")
    table.add_column("Signal type")
    table.add_column("Quality")
    table.add_column("Sample rate", justify="right")
    table.add_column("Center freq", justify="right")

    # Colour the quality cell so the curation state pops at a glance.
    quality_style = {
        "validated": "green",
        "rejected": "red",
        "raw": "yellow",
        "archived": "dim",
    }
    for r in rows:
        quality = r.tags.get("quality", "—")
        style = quality_style.get(quality, "")
        table.add_row(
            r.data_key,
            r.tags.get("signal-type", "—"),
            f"[{style}]{quality}[/]" if style else quality,
            _fmt_hz(r.metadata.get("sample-rate"), "MS/s"),
            _fmt_hz(r.metadata.get("center-freq"), "MHz"),
        )

    console.print(table)
    console.print(f"[bold]{len(rows)}[/] capture(s)")


def main(argv: list[str] | None = None, *, reader: CaptureReader | None = None) -> int:
    """Entry point. ``reader`` is injectable for testing (moto-backed)."""
    args = _build_parser().parse_args(argv)
    # Route logs to stderr so stdout stays clean for the table / JSON result.
    configure_logging()
    console = Console()

    # Parse filters early — a malformed --tag is a usage error (exit 2).
    try:
        filters = _parse_filters(args)
    except ValueError as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}))
        else:
            console.print(f"[bold red]✗ {exc}[/]")
        return 2

    # Build the reader unless one was injected. Failure here is config -> 2.
    if reader is None:
        try:
            reader = CaptureReader(StorageClient())
        except Exception as exc:
            # Broad catch: any settings/client build failure -> exit 2.
            if args.json:
                print(json.dumps({"status": "error", "error": str(exc)}))
            else:
                console.print(f"[bold red]✗ Configuration error:[/] {exc}")
            return 2

    # Run the listing. A listing failure is an infrastructure problem -> 1.
    try:
        rows = _list_matching(reader, prefix=args.prefix, filters=filters)
    except StorageError as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}))
        else:
            console.print(f"[bold red]✗ Storage error:[/] {exc}")
        return 1
    except Exception as exc:
        # Defensive catch-all -> exit 2 rather than a raw traceback.
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}))
        else:
            console.print(f"[bold red]✗ Unexpected error:[/] {exc}")
        return 2

    # Render.
    if args.json:
        print(json.dumps({
            "prefix": args.prefix,
            "filters": filters,
            "total": len(rows),
            "captures": [
                {"data_key": r.data_key, "tags": r.tags, "metadata": r.metadata}
                for r in rows
            ],
        }))
    elif not rows:
        scope = f" under prefix {args.prefix!r}" if args.prefix else ""
        suffix = " matching the filters" if filters else ""
        console.print(f"[yellow]No captures found{scope}{suffix}.[/]")
    else:
        _print_table(console, rows)

    return 0


if __name__ == "__main__":
    sys.exit(main())
