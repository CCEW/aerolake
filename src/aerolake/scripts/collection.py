"""CLI to group Recordings under a prefix into a SigMF Collection.

Thin wrapper over :class:`aerolake.consumer.collection.CollectionBuilder`: it
scans ``--prefix`` for complete Recordings, assembles a conformant
``.sigmf-collection`` object (name/description from the arguments, author from
the system login), writes it at the root of the prefix, and prints a summary
(how many Recordings grouped, how many orphans skipped, where the file landed).

Selection is **by prefix** and **arguments**, not a JSON file:

    uv run aerolake-collection --prefix gnss_l1/2026-06-17/ --name "campaign" \\
        --description "GNSS L1, 17 June field session"

Pass ``--dry-run`` to preview the plan without writing anything, and ``--json``
for machine-readable output (both mirror aerolake-validate / aerolake-list).

Exit codes
----------
0 : Done (zero complete Recordings under the prefix is a normal result — the
    collection is simply not written, and a notice is printed).
1 : Storage layer failure (MinIO unreachable, bucket missing, etc.).
2 : Configuration or unexpected error.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from rich.console import Console

from aerolake.common.logging import configure_logging
from aerolake.common.storage import StorageClient, StorageError
from aerolake.consumer.collection import CollectionBuilder, CollectionPlan


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aerolake-collection",
        description=(
            "Group every complete Recording under a prefix into a single "
            "SigMF .sigmf-collection file, written at the root of that prefix."
        ),
    )
    parser.add_argument(
        "--prefix",
        required=True,
        help="Bucket prefix to group, e.g. 'gnss_l1/2026-06-17/'.",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Collection name; slugified into the .sigmf-collection filename.",
    )
    parser.add_argument(
        "--description",
        default=None,
        help="Free-text description, stored as core:description.",
    )
    parser.add_argument(
        "--author",
        default=None,
        help="Override the author (defaults to the system login).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the plan without writing the .sigmf-collection.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output a JSON object instead of a human-readable summary.",
    )
    return parser


def _system_author() -> str:
    """Deduce the author from the system login, with a safe fallback."""
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _emit_json(plan: CollectionPlan, *, written: bool) -> None:
    """Print the plan as a JSON object (stable shape for scripting)."""
    print(
        json.dumps(
            {
                "status": "ok",
                "prefix": plan.prefix,
                "collection_key": plan.collection_key,
                "written": written,
                "n_streams": len(plan.streams),
                "streams": [s.name for s in plan.streams],
                "orphans": plan.orphans,
            }
        )
    )


def _print_summary(console: Console, plan: CollectionPlan, *, written: bool, dry_run: bool) -> None:
    """Render a human-readable summary of what was grouped (or would be)."""
    console.print(
        f"[bold]{len(plan.streams)}[/] Recording(s) grouped under prefix [cyan]{plan.prefix}[/]"
    )
    for stream in plan.streams:
        console.print(f"  • {stream.name}")
    # Orphans are skipped but always reported, so nothing silently disappears.
    if plan.orphans:
        console.print(f"[yellow]{len(plan.orphans)} orphan(s) skipped:[/]")
        for key in plan.orphans:
            console.print(f"  [yellow]- {key}[/]")
    if written:
        console.print(f"[green]✓ Collection written to[/] [cyan]{plan.collection_key}[/]")
    elif dry_run:
        console.print(f"[dim]Dry run — would write[/] [cyan]{plan.collection_key}[/]")


def main(argv: list[str] | None = None, *, builder: CollectionBuilder | None = None) -> int:
    """Entry point. ``builder`` is injectable for testing (moto-backed)."""
    args = _build_parser().parse_args(argv)
    # Logs to stderr so stdout stays clean for the summary / --json result.
    configure_logging()
    console = Console()

    author = args.author or _system_author()

    # Build the builder unless one was injected. Failure here is config -> 2.
    if builder is None:
        try:
            builder = CollectionBuilder(StorageClient())
        except Exception as exc:
            if args.json:
                print(json.dumps({"status": "error", "error": str(exc)}))
            else:
                console.print(f"[bold red]✗ Configuration error:[/] {exc}")
            return 2

    # Plan the collection (scan + hash). A storage failure here is exit 1.
    try:
        plan = builder.build(
            prefix=args.prefix,
            name=args.name,
            description=args.description,
            author=author,
        )
    except StorageError as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}))
        else:
            console.print(f"[bold red]✗ Storage error:[/] {exc}")
        return 1
    except Exception as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}))
        else:
            console.print(f"[bold red]✗ Unexpected error:[/] {exc}")
        return 2

    # Nothing to group: a valid, normal outcome. We don't write an empty
    # collection — we just say so and exit cleanly.
    if not plan.streams:
        if args.json:
            _emit_json(plan, written=False)
        else:
            scope = plan.prefix or "(whole bucket)"
            console.print(f"[yellow]No complete Recordings under {scope} — nothing to group.[/]")
            if plan.orphans:
                console.print(f"[yellow]{len(plan.orphans)} orphan(s) found and skipped.[/]")
        return 0

    # Write, unless this is a dry run. The upload failing is exit 1.
    written = False
    if not args.dry_run:
        try:
            builder.write(plan)
            written = True
        except StorageError as exc:
            if args.json:
                print(json.dumps({"status": "error", "error": str(exc)}))
            else:
                console.print(f"[bold red]✗ Storage error:[/] {exc}")
            return 1

    if args.json:
        _emit_json(plan, written=written)
    else:
        _print_summary(console, plan, written=written, dry_run=args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
