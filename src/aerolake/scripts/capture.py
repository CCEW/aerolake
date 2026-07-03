"""File-driven capture for AeroLake (``aerolake-capture --config x.json``).

The standard way to record: a capture is described once in a JSON file,
validated, and replayed. A config file is reviewable, version-controllable, and
reproducible. For a synthetic capture (no hardware), point the config's
``source`` block at the synthetic generator (see ``examples/``).

Flow
----
A validated :class:`CaptureConfig` is mapped onto the orchestrator. The
descriptive fields (author, description, license, geolocation, annotation,
antenna) are flattened into a :class:`RichMetadata` so everything the user
declares lands in the stored ``.sigmf-meta`` (and the two richest criteria --
location, antenna model -- also become searchable S3 tags).

The capture is *prepared* (acquired + encoded) but nothing is stored until the
operator confirms: a post-capture summary is shown, then "Push to MinIO?". This
deliberate step guards the lakehouse against an acquisition launched and
forgotten. If the operator declines the push, they are offered to keep the
capture on disk (under ``captures/``) so a long recording is not lost;
declining both discards it.

Exit codes
----------
0 : Capture uploaded, saved locally, or intentionally discarded.
1 : Storage layer failure (MinIO unreachable, bucket missing, etc.).
2 : Configuration error (bad path, malformed JSON, schema violation).
3 : Capture or unexpected error.
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from aerolake.common.logging import configure_logging
from aerolake.common.storage import StorageError
from aerolake.producer.capture_config import CaptureConfig
from aerolake.producer.config_loader import ConfigError, load_capture_config
from aerolake.producer.gps import GpsReader, read_geolocation
from aerolake.producer.orchestrator import (
    RichMetadata,
    prepare_capture,
    push_capture,
    save_capture_locally,
)
from aerolake.producer.sigmf_writer import AnnotationFields, AntennaFields
from aerolake.producer.soapy_source import SoapyParams


def _summary_table(config: CaptureConfig) -> Table:
    """Build the pre-capture recap shown to the user."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan")
    table.add_column()

    signal = config.signal_type
    if config.signal_type_detail:
        signal += f" ({config.signal_type_detail})"
    table.add_row("Signal", signal)
    table.add_row("Frequency", f"{config.center_freq / 1e6:.3f} MHz")
    table.add_row("Sample rate", f"{config.sample_rate / 1e6:.1f} MS/s")
    table.add_row("Duration", f"{config.duration_s} s")

    params = config.source_params()
    if isinstance(params, SoapyParams):
        table.add_row("Source", f"Real SDR ({params.driver}, AGC={params.agc})")
    else:
        table.add_row("Source", "Synthetic")

    if config.location is not None:
        table.add_row("Location", config.location.name)
        table.add_row("Motion", "dynamic" if config.location.mobile else "static")
        if config.location.gps:
            table.add_row("Position", "live GPS fix (gpsd)")
        elif config.location.geolocation is not None:
            g = config.location.geolocation
            table.add_row("Position", f"{g.latitude:.4f}, {g.longitude:.4f}")
    else:
        table.add_row("Location", "(not specified)")

    return table


def _resolve_geolocation(
    config: CaptureConfig,
    *,
    gps_reader: GpsReader | None = None,
) -> dict[str, object] | None:
    """Resolve the capture's geolocation: live gpsd fix, manual point, or none.

    If the config requests a live fix (``location.gps``), read one from gpsd —
    this is the recorder's *actual* position, mapped to a SigMF-conformant
    ``core:geolocation`` (ADR-016). The reader is injectable so this is testable
    without a daemon. Otherwise fall back to the hand-typed point, or to nothing.

    Returns None when there is no location block, no manual point, or gpsd has no
    fix. Propagates RuntimeError if a *requested* live fix cannot be read (gpsd
    unreachable), so the CLI can report it rather than silently dropping position.
    """
    loc = config.location
    if loc is None:
        return None
    if loc.gps:
        # read_geolocation returns None on "no fix" and raises on "gpsd down".
        return read_geolocation(reader=gps_reader)
    if loc.geolocation is not None:
        return loc.geolocation.to_geojson()
    return None


