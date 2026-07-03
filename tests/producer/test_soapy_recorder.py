"""Unit tests for aerolake.producer.soapy_source.SdrRecorder.

The whole point of the OOP refactor is testability: SdrRecorder takes its device
through an injected ``device_opener``, so we can drive it with a FakeDevice and
exercise every path — config read-back, the read loop, overflow recovery, AGC
fallback, teardown ordering, the context manager — without any SDR plugged in.
"""

from __future__ import annotations

import types

import numpy as np
import pytest
from SoapySDR import SOAPY_SDR_OVERFLOW

from aerolake.producer.soapy_source import (
    SdrCapture,
    SdrRecorder,
    capture_from_sdr,
)


class FakeDevice:
    """A stand-in SoapySDR device that records what it was asked to do.

    Implements only the methods SdrRecorder actually calls (duck typing). The
    getters return *effective* values that differ from what is requested, so a
    test can assert the recorder stores what the hardware reports, not intent.
    """

    def __init__(
        self,
        *,
        eff_sample_rate: float = 1000.0,
        eff_center_freq: float = 1_575_420_000.0,
        eff_gain: float = 42.5,
        eff_antenna: str = "RX",
        has_gain_mode: bool = True,
        overflow_on_first_read: bool = False,
    ) -> None:
        self.eff_sample_rate = eff_sample_rate
        self.eff_center_freq = eff_center_freq
        self.eff_gain = eff_gain
        self.eff_antenna = eff_antenna
        self._has_gain_mode = has_gain_mode
        self._overflow_pending = overflow_on_first_read
        # Trace of side-effecting calls, for asserting behaviour & teardown order.
        self.calls: list[str] = []
        self.gain_set_to: float | None = None
        self.gain_mode_set_to: bool | None = None

    # --- identity / provenance ---
    def getDriverKey(self) -> str:
        return "fakesdr"

    # --- configuration ---
    def setSampleRate(self, direction, channel, rate) -> None:
        self.calls.append("setSampleRate")

    def setFrequency(self, direction, channel, freq) -> None:
        self.calls.append("setFrequency")

    def hasGainMode(self, direction, channel) -> bool:
        return self._has_gain_mode

    def setGainMode(self, direction, channel, enabled) -> None:
        self.gain_mode_set_to = enabled
        self.calls.append("setGainMode")

    def setGain(self, direction, channel, value) -> None:
        self.gain_set_to = value
        self.calls.append("setGain")

    def setAntenna(self, direction, channel, name) -> None:
        self.calls.append("setAntenna")

    # --- read-back getters ---
    def getSampleRate(self, direction, channel) -> float:
        return self.eff_sample_rate

    def getFrequency(self, direction, channel) -> float:
        return self.eff_center_freq

    def getAntenna(self, direction, channel) -> str:
        return self.eff_antenna

    def getGain(self, direction, channel) -> float:
        return self.eff_gain

    # --- stream lifecycle ---
    def setupStream(self, direction, fmt, channels):
        self.calls.append("setupStream")
        return "STREAM"

    def activateStream(self, stream) -> None:
        self.calls.append("activateStream")

    def readStream(self, stream, buffers, num_elems, timeoutUs=0):
        # One pending overflow: report it once (no data) then deliver normally.
        if self._overflow_pending:
            self._overflow_pending = False
            return types.SimpleNamespace(ret=SOAPY_SDR_OVERFLOW)
        # Fill the caller's buffer with a recognizable constant and report the
        # full count, so a capture completes in a single normal read.
        buffers[0][:num_elems] = 1 + 1j
        return types.SimpleNamespace(ret=num_elems)

    def deactivateStream(self, stream) -> None:
        self.calls.append("deactivateStream")

    def closeStream(self, stream) -> None:
        self.calls.append("closeStream")


def _opener_for(device: FakeDevice, *, serial: str = "FAKE123"):
    """Build a device_opener returning ``device`` and a provenance info dict."""

    def opener(driver: str):
        return device, {"driver": driver, "serial": serial, "label": "Fake SDR"}

    return opener


# --- capture() one-shot --------------------------------------------------


