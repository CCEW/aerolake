# Project history — context from the scoping discussions

> Summary of the two design discussions held with Claude (desktop app) between
> 21 and 29 May 2026, before Claude Code was set up. The raw transcripts are
> archived locally under `docs/context/transcripts/` (not versioned).
> This document captures the **why** and the **human context** that the code and
> the ADRs do not say. The formal architecture decisions live in `docs/adr/`.

## The project in one sentence

Deployment and validation of a **multi-constellation RF record-and-playback
system**: record raw radio-frequency signals, store them cleanly in a data
lakehouse, and be able to **replay** them physically into real laboratory
receivers to validate that they work.

- **Final goal (as stated by the project lead)**: *"build our own curated
  dataset"*. The value is in the **guaranteed quality** of every capture that
  enters the lake, not in the retention policy.
- **Théo's personal goal**: build **the best possible infrastructure** — a
  project as complete and clean as possible.
- The lab's reference hardware is the **Safran RF Record and Playback**.

## The 3 expected demos / deliverables

Three "record → replay" benches on real receivers:

| Demo | Band / frequency | Bandwidth | Target receiver | Owner |
|------|-------------------|-----------|-----------------|-------|
| **GNSS** | L1 — **1575.42 MHz** | 2 MHz | Ublox 9FP | — |
| **Iridium** | 1.626 GHz | 2 MHz | Wissem's receiver | Wissem |
| **Starlink** | centred on 1.4 GHz | **25 MHz** (~200 MB/s) | Ahmad's receiver | Ahmad |

> ⚠️ In the initial brief the GNSS L1 frequency was written "1545.42 MHz" —
> that is a typo. The correct value (and the one coded in the presets) is
> **1575.42 MHz**.

The actual starting order was adjusted by the project lead: we do **not** start
with GNSS but with **Iridium** (Wissem's data is available). Frequency, format
and datatype are considered plain **user inputs** — what Théo must master, and
focus on, is the **structure of the data**.

## The people

- **Abdu** — project lead. He gives the directions (the 2026-05-29 call that
  reprioritised towards quality, see ADR-004). *Note: ADR-004 mistakenly refers
  to him as "Malek" in places — the correct name is Abdu.*
- **Malek** — tutor; reviewed and validated the weekly presentation (asked for
  an architecture diagram to be added).
- **René** — upstream meeting that triggered the mission's refocus.
- **Wissem** — colleague; owns the Iridium receiver and provided **real test
  data** ("07 - Raw_data").
- **Ahmad** — owns the Starlink receiver.
- **Pierre Galopin** & **Lucien Millet** — predecessors (legacy **NeSIVA**,
  `BitGrabber.ipynb`, August 2025). Their code used **minio-py** + **s3fs**
  (≠ our boto3 choice, see ADR-001). Documentation/data recovered through
  SharePoint then Google Drive ("AeroLake Legacy - Project").
- Team: **NESIVA** (context of the weekly meeting).

## Key decisions and directions (the "why")

- **Storage: MinIO confirmed**, with a **cataloguing brick** to be added on top
  to give it structure (the tutor's challenge "why MinIO?" was settled in favour
  of MinIO).
- **Quality over real time** (Abdu's call, 2026-05-29 — formalised in ADR-004):
  - No immediate real-time need. The consumer must respect the data's **time
    pacing**, not the absolute latency (example given: a slow process such as
    daily temperature variation).
  - The real question is **throughput** ("does the lake keep up, yes or no?"),
    not latency — the network is WiFi, outside our control.
  - **Manual archiving**, no automatic retention policy. *BUT* Théo chose to
    **keep the archiving brick** in the code anyway (more complete, removable if
    needed).
- **Two codebases expected**: one in **Python** and one in **GNU Radio**. The
  requested GNU Radio flowgraph is a **"Record / Playback"** one (capture +
  replay). GNU Radio **is not installed yet** on the machine.
- **Code style**: Théo wants **abundant, pedagogical comments** in the code to
  absorb it properly (asked several times). This is consistent with the comment
  density already present in `src/`.
- The synthetic generator's **full-scale (clipping)** bug was detected *by* the
  quality layer, then fixed (default `tone_amplitude` → -20 dBFS). See ADR-004.

## Roadmap wanted by Théo (explicit order)

1. **Finish the infrastructure** cleanly (current priority).
2. Then a **GUI / visualisation interface**, configurable and good-looking.
3. **Only at the end**, test with **real data** (SDR + Wissem's data).

### Envisaged GUI (not started yet)

A configurable capture viewer, designed so that **any user** can operate it
easily:
- Selectable views: **FFT**, **spectrogram**, **constellation**, etc.
- Display of the **quality report**.
- Careful aesthetics, **aerospace theme**.
- Reservation about a capture dropdown if there are ever ~10,000 of them (think
  of another selection mode at that scale).
- An **X server** is available on Théo's side. Aesthetics come **after** a solid
  infrastructure.

## Available hardware & environment

- **SDR**: **BladeRF** and **RTL-SDR** available. Antennas available.
- **Real data**: Wissem's dataset (Iridium).
- **Remote MinIO**: access granted → **https://fast.etsmtl.ca/** (ÉTS
  Montréal). Théo wants to finish locally before switching to it.
- **Dev machine**: Windows 11 + **WSL2 Ubuntu** (host `FRAISE68`), Docker,
  `uv`. **Private GitHub** repository, tutor invited.

## Link with the state of the code

At the time of this summary (end of Sprint 2): batch infrastructure + quality
layer in place and tested on synthetic data. Still to come, in order: finishing
the infrastructure, the visualisation GUI, the GNU Radio Record/Playback
flowgraph, streaming (multipart + HTTP Range + ZeroMQ, deferred by ADR-004),
then real SDR integration (BladeRF / RTL-SDR) and the switch to the remote ÉTS
MinIO.
