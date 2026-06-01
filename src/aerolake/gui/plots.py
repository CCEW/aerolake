"""Pure plotting/DSP functions for the AeroLake GUI.

Design choice mirrored from ``aerolake.quality.metrics``: everything here is a
**pure function**. No Streamlit, no MinIO, no global state — just
``samples in → (numbers | Plotly figure) out``. That keeps the digital signal
processing (DSP) unit-testable on synthetic arrays, and lets the Streamlit app
(:mod:`app`) stay a thin layer of glue.

The module offers two layers:

  1. ``compute_*`` functions — take IQ samples, return plain numpy arrays
     (frequencies, times, power). Easy to assert on in tests.
  2. ``*_figure`` functions — call the compute layer and wrap the result in a
     themed Plotly figure for display.

A note on the signal model
--------------------------
IQ samples are complex64: ``z[n] = I[n] + j·Q[n]``. The complex (analytic)
representation means the spectrum is *two-sided* and not symmetric — a tone at
+100 kHz is distinct from one at -100 kHz (unlike a real-valued signal). That
is exactly why SDRs sample in IQ: it captures the full bandwidth around the
center frequency, positive AND negative offsets.
"""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go

from aerolake.gui import theme

# A tiny floor added before every log10 so a zero-power bin becomes a large
# negative dB value instead of raising / returning -inf.
_DB_EPS = 1e-20


# ---------------------------------------------------------------------------
# Plain-language explanations (for the GUI's "Explain" mode)
# ---------------------------------------------------------------------------
# Deliberately jargon-light: written so someone outside RF can understand what
# each view shows and how to read it. The app renders these next to each chart.

EXPLANATIONS: dict[str, str] = {
    "spectrum": (
        "<b>Spectrum</b> — which radio frequencies the signal uses. "
        "Left-to-right is frequency (in MHz); up-down is power (how strong, in "
        "dB). A <b>tall peak</b> means a strong signal at that frequency; the "
        "flat bottom is background noise. Think of it as the signal's "
        "'fingerprint' in frequency."
    ),
    "spectrogram": (
        "<b>Spectrogram</b> — the same idea, but showing how the spectrum "
        "changes <b>over time</b>. Left-to-right is time, up-down is frequency, "
        "and <b>colour</b> is power (bright = strong, dark = quiet). It reveals "
        "signals that switch on/off or drift, which a single spectrum hides."
    ),
    "constellation": (
        "<b>IQ constellation</b> — every sample is a dot placed by its two "
        "parts (I and Q). A clean tone traces a <b>ring</b>; a digital "
        "transmission shows tight <b>clusters</b>; pure noise is a fuzzy "
        "<b>blob</b>. The shape hints at how information is encoded."
    ),
}


def describe_signal(
    samples: np.ndarray,
    sample_rate: float,
    center_freq: float = 0.0,
    *,
    nperseg: int = 1024,
) -> str:
    """Return a one-sentence, plain-language summary of the signal.

    Computes where the strongest energy sits and how far it stands above the
    noise floor, phrased for a non-specialist. Example output:
    "Strongest energy near 1575.520 MHz, about 30 dB above the noise floor."
    """
    if len(samples) == 0:
        return "No samples to describe."

    freqs_hz, power_db = compute_spectrum(
        samples, sample_rate, center_freq, nperseg=nperseg
    )
    # The spectrum is normalised so the peak sits at 0 dB. The median bin is a
    # robust stand-in for the "noise floor", so the peak's height above it
    # (= -median) is a simple, honest "how much does the signal stand out".
    peak_freq_mhz = float(freqs_hz[int(np.argmax(power_db))]) / 1e6
    noise_floor_db = float(np.median(power_db))
    snr_above_db = -noise_floor_db
    return (
        f"Strongest energy near {peak_freq_mhz:.3f} MHz, "
        f"about {snr_above_db:.0f} dB above the noise floor."
    )


# ---------------------------------------------------------------------------
# Compute layer — pure numpy, no plotting
# ---------------------------------------------------------------------------

