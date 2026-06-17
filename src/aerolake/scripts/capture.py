"""File-driven capture for AeroLake (``aerolake-capture --config x.json``).

The declarative counterpart to the interactive ``aerolake-record`` menu: a
capture is described once in a JSON file, validated, and replayed without any
prompts. This is the path meant to become the standard way to record -- a
config file is reviewable, version-controllable, and reproducible, where a
menu session is not.

Scope of this step
------------------
A validated :class:`CaptureConfig` is mapped onto :func:`capture_and_upload`.
The descriptive fields (author, description, license, geolocation, annotation,
antenna) are flattened into a :class:`RichMetadata` and threaded all the way to
the SigMF encoder, so everything the user declares lands in the stored
``.sigmf-meta`` (and the two richest criteria -- location, antenna model --
also become searchable S3 tags). Push to MinIO stays automatic here; the
deliberate "validate before push" confirmation is a later step.

Exit codes
----------
0 : Capture uploaded successfully.
1 : Storage layer failure (MinIO unreachable, bucket missing, etc.).
2 : Configuration error (bad path, malformed JSON, schema violation).
3 : Unexpected error.
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.table import Table

from aerolake.common.logging import configure_logging
from aerolake.common.storage import StorageError
from aerolake.producer.capture_config import CaptureConfig
from aerolake.producer.config_loader import ConfigError, load_capture_config
from aerolake.producer.orchestrator import RichMetadata, capture_and_upload
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
    else:
        table.add_row("Location", "(not specified)")

    return table


def _build_rich_metadata(config: CaptureConfig) -> RichMetadata:
    """Flatten the config's descriptive blocks into a RichMetadata.

    Pulls geolocation into GeoJSON, and flattens the annotation and antenna
    sub-models into the plain dicts the encoder expects, dropping any field the
    user left unset so absent values stay truly absent in the .sigmf-meta.
    """
    geolocation: dict[str, object] | None = None
    if config.location is not None and config.location.geolocation is not None:
        geolocation = config.location.geolocation.to_geojson()

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

    # --- Map to the existing engine --------------------------------------
    # Only the fields capture_and_upload already understands are forwarded.
    # Richer config fields (author/description/license/geolocation/annotation/
    # antenna) are validated and available on `config` but threaded into the
    # encoder in a later step.
    location_name = config.location.name if config.location is not None else None
    mobile = config.location.mobile if config.location is not None else False

    console.print("\n[bold cyan]>[/] Recording...")
    try:
        result = capture_and_upload(
            signal_type=config.signal_type,
            signal_type_detail=config.signal_type_detail,
            duration_s=config.duration_s,
            sample_rate=config.sample_rate,
            center_freq=config.center_freq,
            source=config.source_params(),
            operator=config.operator,
            location=location_name,
            mobile=mobile,
            rich=_build_rich_metadata(config),
        )
    except StorageError as exc:
        console.print(f"[bold red]✗ Storage error:[/] {exc}")
        return 1
    except Exception as exc:
        console.print(f"[bold red]✗ Unexpected error:[/] {exc}")
        return 3

    console.print("[bold green]✓ Capture recorded[/]")
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("Session ID", result.session_id)
    table.add_row("Data key", result.data_key)
    table.add_row("Samples", f"{result.sample_count:,}")
    console.print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