def _build_rich_metadata(
    config: CaptureConfig, geolocation: dict[str, object] | None
) -> RichMetadata:
    """Flatten the config's descriptive blocks into a RichMetadata.

    Flattens the annotation and antenna sub-models into the plain dicts the
    encoder expects, dropping any field the user left unset so absent values
    stay truly absent in the .sigmf-meta. ``geolocation`` is resolved by the
    caller (a manual point or a live gpsd fix) and threaded straight through.
    """
    annotation: AnnotationFields | None = None
    ann: AnnotationFields = {}
    if config.annotation is not None:
        a = config.annotation
        if a.label is not None:
            ann["label"] = a.label
        if a.comment is not None:
            ann["comment"] = a.comment
        if a.freq_lower_edge is not None and a.freq_upper_edge is not None:
            ann["freq_lower_edge"] = a.freq_lower_edge
            ann["freq_upper_edge"] = a.freq_upper_edge
    # Antenna pointing fields live in the annotation per the SigMF spec, even
    # when the user supplied no annotation block of their own -- otherwise they
    # would be silently dropped.
    if config.antenna is not None:
        if config.antenna.polarization is not None:
            ann["polarization"] = config.antenna.polarization
        if config.antenna.azimuth_angle is not None:
            ann["azimuth_angle"] = config.antenna.azimuth_angle
        if config.antenna.elevation_angle is not None:
            ann["elevation_angle"] = config.antenna.elevation_angle
    annotation = ann or None

    antenna: AntennaFields | None = None
    if config.antenna is not None:
        # Global antenna scalars only; pointing fields already went to the
        # annotation above. Dump the model, drop None, drop the three pointing
        # keys (they are not Global antenna fields).
        ant: AntennaFields = {}
        dumped = config.antenna.model_dump(exclude_none=True)
        for key, value in dumped.items():
            if key in ("polarization", "azimuth_angle", "elevation_angle"):
                continue
            ant[key] = value  # type: ignore[literal-required]
        antenna = ant or None

    return RichMetadata(
        author=config.author,
        description=config.description,
        license=config.license,
        geolocation=geolocation,
        annotation=annotation,
        antenna=antenna,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aerolake-capture",
        description="Record a capture described by a JSON configuration file.",
    )
    parser.add_argument(
        "--config",
        required=True,
        metavar="PATH",
        help="Path to a JSON capture configuration file.",
    )
    args = parser.parse_args(argv)

    configure_logging()
    console = Console()

    # --- Load + validate -------------------------------------------------
    try:
        config = load_capture_config(args.config)
    except ConfigError as exc:
        console.print(f"[bold red]✗ Configuration error:[/] {exc}")
        return 2

    console.print("[bold cyan]AeroLake — capture from config[/]")
    console.print(_summary_table(config))

    # --- Prepare (capture + encode), store nothing yet -------------------
    location_name = config.location.name if config.location is not None else None
    mobile = config.location.mobile if config.location is not None else False

    # Resolve geolocation before capturing: a live gpsd fix (location.gps) or a
    # hand-typed point. A live fix is the recorder's actual position; if gpsd is
    # unreachable we stop (exit 3) rather than silently record without it.
    try:
        geolocation = _resolve_geolocation(config)
    except RuntimeError as exc:
        console.print(f"[bold red]✗ GPS error:[/] {exc}")
        return 3
    if config.location is not None and config.location.gps and geolocation is None:
        console.print(
            "[yellow]⚠ GPS requested but no fix available — "
            "the capture will carry no geolocation.[/]"
        )

    console.print("\n[bold cyan]>[/] Recording...")
    try:
        prepared = prepare_capture(
            signal_type=config.signal_type,
            signal_type_detail=config.signal_type_detail,
            duration_s=config.duration_s,
            sample_rate=config.sample_rate,
            center_freq=config.center_freq,
            source=config.source_params(),
            operator=config.operator,
            location=location_name,
            mobile=mobile,
            rich=_build_rich_metadata(config, geolocation),
        )
    except Exception as exc:
        console.print(f"[bold red]✗ Capture error:[/] {exc}")
        return 3

    # --- Post-capture summary --------------------------------------------
    # Show what was actually recorded so the operator decides in full
    # knowledge before anything reaches MinIO. This is the guard against an
    # acquisition launched and forgotten silently filling the lakehouse.
    recap = Table(show_header=False, box=None, padding=(0, 2))
    recap.add_column(style="cyan")
    recap.add_column()
    recap.add_row("Samples", f"{prepared.sample_count:,}")
    recap.add_row("Size", f"{prepared.size_bytes / 1e6:.1f} MB")
    actual_duration = prepared.sample_count / config.sample_rate if config.sample_rate else 0.0
    recap.add_row("Duration", f"{actual_duration:.2f} s")
    if prepared.overflow_count is not None:
        recap.add_row("Overflows", str(prepared.overflow_count))
    console.print("\n[bold]Captured[/]")
    console.print(recap)

    # --- Deliberate push (the validate-before-upload guard) --------------
    if Confirm.ask("\nPush this capture to MinIO?", default=True):
        try:
            result = push_capture(prepared, with_preview=True)
        except StorageError as exc:
            console.print(f"[bold red]✗ Storage error:[/] {exc}")
            return 1
        except Exception as exc:
            console.print(f"[bold red]✗ Unexpected error:[/] {exc}")
            return 3

        console.print("[bold green]✓ Capture uploaded[/]")
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="cyan")
        table.add_column()
        table.add_row("Session ID", result.session_id)
        table.add_row("Data key", result.data_key)
        table.add_row("Samples", f"{result.sample_count:,}")
        console.print(table)
        return 0

    # Not pushed: offer to keep it on disk so a (possibly long) capture is not
    # lost. Declining both simply discards the in-memory capture.
    if Confirm.ask("Keep it locally instead?", default=True):
        try:
            out_dir = save_capture_locally(prepared)
        except OSError as exc:
            console.print(f"[bold red]✗ Could not save locally:[/] {exc}")
            return 3
        console.print(f"[bold green]✓ Saved locally:[/] {out_dir}")
        return 0

    console.print("[yellow]Discarded — nothing was stored.[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
