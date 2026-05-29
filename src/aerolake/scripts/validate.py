"""CLI for batch quality validation of AeroLake captures.

Curates a whole bucket prefix in a single pass. For every *complete* capture
under the prefix it:

  1. reads + decodes the capture,
  2. runs the full quality assessment (clipping, RMS, DC offset, completeness,
     SigMF metadata validity — see ``aerolake.quality``),
  3. promotes the MinIO ``quality`` tag to ``validated`` or ``rejected``,
  4. stores a ``quality_report.json`` next to the capture,

then prints a summary table. This is the orchestration layer on top of
``CaptureReader.validate`` (which handles one capture); it is the "finisher"
of the quality layer described in ADR-004 — the step that turns a bucket of
``quality=raw`` captures into a curated subset filterable by
``quality=validated``.

``--dry-run`` makes the pass read-only: verdicts are computed and displayed,
but no tag is promoted and no report is written. Use it to preview what a
real run would do before mutating the bucket.

Exit codes
----------
0 : Run completed. Captures may be validated OR rejected — both are normal
    outcomes of curation, not errors.
1 : Storage layer failure (MinIO unreachable, bucket missing, etc.).
2 : Configuration or unexpected error.

Usage
-----
    uv run aerolake-validate --prefix gnss_l1/ --expected-duration 1.0
    uv run aerolake-validate --prefix iridium/ --dry-run
    uv run aerolake-validate --json
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
class CaptureOutcome:
    """The result of validating a single capture in the batch.

    Attributes
    ----------
    data_key
        Key of the ``.sigmf-data`` object that was validated.
    is_valid
        The verdict, or None if the capture could not be processed.
    quality
        The quality value the verdict maps to (``validated``/``rejected``),
        or None when ``error`` is set.
    n_failed
        Number of failed quality checks (0 for a clean capture).
    error
        Human-readable error string if this capture could not be validated
        (e.g. unsupported datatype), else None. A non-None error does NOT
        abort the batch — the loop records it and moves on.
    """

    data_key: str
    is_valid: bool | None
    quality: str | None
    n_failed: int
    error: str | None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aerolake-validate",
        description=(
            "Validate the quality of every capture under a bucket prefix and "
            "promote their MinIO quality tags (validated/rejected)."
        ),
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="Bucket prefix to curate, e.g. 'gnss_l1/' (default: whole bucket).",
    )
    parser.add_argument(
        "--expected-duration",
        type=float,
        default=None,
        help=(
            "Intended capture duration in seconds, forwarded to the "
            "completeness check. Omit to derive it from the data (which makes "
            "the completeness check trivially pass)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and show verdicts without promoting tags or writing reports.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output a JSON report instead of a human-readable table.",
    )
    return parser


def _validate_prefix(
    reader: CaptureReader,
    *,
    prefix: str,
    expected_duration: float | None,
    dry_run: bool,
) -> list[CaptureOutcome]:
    """Validate every complete capture under ``prefix``.

    Per-capture failures (e.g. an unsupported datatype) are caught and
    recorded as a CaptureOutcome with an ``error`` set, so one bad capture
    never aborts the whole curation pass. A failure to *list* the bucket,
    however, is a real infrastructure problem and is allowed to propagate.
    """
    keys = reader.list_captures(prefix=prefix)

    outcomes: list[CaptureOutcome] = []
    for key in keys:
        try:
            report = reader.validate(
                key,
                expected_duration_s=expected_duration,
                # In a dry run we neither write the report artifact nor touch
                # the tag — the pass is purely observational.
                store_report=not dry_run,
                promote_tag=not dry_run,
            )
        except (StorageError, ValueError) as exc:
            # ValueError covers unsupported SigMF datatypes; StorageError on a
            # single object (e.g. a transient read failure) shouldn't sink the
            # whole batch either.
            outcomes.append(
                CaptureOutcome(
                    data_key=key,
                    is_valid=None,
                    quality=None,
                    n_failed=0,
                    error=str(exc),
                )
            )
            continue

        outcomes.append(
            CaptureOutcome(
                data_key=key,
                is_valid=report.is_valid,
                quality="validated" if report.is_valid else "rejected",
                n_failed=len(report.failed_checks),
                error=None,
            )
        )
    return outcomes


def _print_table(
    console: Console, outcomes: list[CaptureOutcome], *, dry_run: bool
) -> None:
    """Render the per-capture outcomes and a summary line."""
    table = Table(title="Quality validation" + (" (dry run)" if dry_run else ""))
    table.add_column("Capture", overflow="fold")
    table.add_column("Verdict", justify="center")
    table.add_column("Failed", justify="right")
    table.add_column("Quality tag")

    for o in outcomes:
        if o.error is not None:
            verdict = "[yellow]error[/]"
            quality = f"[yellow]{o.error}[/]"
        elif o.is_valid:
            verdict = "[green]✓ valid[/]"
            quality = o.quality or ""
        else:
            verdict = "[red]✗ rejected[/]"
            quality = o.quality or ""
        # In a dry run nothing is actually written, so flag the tag column.
        if dry_run and o.error is None:
            quality = f"{quality} [dim](not written)[/]"
        table.add_row(o.data_key, verdict, str(o.n_failed), quality)

    console.print(table)

    n_valid = sum(1 for o in outcomes if o.is_valid is True)
    n_rejected = sum(1 for o in outcomes if o.is_valid is False)
    n_error = sum(1 for o in outcomes if o.error is not None)
    summary = (
        f"[bold]{len(outcomes)}[/] captures — "
        f"[green]{n_valid} validated[/], "
        f"[red]{n_rejected} rejected[/], "
        f"[yellow]{n_error} errors[/]"
    )
    if dry_run:
        summary += " [dim](dry run — no tags promoted, no reports written)[/]"
    console.print(summary)


def _build_json(
    outcomes: list[CaptureOutcome], *, prefix: str, dry_run: bool
) -> dict:
    """Assemble the machine-readable report for --json output."""
    return {
        "prefix": prefix,
        "dry_run": dry_run,
        "total": len(outcomes),
        "validated": sum(1 for o in outcomes if o.is_valid is True),
        "rejected": sum(1 for o in outcomes if o.is_valid is False),
        "errors": sum(1 for o in outcomes if o.error is not None),
        "captures": [
            {
                "data_key": o.data_key,
                "is_valid": o.is_valid,
                "quality": o.quality,
                "n_failed": o.n_failed,
                "error": o.error,
            }
            for o in outcomes
        ],
    }


def main(argv: list[str] | None = None, *, reader: CaptureReader | None = None) -> int:
    """Entry point. ``reader`` is injectable for testing (moto-backed)."""
    args = _build_parser().parse_args(argv)
    # Route logs to stderr so stdout stays clean for the table / JSON result.
    configure_logging()
    console = Console()

    # Build the reader (and its StorageClient, which loads settings) unless one
    # was injected. Failure here is a configuration problem -> exit code 2.
    if reader is None:
        try:
            reader = CaptureReader(StorageClient())
        except Exception as exc:
            # Broad catch on purpose: any failure to load settings or build the
            # client is a configuration problem the user must fix -> exit 2.
            if args.json:
                print(json.dumps({"status": "error", "error": str(exc)}))
            else:
                console.print(f"[bold red]✗ Configuration error:[/] {exc}")
            return 2

    # Run the curation pass. A listing failure is an infrastructure problem.
    try:
        outcomes = _validate_prefix(
            reader,
            prefix=args.prefix,
            expected_duration=args.expected_duration,
            dry_run=args.dry_run,
        )
    except StorageError as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}))
        else:
            console.print(f"[bold red]✗ Storage error:[/] {exc}")
        return 1
    except Exception as exc:
        # Defensive catch-all: anything unexpected -> exit 2 rather than a
        # raw traceback in the user's terminal.
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}))
        else:
            console.print(f"[bold red]✗ Unexpected error:[/] {exc}")
        return 2

    # Render.
    if args.json:
        print(json.dumps(_build_json(outcomes, prefix=args.prefix, dry_run=args.dry_run)))
    elif not outcomes:
        console.print(
            f"[yellow]No complete captures found under prefix {args.prefix!r}.[/]"
        )
    else:
        _print_table(console, outcomes, dry_run=args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
