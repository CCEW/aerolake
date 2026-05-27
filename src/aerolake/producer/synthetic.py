"""Synthetic signal generation for AeroLake testing.

Produces complex-valued IQ samples without needing real hardware. Useful
for end-to-end pipeline testing (capture -> SigMF -> MinIO -> consumer)
when no SDR is connected.

All generators return :class:`SyntheticSignal`, a lightweight container
holding the samples and the minimal metadata required to encode them
as a SigMF capture downstream.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SyntheticSignal:
    """A generated signal with the metadata needed to encode it as SigMF.

    Attributes
    ----------
    samples
        IQ samples as a 1-D ``np.complex64`` array (one complex value per
        sample, 8 bytes each).
    sample_rate
        Sample rate in Hz (samples per second).
    center_freq
        Hardware center frequency in Hz. Cosmetic for synthetic generators
        (the tone is built in baseband), but propagated into SigMF metadata
        so downstream consumers see a realistic value.
    description
        Human-readable summary of what was generated. Goes into the SigMF
        ``core:description`` field.
    """

    samples: np.ndarray
    sample_rate: float
    center_freq: float
    description: str


def generate_tone(
    duration_s: float,
    sample_rate: float,
    center_freq: float,
    tone_offset_hz: float = 100_000.0,
    snr_db: float = 20.0,
    seed: int | None = None,
) -> SyntheticSignal:
    """Generate a complex sinusoid at ``tone_offset_hz`` with AWGN.

    The generated signal is a pure tone at the given frequency offset from
    DC (which corresponds to ``center_freq`` in the RF world), plus
    additive white Gaussian noise scaled to the requested SNR.

    Parameters
    ----------
    duration_s
        Capture duration in seconds.
    sample_rate
        Sample rate in Hz. For GNSS L1 we'd typically use 2 MHz.
    center_freq
        Hardware center frequency in Hz, used for SigMF metadata only.
    tone_offset_hz
        Frequency of the tone relative to center, in Hz. Default 100 kHz,
        which is far enough from DC to be clearly distinguishable.
    snr_db
        Signal-to-noise ratio in dB. Higher = cleaner. Default 20 dB.
    seed
        Optional RNG seed for reproducible noise. Useful in tests.

    Returns
    -------
    SyntheticSignal
        Container with samples and metadata ready for SigMF encoding.
    """
    rng = np.random.default_rng(seed)
    n_samples = int(duration_s * sample_rate)
    t = np.arange(n_samples) / sample_rate

    # Pure tone in the complex (IQ) domain.
    # exp(2j*pi*f*t) is a unit-amplitude single-frequency signal that exists
    # ONLY at frequency f, with no mirror image at -f (unlike a real cosine).
    tone = np.exp(2j * np.pi * tone_offset_hz * t).astype(np.complex64)

    # Additive White Gaussian Noise (AWGN), independent on I and Q channels.
    # noise_amplitude is set so the resulting SNR (in dB) matches snr_db.
    noise_amplitude = 10 ** (-snr_db / 20)
    noise_i = rng.normal(0.0, noise_amplitude / np.sqrt(2), n_samples)
    noise_q = rng.normal(0.0, noise_amplitude / np.sqrt(2), n_samples)
    noise = (noise_i + 1j * noise_q).astype(np.complex64)

    samples = tone + noise

    return SyntheticSignal(
        samples=samples,
        sample_rate=sample_rate,
        center_freq=center_freq,
        description=(
            f"Synthetic tone at {tone_offset_hz / 1000:.1f} kHz offset "
            f"from {center_freq / 1e6:.3f} MHz, SNR {snr_db:.0f} dB"
        ),
    )
