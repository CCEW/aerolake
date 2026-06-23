#!/usr/bin/env bash
# One-call acquisition: prepares EVERYTHING then captures from a JSON config.
#
#   ./acquire.sh examples/test-complet.json
#
# Chains the steps so you only type one line:
#   1. Docker daemon  (starts Docker Desktop if it isn't running)
#   2. RTL-SDR USB    (attaches it to WSL via usbipd, if not already attached)
#   3. SoapySDR bridge into the venv (idempotent; needed after any `uv sync`)
#   4. MinIO up       (the lakehouse)
#   5. healthcheck    (stops here if storage is unreachable)
#   6. the capture    (aerolake-capture --config <file>)
set -euo pipefail

CONFIG="${1:-examples/test-rtlsdr.json}"
RTLSDR_HWID="0bda:2838"   # RTL-SDR (Realtek RTL2838) — VID:PID, port-independent

if [[ ! -f "$CONFIG" ]]; then
  echo "✗ Config introuvable : $CONFIG" >&2
  echo "  Usage : ./acquire.sh <config.json>   (ex. examples/test-complet.json)" >&2
  exit 2
fi

# --- 1. Docker daemon (lance Docker Desktop si besoin) --------------------
echo "▶ 1/6  Docker…"
if ! docker info >/dev/null 2>&1; then
  echo "    Docker non démarré — lancement de Docker Desktop…"
  cmd.exe /c start "" "C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe" >/dev/null 2>&1 || true
  printf "    attente du démon Docker"
  for _ in $(seq 1 60); do
    if docker info >/dev/null 2>&1; then printf " ✓\n"; break; fi
    printf "."; sleep 2
  done
  docker info >/dev/null 2>&1 || {
    printf "\n✗ Docker ne répond pas. Démarre Docker Desktop à la main puis relance.\n" >&2
    exit 1
  }
fi

# --- 2. RTL-SDR attaché à WSL (usbipd) ------------------------------------
# WSL2 ne voit pas l'USB par défaut. On attache le RTL-SDR s'il ne l'est pas
# déjà (par VID:PID, donc peu importe le port). 'bind' est persistant et déjà
# fait ; seul 'attach' doit être refait après un débranchement/redémarrage.
echo "▶ 2/6  RTL-SDR (USB → WSL)…"
if lsusb 2>/dev/null | grep -qiE "2838|rtl"; then
  echo "    déjà attaché ✓"
else
  usbipd.exe attach --wsl --hardware-id "$RTLSDR_HWID" >/dev/null 2>&1 || true
  sleep 2
  if lsusb 2>/dev/null | grep -qiE "2838|rtl"; then
    echo "    attaché ✓"
  else
    echo "    ⚠ RTL-SDR pas détecté (branché ? source synthétique ? autre SDR ?)."
    echo "      Au besoin, à la main en PowerShell : usbipd attach --wsl --busid 1-1"
  fi
fi

# --- 3. Pont SoapySDR dans le venv ----------------------------------------
echo "▶ 3/6  SoapySDR…"
bash setup-soapy.sh

# --- 4. MinIO (le lakehouse) ----------------------------------------------
echo "▶ 4/6  MinIO…"
( cd docker && docker compose up -d )

# --- 5. Healthcheck -------------------------------------------------------
echo "▶ 5/6  Healthcheck…"
uv run aerolake-healthcheck

# --- 6. Capture -----------------------------------------------------------
echo "▶ 6/6  Capture (config : $CONFIG)…"
uv run aerolake-capture --config "$CONFIG"
