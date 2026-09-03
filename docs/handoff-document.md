# AeroLake — Handoff Document

> **Project**: AeroLake — the LASSENA RF data lakehouse (ÉTS Montréal)
> **Author**: Théo Schmitt (intern, 2026) · **Supervisor**: Abdessamad (Abdu) Amrhar
> **Document date**: 2026-07-22
>
> **Purpose of this document: that someone who has NEVER seen the project — nor
> software-defined radio, nor the "cloud", nor Python — can understand, install,
> use and evolve AeroLake without its author.** Everything is here: the concepts
> explained from zero, the user guide, the code, the past decisions and the
> roadmap.

---

## How to read this document (pick your path)

| You are… | Read first |
|---|---|
| **A user** (I just want to record/find signals) | Parts 1, 2, 4 |
| **The new technical owner** (I'm taking over the project) | Everything, in order — plan ~one day |
| **A developer in a hurry** (I need to change the code) | Parts 2.3, 5, 6, 7 |
| **A project manager** (where do we stand?) | Parts 1 and 8 |

Suggested first week for a successor:
**Day 1** — Parts 1 and 2 (understand). **Day 2** — Part 3 (install, run a
synthetic capture). **Day 3** — Part 4 (every feature, GUI included).
**Days 4-5** — Parts 5 to 7 (the code + the decisions). Then Part 8 is your
to-do list.

---

# Part 1 — The project in two pages

## 1.1 The starting problem

LASSENA records **radio signals** (GNSS/GPS, Iridium, ADS-B, Starlink…) with
software-defined radios (SDR). Before AeroLake, every recording was a **mute**
binary file on someone's disk: without asking its author, it was impossible to
know at which frequency it was taken, at which sample rate, where, when, with
which hardware. The result: data unusable by anyone else, lost when the person
leaves, impossible to replay.

## 1.2 The answer: AeroLake

AeroLake is the lab's **RF lakehouse**: a complete chain that

1. **captures** a signal (real SDR — RTL-SDR, BladeRF — or a synthetic test signal),
2. **writes it in the open SigMF standard** (the raw signal + its JSON ID card),
3. **stores it in shared storage** (MinIO on the lab's FAST server) with
   **searchable metadata and tags** and an automatic **spectrum preview** PNG,
4. lets you **find it back** (filterable catalogue), **read any time window**
   (even in a multi-GB file, only the requested window is downloaded), **replay**
   it at its original rate, **stream** it over the network (ZeroMQ) and **export
   it to GNU Radio** for RF re-emission.

A **web interface** (one click, zero terminal) makes all of this usable by
non-developers.

```
  [Acquisition station]  ← the SDR is plugged in here, AeroLake installed here
        │  SigMF upload (HTTPS)
        ▼
  [FAST : MinIO]         ← the SHARED lakehouse (everyone reads/writes here)
        ▲  web browser (console)
  [any lab PC]           ← browse/preview/replay captures, zero install
```

## 1.3 The people

| Who | Role |
|---|---|
| **Abdu** (Abdessamad Amrhar) | Project lead, FAST/MinIO admin — he grants the accesses |
| **Malek** | Tutor |
| **Camila** | GNU Radio / RF re-emission owner (the "pure RF" branch, ADR-019) |
| **Wissem / Ahmad** | Receiver owners (RFSoC…) |
| **Théo Schmitt** | AeroLake's author (gone — hence this document) |

## 1.4 Where things live

| What | Where |
|---|---|
| **Source code** | `aerolake` repo — GitHub `Lafraise6813/aerolake` (⚠ personal account, to be transferred to the lab GitLab, see Part 8) |
| **Shared storage** | FAST: console **https://minio.fast.etsmtl.ca/browser**, S3 API **https://minio-api.fast.etsmtl.ca** |
| **FAST portal** | https://fast.etsmtl.ca |
| **Online documentation** | Confluence, LASSENA space, page "Project: AeroLake" |
| **AeroLake web interface** | `http://<acquisition-station>:8501` |
| **This document + all docs** | the repo's `docs/` folder (the repo copy is the source of truth) |

---

# Part 2 — The concepts, from zero

*This part assumes NO prior knowledge. Each section stands on its own.*

## 2.1 A radio signal inside a computer: IQ samples

A software-defined radio (**SDR**) is an antenna + a converter that turns radio
waves into **numbers**. It measures the received field several million times per
second; each measurement is a **sample**.

Each sample is a **complex number**: an **I** part (*in-phase*) and a **Q** part
(*quadrature*). Why complex? Because a single number only captures the
instantaneous amplitude; the (I, Q) pair captures **amplitude AND phase**, which
faithfully represents a whole band of frequencies around the listening
frequency. Two golden rules:

- **The sample rate = the observed bandwidth.** Sampling at 2 MHz ⇒ you "see"
  2 MHz of spectrum around the centre frequency.
- **The centre frequency** is where in the spectrum you are listening.
  E.g. GPS L1 = 1,575.42 MHz.

In AeroLake every sample is stored as **`cf32_le`**: two 32-bit floats (I then
Q), little-endian — i.e. **8 bytes per sample**. A 10 s GPS capture at 2 MHz =
20 million samples = 160 MB. This is the format GNU Radio reads natively
("complex").

## 2.2 What is a data lakehouse?

Three data-storage architectures, in historical order:

- **The data warehouse** — data is **structured on the way in** (tables, strict
  schemas, SQL). Great for reliable reports; but rigid, expensive, and unsuited
  to bulky raw data (a radio signal is not a table).
- **The data lake** — you **pour everything in raw**, as-is, into cheap storage.
  Flexible and inexpensive; but without discipline the lake becomes a **data
  swamp**: terabytes of files nobody can interpret any more. That is exactly the
  situation AeroLake had to fix.
- **The lakehouse** — the best of both: **the lake's raw, cheap storage**, PLUS
  **the warehouse's discipline**: every piece of data is described (metadata),
  catalogued (searchable tags), verifiable (integrity fingerprint) and readable
  by standard tools.

**AeroLake is a lakehouse for radio:**

| "Lake" side (raw, cheap) | "Warehouse" side (discipline) |
|---|---|
| The raw, untouched IQ samples (`.sigmf-data`) | The standardised JSON description (`.sigmf-meta`, SigMF format) |
| S3 object storage (MinIO), extensible | HTTP metadata + searchable S3 tags (ADR-003) |
| Any volume, any signal | sha512 fingerprint, `aerolake-list` catalogue, PNG preview |

A future "SQL layer" (Apache Iceberg — see Part 8) would complete the picture,
but the heart of the lakehouse is already there.

## 2.3 SigMF — the signal's ID card

**The problem**: a 160 MB file of IQ samples contains **no** information about
itself. Without knowing the sample rate, the frequency and the byte format, it
is **permanently unreadable**.

**The solution**: [SigMF](https://sigmf.org) (*Signal Metadata Format*), an
**open** standard from the SDR community. A SigMF "recording" = **two files with
the same name, side by side**:

```
capture.sigmf-data   ← the signal: the RAW IQ samples, no header, no
                       compression — exactly what the antenna received.
capture.sigmf-meta   ← the ID card: a JSON file, readable by a human
                       AND by any tool.
```

The `.sigmf-meta` has three sections:

1. **`global`** — the technical sheet: `core:datatype` (`cf32_le` for us),
   `core:sample_rate`, `core:version` (spec version), `core:sha512` (the
   **integrity fingerprint**: the mathematical proof that the stored bytes are
   bit-for-bit the captured ones), author, description, licence, hardware, plus
   our `aerolake:*` fields (signal type, operator, location, overflows…).
2. **`captures`** — the acquisition context: centre frequency, date/time
   (`core:datetime`, UTC), **geolocation** (`core:geolocation`, a GeoJSON Point
   — careful, GeoJSON order is **[longitude, latitude, altitude]**).
3. **`annotations`** — labelled regions of the signal (from sample X to sample
   Y, in frequency band Z): "an Iridium burst here", the antenna pointing
   (azimuth/elevation/polarisation)…

On top of that come **extensions** (we use `antenna:` to describe the antenna:
model, gain…) and **collections** (`.sigmf-collection`: a document grouping
several recordings of one campaign — ADR-014).

**Why SigMF changes everything**: the data+meta pair is readable by GNU Radio,
Inspectrum, the Python `sigmf` library, and by anyone in ten years. The signal
is preserved **sample by sample** (sha512 integrity): what you replay is
**exactly** what was received — the prerequisite for the playback/re-emission
required by the project mandate.

## 2.4 NAS, S3 object storage, MinIO and FAST

- **A NAS** (*Network Attached Storage*) is "the network's hard drive": an
  always-on machine whose only job is to store, plugged into the lab network so
  that **everyone** reads and writes in the same place. No more data trapped on
  an intern's PC.
- **S3 object storage** is the modern way to talk to that storage. Instead of
  folders/files, you handle **objects** stored in **buckets**, designated by a
  **key** (e.g. `gnss_l1/2026-07-16/…/capture.sigmf-data`). The S3 API (created
  by Amazon) has become the **de-facto standard**: dozens of tools speak it.
- **MinIO** is a **self-hosted** S3 server: the lab has "its own Amazon S3", on
  its own machines, with no external cloud.
- **FAST** (https://fast.etsmtl.ca) is the ÉTS/LASSENA self-hosted services
  platform. It hosts the lab's MinIO — that is **our production lakehouse**.
  Web console: https://minio.fast.etsmtl.ca/browser; S3 API:
  https://minio-api.fast.etsmtl.ca (HTTPS through the Traefik proxy; the direct
  :9000 port is closed).

**Capital architecture point**: in AeroLake the storage address is a **setting**
(`.env`), not code. Moving from the local dev MinIO to production FAST — or
tomorrow to **Garage** (see Part 8) — means changing one configuration line,
zero lines of code (ADR-001/020).

## 2.5 HTTP metadata vs S3 tags (the ADR-003 convention)

S3 storage offers two ways to attach information to an object, and we use
**both, each for what it does best**:

| | HTTP metadata (`x-amz-meta-*`) | S3 tags |
|---|---|---|
| **Nature** | Technical/continuous values | Categorical/enumerable values |
| **Examples** | sample-rate, center-freq, sample-count, session-id | signal-type, hardware, operator, location |
| **Reading** | HEAD request (cheap, zero body bytes downloaded) | GetObjectTagging (indexable → **search**) |
| **Use** | Inspect a capture without downloading it | Filter the catalogue (`aerolake-list --signal-type gnss_l1`) |

Both are attached **only to the `.sigmf-data`** object — the `.sigmf-meta`
doesn't need them: its body *is* the description.

## 2.6 Reading a slice without downloading everything: HTTP Range and multipart

- **HTTP Range** (reads, ADR-009) — you ask for "bytes 1,600,000,000 to
  1,616,000,000" and the server sends **only that**. This is what lets you view
  the *t=200 s, 10 s* window of a multi-GB capture in seconds (`read_segment`,
  the GUI's "scrub").
- **Multipart upload** (writes, ADR-010) — the upload goes in 8 MiB chunks: RAM
  stays bounded whatever the file size, and a mid-flight failure is cleanly
  aborted.

## 2.7 Live streaming: ZeroMQ Pub/Sub

**ZeroMQ** is a lightweight network messaging library. The **Pub/Sub** pattern:
a **publisher** emits messages on a port, **subscribers** subscribe — no central
broker. AeroLake uses it to **replay a capture live over the network**
(ADR-008): the player re-reads the samples at their original rate and publishes
them frame by frame; any machine in the lab can subscribe and receive the
stream (wire format: 3 parts — topic, JSON header, complex64 bytes).

## 2.8 SDR, SoapySDR and GNU Radio — who does what (ADR-019)

- **SoapySDR** is the hardware-abstraction layer: one API to talk to every SDR
  (RTL-SDR, BladeRF…). AeroLake uses it for config-driven acquisition.
- **GNU Radio** is the signal-processing workshop (graphical flowgraphs).
  **Agreed division of labour (ADR-019)**: GNU Radio owns the demanding "RF
  edges" — very-high-rate recording and **RF re-emission** (with Camila) —
  while AeroLake owns the **lakehouse** (store, catalogue, serve, software
  replay).
- **The contract between the two worlds is the `.sigmf-data` file itself**: raw
  `cf32_le`, which GNU Radio's File Source/File Sink blocks read and write
  natively ("complex" type), with no special block. A file recorded by GNU
  Radio enters the lakehouse through `aerolake-ingest`; a capture leaves toward
  GNU Radio through the GUI's export button.

---

# Part 3 — Install and run

## 3.1 Installation overview

There are **two machine roles**:

- **The acquisition station** (the one with the SDR plugged in): AeroLake is
  installed here; it also serves the web interface to everyone.
- **Every other PC**: nothing to install — a browser is enough (MinIO console
  to browse, the acquisition station's AeroLake interface to capture/replay).

## 3.2 Acquisition-station prerequisites

1. **Git** and **uv** (the project's Python manager —
   https://github.com/astral-sh/uv). Python 3.12+ is installed by uv
   automatically.
2. **SoapySDR + the SDR's driver** (Linux/WSL):
   `sudo apt install soapysdr-tools soapysdr-module-rtlsdr` (adapt to the
   hardware).
3. **On Windows**: WSL2 (Ubuntu) + **usbipd-win** to pass the SDR's USB into
   WSL.
4. *(Optional, RF branch)* **system GNU Radio**: `sudo apt install gnuradio`
   (3.10+). It lives OUTSIDE the uv project — see §5.19.

## 3.3 Step-by-step install

```bash
git clone <repo-url> && cd aerolake
uv sync --extra gui              # installs everything, web interface included
bash setup-soapy.sh              # SoapySDR system → venv bridge
                                 # (⚠ RERUN after every `uv sync`)
cp .env.example .env             # then edit .env (see below)
uv run aerolake-healthcheck      # checks .env + storage reachable + bucket OK
```

### The `.env` — THE configuration file

All values are `AEROLAKE_*` variables (loaded by pydantic-settings; real
environment variables override the file). **The `.env` holds secrets: it is
NEVER committed** (`.env.example` is the template).

```
# ---- Production: the lab's FAST ----
AEROLAKE_S3_ENDPOINT=https://minio-api.fast.etsmtl.ca
AEROLAKE_S3_ACCESS_KEY=<access key created in the MinIO console>
AEROLAKE_S3_SECRET_KEY=<matching secret key>
AEROLAKE_S3_BUCKET=aerolake-captures
# FAST's TLS certificate is signed by the ÉTS internal authority:
AEROLAKE_S3_CA_BUNDLE=/path/to/ets-root-ca.pem   # (ask Abdu for the .pem)
# — failing that, temporarily: AEROLAKE_S3_VERIFY_SSL=false

# ---- Local dev (no lab network): MinIO in Docker ----
# AEROLAKE_S3_ENDPOINT=http://localhost:9000
# AEROLAKE_S3_ACCESS_KEY=minioadmin
# AEROLAKE_S3_SECRET_KEY=minioadmin
# AEROLAKE_S3_BUCKET=aerolake-captures
```

### Local development MinIO (optional)

```bash
cd docker && docker compose up -d    # API :9000, console :9001, bucket auto-created
```

### (Windows) attach the SDR to WSL

Once and for all (as administrator):

```powershell
usbipd list                  # find the SDR's BUSID
usbipd bind --busid <X-Y>    # persistent
```

Then `acquire.sh` does the `attach` automatically at every launch.

## 3.4 Launch the web interface

```bash
uv run aerolake-gui        # serves on 0.0.0.0:8501
```

On Windows: **double-click `launch-gui.vbs`** at the repo root — it does
everything without a terminal (hidden start + browser opens). A shortcut to
this file in `shell:startup` = interface started automatically when the
station boots. Colleagues then open `http://<station>:8501`.

## 3.5 Check the project's health

```bash
uv run ruff check .    # lint (0 errors expected)
uv run mypy src        # types (0 errors expected)
uv run pytest          # ~210 tests, all green, no hardware or server needed
```

---

# Part 4 — Using AeroLake day to day

## 4.1 Make a capture (web interface — recommended)

**Step 0 — open the interface**: double-click "AeroLake GUI" on the
acquisition station, or `http://<station>:8501` from any PC.

**Step 1 — drop a config.** Drag a **`.toml`** file (recommended — comments
allowed) or `.json`. **Commented templates** are in `examples/`:
`capture.example.toml` = minimal template; `capture.full.toml` = **every**
field, marked *(required)* / *(optional)*.

```toml
signal_type = "gnss_l1"          # category → storage layout + search tag
center_freq = 1_575_420_000      # centre frequency in Hz
sample_rate = 2_000_000          # sample rate in Hz (= captured bandwidth)
duration_s  = 10                 # duration in seconds

[source]
type   = "soapy"                 # "soapy" = real SDR ; "synthetic" = test signal
driver = "rtlsdr"                # rtlsdr, bladerf, …
```

**Step 2 — (optional) point the antenna on the map**: open the "📍 Set
position" panel, click the antenna's exact spot → the position goes into the
SigMF metadata (otherwise the config file's position is used; otherwise
nothing — never an invented position).

**Step 3 — Start.** The app **validates the config before touching the
hardware**, captures, then shows: sample count, size, duration and the
**spectrum**.

**Step 4 — decide**: **⬆ Push to MinIO** (joins the shared lakehouse) /
**💾 Keep locally** (the station's `captures/` folder) / **🗑 Discard**.
A human decides **at every capture** — by design (ADR-018): there is no
automated "quality verdict"; the operator sees the spectrum and decides.

## 4.2 Make a capture (command line)

```bash
./acquire.sh examples/<config>.toml
```

`acquire.sh` is the all-in-one: it detects whether storage is local or remote
(from the `.env` endpoint), starts Docker if needed, attaches USB, checks
SoapySDR, runs the healthcheck, captures, shows the summary and asks for
confirmation before pushing. Lower-level equivalent:
`uv run aerolake-capture --config my_capture.toml`.

## 4.3 Find and view a capture

- **With nothing installed**: MinIO console
  (https://minio.fast.etsmtl.ca/browser) → bucket → browse by signal type then
  by date → click **`capture-preview.png`**: the spectrum shows instantly.
- **In the AeroLake interface**: **▶ Playback** tab → pick a capture →
  metadata + preview → *Start / Window* sliders to view the **spectrum of any
  moment** (only the requested window is downloaded — HTTP Range).
- **Command line**: Initialize the catalog queries `init-sync.sh` from wsl/linux or double-click on `init-sync.bat` from Windows . Then, `uv run aerolake-list --signal-type gnss_l1`
  (filterable catalogue without downloading a single signal byte).

## 4.4 Replay a capture — the three modes

1. **Visualise** a precise moment: Playback tab (above).
2. **Stream live over the network** (ZeroMQ) — the Playback tab shows the
   ready-to-copy command:

```bash
uv run aerolake-stream --key <capture> --bind tcp://*:5555     # sender
uv run aerolake-subscribe --address tcp://<station>:5555       # receiver
```

3. **Re-emit over RF** (GNU Radio + a TX SDR, e.g. BladeRF): the **"Export for
   GNU Radio"** button in the Playback tab → load the `.sigmf-data` in
   `gnuradio/playback.grc`. *(RF branch — owner: Camila; ADR-019.)*

## 4.5 Ingest an existing recording

To bring into the lakehouse an IQ file recorded **elsewhere** (GNU Radio,
RFSoC…):

```bash
uv run aerolake-ingest capture.bin --signal-type gnss_l1 \
    --sample-rate 2e6 --center-freq 1575.42e6
```

Accepts a file **or a folder** of RFSoC packets (`RX0_pkt_*.bin`, concatenated
in numeric order). Raw ingest accepts `cf32`, `cu8`, `cs16`, `ci16_le`, and
`cs32`; all inputs are converted and normalized to stored `cf32_le`, and the
upload is streamed with bounded memory.

For an existing `capture.sigmf-data` / `capture.sigmf-meta` pair, omit the
metadata flags:

```bash
uv run aerolake-ingest capture.sigmf-data
```

Before uploading, the pair-ingest checklist validates the SigMF JSON, checks
sample-byte alignment, verifies an existing `global.core:sha512`, adds a
missing hash, converts declared `ci16_le` data to `cf32_le`, and canonicalizes
legacy `cf32` metadata to `cf32_le`. The uploaded hash always describes the
stored bytes; local files are not modified. `global.aerolake:signal_type` is
required: if it is absent, ingest stops and asks the operator to indicate the
signal type before retrying. This value controls both the `signal-type` label
and the bucket prefix. Existing-pair mode accepts `cf32`, `cf32_le`, and
`ci16_le`.

Preview or thumbnail assets are not generated by AeroLake ingest. If IQEngine is
used, those sidecars appear only after the capture is refreshed and opened in
the IQEngine UI. AeroLake's authoritative outputs remain the SigMF pair and the
validated metadata document.

## 4.6 Group a campaign into a collection

```bash
uv run aerolake-collection --prefix gnss_l1/2026-07-16/ \
    --name "rooftop-campaign" --description "…"    # --dry-run to preview
```

Writes a `.sigmf-collection` (SigMF v1.2) at the prefix root; incomplete
recordings (orphans) are reported and skipped.

## 4.7 Quick troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "Could not connect to the endpoint URL" on Push | Storage unreachable | FAST: network/VPN + `.env`. Local: `cd docker && docker compose up -d` |
| "No SDR found for driver=…" | SDR not visible to WSL | Replug, then `./acquire.sh` (redoes the attach); check `usbipd list` on Windows |
| `CERTIFICATE_VERIFY_FAILED` | Unknown ÉTS internal CA | `AEROLAKE_S3_CA_BUNDLE=<ets-root-ca.pem>` (ask Abdu) |
| "AccessDenied" everywhere | The key has no S3 policy | Ask the FAST admin (Abdu) for a read/write policy on the bucket |
| "Crushed"/flat spectrum | Gain too high (clipping) | Lower the input power or leave `agc = true` |
| The GUI won't open | App not running on the station | Double-click "AeroLake GUI"; else §3.4 |
| SoapySDR import broken after `uv sync` | venv bridge recreated | Rerun `bash setup-soapy.sh` |
| `aerolake-healthcheck` fails | `.env` incomplete/wrong | Check endpoint, keys, bucket name |

---

# Part 5 — The code, explained end to end

*The code is deliberately **heavily commented** (an intentional pedagogical
choice): the source file often answers the question itself. This part gives the
map and each piece's role; when in doubt, open the file.*

## 5.1 The map

```
                    ┌── synthetic.py (test signal)
  CONFIG (.toml) ──►│── soapy_source.py (real SDR)        PRODUCER
                    └── ingest.py (existing IQ file)
                              │  IQ samples (complex64)
                              ▼
                    sigmf_writer.py  → .sigmf-data + .sigmf-meta
                              │
                    orchestrator.py  → keys, HTTP metadata, S3 tags
                              ▼
                    storage.py ───────► MinIO  (THE single access point)
                              ▲
                    reader.py (list / inspect / read / read_segment)   CONSUMER
                              │
            ┌─────────────────┼──────────────────┐
        player.py         stream.py         collection.py
     (paced replay)     (ZeroMQ bus)     (.sigmf-collection)

        gui/app.py = a Streamlit facade over exactly these bricks
```

**If you only remember 3 files**: `orchestrator.py` (the sequencing),
`sigmf_writer.py` (the format), `storage.py` (the storage access). **Master 6
and you master AeroLake**: those three + `config.py`, `capture_config.py`,
`reader.py`.

## 5.2 Repository layout

```
aerolake/
├── src/aerolake/
│   ├── common/       config, logging, storage  (shared infra)
│   ├── producer/     acquisition → SigMF → upload preparation
│   ├── consumer/     MinIO reads → replay / streaming / collections
│   ├── gui/          Streamlit web interface (optional [gui] extra)
│   └── scripts/      the 8 CLIs (pyproject [project.scripts] entries)
├── tests/            mirror of src/ (moto = simulated S3) + tests/integration/
├── examples/         TOML/JSON config templates (validated by the tests)
├── gnuradio/         record.grc / playback.grc (system GNU Radio, outside venv)
├── docker/           local dev MinIO (docker-compose)
├── docs/             ADRs + guides (including this document)
├── acquire.sh        all-in-one capture (USB + Soapy + healthcheck + capture)
├── launch-gui.bat/.vbs   terminal-free Windows launcher
└── setup-soapy.sh    SoapySDR system → venv bridge (rerun after uv sync)
```

## 5.3 Design principles (respect them when changing the code)

1. **One single S3 access point**: everything goes through `StorageClient`
   (ADR-001). Never boto3 anywhere else.
2. **Dependency injection everywhere**: the SDR (`device_opener`), gpsd
   (`reader`), the player's clock (`sleep`), the ZeroMQ sockets, the CLIs'
   `storage_client` — everything is injectable, hence **testable without
   hardware or a server**.
3. **Prepare ≠ store**: `prepare_capture()` builds everything in memory,
   `push_capture()` uploads. In between, **a human decides** (ADR-018).
4. **Errors bubble up typed**: `StorageError`, `ConfigError` — the CLIs catch
   them and exit with documented codes (0 ok / 1 storage / 2 config /
   3 capture-unexpected).
5. **Pedagogical code**: the comment density is intentional — keep it.

## 5.4 Cross-cutting invariants (know these COLD)

- **Bucket layout**:
  `{signal_type}/{YYYY-MM-DD}/{YYYY-MM-DD_HHhMMmSS}_{source}_{id8}/capture.*` —
  parent date in UTC (stable sorting), leaf folder in local time (readable),
  `id8` = 8 random hex chars. A capture is "complete" only if `.sigmf-data`
  **and** `.sigmf-meta` both exist; orphans are skipped.
- **Metadata vs tags**: see §2.5 (ADR-003). Both **only on the `.sigmf-data`**.
- **Upload order: `.sigmf-meta` BEFORE `.sigmf-data`** — a reader arriving
  between the two sees interpretable JSON, not orphan bytes.
- **`update_tags` is a full REPLACE** (that's the S3 API, not a choice): to
  change ONE tag → read, merge, rewrite. Forgetting the merge **wipes** the
  other tags.
- **Datatype**: everything is normalised to **`cf32_le`** (8 bytes/sample) —
  what GNU Radio reads natively.
- **Endpoint switch**: empty `s3_endpoint` → real AWS (which is what **moto**
  intercepts in tests); set → MinIO/S3-compatible. Migrating storage = changing
  the `.env`, zero code.

## 5.5 Module reference (summary)

| Module | Role and key points |
|---|---|
| `common/config.py` | `Settings` (pydantic-settings, `AEROLAKE_*` + `.env`, `SecretStr` for the secret key, TLS options `s3_verify_ssl`/`s3_ca_bundle`); **always go through `get_settings()`** (lru_cache) |
| `common/logging.py` | `configure_logging()`: structlog → stderr; stdout stays clean for results |
| `common/storage.py` | **`StorageClient`** — THE S3 access point: health_check, HEAD (exists/size/metadata), tags, `upload_bytes`, **`upload_multipart`** (streamed, bounded RAM, ADR-010), `download_bytes`, **`download_range`** (ADR-009), list, delete; tag sanitisation; single `StorageError` |
| `producer/synthetic.py` | `generate_tone()`: sine + AWGN noise, reproducible `seed` — the whole chain testable without hardware |
| `producer/soapy_source.py` | **`SdrRecorder`** (ADR-015): full SDR lifecycle, injectable `device_opener`, re-reads effective values, counts overflows; `capture_from_sdr` = shim |
| `producer/gps.py` | `read_geolocation()` (ADR-016): ONE validated gpsd fix → GeoJSON Point [lon, lat, alt], or `None` — never an invented position |
| `producer/capture_config.py` | `CaptureConfig` (pydantic, `extra="forbid"`): the TOML/JSON schema, cross-validations (gps exclusive, band edges in pairs) |
| `producer/config_loader.py` | `load_capture_config()`: TOML (tomllib) or JSON → validated config; failures → one readable `ConfigError` (exit 2) |
| `producer/sigmf_writer.py` | `encode()`: samples + metadata → `.sigmf-data`/`.sigmf-meta` bytes (sha512, spec version, geolocation, annotation, antenna extension); `EncodableSignal` = a source's minimal contract |
| `producer/orchestrator.py` | **`prepare_capture()`** (all in memory, nothing stored) then **`push_capture()`** (meta before data, best-effort PNG preview); `save_capture_locally()`; keys + metadata + tags built here |
| `producer/ingest.py` | `ingest_files()`: existing IQ file(s) → lakehouse, streamed (cu8/cs16/cs32 → cf32 conversion, rolling sha512); the GNU Radio → lakehouse bridge |
| `producer/preview.py` | `render_spectrum_png()`: PSD + waterfall, Agg backend, subsampled |
| `consumer/reader.py` | **`CaptureReader`**: `list_captures` (complete pairs), `inspect` (HEAD, zero bytes), `read`, **`read_segment`** (partial read by time window) |
| `consumer/player.py` | `CapturePlayer.play()` (ADR-007): frames at the original rate, `on_frame` = the hook point, injected clock |
| `consumer/stream.py` | `FramePublisher`/`FrameSubscriber` (ADR-008) + `encode_frame`/`decode_frame` (pure wire format) |
| `consumer/collection.py` | `CollectionBuilder` (ADR-014): scan / build (= natural dry-run) / write |
| `gui/app.py` | Streamlit facade **with no logic of its own**: same bricks as the CLI; state in `st.session_state`; folium map; ColorBends WebGL background; GNU Radio export |
| `scripts/` (8 CLIs) | healthcheck, capture, ingest, list, collection, play, stream, subscribe — logging first, rich output, exit codes 0/1/2/3, injectable dependencies |
| `gnuradio/` | `record.grc`/`playback.grc` — **system** GNU Radio (outside venv); the bridge = the `.sigmf-data` itself; headless validation: `grcc -o /tmp gnuradio/playback.grc` |

*(The long, class-by-class version of this reference is on the "Complete code
documentation" page and in `docs/code-documentation.md` — in French; ask Claude
to translate any section you need.)*

---

# Part 6 — Tests and CI

**No unit test touches real hardware or a real server.** Everything is
simulated through dependency injection:

| Real dependency | Test substitute |
|---|---|
| S3/MinIO | **moto** (simulated S3) via `tests/conftest.py` (`test_settings`, `mock_s3`, `storage_client`) |
| SDR (SoapySDR) | fake `device_opener` |
| gpsd | fake `reader` |
| player clock | fake `sleep` |
| ZeroMQ sockets | fake sockets |
| prepare/push in the CLIs | injected stubs |

Also worth knowing: `tests/test_examples_valid.py` validates **every** template
in `examples/` against the schema; `tests/gui/` runs a Streamlit AppTest smoke
test (skipped without the gui extra).

**The integration test** (`tests/integration/`, opt-in
`AEROLAKE_RUN_INTEGRATION=1`) does a **real** round trip (multipart + Range +
tagging). It wears two hats: it runs in CI against a real MinIO container, and
**it is the conformance test for any candidate S3 storage** — it validated
SeaweedFS unchanged (ADR-020), and it is what must be run against Garage.

```bash
uv run pytest                          # everything (~210 tests)
AEROLAKE_RUN_INTEGRATION=1 uv run pytest -m integration   # integration (real server)
```

CI (`.github/workflows/ci.yml`): a lint+types+tests job (`ruff`, `mypy`,
`pytest -m "not integration"`) + an integration job (MinIO container).

---

# Part 7 — The decisions (ADRs): the code's "why"

The **ADRs** (`docs/adr/`) are the project's memory. **Rule: never reverse a
choice without reading its ADR; any decision of comparable weight deserves a
new ADR.**

| ADR | Decision | The gist |
|---|---|---|
| 001 | **boto3** over the MinIO SDK | S3 portability + testable with moto; empty endpoint = AWS/moto, set = MinIO |
| 002 | batch upload first, streaming later | streaming arrived via ADR-008/010 |
| 003 | **metadata vs tags** | the §2.5 convention + the bucket layout |
| 004 | ~~quality before streaming~~ | *removed* (corrected by ADR-013/018) |
| 005 | ~~quality-tag lifecycle~~ | *removed* (ADR-018) |
| 006 | ~~visualisation GUI~~ | *archived* (ADR-013) — the current GUI (2026-06) is a NEW, in-mandate component |
| 007 | **playback strategy** | paced software replay now; SDR re-emission later |
| 008 | **ZeroMQ Pub/Sub streaming** | the frame-broadcast bus |
| 009 | **partial reads via HTTP Range** | `read_segment` + offset/length on the GNU Radio side |
| 010 | **streamed multipart upload** | bounded RAM whatever the size |
| 011 | ~~`.h5` analysis viewer~~ | *archived* (ADR-013) |
| 012 | ~~RF re-emission (v1)~~ | *archived* (ADR-013) — superseded by ADR-019 |
| 013 | **realignment on the mandate** (2026-06-08) | priority is RX → MinIO → ZMQ; GUI/analysis/TX archived on `archive/explorations-v1` |
| 014 | **SigMF Collections** | group a campaign under a prefix |
| 015 | **OOP `SdrRecorder`** | SDR lifecycle as one object; injectable `device_opener` |
| 016 | **SigMF-native geolocation via gpsd** | validated fix or `None` — never an invented position |
| 018 | **remove the quality layer** | a human decides at every capture; supersedes 004/005 |
| 019 | **record/playback division of labour** | GNU Radio = RF edges; AeroLake = lakehouse; the `.sigmf-data` is the contract |
| 020 | **MinIO community EOL** | pinned MinIO short-term; SeaweedFS validated as fallback; **Garage excluded at the time for lack of tagging** — but see Part 8 |

---

# Part 8 — State of affairs & the successor's roadmap

*State as of 2026-07-22. This part is THE successor's to-do list.*

## 8.1 What works, verified

- ✅ Complete **real capture** chain: RTL-SDR validated end-to-end on a
  signal-generator bench; BladeRF supported; synthetic mode available without
  hardware.
- ✅ Full **web interface** (one-click capture, map, spectrum, Playback with
  scrub, GNU Radio export, ZeroMQ command) + terminal-free Windows launcher.
- ✅ Partial reads (Range), paced replay, ZeroMQ streaming, collections, ingest
  (files + RFSoC folders), tag-based catalogue, PNG previews.
- ✅ ~210 green tests without hardware; CI lint+types+tests+integration.
- ✅ **Connection to the FAST server established**: HTTPS endpoint reachable,
  internal TLS handled (`s3_ca_bundle`/`s3_verify_ssl` options), access keys
  created.
- ✅ Docs published on Confluence (user manual + code reference + this
  document).

## 8.2 ⚠ Blocked / pending (unblock these FIRST)

1. ✅**S3 rights on FAST** — the access key sees the buckets (`raw-data`,
   `data-parquet`) but has **no rights** (AccessDenied on read AND write).
   **Ask Abdu** for a read/write policy (Put/Get/DeleteObject, ListBucket,
   Get/PutObjectTagging, multipart) on an `aerolake-captures` bucket OR a
   `raw-data` prefix — then make **the first real capture pushed to FAST**.
2. **ÉTS CA certificate** — ask Abdu for the "ETS Montreal Root CA" `.pem`,
   set it in `AEROLAKE_S3_CA_BUNDLE` and **remove**
   `AEROLAKE_S3_VERIFY_SSL=false` (a temporary workaround).
3. **Lab GitLab** — the code is on the author's personal GitHub. As soon as
   access to `gitlab.lassena.etsmtl.ca` lands: create the project,
   `git remote add gitlab … && git push gitlab main`, and grant maintainer
   rights to Abdu + a colleague.

## 8.3 The Garage migration (FAST's decision, work to do — future ADR-021)

**FAST has decided to migrate MinIO → Garage** (MinIO's community edition is
end-of-life — ADR-020). Consequence: **Garage does NOT support S3 object
tagging** (verified), and the discovery layer relies on tags (ADR-003).

**Adaptation plan (in order)**:

1. Move the categorical values (signal-type, operator, hardware, location…)
   from **S3 tags** to **`x-amz-meta-*` metadata** (the `aerolake-list`
   catalogue already reads metadata via HEAD; during the transition the
   orchestrator can write both).
2. **Validate with the integration suite** against a Garage container — the
   same method, suite unchanged, that validated SeaweedFS (ADR-020).
3. Write **ADR-021** documenting the adaptation.
4. Switch the `.env` to the Garage endpoint on D-day — that's all.

## 8.4 Planned evolutions (not started)

- **GNU Radio playback validated on a bench**: run `gnuradio/playback.grc` on
  a real capture, write the short runbook.
- **RF re-emission** with Camila: BladeRF TX, cabled + attenuated (ADR-019;
  never radiated without authorisation).
- **GUI: config form** (frequency/duration → generates the TOML) so no file
  needs to be edited at all — the clickable map was the first brick.
- **SQL layer / Apache Iceberg**: the queryable lakehouse — a deeper evolution
  (see `docs/pitch-architecture.md`).

## 8.5 Known pitfalls (scar tissue, summarised)

- `setup-soapy.sh` must be **rerun after every `uv sync`**.
- `usbipd attach` must be redone after unplugging/reboot (`acquire.sh` does it).
- `update_tags` **replaces** the whole tag set (read-merge-rewrite).
- Special characters in tags are sanitised (a comma in `location` once made an
  entire upload fail).
- GeoJSON order is **[lon, lat, alt]** — the reverse of the "lat, lon"
  intuition.
- The `.env` is **never** committed; `.env.example` is the template.

---

# Part 9 — Appendices

## 9.1 The project's documents (in reading order)

| Document | Role |
|---|---|
| `docs/code-map.md` | the one-page code map (French — ask Claude to translate) |
| `docs/user-manual.md` | the user guide (French; also on Confluence) |
| `docs/code-documentation.md` | the detailed code reference (French; also on Confluence) |
| `HANDOFF.md` | the operational takeover cheat-sheet (short version of this document) |
| `docs/adr/001…020` | every decision, dated and argued (in English) |
| `docs/handoff-document.md` | **this document** (Markdown source; French original: `docs/handoff-document.md`) |
| `docs/context/historique-discussions.md` | the pre-repo history (May 2026) |

## 9.2 One-minute glossary

| Term | In one line |
|---|---|
| **IQ** | the complex samples (I=in-phase, Q=quadrature) of a digitised radio signal |
| **cf32_le** | complex float 32-bit little-endian — 8 bytes/sample, GNU Radio's native format |
| **SigMF** | the open "raw signal + JSON ID card" standard |
| **SDR** | software-defined radio (RTL-SDR, BladeRF…) |
| **SoapySDR** | the universal API to drive SDRs |
| **GNU Radio** | the signal-processing workshop (flowgraphs) |
| **Lakehouse** | the data lake's raw storage + the data warehouse's discipline |
| **S3** | the standard object-storage API (buckets, keys, objects) |
| **MinIO / Garage / SeaweedFS** | self-hosted S3 servers |
| **FAST** | the lab's self-hosted services platform (hosts the storage) |
| **HTTP Range** | requesting only a byte range of an object |
| **Multipart** | uploading an object in chunks (bounded RAM) |
| **ZeroMQ Pub/Sub** | lightweight publisher/subscribers network messaging |
| **gpsd** | the Linux daemon that talks to GPS receivers |
| **ADR** | Architecture Decision Record — a choice's written "why" |
| **moto** | the in-memory S3 simulation used by the tests |
| **uv** | the project's Python environment/dependency manager |

## 9.3 Contacts

- **Supervisor / FAST admin**: Abdu (Abdessamad Amrhar) — accesses, S3 rights,
  CA certificate.
- **RF branch / GNU Radio**: Camila Nino Francia — camila.francia2004@gmail.com (feeding the lakehouse).
- **Author**: Théo Schmitt — theo.schmitt02@gmail.com (archaeology questions
  only: everything necessary is supposed to be in this document — if something
  is missing, that's a handoff bug; fix it in `docs/handoff-document.md`).

---

*End of document. Good luck — and take care of the lakehouse.* 🛰️
