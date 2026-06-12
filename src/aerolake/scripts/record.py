"""Interactive, guided capture for AeroLake (``aerolake-record``).

A question-and-answer front end over the same capture engine as
``aerolake-producer``. It exists so that anyone on the team — including a
newcomer who has never seen the code — can record a properly tagged capture
without remembering any command-line flags.

Design choices that serve data retrievability
----------------------------------------------
The whole point of the lab's metadata effort is that captures stay findable.
Free-text answers are the enemy of that ("gnss", "GNSS", "gps L1" all become
different, unsearchable values). So this menu uses numbered choices for every
field whose values are known in advance (signal type, mobile/fixed, location),
and only falls back to free text where the value genuinely cannot be predicted
(the "Other" entries). Numbered choice in, consistent metadata out.

This script is intentionally separate from ``aerolake-producer``: that one stays
the scriptable/expert entry point with explicit flags; this one is the friendly
guided path. Both call the same :func:`capture_and_upload`, so they write
identical metadata.

Exit codes
----------
0 : Capture uploaded successfully (or the user chose to cancel).
1 : Storage layer failure (MinIO unreachable, bucket missing, etc.).
2 : Unexpected error.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from rich.console import Console
from rich.prompt import Confirm, FloatPrompt, Prompt
from rich.table import Table

from aerolake.common.logging import configure_logging
from aerolake.common.storage import StorageError
from aerolake.producer.orchestrator import capture_and_upload
from aerolake.producer.soapy_source import SoapyParams
from aerolake.producer.synthetic import SyntheticParams


@dataclass(frozen=True)
class SignalChoice:
    """One entry in the signal-type menu."""

    signal_type: str
    center_freq: float
    sample_rate: float
    label: str


SIGNAL_CHOICES: list[SignalChoice] = [
    SignalChoice("gnss_l1", 1_575_420_000, 2_000_000, "GNSS L1 (1575.42 MHz, 2 MHz)"),
    SignalChoice("iridium", 1_626_270_000, 2_000_000, "Iridium (1626.27 MHz, 2 MHz)"),
    SignalChoice("starlink", 1_400_000_000, 25_000_000, "Starlink (1400 MHz, 25 MHz)"),
]


LOCATION_CHOICES: list[str] = [
    "LASSENA",
    "LASSENA rooftop",
    "In a car",
    "In a plane",
]


def _ask_signal(console: Console) -> dict:
    """Numbered menu for the signal type. Returns capture kwargs.

    Choosing "Other" asks for a free-text label plus the center frequency and
    sample rate, since those can't be predicted. Everything else comes straight
    from the known choices, so the stored signal_type is always consistent.
    """
    console.print("\n[bold]Which signal are you recording?[/]")
    for i, choice in enumerate(SIGNAL_CHOICES, start=1):
        console.print(f"  [cyan]{i}[/] {choice.label}")
    other_index = len(SIGNAL_CHOICES) + 1
    console.print(f"  [cyan]{other_index}[/] Other (specify)")

    valid = [str(i) for i in range(1, other_index + 1)]
    pick = Prompt.ask("Your choice", choices=valid, default="1")

    if pick == str(other_index):
        detail = Prompt.ask("  Describe the signal (free text)")
        center_freq = FloatPrompt.ask("  Center frequency in Hz (e.g. 433e6)")
        sample_rate = FloatPrompt.ask("  Sample rate in Hz (e.g. 2e6)")
        return {
            "signal_type": "other",
            "signal_type_detail": detail,
            "center_freq": center_freq,
            "sample_rate": sample_rate,
        }

    choice = SIGNAL_CHOICES[int(pick) - 1]
    return {
        "signal_type": choice.signal_type,
        "center_freq": choice.center_freq,
        "sample_rate": choice.sample_rate,
    }


def _ask_source(console: Console) -> SyntheticParams | SoapyParams:
    """Numbered menu for the acquisition source.

    Synthetic needs no hardware and is the safe default. The real-SDR
    entries map to SoapySDR driver keys; picking one asks only for the
    gain (the one knob that varies per band/antenna), keeping the guided
    flow short. The antenna port stays on the device default.
    """
    console.print("\n[bold]Signal source?[/]")
    console.print("  [cyan]1[/] Synthetic (no hardware needed)")
    console.print("  [cyan]2[/] Real SDR — RTL-SDR")
    console.print("  [cyan]3[/] Real SDR — BladeRF")
    pick = Prompt.ask("Your choice", choices=["1", "2", "3"], default="1")

    if pick == "1":
        return SyntheticParams()

    driver = "rtlsdr" if pick == "2" else "bladerf"
    gain = FloatPrompt.ask(
        "  Gain in dB (GPS L1 with an active antenna needs a high gain)",
        default=40.0,
    )
    return SoapyParams(driver=driver, gain=gain)


def _ask_mobile(console: Console) -> bool:
    """Two-choice fixed/mobile question — no free text, no inconsistency."""
    console.print("\n[bold]Was the acquisition fixed or mobile?[/]")
    console.print("  [cyan]1[/] Fixed")
    console.print("  [cyan]2[/] Mobile")
    return Prompt.ask("Your choice", choices=["1", "2"], default="1") == "2"


def _ask_location(console: Console) -> str | None:
    """Numbered menu for the recording location.

    The lab has a handful of recurring locations, so a numbered list keeps them
    consistent (the same place is always spelled the same way, stays
    searchable). "Other" falls back to free text for anything not listed, and
    "Skip" stores no location at all — useful when it genuinely doesn't apply.
    Mobility is asked separately, so a moving acquisition is marked mobile on
    its own question regardless of the place picked here.
    """
    console.print("\n[bold]Where are you recording?[/]")
    for i, loc in enumerate(LOCATION_CHOICES, start=1):
        console.print(f"  [cyan]{i}[/] {loc}")
    other_index = len(LOCATION_CHOICES) + 1
    skip_index = other_index + 1
    console.print(f"  [cyan]{other_index}[/] Other (specify)")
    console.print(f"  [cyan]{skip_index}[/] Skip (no location)")

    valid = [str(i) for i in range(1, skip_index + 1)]
    pick = Prompt.ask("Your choice", choices=valid, default=str(skip_index))

    if pick == str(skip_index):
        return None
    if pick == str(other_index):
        answer = Prompt.ask("  Describe the location (free text)").strip()
        return answer or None
    return LOCATION_CHOICES[int(pick) - 1]


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    console = Console()

    console.print("[bold cyan]AeroLake — guided capture[/]")
    console.print("[dim]Answer the prompts; the operator is taken from your session.[/]")

    source = _ask_source(console)
    signal = _ask_signal(console)
    duration_s = FloatPrompt.ask("\n[bold]Capture duration in seconds[/]", default=1.0)
    mobile = _ask_mobile(console)
    location = _ask_location(console)

    console.print("\n[bold]Summary[/]")
    recap = Table(show_header=False, box=None, padding=(0, 2))
    recap.add_column(style="cyan")
    recap.add_column()
    recap.add_row("Signal", signal["signal_type"]
                  + (f" ({signal['signal_type_detail']})" if "signal_type_detail" in signal else ""))
    recap.add_row("Frequency", f"{signal['center_freq']/1e6:.3f} MHz")
    recap.add_row("Sample rate", f"{signal['sample_rate']/1e6:.1f} MS/s")
    recap.add_row("Duration", f"{duration_s} s")
    recap.add_row("Mobile", "yes" if mobile else "no")
    recap.add_row("Location", location or "(not specified)")
    if isinstance(source, SoapyParams):
        recap.add_row("Source", f"Real SDR ({source.driver}, gain {source.gain:.0f} dB)")
    else:
        recap.add_row("Source", "Synthetic")
    console.print(recap)

    if not Confirm.ask("\nStart the recording?", default=True):
        console.print("[yellow]Cancelled.[/]")
        return 0

    console.print("\n[bold cyan]>[/] Recording...")
    try:
        result = capture_and_upload(
            duration_s=duration_s,
            mobile=mobile,
            location=location,
            source=source,
            **signal,
        )
    except StorageError as exc:
        console.print(f"[bold red]✗ Storage error:[/] {exc}")
        return 1
    except Exception as exc:
        console.print(f"[bold red]✗ Unexpected error:[/] {exc}")
        return 2

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
