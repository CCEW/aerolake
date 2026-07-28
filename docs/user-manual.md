# AeroLake — User Manual

> **Who is this for?** Anyone at LASSENA who wants to **record, find or replay**
> RF signals — without being a developer. No command line is needed for daily
> use.

---

## 1. AeroLake in 30 seconds

AeroLake is the lab's **RF lakehouse**: every acquisition is recorded in the
open **SigMF** standard, stored in the shared **MinIO** storage (FAST server),
described by **searchable metadata and tags**, with an automatically generated
**spectrum preview**.

One stored capture = 3 objects side by side:

```
{signal_type}/{date}/{session}/capture.sigmf-data    ← the signal (raw IQ samples)
                               capture.sigmf-meta    ← its description (SigMF JSON)
                               capture-preview.png   ← the spectrum + waterfall preview
```

The **signal itself** is preserved, sample by sample (sha512 integrity): what
you replay is **exactly** what was received.

## 2. The addresses to know

| What | Where |
|---|---|
| **AeroLake interface** (capture + playback) | `http://<acquisition-station>:8501` |
| **FAST MinIO console** (browse the lakehouse) | https://minio.fast.etsmtl.ca/browser |
| FAST portal | https://fast.etsmtl.ca |
| Source code + documentation | the `aerolake` repository (LASSENA GitLab) |

## 3. Make a capture (web interface — recommended)

**Step 0 — open the interface.** On the acquisition station: double-click the
**"AeroLake GUI"** shortcut (the browser opens by itself). From another PC on
the network: open `http://<station>:8501`.

**Step 1 — drop a config.** Drag a **`.toml`** file (recommended) or `.json`
describing the capture. **Commented templates** are in the repository's
`examples/` folder — copy one, adjust 3-4 values, done:

```toml
signal_type = "gnss_l1"          # category → storage layout + search tag
center_freq = 1_575_420_000      # centre frequency in Hz
sample_rate = 2_000_000          # sample rate in Hz (= captured bandwidth)
duration_s  = 10                 # duration in seconds

[source]
type   = "soapy"                 # "soapy" = real SDR ; "synthetic" = test signal
driver = "rtlsdr"                # rtlsdr, bladerf, …
```

> 📄 `examples/capture.example.toml` = minimal commented template;
> `examples/capture.full.toml` = **every** possible field, marked *(required)* /
> *(optional)* — keep what you need, delete the rest.

**Step 2 — (optional) point the antenna on the map.** Open the "📍 Set the
antenna position on the map" panel, click the antenna's exact spot → the
position goes into the SigMF metadata. (Otherwise the config file's position is
used.)

**Step 3 — Start.** One click. The app validates the config **before** touching
the hardware, captures, then shows: sample count, size, duration, and the
**spectrum**.

**Step 4 — decide.**
- **⬆ Push to MinIO** → the capture joins the shared lakehouse;
- **💾 Keep locally** → written to the station's disk (`captures/` folder);
- **🗑 Discard** → nothing is stored.

## 4. Find and view a capture

**With nothing installed**: open the **MinIO console**
(https://minio.fast.etsmtl.ca/browser) → the captures bucket → browse by signal
type then by date → click the **`capture-preview.png`** to see the spectrum
immediately.

**In the AeroLake interface**: **▶ Playback** tab → pick a capture from the list
→ metadata + preview are shown → with the *Start / Window* sliders, view the
**spectrum of any moment** (only the requested window is downloaded, even on a
multi-GB capture).

## 5. Replay a capture

Three modes, from simplest to most complete:

1. **Visualise** a moment: Playback tab (above).
2. **Stream live over the network (ZeroMQ)**: the Playback tab shows the
   ready-to-copy command —
   ```bash
   uv run aerolake-stream --key <capture> --bind tcp://*:5555      # sender side
   uv run aerolake-subscribe --address tcp://<station>:5555        # receiver side
   ```
3. **Re-emit over RF** (GNU Radio + a TX SDR, e.g. BladeRF): the **"Export for
   GNU Radio"** button in the Playback tab → load the `.sigmf-data` in the
   `gnuradio/playback.grc` flowgraph. *(RF branch of the project — see ADR-019;
   owner: Camila.)*

## 6. For power users: the command line

Every feature also exists as a CLI (from the repository):

```bash
uv run aerolake-healthcheck                       # config + MinIO responding?
uv run aerolake-capture --config my_capture.toml  # file-driven capture
uv run aerolake-list --signal-type gnss_l1        # catalogue: list/filter by tag
uv run aerolake-ingest file.bin --signal-type X --sample-rate 2e6 --center-freq 1575.42e6
                                                  # ingest an already-recorded IQ file
uv run aerolake-play --prefix gnss_l1/ --start 200 --duration 10   # replay a window, paced
uv run aerolake-collection --prefix gnss_l1/2026-07-01/ --name "campaign"  # group into a collection
./acquire.sh my_capture.toml                      # all-in-one: USB + SoapySDR + healthcheck + capture
```

## 7. Set up a new acquisition station

This is a one-time job for a technical referent — the full procedure
(requirements, `.env`, USB/WSL, launch shortcut, auto-start) is in
**`HANDOFF.md`** at the repository root.

The essentials: clone the repository, `uv sync --extra gui`,
`bash setup-soapy.sh`, copy `.env.example` → `.env` and set the FAST endpoint
(`https://minio-api.fast.etsmtl.ca`) + the service-account access keys, then
create the shortcut to `launch-gui.vbs`.

## 8. Quick troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "Could not connect to the endpoint URL" on Push | MinIO unreachable | FAST: check network/VPN and the `.env`. Local: start Docker (`cd docker && docker compose up -d`) |
| "No SDR found for driver=…" | SDR not visible to WSL | Replug, then `./acquire.sh` (does the `usbipd attach`), or check `usbipd list` on Windows |
| "Crushed"/flat spectrum | Gain too high (clipping) | Lower the input power or leave `agc = true` |
| The GUI won't open | App not running | Double-click "AeroLake GUI" on the station; else see HANDOFF §5 |
| `aerolake-healthcheck` fails | `.env` incomplete/wrong | Check endpoint, keys, bucket name |
| SoapySDR import fails after `uv sync` | venv bridge broken | Rerun `bash setup-soapy.sh` |

## 9. Where to find more

- **`docs/code-map.md`** — the whole codebase in one page (start there).
- **`docs/passation-en.md`** — the complete handoff document.
- **`docs/code-documentation.md`** — the class-by-class code reference.
- **`docs/adr/`** — the *why* behind every decision (ADR-001 → 020).
- **`HANDOFF.md`** — taking over the project, installing a station, migrating.
