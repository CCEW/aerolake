# ADR-011 — Iridium analysis viewer (decoded .h5), separate from the IQ core

> **Archivé — hors-périmètre phase 1 (voir ADR-013).** Ce composant a été retiré de `main` et préservé sur la branche `archive/explorations-v1`. Cet ADR est conservé comme trace de décision.

- **Status:** Accepted
- **Date:** 2026-06-01
- **Author:** Théo Schmitt

## Context

Wissem's recordings shared as `.h5` (HDF5) turned out **not** to be raw IQ: they
are the *output* of the GR-Iridium Toolkit — a table of decoded bursts
(`Timestamp, Satellite_ID, X, Y, Z, Lat, Long, Altitude, Spot_Beam_Number,
Frequency, Signal, Noise, SNR`) plus capture-setup attributes (BladeRF 2.0,
10 MS/s, 1622 MHz, 9 MHz BW). They are *results*, not *signal*, so they do not
fit the SigMF/IQ pipeline.

These tables are still valuable as **ground-truth / reference** (which satellite
at which SNR/frequency, when), and a quick viewer was requested.

## Decision

**Add a small, self-contained `aerolake.analysis` package for decoded Iridium
tables, deliberately separate from the IQ lakehouse core.** It does **not**
touch MinIO, SigMF, or the capture pipeline.

- `analysis/iridium.py` — pure functions: `load_iridium_analysis(path)` (h5py),
  `summarize`, and Plotly figure builders (SNR-over-time, bursts-per-satellite,
  SNR histogram, frequency-over-time). Unit-tested on a synthetic `.h5`.
- `analysis/iridium_app.py` + `iridium_launch.py` — a thin Streamlit app,
  `aerolake-iridium`, reusing the GUI theme.
- `h5py` added to the `dev` and `gui` dependency groups (loader only).

## Rationale

- **Honest separation of concerns.** This is analysis of *decoded* output, a
  different data type from the project's core (raw IQ). Folding it into the IQ
  pipeline would blur what AeroLake *is*. A dedicated `analysis/` package keeps
  the boundary explicit.
- **Reuses the patterns.** Same pure-vs-glue split and aerospace theme as the
  capture GUI; no new conventions.
- **Useful now.** Confirms the real Iridium capture parameters (BladeRF,
  10 MS/s, 1622 MHz) for the live SDR test, and serves as ground truth.

## Consequences

- A new optional capability (`aerolake-iridium`) and an `h5py` dependency, both
  isolated from the core pipeline (the core neither imports nor needs them).
- It is **not** an ingestion path: these `.h5` are never written into MinIO as
  captures. Real Iridium IQ (for the lake) still comes from the SDR capture or
  raw IQ files, ingested via `aerolake-ingest`.

## Update (2026-06-02) — generalised to GPS / IMU / Iridium

Inspecting `flight_test.h5` / `vehicle_test.h5` revealed they bundle **three
modalities** per file (`GPS_Analysis`, `IMU_Analysis`, `Iridium_Analysis`), each
with many test-run datasets — not just Iridium. The viewer was therefore
generalised (same decision, broader scope, still outside the IQ core):

- `iridium.py` → **`tables.py`**: a generic `load_table`/`list_datasets` +
  kind detection + per-modality figures (`figures_for`), with a generic
  column-vs-time fallback.
- `iridium_app.py`/`iridium_launch.py` → **`app.py`/`launch.py`**; the entry
  point `aerolake-iridium` → **`aerolake-analysis`** (file picker → dataset
  picker → type-aware plots: GPS ground track, IMU orientation/accel/gyro,
  Iridium SNR/frequency).

## References

- `src/aerolake/analysis/iridium.py`, `iridium_app.py`; `tests/analysis/`
- ADR-006 (GUI: Streamlit + Plotly + theme reused here)
- Wissem's `static_test.h5` / `flight_test.h5` / `vehicle_test.h5`
