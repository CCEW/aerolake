# AeroLake — the pitch: why this architecture?

A plain-language document. It explains **the problem** we solve and **why** we
picked these three technical bricks. Read it before the code; it assumes no
prior knowledge of SigMF, MinIO or data lakehouses.

---

## 1. The problem

A laboratory like LASSENA records a lot of **radio-frequency (RF) signals**:
GNSS (GPS), Iridium, Starlink… Each recording is a stream of **IQ samples** —
millions of complex numbers per second. One minute of capture at 2 MHz is
already ~1 GB.

Without organisation, chaos sets in fast:

- **Mute binary files.** `capture_03_final_v2.bin`: which frequency? which
  sample rate? which receiver? when? where? Nobody knows.
- **Home-grown formats.** Everyone invents their own convention; six months
  later nobody can read the captures of a colleague who has left.
- **No search.** "Give me every validated GPS L1 capture taken on the roof"
  turns into a manual folder hunt.
- **The volume.** You cannot load a multi-GB file into RAM just to read back
  10 seconds of it.

**AeroLake's goal**: let any member of the lab **find, read back and replay**
any capture, thanks to **standardised metadata**. Capture → store → index →
replay, without chaos.

The pipeline, in one line:

```
Producer (capture → SigMF)  →  MinIO (lakehouse)  →  Consumer (extraction → ZeroMQ)
```

---

## 2. The technical triptych

Three structuring choices. Each answers a precise part of the problem.

### 🅰 SigMF — to standardise the metadata

[SigMF](https://github.com/sigmf/SigMF) (*Signal Metadata Format*) is an **open
standard** for describing an RF recording. One capture = two files:

- `….sigmf-data`: the raw bytes of the IQ samples.
- `….sigmf-meta`: a readable JSON describing everything — centre frequency,
  sample rate, data type, date, position, antenna, annotations…

**Why SigMF rather than a home-grown format?**

- **Self-describing**: the capture carries its own manual. No more mute files.
- **Interoperable**: GNU Radio, the SDR community tools, and any other lab read
  SigMF natively. We do not lock ourselves into our own concoction.
- **Durable**: a documented standard survives the departure of whoever wrote it.
- **Verifiable**: we check a capture's conformance *before* storing it — a
  structural error is caught immediately, not six months later.

> That is the meaning of the *"GPSD trap"* (see ADR-016): even the GPS position
> is translated into SigMF's standard `core:geolocation` field, instead of
> copying the raw format of the GPS daemon.

### 🅱 MinIO — for the object storage

[MinIO](https://min.io/) is an **S3-compatible object storage**, open-source,
which we run **locally** (or on a lab server, here `fast.etsmtl.ca`).

**Why MinIO?**

- **S3-compatible**: we speak the same language as Amazon S3 — the industry's
  standard API. The code works the same locally and in the cloud (just change
  the URL). We are tied to no vendor.
- **Scalable and fast**: designed for large binary volumes, exactly our case
  (gigabytes of IQ).
- **Native metadata and tags**: every object carries headers (`x-amz-meta-*`)
  and indexable **tags**, readable *without downloading the file*. That is the
  key to fast search.
- **Open-source and local**: no cloud cost, data under our control, ideal for a
  lab.

### 🅲 Data lakehouse — to index and query intelligently

A short vocabulary clarification, because this is the whole point:

| | Description | Limit |
|---|---|---|
| **Data lake** | Everything is poured in as-is: "the lake of data". Raw storage, cheap, flexible. | Without an index, finding something means digging by hand. |
| **Data warehouse** | Cleaned, structured data, queryable in SQL. | Rigid, expensive, poorly suited to raw binary. |
| **Data lakehouse** | **The best of both**: a lake's raw storage **+** an intelligent cataloguing layer to query it (ideally in SQL). | — |

**Why aim for a lakehouse and not just a lake?**
Because we want both: keep the raw bytes (SigMF on MinIO, cheap and flexible)
**AND** be able to ask questions on top — "every validated Iridium capture from
June", "the ones taken while moving" — without fetching a single sample byte.

---

## 3. Where AeroLake honestly stands

This is the point **not to oversell** in a presentation. Today AeroLake is a
**data lake with a cataloguing layer**, not (yet) a full SQL lakehouse:

- ✅ **Raw storage**: SigMF on MinIO, with a clear key convention
  (`{type}/{date}/{session}/…`).
- ✅ **Cataloguing layer**: the **S3 tags** (`signal-type`, `quality`,
  `hardware`, `recorder`…) and the object metadata make captures **filterable
  without downloading** (the `aerolake-list` command, see ADR-003). We also
  promote a quality tag `raw → validated/rejected`.
- ✅ **Grouping**: SigMF *Collections* link several captures from one campaign.
- ✅ **Targeted extraction**: partial reads through *HTTP Range* (read back
  t=200s without loading the whole file) then publication on a **ZeroMQ Pub/Sub**
  bus.
- ✅ **Metadata query integration**: IQEngine provides the MongoDB-backed
  catalog and query API, while MinIO remains the store for the SigMF files.
  Reusing that catalog avoids duplicating database infrastructure in AeroLake
  (see ADR-021 and ADR-022). A future SQL/analytical layer such as Parquet or
  Apache Iceberg remains a separate evolution, not a prerequisite for metadata
  discovery.

**Honest wording for the pitch:** "AeroLake lays the foundations of an RF
lakehouse — standardised object storage + cataloguing through tags and
metadata. The SQL query layer is the natural next step."

---

## 4. The common thread

Everything holds together thanks to **one idea**: a capture is never a mute
file. It carries its metadata (SigMF), exposed in an indexable way (MinIO
tags), therefore findable and replayable by the whole lab (targeted extraction
+ ZeroMQ). SigMF answers *what*, MinIO answers *where*, the lakehouse answers
*how you find your way around*.

---

### Going further

- Detailed design decisions: `docs/adr/` (every choice has its ADR).
- The refocus on the mandate (priority to the RX pipeline): ADR-013.
- Project context and history: `docs/context/`.
