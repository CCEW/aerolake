"""Real SDR acquisition for AeroLake via SoapySDR.

Opens a physical SDR (RTL-SDR, BladeRF, ...) through the SoapySDR
hardware-abstraction layer, captures a fixed-duration block of IQ samples
into RAM, and returns it as an :class:`SdrCapture`.

This is the real-signal counterpart to the synthetic generators. The
returned container exposes the same four attributes the SigMF encoder
reads (``samples``, ``sample_rate``, ``center_freq``, ``description``),
plus hardware-provenance fields (driver, serial, gain, antenna) that only
a real capture can carry.

Object-oriented design (ADR-015)
--------------------------------
The radio is modelled as a single **object**, :class:`SdrRecorder`, that holds
both the *state* of the device (its configuration and the values read back from
the hardware) and the *actions* you can perform on it (open, configure, start,
read, stop, close). This replaces a pile of free functions juggling loose
variables: everything about "the recorder" now lives behind one well-defined
interface. The legacy :func:`capture_from_sdr` is kept as a thin wrapper that
delegates to the class, so existing callers (the orchestrator, the CLI) are
unchanged.

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
* **The device is injectable.** :class:`SdrRecorder` does not call SoapySDR
  directly to *open* a device; it asks a ``device_opener`` callable for one.
  In production that callable is :func:`_open_soapy_device` (real hardware);
  in tests a fake opener returns a stand-in device, so the whole recorder —
  config read-back, the read loop, overflow counting, teardown — is exercised
  without any SDR plugged in. Same dependency-injection trick the project uses
  for the injectable clock (player), socket (stream) and moto (storage).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import SoapySDR
from SoapySDR import SOAPY_SDR_CF32, SOAPY_SDR_OVERFLOW, SOAPY_SDR_RX

# SoapySDR delivers samples in bursts. We read into a working buffer of this
# many complex samples per readStream() call. 65536 is a comfortable size:
# large enough to keep syscall overhead low, small enough to stay responsive.
_READ_CHUNK = 65536

# Per-readStream timeout in microseconds. If the device delivers nothing
# within this window we treat it as a read error rather than blocking forever.
_READ_TIMEOUT_US = 1_000_000

# Fallback front-end gain (dB) used only when a device exposes no AGC.
_FALLBACK_GAIN_DB = 40.0

# A "device opener" takes a driver key and returns the opened device handle
# plus its enumeration args (serial/product/label/...). Production uses
# _open_soapy_device; tests inject a fake to run without hardware. ``Any`` is
# deliberate: a SoapySDR.Device is untyped, and a test fake only needs to
# duck-type the handful of methods used below (setSampleRate, getFrequency,
# setupStream, readStream, ...).
DeviceOpener = Callable[[str], tuple[Any, dict[str, str]]]


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
    overflow_count: int


def list_devices() -> list[dict[str, str]]:
    """Enumerate every SDR currently visible to SoapySDR.

    Returns a list of attribute dicts (one per device), each typically
    carrying keys like ``driver``, ``label`` and ``serial``. An empty list
    means no SDR is connected (or no matching SoapySDR module is installed).
    """
    return [dict(d) for d in SoapySDR.Device.enumerate()]


def _open_soapy_device(driver: str) -> tuple[Any, dict[str, str]]:
    """Open the first SoapySDR device matching ``driver``.

    Returns ``(device, info)`` where ``info`` is the enumeration args dict
    (serial, product, tuner, label, ...) — the provenance we record in the
    metadata. Raises ``RuntimeError`` with a helpful message if nothing matches
    or the device fails to open.

    This is the **default** opener injected into :class:`SdrRecorder`. Isolating
    the SoapySDR-specific open logic here is what makes the recorder testable:
    a test passes a different opener and never touches real hardware.
    """
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
    return device, dict(device_args)


class SdrRecorder:
    """Object-oriented wrapper around a SoapySDR receive device.

    One :class:`SdrRecorder` *is* one radio. It bundles the device's **state**
    (configuration + values read back from the hardware) and the **actions**
    you perform on it, so callers manipulate a single coherent object instead
    of threading loose handles and parameters through free functions.

    Two ways to use it
    ------------------
    1. **One-shot** — the common case. :meth:`capture` does the full cycle
       (open → configure → start → read → stop → close) and hands you an
       :class:`SdrCapture`::

           rec = SdrRecorder(driver="rtlsdr", agc=True)
           cap = rec.capture(duration_s=1.0, sample_rate=2e6, center_freq=1_575_420_000)

    2. **Step by step** — when you need finer control, drive the lifecycle
       yourself. Use it as a context manager so the device is always released,
       even if an error occurs mid-capture::

           with SdrRecorder(driver="bladerf") as rec:
               rec.configure(sample_rate=2e6, center_freq=1_575_420_000)
               rec.start()
               samples, overflows = rec.read(n_samples)
               rec.stop()

    Attributes (the "state" half of the object)
    -------------------------------------------
    Everything is stored as ``self._…`` (private by convention — the leading
    underscore says "internal, read me through the properties"). Effective
    values start at neutral defaults and are filled by :meth:`configure` /
    :meth:`read` once the hardware reports them back.
    """

    def __init__(
        self,
        *,
        driver: str = "rtlsdr",
        channel: int = 0,
        agc: bool = True,
        antenna: str | None = None,
        device_opener: DeviceOpener | None = None,
    ) -> None:
        """Configure the recorder. Does **not** open the device yet.

        Construction only records *intent* (what we want). The hardware is
        touched lazily by :meth:`open` / :meth:`capture`, so building an
        ``SdrRecorder`` is cheap and side-effect-free.

        Parameters
        ----------
        driver
            SoapySDR driver key selecting the hardware (``rtlsdr``, ``bladerf``).
        channel
            RX channel index. 0 for single-channel devices like the RTL-SDR.
        agc
            Prefer the device AGC; fall back to a fixed gain if unavailable.
        antenna
            Optional antenna port to select. ``None`` keeps the device default.
        device_opener
            Injection seam: a callable ``driver -> (device, info)``. Defaults to
            :func:`_open_soapy_device` (real hardware). Tests pass a fake.
        """
        # --- Configuration: what we WANT (set once, at construction) --------
        self._driver = driver
        self._channel = channel
        self._agc = agc
        self._requested_antenna = antenna
        self._open_device: DeviceOpener = device_opener or _open_soapy_device

        # --- Runtime handles: filled by open()/start(), None until then -----
        self._device: Any | None = None
        self._stream: Any | None = None
        self._info: dict[str, str] = {}

        # --- Effective values: what the hardware ACTUALLY applied -----------
        # Populated by configure() (rate/freq/antenna) and read() (gain), since
        # with AGC the gain only settles once real samples have flowed.
        self._driver_key = driver
        self._serial = "unknown"
        self._eff_sample_rate = 0.0
        self._eff_center_freq = 0.0
        self._eff_gain = 0.0
        self._eff_antenna = ""

    # --- Read-only views of the state (properties) -----------------------
    # Properties expose internal state without letting callers mutate it: you
    # read `rec.serial`, you can't accidentally reassign it. This is OOP
    # encapsulation — the object guards its own invariants.

    @property
    def is_open(self) -> bool:
        """True once :meth:`open` has acquired a device handle."""
        return self._device is not None

    @property
    def driver_key(self) -> str:
        """Effective driver key reported by the device."""
        return self._driver_key

    @property
    def serial(self) -> str:
        """Hardware serial number (``"unknown"`` if the device exposes none)."""
        return self._serial

    @property
    def hardware_info(self) -> dict[str, str]:
        """Full enumeration args (serial, product, tuner, label, ...)."""
        return dict(self._info)

    @property
    def effective_sample_rate(self) -> float:
        """Sample rate the device actually applied, in Hz."""
        return self._eff_sample_rate

    @property
    def effective_center_freq(self) -> float:
        """Center frequency the device actually applied, in Hz."""
        return self._eff_center_freq

    @property
    def effective_gain(self) -> float:
        """Overall gain in dB as last read back (after streaming, if AGC)."""
        return self._eff_gain

    @property
    def effective_antenna(self) -> str:
        """Antenna port the device actually selected."""
        return self._eff_antenna

    # --- Lifecycle actions -----------------------------------------------

    def open(self) -> None:
        """Acquire the device handle via the injected opener.

        Idempotent-ish: opening an already-open recorder is a programming error
        (raises), since it would leak the previous handle.
        """
        if self._device is not None:
            raise RuntimeError("SdrRecorder is already open")
        # Delegate to the injected opener. In production this enumerates and
        # opens real hardware; in tests it returns a fake. The recorder code
        # below is identical either way — that's the point of the seam.
        self._device, self._info = self._open_device(self._driver)
        self._serial = self._info.get("serial", "unknown")
        self._driver_key = str(self._device.getDriverKey())

    def configure(self, *, sample_rate: float, center_freq: float) -> None:
        """Set the front-end and read back the *effective* radio parameters.

        Requested values may be quantized by the hardware, so we immediately
        read back what the device applied and store that. Gain is **not** read
        here: with AGC it only stabilizes once samples flow, so it is captured
        later in :meth:`read`.
        """
        if sample_rate <= 0:
            raise ValueError("sample_rate must be > 0")
        dev = self._require_device()
        ch = self._channel

        dev.setSampleRate(SOAPY_SDR_RX, ch, sample_rate)
        dev.setFrequency(SOAPY_SDR_RX, ch, center_freq)
        # Gain: prefer the device AGC so the front-end tracks the antenna/signal
        # level. If the device has no AGC, fall back to a fixed mid-range gain.
        if self._agc and dev.hasGainMode(SOAPY_SDR_RX, ch):
            dev.setGainMode(SOAPY_SDR_RX, ch, True)
        else:
            dev.setGain(SOAPY_SDR_RX, ch, _FALLBACK_GAIN_DB)
        if self._requested_antenna is not None:
            dev.setAntenna(SOAPY_SDR_RX, ch, self._requested_antenna)

        # Read back what the hardware actually applied. The SigMF metadata must
        # describe reality, not intent.
        self._eff_sample_rate = float(dev.getSampleRate(SOAPY_SDR_RX, ch))
        self._eff_center_freq = float(dev.getFrequency(SOAPY_SDR_RX, ch))
        self._eff_antenna = str(dev.getAntenna(SOAPY_SDR_RX, ch))

    def start(self) -> None:
        """Open and activate the RX stream (CF32 → numpy complex64)."""
        dev = self._require_device()
        if self._stream is not None:
            raise RuntimeError("stream already started")
        self._stream = dev.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32, [self._channel])
        dev.activateStream(self._stream)

    def read(self, n_samples: int) -> tuple[np.ndarray, int]:
        """Read ``n_samples`` complex64 samples from the active stream.

        Loops over readStream(), copying each burst into a pre-allocated buffer
        until the target is reached. Pre-allocating once (rather than
        concatenating per chunk) keeps memory predictable for long captures.

        Overflows (SOAPY_SDR_OVERFLOW) are **recoverable**: the device dropped
        some samples (typically a transient USB/host latency hiccup), but the
        stream can keep going. We count them and continue rather than aborting,
        so the capture still completes. Other negative codes are fatal.

        As a side effect, the effective **gain** is snapshotted here, after the
        samples have flowed — with AGC enabled that is the most representative
        value to record.

        Returns
        -------
        (samples, overflow_count)
        """
        dev = self._require_device()
        if self._stream is None:
            raise RuntimeError("call start() before read()")

        out = np.empty(n_samples, dtype=np.complex64)
        chunk = np.empty(_READ_CHUNK, dtype=np.complex64)
        filled = 0
        overflow_count = 0

        while filled < n_samples:
            remaining = n_samples - filled
            want = min(_READ_CHUNK, remaining)

            # readStream fills `chunk[:want]` and returns a status object whose
            # .ret is the number of samples read, or a negative SoapySDR code.
            status = dev.readStream(self._stream, [chunk[:want]], want, timeoutUs=_READ_TIMEOUT_US)
            nread = status.ret

            if nread == SOAPY_SDR_OVERFLOW:
                # Recoverable: samples were dropped but the stream continues.
                overflow_count += 1
                continue
            if nread < 0:
                raise RuntimeError(
                    f"readStream failed after {filled} samples (SoapySDR error code {nread})"
                )
            if nread == 0:
                # No samples this round (timeout). Retry; the loop bound
                # guarantees progress as long as the device keeps delivering.
                continue

            out[filled : filled + nread] = chunk[:nread]
            filled += nread

        # Snapshot the gain now, after streaming (AGC has settled on the signal).
        self._eff_gain = float(dev.getGain(SOAPY_SDR_RX, self._channel))
        return out, overflow_count

    def stop(self) -> None:
        """Deactivate and close the RX stream (safe to call if never started)."""
        if self._stream is None:
            return
        dev = self._require_device()
        # Always tear both down, even if deactivate raises, so the device is
        # left unlocked for the next capture.
        try:
            dev.deactivateStream(self._stream)
        finally:
            dev.closeStream(self._stream)
            self._stream = None

    def close(self) -> None:
        """Release the device handle (and the stream if still open).

        Idempotent: closing an already-closed recorder is a no-op, so it is safe
        to call from a ``finally`` or a context-manager exit.
        """
        if self._stream is not None:
            self.stop()
        # Drop our handle. SoapySDR releases the device when the last reference
        # goes away; doing it explicitly keeps the SDR free for the next user.
        self._device = None

    # --- One-shot convenience + context manager --------------------------

    def capture(self, *, duration_s: float, sample_rate: float, center_freq: float) -> SdrCapture:
        """Run a full capture cycle and return an :class:`SdrCapture`.

        Open → configure → start → read → stop → close, with teardown
        guaranteed by ``try/finally`` even if the read fails. This is the
        high-level method most callers want; the legacy :func:`capture_from_sdr`
        is just a function-shaped alias for it.
        """
        if duration_s <= 0:
            raise ValueError("duration_s must be > 0")

        self.open()
        try:
            self.configure(sample_rate=sample_rate, center_freq=center_freq)
            # Number of samples to collect, based on the EFFECTIVE rate so the
            # requested wall-clock duration is honoured even after quantization.
            n_target = int(duration_s * self._eff_sample_rate)
            self.start()
            try:
                samples, overflow_count = self.read(n_target)
            finally:
                self.stop()
        finally:
            self.close()

        description = (
            f"Real SDR capture: driver={self._driver_key}, serial={self._serial}, "
            f"{self._eff_center_freq / 1e6:.3f} MHz, "
            f"{self._eff_sample_rate / 1e6:.3f} Msps, gain {self._eff_gain:.1f} dB, "
            f"antenna {self._eff_antenna}, {len(samples)} samples"
            + (f", {overflow_count} overflow(s)" if overflow_count else "")
        )
        return SdrCapture(
            samples=samples,
            sample_rate=self._eff_sample_rate,
            center_freq=self._eff_center_freq,
            description=description,
            driver=self._driver_key,
            serial=self._serial,
            hardware_info=self._info,
            overflow_count=overflow_count,
            gain=self._eff_gain,
            antenna=self._eff_antenna,
        )

    def __enter__(self) -> SdrRecorder:
        """Enter a ``with`` block. Opens the device if not already open."""
        if not self.is_open:
            self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        """Leave a ``with`` block — always release the device, error or not."""
        self.close()

    # --- Internal helpers -------------------------------------------------

    def _require_device(self) -> Any:
        """Return the open device or raise a clear error if there is none."""
        if self._device is None:
            raise RuntimeError("SdrRecorder is not open; call open() first")
        return self._device


def capture_from_sdr(
    *,
    duration_s: float,
    sample_rate: float,
    center_freq: float,
    driver: str = "rtlsdr",
    agc: bool = True,
    antenna: str | None = None,
    channel: int = 0,
    device_opener: DeviceOpener | None = None,
) -> SdrCapture:
    """Capture ``duration_s`` seconds of IQ from a real SDR.

    Backward-compatible function shim: it builds an :class:`SdrRecorder` and
    calls :meth:`SdrRecorder.capture`. Existing callers (the orchestrator, the
    capture CLI) keep working unchanged; new code can use the class directly.

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
    agc
        Prefer the device AGC; fall back to a fixed gain if unavailable.
    antenna
        Optional antenna port to select. If ``None``, the device default is
        kept. Stored result always reflects the actually selected port.
    channel
        RX channel index. 0 for single-channel devices like the RTL-SDR.
    device_opener
        Injection seam forwarded to :class:`SdrRecorder` (defaults to real
        hardware). Mainly for tests.

    Returns
    -------
    SdrCapture
        The captured samples plus the effective device configuration.

    Raises
    ------
    RuntimeError
        If no device matches ``driver``, or if the stream read fails.
    """
    recorder = SdrRecorder(
        driver=driver,
        channel=channel,
        agc=agc,
        antenna=antenna,
        device_opener=device_opener,
    )
    return recorder.capture(
        duration_s=duration_s,
        sample_rate=sample_rate,
        center_freq=center_freq,
    )
