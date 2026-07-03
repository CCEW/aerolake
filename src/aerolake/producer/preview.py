"""Render a quick visual preview (spectrum + waterfall) of a capture.

This produces the little PNG that gets stored next to the ``.sigmf-data`` /
``.sigmf-meta`` in MinIO, so anyone browsing the lakehouse can *see* what a
capture contains at a glance — without downloading the (possibly multi-GB)
samples and opening Inspectrum / GNU Radio.

It is deliberately a **pure rendering helper**: it takes samples in, returns
PNG bytes out — no storage, no I/O beyond drawing. The capture flow
(:func:`aerolake.producer.orchestrator.push_capture`) calls it best-effort, so
a rendering hiccup never blocks the capture or its upload.

matplotlib is imported lazily (inside the function) so it is only loaded when a
preview is actually requested — importing this module stays cheap.
"""

from __future__ import annotations

import io

import numpy as np

# Cap the number of samples fed to the plots. A preview only needs to be
# representative, not exhaustive; capping keeps rendering fast and memory bounded
# even for a multi-million-sample capture.
_MAX_SAMPLES = 2_000_000
_PSD_SAMPLES = 1 << 20  # 1,048,576 samples for the PSD (power-of-two = fast FFT)


def render_spectrum_png(
    samples: np.ndarray,
    sample_rate: float,
    center_freq: float,
) -> bytes:
    """Return PNG bytes: a PSD (spectrum) on top, a spectrogram below.

    Parameters
    ----------
    samples
        Complex IQ samples (``np.complex64``). Only a leading slice is used.
    sample_rate
        Sample rate in Hz (sets the frequency/time axes).
    center_freq
        Center frequency in Hz (shown in the title; the axes stay relative to
        it, i.e. 0 Hz on the plot = ``center_freq``).
    """
    # Lazy import: matplotlib is heavy and only needed here. 'Agg' is the
    # headless backend (no screen) so this works on a server / in tests.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x = samples[:_MAX_SAMPLES]
    n_psd = min(len(x), _PSD_SAMPLES)
    # Spectrogram over ~1 second of signal (or the whole slice if shorter).
    n_spec = min(len(x), int(sample_rate)) if sample_rate > 0 else len(x)

    fig, (ax_psd, ax_spec) = plt.subplots(2, 1, figsize=(9, 7))

    # Top: power spectral density — "which frequencies are present, how strong".
    ax_psd.psd(x[:n_psd], NFFT=4096, Fs=sample_rate)
    ax_psd.set_title(
        f"Spectre (PSD) — centre {center_freq / 1e6:.3f} MHz, {sample_rate / 1e6:.3f} MS/s"
    )
    ax_psd.set_xlabel("Fréquence relative au centre (Hz)")

    # Bottom: spectrogram / waterfall — frequency over time.
    ax_spec.specgram(x[:n_spec], NFFT=2048, Fs=sample_rate)
    ax_spec.set_title("Spectrogramme (waterfall)")
    ax_spec.set_xlabel("Temps (s)")
    ax_spec.set_ylabel("Fréquence relative au centre (Hz)")

    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=110)
    plt.close(fig)  # free the figure (matplotlib keeps them alive otherwise)
    return buffer.getvalue()
