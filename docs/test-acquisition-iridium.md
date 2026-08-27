# Iridium acquisition test (RFSoC) — hardware & protocol

> Test plan for the Iridium acquisition with the **RFSoC**, **integrated into
> AeroLake** (ingestion / quality / visualisation).
>
> **Scoping note — design freedom.** Lucien Millet's *Technical Report*
> (§2-1.2 & 4-3.3) is used here only as a **reference to understand the
> approach** and to provide a **proven baseline** (parameters, hardware).
> **I remain free in my choices**: frequency, channel, sample rate, hardware and
> protocol can be adapted to my objectives — the values below are a starting
> point, not a constraint.

## 1. Goal

Receive the **Iridium** signal on a fixed simplex channel, record it as IQ, then
**bring it into the AeroLake lakehouse** (ingestion → MinIO → quality
validation → visualisation), to validate the chain on **real RF data**.

## 2. Reminder — Iridium signal structure

- Iridium NEXT constellation: 66 satellites, polar orbits ~780 km.
- User link: **1616 – 1626.5 MHz**
  - Duplex 1616–1626 MHz (240 channels)
  - **Simplex 1626–1626.5 MHz** (12 channels, *broadcast* — this is our target)
- Useful continuous channels (transmitted permanently, ideal for analysis):
  - Ch 3 — Quaternary Messaging: **1626.104 MHz**
  - **Ch 7 — Ring Alert: 1626.271 MHz** ← the channel Lucien selected
  - Ch 11 — Primary Messaging: **1626.438 MHz**

## 3. Acquisition parameters — baseline (from Lucien, adjust freely)

| Parameter | Value | Why |
|---|---|---|
| Centre frequency | **1626.271 MHz** (Ch 7 Ring Alert) | stable and predictable simplex channel |
| Sample rate | **400 kHz** | covers the Iridium channel (31.5 kHz) + Doppler/offset margin |
| Bandwidth | **= 400 kHz** | SDR filtering matched to the window → anti-aliasing |
| Clock reference | **external 10 MHz** | the RFSoC internal clock drifts ~20 kHz → Doppler unusable |
| Warm-up | **≥ 15 min** | thermal stabilisation (~100 Hz residual even after) |
| Recording duration | to define (e.g. 10–30 min) | long enough for several satellite passes |

> ⚠️ **The most critical point of the report**: without an **external 10 MHz
> reference**, the RFSoC's drift makes the Doppler analysis wrong. Lucien
> achieved ~50 Hz of Doppler error with a 10 MHz generator; the plan was an
> OCXO **CTI OSC5A2B02**.

## 4. Bill of materials

| # | Item | Detail / model (lab ref.) | Essential? |
|---|---|---|---|
| 1 | **SDR — RFSoC** | RFSoC board + **Mohamed Same**'s acquisition software | ✅ |
| 2 | **Iridium antenna** | active L-band — **Iridium-AT1621-12** (+ magnetic base *Caan 33-27210-00-5000*) | ✅ |
| 3 | **External 10 MHz reference** | 10 MHz signal generator **or** OCXO **CTI OSC5A2B02** | ✅ (Doppler quality) |
| 4 | **Coaxial cables** | SMA, low loss (antenna → SDR; 10 MHz ref → SDR) | ✅ |
| 5 | **Host computer** | lab PC (RFSoC acquisition); Raspberry Pi 5 used in the dynamic setup | ✅ |
| 6 | **Roof access + antenna mount** | clear sky view; 3D-printed mounts (Lucien) | ✅ |
| 7 | **Oscilloscope** | check the 10 MHz (compare generator vs OCXO) | ⭐ recommended |
| 8 | LNA / attenuators | if the level is too low / too high (to be assessed) | ⚪ as needed |
| 9 | (Dynamic) **BladeRF 2.0** + antenna + VN100 (IMU) + ublox GPS | mobile alternative (Wissem's method, wideband 10 MS/s @ 1622 MHz) | ⚪ different regime |

## 5. Test protocol (steps)

1. **Installation**: mount the Iridium antenna on the roof (clear sky), connect
   antenna → (LNA?) → the RFSoC's RX input over coax.
2. **Clock**: connect the **external 10 MHz reference** to the RFSoC's ref
   input. (Check the signal on the oscilloscope.)
3. **Power up + warm-up**: switch on and **wait ≥ 15 min**.
4. **Configure the acquisition** (Mohamed Same's RFSoC software):
   `center = 1626.271 MHz`, `sample_rate = 400 kHz`, `bandwidth = 400 kHz`.
5. **Record** the IQ for the chosen duration (e.g. 10–30 min).
6. **Retrieve the IQ file** produced by the acquisition software.
7. **AeroLake ingestion** (see §6) → MinIO → quality validation → visualisation.
8. **Post-processing** (optional, outside AeroLake): GR-Iridium Toolkit /
   Lucien's Doppler tools for SNR & Doppler drift.

## 6. AeroLake integration (where OUR work comes in)

The RFSoC records with **its own software** (not our GNU Radio). AeroLake takes
over **as soon as the IQ file exists**:

```bash
# 1. Ingestion: IQ file -> SigMF -> MinIO (multipart) with tags
uv run aerolake-ingest <file.iq> \
    --signal-type iridium --sample-rate 400e3 --center-freq 1626.271e6 \
   --hardware rfsoc --datatype <cf32|cs16|ci16_le|cu8>  # source format

# 2. Quality validation + tag promotion
uv run aerolake-validate --prefix iridium/ --expected-duration <duration_s>

# 3. Spectrum / spectrogram / constellation visualisation
uv run --group gui aerolake-gui
```

> The `--datatype` value describes the RFSoC file's source format. AeroLake
> validates sample alignment, streams the file, and stores normalized
> `cf32_le`; integer inputs such as `ci16_le` are converted during ingest.

If the RFSoC software already writes a SigMF pair, ingest it without metadata
flags. AeroLake validates the pair, verifies or adds `core:sha512`, converts a
declared `ci16_le` pair to `cf32_le`, and uploads the metadata before the data.
The local pair is not modified.

## 7. Questions to clarify before the test

1. **Regime**: static rooftop (RFSoC, narrowband Ch 7 @ 400 kHz, this document)
   or dynamic (BladeRF, wideband 10 MS/s @ 1622 MHz)?
2. **Output format** of the RFSoC acquisition software (for `--datatype`).
3. **10 MHz reference** available (generator or CTI OCXO)?
4. **Antenna** Iridium-AT1621-12 available + roof access?
5. Who drives the RFSoC acquisition software (Mohamed Same) — available on the
   day?

## References

- *Technical Report* — Lucien Millet, §2-1 (Data Acquisition), §2-1.2.1 (RFSoC
  wrapper / clock), §4-3.3 (Iridium Analysis).
- Wissem's HDF5 attributes (`Test Setup Materials`, `Recording Materials`).
