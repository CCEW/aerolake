"""Unit tests for the pure DSP/plot functions in aerolake.gui.plots.

We test the compute layer numerically (shapes + that a known tone lands at the
right frequency) and sanity-check that the figure builders return Plotly
figures without raising, including on empty input. The Streamlit app itself is
not unit-tested (it's thin glue over these functions).
"""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
import pytest

from aerolake.gui import plots
from aerolake.producer.synthetic import generate_tone

# A realistic tone: 100 kHz offset from a 1.5 GHz center, sampled at 2 MS/s.
SAMPLE_RATE = 2_000_000.0
CENTER_FREQ = 1_500_000_000.0
TONE_OFFSET = 100_000.0


@pytest.fixture
def tone() -> np.ndarray:
    """20 000 deterministic IQ samples of a clean -20 dBFS tone."""
    signal = generate_tone(
        duration_s=0.01,
        sample_rate=SAMPLE_RATE,
        center_freq=CENTER_FREQ,
        tone_offset_hz=TONE_OFFSET,
        snr_db=30.0,
        seed=42,
    )
    return signal.samples


# --- compute_spectrum ----------------------------------------------------

def test_spectrum_shapes_match_nperseg(tone) -> None:
    freqs, power_db = plots.compute_spectrum(
        tone, SAMPLE_RATE, CENTER_FREQ, nperseg=1024
    )
    assert freqs.shape == (1024,)
    assert power_db.shape == (1024,)


def test_spectrum_peak_lands_on_the_tone(tone) -> None:
    """The strongest bin should sit at center + tone offset (within one bin)."""
    nperseg = 1024
    freqs, power_db = plots.compute_spectrum(
        tone, SAMPLE_RATE, CENTER_FREQ, nperseg=nperseg
    )
    peak_freq = freqs[int(np.argmax(power_db))]
    expected = CENTER_FREQ + TONE_OFFSET
    bin_width = SAMPLE_RATE / nperseg
    assert abs(peak_freq - expected) <= 2 * bin_width


def test_spectrum_is_normalised_to_peak_zero_db(tone) -> None:
    _, power_db = plots.compute_spectrum(tone, SAMPLE_RATE, CENTER_FREQ)
    assert power_db.max() == pytest.approx(0.0)


def test_spectrum_handles_signal_shorter_than_nperseg() -> None:
    short = np.ones(10, dtype=np.complex64)
    freqs, power_db = plots.compute_spectrum(short, SAMPLE_RATE, nperseg=1024)
    # nperseg is clamped to the signal length.
    assert freqs.shape == (10,)
    assert power_db.shape == (10,)


def test_spectrum_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        plots.compute_spectrum(np.array([], dtype=np.complex64), SAMPLE_RATE)


# --- compute_spectrogram -------------------------------------------------

def test_spectrogram_shapes_are_consistent(tone) -> None:
    times, freqs, power = plots.compute_spectrogram(
        tone, SAMPLE_RATE, CENTER_FREQ, nperseg=256, max_frames=100
    )
    assert freqs.shape == (256,)
    assert power.shape == (len(times), 256)


def test_spectrogram_respects_max_frames(tone) -> None:
    """The frame count never exceeds the requested cap."""
    times, _, power = plots.compute_spectrogram(
        tone, SAMPLE_RATE, nperseg=256, max_frames=50
    )
    assert len(times) <= 50
    assert power.shape[0] == len(times)


# --- describe_signal -----------------------------------------------------

def test_describe_signal_mentions_the_tone_frequency(tone) -> None:
    """The plain-language summary should name the tone's frequency in MHz."""
    summary = plots.describe_signal(tone, SAMPLE_RATE, CENTER_FREQ)
    # center + offset = 1500.1 MHz → the rounded value should appear.
    assert "1500.100 MHz" in summary
    assert "above the noise floor" in summary


def test_describe_signal_handles_empty() -> None:
    summary = plots.describe_signal(np.array([], dtype=np.complex64), SAMPLE_RATE)
    assert "No samples" in summary


# --- figure builders -----------------------------------------------------

def test_figures_return_plotly_objects(tone) -> None:
    assert isinstance(plots.spectrum_figure(tone, SAMPLE_RATE, CENTER_FREQ), go.Figure)
    assert isinstance(
        plots.spectrogram_figure(tone, SAMPLE_RATE, CENTER_FREQ), go.Figure
    )
    assert isinstance(plots.constellation_figure(tone), go.Figure)


def test_constellation_downsamples_large_inputs(tone) -> None:
    """A capture larger than max_points is strided down to max_points."""
    fig = plots.constellation_figure(tone, max_points=1000)
    # The single Scattergl trace should carry exactly max_points markers.
    assert len(fig.data[0].x) == 1000


def test_figures_handle_empty_input() -> None:
    empty = np.array([], dtype=np.complex64)
    for fig in (
        plots.spectrum_figure(empty, SAMPLE_RATE),
        plots.spectrogram_figure(empty, SAMPLE_RATE),
        plots.constellation_figure(empty),
    ):
        assert isinstance(fig, go.Figure)
        # Empty input → a placeholder with a centered annotation, no traces.
        assert len(fig.data) == 0
        assert fig.layout.annotations
