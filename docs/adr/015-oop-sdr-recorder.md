# ADR-015 — Wrap the SoapySDR acquisition in an object (`SdrRecorder`)

- **Status:** Accepted
- **Date:** 2026-06-18
- **Author:** Théo Schmitt
- **Supersedes:** N/A (refactors the `soapy_source` acquisition layer)

## Context

Real-SDR acquisition lived in a single free function, `capture_from_sdr()`: it
enumerated and opened a SoapySDR device, set the sample rate / frequency / gain
/ antenna, read those back, ran the read loop, and tore everything down — all in
one body, threading loose variables (`device`, `stream`, `eff_*`) through nested
`try/finally` blocks. Two problems:

1. **Untestable without hardware.** The function opened a real device at its
   core, so there was no unit test for it at all; the read loop, overflow
   handling and teardown were only ever exercised on a bench with an SDR.
2. **No reusable handle.** A caller could not hold "the radio" and drive it step
   by step (configure, then start, then read a window), only fire the whole
   one-shot.

The supervisor (Abdu, 2026-06-18 call) asked specifically to move past the raw
SoapySDR API with an **object-oriented wrapper** carrying both the radio's data
and its actions — partly as a learning exercise (the intern is new to OOP).

## Decision

**Introduce `SdrRecorder`, a class that models one radio**, and keep
`capture_from_sdr()` as a thin function shim delegating to it.

- **State + actions in one object.** Instance attributes hold the configuration
  and the effective values read back from the hardware; methods are the
  lifecycle: `open` → `configure` → `start` → `read` → `stop` → `close`, plus a
  one-shot `capture()` and context-manager support (`with SdrRecorder(...)`)
  that guarantees teardown on error. Properties expose state read-only.
- **The device is injected.** The constructor takes a `device_opener`
  callable (`driver -> (device, info)`), defaulting to `_open_soapy_device`
  (real hardware). Tests pass a `FakeDevice` opener and exercise the entire
  recorder — config read-back, the read loop, overflow recovery, AGC fallback,
  teardown ordering — **with no SDR plugged in**. Same dependency-injection
  seam already used for the injectable clock (player), socket (stream) and moto
  (storage).
- **The recorder does not upload.** Abdu floated "upload" as one of the class's
  actions, but AeroLake keeps acquisition / encoding / storage cleanly
  separated (the `StorageClient` is the single S3 chokepoint, ADR-001/003).
  `SdrRecorder` owns the **device lifecycle only**; encoding and upload stay
  orchestrated above it. One class, one responsibility.
- **Backward compatible.** `capture_from_sdr()` keeps its exact signature (plus
  an optional `device_opener` for tests) and delegates to
  `SdrRecorder.capture()`, so the orchestrator and the capture CLI are
  unchanged.

## Rationale

- **Testability is the headline win**: a previously bench-only code path is now
  covered by fast, hardware-free unit tests.
- **OOP fits here**: a device genuinely *is* a stateful resource with a
  lifecycle — exactly what an object models well (vs. synthetic generation,
  which stays a pure function because it has no state).
- **Step-by-step control** becomes possible (configure → start → read a window)
  without breaking the one-shot convenience.

## Consequences

### Positive

- Real-SDR acquisition is unit-tested for the first time.
- A reusable handle enables future work (e.g. a `read`-windowed or
  squelch-triggered capture) without another rewrite.
- Clear teaching artifact for the OOP transition (state, methods, properties,
  context manager, dependency injection).

### Negative / open

- `SdrRecorder` still targets **block** capture into RAM (short captures);
  continuous / streaming acquisition remains future work.
- The injected device is duck-typed (`Any`) rather than a formal Protocol — a
  SoapySDR device exposes ~15 methods and the library ships no type stubs, so a
  full Protocol would be noise. The required methods are documented on the class.

## Alternatives considered

- **Keep the free function**: rejected — leaves the acquisition path untestable
  and offers no reusable handle.
- **A full `Protocol` for the device**: more type-safe in theory, but verbose
  and low-value against an untyped third-party library; documented duck typing
  is the pragmatic choice.
- **Put encoding + upload inside the class** (as the supervisor floated):
  rejected — it recouples layers the project deliberately keeps separate.

## References

- Supervisor call (Abdu, 2026-06-18): OOP wrapper over SoapySDR
- ADR-001 (boto3 chokepoint), ADR-003 (layering), ADR-013 (mandate; real SDR is future work)
- `src/aerolake/producer/soapy_source.py` (`SdrRecorder`, `capture_from_sdr`),
  `tests/producer/test_soapy_recorder.py` (`FakeDevice`)