def compute_spectrum(
    samples: np.ndarray,
    sample_rate: float,
    center_freq: float = 0.0,
    *,
    nperseg: int = 1024,
) -> tuple[np.ndarray, np.ndarray]:
    """Estimate the power spectrum via Welch's method (averaged periodograms).

    Why Welch and not a single big FFT? A single FFT of a noisy signal gives a
    very *spiky* noise floor — each frequency bin is one noisy estimate. Welch
    splits the signal into overlapping segments, FFTs each, and **averages** the
    power across segments. Averaging many noisy estimates reduces the variance,
    so the noise floor becomes smooth and a real tone stands out clearly. The
    trade-off is frequency resolution (set by ``nperseg``): shorter segments →
    smoother but coarser spectrum.

    Parameters
    ----------
    samples
        1-D complex64 IQ samples.
    sample_rate
        Sample rate in Hz. Sets the frequency span: ±sample_rate/2 around
        the center.
    center_freq
        RF center frequency in Hz. The baseband spectrum (centered on 0) is
        shifted by this so the x-axis reads in real RF terms.
    nperseg
        FFT/segment length in samples. Clamped to the signal length.

    Returns
    -------
    (freqs_hz, power_db)
        ``freqs_hz``: the frequency of each bin in Hz (absolute RF, sorted
        ascending after an fftshift). ``power_db``: power per bin in dB,
        normalised so the peak sits at 0 dB (a relative scale — what matters
        visually is the shape and the dynamic range, not an absolute level).
    """
    n = len(samples)
    if n == 0:
        raise ValueError("compute_spectrum: empty sample array")

    # Can't use a segment longer than the data we have.
    nperseg = min(nperseg, n)

    # Hann window: tapers each segment to zero at its edges. Without it, the
    # abrupt segment boundaries leak energy across the spectrum ("spectral
    # leakage"), smearing sharp tones. The window suppresses that leakage.
    window = np.hanning(nperseg)

    # 50% overlap between segments is the classic Welch choice: it recovers the
    # information the window attenuates near segment edges.
    step = max(1, nperseg // 2)

    # Accumulate the power spectrum of each segment, then average.
    psd_accumulator = np.zeros(nperseg)
    n_segments = 0
    for start in range(0, n - nperseg + 1, step):
        segment = samples[start : start + nperseg] * window
        # np.fft.fft gives the two-sided spectrum with 0 Hz at index 0;
        # fftshift rotates it so 0 Hz (DC) sits in the middle, negative
        # frequencies on the left, positive on the right — the natural way to
        # look at an IQ spectrum.
        spectrum = np.fft.fftshift(np.fft.fft(segment))
        psd_accumulator += np.abs(spectrum) ** 2
        n_segments += 1

    # If the signal was shorter than one full segment, the loop ran zero times;
    # fall back to a single (zero-padded) FFT so we still return something.
    if n_segments == 0:
        segment = samples[:nperseg] * window[: len(samples)]
        spectrum = np.fft.fftshift(np.fft.fft(segment, n=nperseg))
        psd_accumulator = np.abs(spectrum) ** 2
        n_segments = 1

    psd = psd_accumulator / n_segments

    # Frequency axis: fftfreq gives bin frequencies for the given sample rate;
    # fftshift matches the shift we applied to the spectrum; + center_freq
    # turns baseband offsets into absolute RF frequencies.
    freqs_hz = np.fft.fftshift(np.fft.fftfreq(nperseg, d=1.0 / sample_rate))
    freqs_hz = freqs_hz + center_freq

    # Convert to dB relative to the peak (peak → 0 dB). 10·log10 because PSD is
    # already a power quantity (squared magnitude), not an amplitude.
    power_db = 10.0 * np.log10(psd + _DB_EPS)
    power_db = power_db - power_db.max()

    return freqs_hz, power_db


def compute_spectrogram(
    samples: np.ndarray,
    sample_rate: float,
    center_freq: float = 0.0,
    *,
    nperseg: int = 256,
    max_frames: int = 400,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute a Short-Time Fourier Transform (STFT) → power spectrogram.

    A spectrogram is "how the spectrum changes over time": we slide a window
    along the signal, FFT each slice, and stack the results. The result is a 2-D
    map — frequency (y) vs time (x) vs power (colour) — which reveals bursts,
    drifting tones, and hopping signals that a single averaged spectrum hides.

    ``max_frames`` caps the number of time slices so a multi-million-sample
    capture doesn't produce an enormous image: we widen the hop between slices
    to keep the frame count bounded (a deliberate, documented down-sampling).

    Returns
    -------
    (times_s, freqs_hz, power_db)
        ``times_s`` (length T): start time of each frame in seconds.
        ``freqs_hz`` (length F): bin frequencies in absolute RF Hz.
        ``power_db`` (shape [T, F]): power per (time, freq) cell in dB,
        normalised so the global peak is 0 dB.
    """
    n = len(samples)
    if n == 0:
        raise ValueError("compute_spectrogram: empty sample array")

    nperseg = min(nperseg, n)
    window = np.hanning(nperseg)

    # How many frames would a 50%-overlap STFT produce, and how big a hop do we
    # need so we stay under max_frames? We pick the larger of the two so we
    # never exceed the cap, while keeping at least 50% overlap for short signals.
    base_step = max(1, nperseg // 2)
    span = max(1, n - nperseg)
    step = max(base_step, int(np.ceil(span / max(1, max_frames - 1))))

    frame_starts = list(range(0, n - nperseg + 1, step))
    if not frame_starts:  # signal shorter than one segment → a single frame
        frame_starts = [0]

    # Build the [time, freq] power matrix frame by frame.
    power = np.empty((len(frame_starts), nperseg))
    for i, start in enumerate(frame_starts):
        segment = samples[start : start + nperseg]
        if len(segment) < nperseg:  # pad the final short frame with zeros
            segment = np.pad(segment, (0, nperseg - len(segment)))
        spectrum = np.fft.fftshift(np.fft.fft(segment * window))
        power[i] = np.abs(spectrum) ** 2

    times_s = np.array(frame_starts, dtype=float) / sample_rate
    freqs_hz = np.fft.fftshift(np.fft.fftfreq(nperseg, d=1.0 / sample_rate))
    freqs_hz = freqs_hz + center_freq

    power_db = 10.0 * np.log10(power + _DB_EPS)
    power_db = power_db - power_db.max()

    return times_s, freqs_hz, power_db


# ---------------------------------------------------------------------------
# Figure layer — wrap the compute results in themed Plotly figures
# ---------------------------------------------------------------------------

def spectrum_figure(
    samples: np.ndarray,
    sample_rate: float,
    center_freq: float = 0.0,
    *,
    nperseg: int = 1024,
) -> go.Figure:
    """Build a power-spectrum line chart (dB vs MHz)."""
    template = theme.register_theme()
    if len(samples) == 0:
        return _empty_figure("No samples to display", template)

    freqs_hz, power_db = compute_spectrum(
        samples, sample_rate, center_freq, nperseg=nperseg
    )
    freqs_mhz = freqs_hz / 1e6  # Hz → MHz for a readable axis

    fig = go.Figure()
    # "Glow" underlay: a wide, translucent copy of the line drawn first, so the
    # crisp line on top appears to emit a soft cyan halo (a HUD-ish touch).
    fig.add_trace(
        go.Scatter(
            x=freqs_mhz,
            y=power_db,
            mode="lines",
            line=dict(color=theme.ACCENT, width=6),
            opacity=0.25,
            hoverinfo="skip",
            showlegend=False,
        )
    )
    # The crisp spectrum line on top.
    fig.add_trace(
        go.Scatter(
            x=freqs_mhz,
            y=power_db,
            mode="lines",
            line=dict(color=theme.ACCENT, width=1.5),
            name="Power",
            hovertemplate="%{x:.4f} MHz<br>%{y:.1f} dB<extra></extra>",
        )
    )
    fig.update_layout(
        template=template,
        title="Power spectrum (Welch)",
        xaxis_title="Frequency (MHz)",
        yaxis_title="Power (dB, rel. peak)",
        showlegend=False,
    )
    return fig


def spectrogram_figure(
    samples: np.ndarray,
    sample_rate: float,
    center_freq: float = 0.0,
    *,
    nperseg: int = 256,
    max_frames: int = 400,
) -> go.Figure:
    """Build a spectrogram heatmap (time x frequency x power)."""
    template = theme.register_theme()
    if len(samples) == 0:
        return _empty_figure("No samples to display", template)

    times_s, freqs_hz, power_db = compute_spectrogram(
        samples, sample_rate, center_freq, nperseg=nperseg, max_frames=max_frames
    )
    fig = go.Figure(
        go.Heatmap(
            # power_db is [time, freq]; transpose so freq is the y-axis.
            z=power_db.T,
            x=times_s * 1e3,        # seconds → milliseconds
            y=freqs_hz / 1e6,       # Hz → MHz
            colorscale=theme.HEATMAP_COLORSCALE,
            colorbar=dict(title="dB"),
        )
    )
    fig.update_layout(
        template=template,
        title="Spectrogram (STFT)",
        xaxis_title="Time (ms)",
        yaxis_title="Frequency (MHz)",
    )
    return fig


def constellation_figure(
    samples: np.ndarray,
    *,
    max_points: int = 5000,
) -> go.Figure:
    """Build an IQ constellation scatter (Q vs I).

    Plots each sample as a point at (I, Q). For a clean tone you get a ring (the
    phase rotates, the magnitude is constant); for a digital modulation you see
    the characteristic cluster pattern (QPSK → 4 clouds, etc.); for pure noise,
    a fuzzy blob. Large captures are down-sampled to ``max_points`` so the
    browser stays responsive (WebGL ``Scattergl`` is used for the same reason).
    """
    template = theme.register_theme()
    n = len(samples)
    if n == 0:
        return _empty_figure("No samples to display", template)

    # Evenly stride through the samples to keep at most max_points of them.
    # Even striding (vs taking the first N) preserves the whole capture's shape.
    if n > max_points:
        idx = np.linspace(0, n - 1, max_points).astype(int)
        shown = samples[idx]
    else:
        shown = samples

    fig = go.Figure(
        go.Scattergl(
            x=shown.real,
            y=shown.imag,
            mode="markers",
            marker=dict(color=theme.ACCENT, size=3, opacity=0.5),
            name="IQ",
        )
    )
    fig.update_layout(
        template=template,
        title=f"IQ constellation ({len(shown):,} of {n:,} samples)",
        xaxis_title="In-phase (I)",
        yaxis_title="Quadrature (Q)",
    )
    # Equal aspect ratio so a circle looks like a circle, not an ellipse.
    fig.update_yaxes(scaleanchor="x", scaleratio=1)
    return fig


def _empty_figure(message: str, template: str) -> go.Figure:
    """A themed placeholder figure with a centered message."""
    fig = go.Figure()
    fig.update_layout(
        template=template,
        annotations=[
            dict(
                text=message,
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
                font=dict(color=theme.TEXT_MUTED, size=16),
            )
        ],
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
    )
    return fig