def test_capture_stores_effective_values_and_provenance() -> None:
    dev = FakeDevice(eff_sample_rate=2_048_000.0, eff_gain=40.2, eff_antenna="LNAW")
    rec = SdrRecorder(driver="bladerf", device_opener=_opener_for(dev, serial="S1"))

    cap = rec.capture(duration_s=0.001, sample_rate=2_000_000, center_freq=1.5e9)

    assert isinstance(cap, SdrCapture)
    # Effective values come from the device read-back, not the request.
    assert cap.sample_rate == 2_048_000.0
    assert cap.gain == 40.2
    assert cap.antenna == "LNAW"
    assert cap.driver == "fakesdr"
    assert cap.serial == "S1"
    assert cap.hardware_info["label"] == "Fake SDR"
    # n = duration * effective_rate = 0.001 * 2_048_000 = 2048 samples.
    assert cap.samples.dtype == np.complex64
    assert len(cap.samples) == 2048
    assert cap.overflow_count == 0


def test_capture_honours_effective_rate_for_sample_count() -> None:
    """Sample count uses the EFFECTIVE rate, not the requested one."""
    dev = FakeDevice(eff_sample_rate=1000.0)
    rec = SdrRecorder(device_opener=_opener_for(dev))

    cap = rec.capture(duration_s=0.5, sample_rate=999_999, center_freq=1e9)

    assert len(cap.samples) == 500  # 0.5 s * 1000 Hz


def test_overflow_is_counted_and_recovered() -> None:
    dev = FakeDevice(eff_sample_rate=1000.0, overflow_on_first_read=True)
    rec = SdrRecorder(device_opener=_opener_for(dev))

    cap = rec.capture(duration_s=0.01, sample_rate=1000, center_freq=1e9)

    assert cap.overflow_count == 1
    assert len(cap.samples) == 10  # still completes despite the overflow
    assert "overflow" in cap.description


def test_agc_enables_gain_mode_when_available() -> None:
    dev = FakeDevice(has_gain_mode=True)
    rec = SdrRecorder(agc=True, device_opener=_opener_for(dev))

    rec.capture(duration_s=0.001, sample_rate=1000, center_freq=1e9)

    assert dev.gain_mode_set_to is True
    assert dev.gain_set_to is None  # never fell back to a fixed gain


def test_falls_back_to_fixed_gain_without_agc() -> None:
    dev = FakeDevice(has_gain_mode=False)
    rec = SdrRecorder(agc=True, device_opener=_opener_for(dev))

    rec.capture(duration_s=0.001, sample_rate=1000, center_freq=1e9)

    assert dev.gain_set_to == 40.0  # _FALLBACK_GAIN_DB
    assert dev.gain_mode_set_to is None


# --- teardown & lifecycle ------------------------------------------------


def test_capture_tears_down_stream_then_releases_device() -> None:
    dev = FakeDevice()
    rec = SdrRecorder(device_opener=_opener_for(dev))

    rec.capture(duration_s=0.001, sample_rate=1000, center_freq=1e9)

    # Stream torn down (deactivate before close), and the recorder is released.
    assert dev.calls.index("deactivateStream") < dev.calls.index("closeStream")
    assert rec.is_open is False


def test_context_manager_opens_and_always_closes() -> None:
    dev = FakeDevice()
    rec = SdrRecorder(device_opener=_opener_for(dev))

    with rec as r:
        assert r.is_open is True
    assert rec.is_open is False


def test_open_twice_raises() -> None:
    rec = SdrRecorder(device_opener=_opener_for(FakeDevice()))
    rec.open()
    with pytest.raises(RuntimeError, match="already open"):
        rec.open()


def test_read_before_start_raises() -> None:
    rec = SdrRecorder(device_opener=_opener_for(FakeDevice()))
    rec.open()
    with pytest.raises(RuntimeError, match="start"):
        rec.read(10)


def test_configure_rejects_nonpositive_rate() -> None:
    rec = SdrRecorder(device_opener=_opener_for(FakeDevice()))
    rec.open()
    with pytest.raises(ValueError, match="sample_rate"):
        rec.configure(sample_rate=0, center_freq=1e9)


# --- backward-compatible function shim -----------------------------------


def test_capture_from_sdr_delegates_to_recorder() -> None:
    dev = FakeDevice(eff_sample_rate=1000.0, eff_gain=33.0)
    cap = capture_from_sdr(
        duration_s=0.01,
        sample_rate=1000,
        center_freq=1e9,
        driver="rtlsdr",
        device_opener=_opener_for(dev, serial="SHIM"),
    )

    assert isinstance(cap, SdrCapture)
    assert cap.serial == "SHIM"
    assert cap.gain == 33.0
    assert len(cap.samples) == 10
