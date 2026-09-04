"""CLI to list and filter AeroLake captures — the bucket "catalog".

Answers the discovery question from ADR-003: *"which captures are in the lake,
and which ones match what I'm looking for?"* — without downloading a single
sample byte. It pages the bucket, and for each complete capture reads only its
S3 tags and ``x-amz-meta-*`` metadata (two HEAD-class requests via
``CaptureReader.inspect``), then prints a table.

Filtering is done on **tags** (the categorical, enumerable attributes:
signal-type, hardware — see ADR-003), combined with AND. Technical
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
    uv run aerolake-list --prefix gnss_l1/ --signal-type iridium
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

from aerolake.common.config import get_settings
from aerolake.common.iqengine import IQEngineCatalog, IQEngineError
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
    parser.add_argument("--hardware", help="Filter on the hardware tag.")
    parser.add_argument("--min-frequency", type=float, help="Minimum capture frequency in Hz.")
    parser.add_argument("--max-frequency", type=float, help="Maximum capture frequency in Hz.")
    parser.add_argument("--min-datetime", help="Earliest IQEngine capture datetime.")
    parser.add_argument("--max-datetime", help="Latest IQEngine capture datetime.")
    parser.add_argument("--text", help="Search text across IQEngine metadata fields.")
    parser.add_argument("--author", help="Filter by metadata author.")
    parser.add_argument("--location", help="Filter by capture location.")
    parser.add_argument("--operator", help="Filter by recording operator.")
    parser.add_argument("--recorder", help="Filter by recorder software.")
    parser.add_argument("--account", action="append", default=[], help="Restrict the IQEngine datasource account (repeatable).")
    parser.add_argument("--container", action="append", default=[], help="Restrict the IQEngine datasource container (repeatable).")
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
    parser.add_argument(
        "--catalog",
        choices=("auto", "minio", "iqengine"),
        default="auto",
        help="Catalog source: auto uses IQEngine when configured, otherwise MinIO.",
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
    if args.hardware is not None:
        filters["hardware"] = args.hardware
    for name in (
        "min_frequency",
        "max_frequency",
        "min_datetime",
        "max_datetime",
        "text",
        "author",
        "location",
        "operator",
        "recorder",
    ):
        value = getattr(args, name)
        if value is not None:
            filters[name] = value
    for name in ("account", "container"):
        values = getattr(args, name)
        if values:
            filters[name] = values
    # Then generic key=value pairs (these win if they repeat a named one).
    for raw in args.tag:
        if "=" not in raw:
            raise ValueError(f"--tag must be KEY=VALUE, got {raw!r} (missing '=')")
        key, value = raw.split("=", 1)
        if not key:
            raise ValueError(f"--tag has an empty key: {raw!r}")
        filters[key] = value
    return filters


_TAG_FILTERS = {"signal-type", "hardware"}


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
            rows.append(CaptureRow(data_key=data_key, tags=info.tags, metadata=info.metadata))
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
    table.add_column("Hardware")
    table.add_column("Sample rate", justify="right")
    table.add_column("Center freq", justify="right")

    for r in rows:
        table.add_row(
            r.data_key,
            r.tags.get("signal-type", "—"),
            r.tags.get("hardware", "—"),
            _fmt_hz(r.metadata.get("sample-rate"), "MS/s"),
            _fmt_hz(r.metadata.get("center-freq"), "MHz"),
        )

    console.print(table)
    console.print(f"[bold]{len(rows)}[/] capture(s)")


def main(
    argv: list[str] | None = None,
    *,
    reader: CaptureReader | None = None,
    catalog: IQEngineCatalog | None = None,
) -> int:
    """Entry point with IQEngine catalog integration and MinIO fallback."""
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

    # An injected reader keeps tests and callers deterministic on MinIO.
    reader_was_injected = reader is not None
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

    source = "minio"
    stale = False
    sync_in_flight = False
    sync_error = None
    try:
        advanced_filters = set(filters) - _TAG_FILTERS
        use_iqengine = args.catalog == "iqengine" or (
            args.catalog == "auto" and not reader_was_injected
        )
        if args.catalog == "iqengine" and not get_settings().iqengine_url and catalog is None:
            raise IQEngineError(
                "IQEngine is not configured; set AEROLAKE_IQENGINE_URL in .env"
            )
        if advanced_filters and not use_iqengine and catalog is None:
            raise ValueError(
                "These filters require IQEngine: " + ", ".join(sorted(advanced_filters))
            )
        if catalog is not None or (use_iqengine and get_settings().iqengine_url):
            catalog = catalog or IQEngineCatalog()
            query_filters = {
                ("hw" if key == "hardware" else key.replace("-", "_")): value
                for key, value in filters.items()
            }
            result = catalog.search(prefix=args.prefix, filters=query_filters)
            rows = [
                CaptureRow(data_key=row.data_key, tags=row.tags, metadata=row.metadata)
                for row in result.rows
            ]
            source = "iqengine"
            stale = result.stale
            sync_in_flight = result.sync_in_flight
            sync_error = result.sync_error
        else:
            rows = _list_matching(reader, prefix=args.prefix, filters=filters)
    except IQEngineError as exc:
        if args.catalog == "iqengine":
            if args.json:
                print(json.dumps({"status": "error", "error": str(exc)}))
            else:
                console.print(f"[bold red]✗ IQEngine error:[/] {exc}")
            return 1
        logger_message = f"IQEngine unavailable; using MinIO fallback: {exc}"
        console.print(f"[yellow]{logger_message}[/]")
        rows = _list_matching(reader, prefix=args.prefix, filters=filters)
        source = "minio-fallback"
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
        print(
            json.dumps(
                {
                    "catalog": source,
                    "stale": stale,
                    "sync_in_flight": sync_in_flight,
                    "sync_error": sync_error,
                    "prefix": args.prefix,
                    "filters": filters,
                    "total": len(rows),
                    "captures": [
                        {"data_key": r.data_key, "tags": r.tags, "metadata": r.metadata}
                        for r in rows
                    ],
                }
            )
        )
    elif not rows:
        scope = f" under prefix {args.prefix!r}" if args.prefix else ""
        suffix = " matching the filters" if filters else ""
        console.print(f"[yellow]No captures found{scope}{suffix}.[/]")
    else:
        if source == "iqengine" and stale:
            state = "sync in progress" if sync_in_flight else "sync pending"
            console.print(f"[yellow]Catalog results are stale; {state}.[/]")
        _print_table(console, rows)

    return 0


if __name__ == "__main__":
    sys.exit(main())
