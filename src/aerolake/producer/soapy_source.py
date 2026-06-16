"""Real SDR acquisition for AeroLake via SoapySDR.

Opens a physical SDR (RTL-SDR, BladeRF, ...) through the SoapySDR
hardware-abstraction layer, captures a fixed-duration block of IQ samples
into RAM, and returns it as an :class:`SdrCapture`.

This is the real-signal counterpart to the synthetic generators. The
returned container exposes the same four attributes the SigMF encoder
reads (``samples``, ``sample_rate``, ``center_freq``, ``description``),
plus hardware-provenance fields (driver, serial, gain, antenna) that only
a real capture can carry.

Design notes
------------
* All radio parameters are **read back from the device** after being set.
  Hardware quantizes requested values (a 2.0 MHz sample-rate request may
  resolve to 2.048 MHz, a 40 dB gain to 40.2 dB), so we store what the SDR
  actually applied, not what we asked for.
* The capture is accumulated entirely in RAM. This suits short captures
  (seconds to tens of seconds). Continuous / squelch-triggered streaming
  is future work and intentionally out of scope here.
* The stream format requested from SoapySDR is CF32, which maps directly
  to numpy complex64 — no manual conversion.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import SoapySDR
from SoapySDR import SOAPY_SDR_CF32, SOAPY_SDR_RX

# SoapySDR delivers samples in bursts. We read into a working buffer of this
# many complex samples per readStream() call. 16384 is a comfortable size:
# large enough to keep syscall overhead low, small enough to stay responsive.
_READ_CHUNK = 16384

# Per-readStream timeout in microseconds. If the device delivers nothing
# within this window we treat it as a read error rather than blocking forever.
_READ_TIMEOUT_US = 1_000_000

# Fallback front-end gain (dB) used only when a device exposes no AGC.
_FALLBACK_GAIN_DB = 40.0


@dataclass(frozen=True)
class SoapyParams:
    """Source-specific parameters for real SDR acquisition via SoapySDR.

    Groups the knobs that only make sense for a real capture, so the
    orchestrator can take a single ``source`` object whose *type* selects the
    acquisition path. Synthetic captures use
    :class:`aerolake.producer.synthetic.SyntheticParams` instead.

    Attributes
    ----------
    driver
        SoapySDR driver key selecting the hardware (``rtlsdr``, ``bladerf``).
    agc
        When ``True`` (default), enable the device's automatic gain control so
        the front-end gain tracks the actual antenna/signal level. The gain the
        AGC settled on is read back and stored in the metadata. When the device
        has no AGC, a sensible fixed gain is applied as a fallback.
    antenna
        Optional antenna port to select. ``None`` keeps the device default.
    """

    driver: str = "rtlsdr"
    agc: bool = True
    antenna: str | None = None


@dataclass(frozen=True)
class SdrCapture:
    """A block of IQ samples captured from a real SDR, with provenance.

    The first four attributes mirror what the SigMF encoder consumes, so an
    SdrCapture can be encoded exactly like a synthetic signal. The remaining
    attributes record *which device* produced the samples and *how* it was
    configured — the provenance metadata a real capture must carry.

    Attributes
    ----------
    samples
        IQ samples as a 1-D ``np.complex64`` array (8 bytes each).
    sample_rate
        Effective sample rate in Hz, read back from the device.
    center_freq
        Effective center frequency in Hz, read back from the device.
    description
        Human-readable summary of the capture.
    driver
        SoapySDR driver key of the device (e.g. ``rtlsdr``, ``bladerf``).
    serial
        Hardware serial number, or ``"unknown"`` if the device exposes none.
    gain
        Effective overall gain in dB, read back from the device.
    antenna
        Selected antenna port name, read back from the device.
    """

    samples: np.ndarray
    sample_rate: float
    center_freq: float
    description: str
    driver: str
    serial: str
    gain: float
    antenna: str
    hardware_info: dict[str, str]


def list_devices() -> list[dict[str, str]]:
    """Enumerate every SDR currently visible to SoapySDR.

    Returns a list of attribute dicts (one per device), each typically
    carrying keys like ``driver``, ``label`` and ``serial``. An empty list
    means no SDR is connected (or no matching SoapySDR module is installed).
    """
    return [dict(d) for d in SoapySDR.Device.enumerate()]


def capture_from_sdr(
    *,
    duration_s: float,
    sample_rate: float,
    center_freq: float,
    driver: str = "rtlsdr",
    agc: bool = True,
    antenna: str | None = None,
    channel: int = 0,
) -> SdrCapture:
    """Capture ``duration_s`` seconds of IQ from a real SDR.

    Parameters
    ----------
    duration_s
        Capture length in seconds. Combined with the *effective* sample rate
        to decide how many samples to collect.
    sample_rate
        Requested sample rate in Hz. The device may quantize it; the value
        stored in the result is what the device reports back.
    center_freq
        Requested RF center frequency in Hz (e.g. 1_575_420_000 for GPS L1).
    driver
        SoapySDR driver key selecting the hardware (``rtlsdr``, ``bladerf``).
    gain
        Requested overall gain in dB. GPS L1 sits below the noise floor, so a
        high gain (and a powered active antenna) is needed to see anything.
    antenna
        Optional antenna port to select. If ``None``, the device default is
        kept. Stored result always reflects the actually selected port.
    channel
        RX channel index. 0 for single-channel devices like the RTL-SDR.

    Returns
    -------
    SdrCapture
        The captured samples plus the effective device configuration.

    Raises
    ------
    RuntimeError
        If no device matches ``driver``, or if the stream read fails.
    """
    if duration_s <= 0:
        raise ValueError("duration_s must be > 0")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be > 0")

    # --- 1. Open the device ------------------------------------------------
    # Device(dict) selects hardware by driver key. If nothing matches, SoapySDR
    # raises; we translate that into a clear RuntimeError for the caller.
    # Open by matching the *enumerated* device args, not a bare driver key.
    # Some drivers (notably rtlsdr) reject Device({"driver": ...}) with
    # "make() no match" even when enumerate() lists the device; opening with
    # the full enumerated args dict is the reliable path.
    # enumerate() yields SoapySDRKwargs objects. We must OPEN with the raw
    # object (Device(kwargs)); converting it to a plain dict first makes
    # make() fail with "no match". So we keep the raw object for opening and
    # only cast to dict to read the driver key.
    device_args = None
    for d in SoapySDR.Device.enumerate():
        if dict(d).get("driver") == driver:
            device_args = d
            break
    if device_args is None:
        raise RuntimeError(
            f"No SDR found for driver={driver!r}. "
            f"Is it plugged in and the SoapySDR module installed? "
            f"Visible devices: {list_devices()}"
        )
    try:
        device = SoapySDR.Device(device_args)
    except Exception as exc:
        raise RuntimeError(
            f"Found a {driver!r} device but failed to open it: {exc}. "
            f"Visible devices: {list_devices()}"
        ) from exc

    try:
        # --- 2. Configure the front-end ------------------------------------
        device.setSampleRate(SOAPY_SDR_RX, channel, sample_rate)
        device.setFrequency(SOAPY_SDR_RX, channel, center_freq)
        # Gain: prefer the device AGC so the front-end tracks the antenna/signal
        # level. If the device has no AGC, fall back to a fixed mid-range gain.
        if agc and device.hasGainMode(SOAPY_SDR_RX, channel):
            device.setGainMode(SOAPY_SDR_RX, channel, True)
        else:
            device.setGain(SOAPY_SDR_RX, channel, _FALLBACK_GAIN_DB)
        if antenna is not None:
            device.setAntenna(SOAPY_SDR_RX, channel, antenna)

        # Read back what the hardware actually applied. Requested != effective
        # in general; the SigMF metadata must describe reality, not intent.
        # Note: the effective GAIN is read AFTER the capture (see below), once
        # the AGC has settled on the real signal.
        eff_sample_rate = float(device.getSampleRate(SOAPY_SDR_RX, channel))
        eff_center_freq = float(device.getFrequency(SOAPY_SDR_RX, channel))
        eff_antenna = str(device.getAntenna(SOAPY_SDR_RX, channel))

        # Hardware provenance comes from the enumeration args (serial,
        # product, tuner, manufacturer, label), not getHardwareInfo() — the
        # latter returns module-level info (index, origin) on SoapyRTLSDR.
        info = dict(device_args)
        serial = info.get("serial", "unknown")
        driver_key = str(device.getDriverKey())

        # Number of samples to collect, based on the EFFECTIVE rate so the
        # requested wall-clock duration is honoured even after quantization.
        n_target = int(duration_s * eff_sample_rate)

        # --- 3. Open and activate the RX stream ----------------------------
        stream = device.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32, [channel])
        device.activateStream(stream)
        try:
            samples = _read_n_samples(device, stream, n_target)
            # Read the gain now, after streaming: with AGC enabled the gain
            # tracks the signal during capture, so the post-capture value is
            # the most representative one to record.
            eff_gain = float(device.getGain(SOAPY_SDR_RX, channel))
        finally:
            # Always tear the stream down, even on read failure, so the device
            # is left unlocked for the next capture.
            device.deactivateStream(stream)
            device.closeStream(stream)
    finally:
        # Drop our handle to the device. SoapySDR releases it when the last
        # reference goes away; doing it explicitly keeps the SDR free.
        device = None

    description = (
        f"Real SDR capture: driver={driver_key}, serial={serial}, "
        f"{eff_center_freq / 1e6:.3f} MHz, "
        f"{eff_sample_rate / 1e6:.3f} Msps, gain {eff_gain:.1f} dB, "
        f"antenna {eff_antenna}, {len(samples)} samples"
    )

    return SdrCapture(
        samples=samples,
        sample_rate=eff_sample_rate,
        center_freq=eff_center_freq,
        description=description,
        driver=driver_key,
        serial=serial,
        hardware_info=info,
        gain=eff_gain,
        antenna=eff_antenna,
    )


def _read_n_samples(
    device: SoapySDR.Device,
    stream: object,
    n_target: int,
) -> np.ndarray:
    """Read exactly ``n_target`` complex64 samples from an active stream.

    Loops over readStream(), copying each burst into a pre-allocated output
    buffer until the target is reached. Pre-allocating once (rather than
    concatenating per chunk) keeps memory predictable for long captures.
    """
    out = np.empty(n_target, dtype=np.complex64)
    chunk = np.empty(_READ_CHUNK, dtype=np.complex64)
    filled = 0

    while filled < n_target:
        remaining = n_target - filled
        want = min(_READ_CHUNK, remaining)

        # readStream fills `chunk[:want]` and returns a status object whose
        # .ret is the number of samples actually read (or a negative error).
        status = device.readStream(
            stream, [chunk[:want]], want, timeoutUs=_READ_TIMEOUT_US
        )
        nread = status.ret

        if nread < 0:
            raise RuntimeError(
                f"readStream failed after {filled} samples "
                f"(SoapySDR error code {nread})"
            )
        if nread == 0:
            # No samples this round (timeout/overflow). Retry; the loop bound
            # guarantees progress as long as the device keeps delivering.
            continue

        out[filled : filled + nread] = chunk[:nread]
        filled += nread

    return out
