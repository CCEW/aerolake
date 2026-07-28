#!/usr/bin/env bash
# One-call acquisition. Works BOTH:
#   - locally  : MinIO runs in Docker on this PC (AEROLAKE_S3_ENDPOINT=localhost)
#   - deployed : MinIO runs on the lab NAS (AEROLAKE_S3_ENDPOINT=http://nas:9000)
# It detects which from the .env endpoint and skips the local-Docker step when
# MinIO is remote.
#
#   ./acquire.sh examples/test-complet.json
set -euo pipefail

CONFIG="${1:-examples/test-rtlsdr.json}"
RTLSDR_HWID="0bda:2838"   # RTL-SDR (Realtek RTL2838) — VID:PID, port-independent

if [[ ! -f "$CONFIG" ]]; then
  echo "✗ Config not found: $CONFIG" >&2
  echo "  Usage: ./acquire.sh <config.toml>   (e.g. examples/test-complet.toml)" >&2
  exit 2
fi

# Local vs remote MinIO, read from the .env endpoint.
ENDPOINT="$(grep -E '^AEROLAKE_S3_ENDPOINT=' .env 2>/dev/null | head -1 | cut -d= -f2- | sed 's/[" ]//g' || true)"
case "$ENDPOINT" in
  *localhost*|*127.0.0.1*|"") LOCAL_MINIO=1 ;;
  *)                          LOCAL_MINIO=0 ;;
esac

# --- Docker + MinIO: only when MinIO is LOCAL -----------------------------
if [[ "$LOCAL_MINIO" == "1" ]]; then
  echo "▶ Docker + MinIO (local)…"
  if ! docker info >/dev/null 2>&1; then
    echo "    Docker not running, starting Docker Desktop…"
    cmd.exe /c start "" "C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe" >/dev/null 2>&1 || true
    printf "    waiting for the Docker daemon"
    for _ in $(seq 1 60); do
      if docker info >/dev/null 2>&1; then printf " ✓\n"; break; fi
      printf "."; sleep 2
    done
    docker info >/dev/null 2>&1 || {
      printf "\n✗ Docker is not responding. Start Docker Desktop, then run again.\n" >&2
      exit 1
    }
  fi
  ( cd docker && docker compose up -d )
else
  echo "▶ Remote MinIO: ${ENDPOINT} (no local Docker)"
fi

# --- RTL-SDR attached to WSL (usbipd), Windows/WSL only -------------------
echo "▶ RTL-SDR (USB)…"
if lsusb 2>/dev/null | grep -qiE "2838|rtl"; then
  echo "    already visible ✓"
else
  usbipd.exe attach --wsl --hardware-id "$RTLSDR_HWID" >/dev/null 2>&1 || true
  sleep 2
  if lsusb 2>/dev/null | grep -qiE "2838|rtl"; then
    echo "    attached ✓"
  else
    echo "    ⚠ RTL-SDR not detected (plugged in? synthetic source? another SDR?)."
  fi
fi

# --- SoapySDR bridge inside the venv --------------------------------------
echo "▶ SoapySDR…"
bash setup-soapy.sh

# --- Healthcheck (checks the targeted MinIO, local OR remote) -------------
echo "▶ Healthcheck…"
uv run aerolake-healthcheck

# --- Capture --------------------------------------------------------------
echo "▶ Capture (config: $CONFIG)…"
uv run aerolake-capture --config "$CONFIG"
