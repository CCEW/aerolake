"""Tests for the guided capture menu (aerolake-record).

The interactive prompts read stdin, so rather than drive the whole console
flow we test the part that actually matters for data quality: the menu choices
must stay consistent with the producer presets (same signal_type, frequency,
sample rate), and offer an "other" escape hatch. A drift between the two paths
would mean a capture recorded via the menu gets different metadata than the
same one via aerolake-producer — exactly the inconsistency this whole effort
exists to prevent.
"""

from __future__ import annotations

from aerolake.scripts.producer import PRESETS
from aerolake.scripts.record import LOCATION_CHOICES, SIGNAL_CHOICES


def test_menu_covers_the_mandate_signals() -> None:
    types = {c.signal_type for c in SIGNAL_CHOICES}
    assert types == {"gnss_l1", "iridium", "starlink"}


def test_menu_choices_match_producer_presets() -> None:
    by_type = {c.signal_type: c for c in SIGNAL_CHOICES}
    for preset in PRESETS.values():
        choice = by_type[preset.signal_type]
        assert choice.center_freq == preset.center_freq
        assert choice.sample_rate == preset.sample_rate


def test_every_choice_has_a_human_label() -> None:
    for choice in SIGNAL_CHOICES:
        assert choice.label
        assert len(choice.label) > 3


def test_location_menu_has_the_lab_places() -> None:
    assert "LASSENA" in LOCATION_CHOICES
    assert "LASSENA rooftop" in LOCATION_CHOICES
    # Moving contexts are offered as locations; mobility stays a separate
    # question so a car/plane capture is still explicitly marked mobile.
    assert "In a car" in LOCATION_CHOICES
    assert "In a plane" in LOCATION_CHOICES
