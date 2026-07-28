# AeroLake — Handoff guide

Purpose: let anyone in the laboratory **take over, host and use** AeroLake after
the author leaves, **without depending on his machine**.

---

## 1. What it is (in 30 seconds)
RF pipeline: **capture (SDR) → SigMF → MinIO (lakehouse) → preview + metadata**.
One capture = `capture.sigmf-data` (raw IQ) + `capture.sigmf-meta` (JSON) +
`capture-preview.png` (spectrum), stored under `{signal_type}/{date}/{session}/`.
The *why* behind every choice is in `docs/adr/`; the overview is in
`README.md` and `docs/pitch-architecture.md`.

## 2. Target architecture (multi-user)
```
  [Acquisition station]  ← the SDR is plugged in here, AeroLake installed here
        │  SigMF upload
        ▼
  [Lab server: MinIO]    ← the SHARED lakehouse (everyone reads/writes here)
        ▲  browser (MinIO console :9001)
  [any PC]               ← browse/view captures, zero install
```

## 3. ⚠️ TO DO FIRST, before the departure

### a) Move the code to the laboratory GitLab
The code lives on the author's **personal** GitHub `Lafraise6813/aerolake` → that
access leaves with him. Once access to the **laboratory GitLab** is granted:
```bash
git remote add gitlab git@gitlab.<lab>:<group>/aerolake.git
git push gitlab main
```
(or create the project on GitLab first, then push). **Grant maintainer rights to
Abdu / a colleague.**

### b) Put MinIO on the shared server (instead of the dev PC)
Today MinIO runs in Docker **locally**. To make it survive:
- On the server, start MinIO (the repo's `docker/docker-compose.yml` works as
  is; or use the server's native S3/MinIO service if it has one).
- Create the **`aerolake-captures`** bucket and a **service account**
  (access key + secret key) for the team.
- Note the endpoint, e.g. `http://<server>:9000` (API) and `:9001` (console).
- (Optional) migrate the captures already recorded from the local MinIO.

## 4. Preparing an acquisition machine (the one with the SDR)
1. **Requirements**: `uv`, `git`, SoapySDR + the SDR driver
   (`sudo apt install soapysdr-tools soapysdr-module-rtlsdr`), and on **Windows**
   WSL2 (Ubuntu) + **usbipd-win** (to pass the USB device into WSL).
2. **Fetch + install**:
   ```bash
   git clone <repo-url> && cd aerolake
   uv sync
   bash setup-soapy.sh          # SoapySDR bridge (rerun after every uv sync)
   ```
3. **Configure the `.env`**:
   ```bash
   cp .env.example .env
   # edit .env: AEROLAKE_S3_ENDPOINT = http://<server>:9000
   #            AEROLAKE_S3_ACCESS_KEY / SECRET_KEY = the service account
   #            AEROLAKE_S3_BUCKET = aerolake-captures
   ```
4. **(Windows) attach the SDR to WSL** — once `bind` is done (admin, persistent):
   ```powershell
   usbipd list           # find the SDR's BUSID
   usbipd bind --busid <X-Y>
   ```
   After that `acquire.sh` performs the `attach` automatically at every run.

## 5. Making a capture

**Option A — the web interface (recommended for colleagues, zero terminal):**
```bash
uv sync --extra gui && uv run aerolake-gui    # run ONCE on the acquisition station
```
On Windows: **double-click `launch-gui.vbs`** at the repo root and it does all of
that without a terminal (hidden start + browser). A shortcut to that file in
`shell:startup` = the GUI starts automatically when the station boots.
Everyone then opens `http://<station>:8501` in a browser: drop a TOML/JSON
config → (optional) click the antenna position on the map → Start → review the
spectrum → Push to MinIO / Keep locally / Discard. A **Playback** tab lets you
browse the lakehouse, view the spectrum of any window and export the SigMF for
GNU Radio.

**Option B — the command line:**
```bash
./acquire.sh examples/<config>.toml      # e.g. examples/test-complet.toml
```
`acquire.sh` detects **on its own** whether MinIO is local or remote (from the
`.env` endpoint) and chains: (Docker if local) → USB/SDR → SoapySDR →
healthcheck → capture → upload (data + meta + PNG preview). Answer `y` to push.
Configs (**TOML recommended** — commented — or JSON) live in `examples/`
(see `examples/README.md`; `capture.full.toml` is the complete template).

## 6. Browsing / viewing (zero install)
- **MinIO console**: `http://<server>:9001` → bucket `aerolake-captures` →
  browse → click **`capture-preview.png`** to see the spectrum straight away.
- **Deeper analysis**: download `*.sigmf-data` + `*.sigmf-meta` (same names),
  open them in **Inspectrum** or **GNU Radio** (`gnuradio/playback.grc`).

## 7. Checking everything is healthy (code health)
```bash
uv run ruff check .   &&   uv run mypy src   &&   uv run pytest
```

## 8. Planned evolutions (not done)
- **GUI: config form** (frequency/duration → generates the TOML) so no file has
  to be edited at all; the clickable map was the first brick.
- **RF re-emission**: GNU Radio + BladeRF TX, cabled + attenuated — with Camila
  (division of labour: ADR-019).
- **SQL / Apache Iceberg layer**: the "real" queryable lakehouse — a future
  evolution (see ADR-013, `docs/pitch-architecture.md`).

## 9. Points of attention
- **The `.env` is never committed** (secrets). Use `.env.example` as the template.
- **`setup-soapy.sh`**: rerun after every `uv sync` (it rebuilds the SoapySDR bridge).
- **`usbipd attach`**: redo it after a reboot/unplug (acquire.sh handles it).
- The current dev `.env` holds duplicate keys (cosmetic, harmless).

## 10. Contacts / memory
- Author: **Théo Schmitt**. Supervisor: **Abdu**.
- Project history and context: `docs/context/`, `docs/adr/` (ADR-001 → 020).
