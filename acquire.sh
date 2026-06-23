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
  echo "✗ Config introuvable : $CONFIG" >&2
  echo "  Usage : ./acquire.sh <config.json>   (ex. examples/test-complet.json)" >&2
  exit 2
fi

# Local vs remote MinIO, read from the .env endpoint.
ENDPOINT="$(grep -E '^AEROLAKE_S3_ENDPOINT=' .env 2>/dev/null | head -1 | cut -d= -f2- | sed 's/[" ]//g' || true)"
case "$ENDPOINT" in
  *localhost*|*127.0.0.1*|"") LOCAL_MINIO=1 ;;
  *)                          LOCAL_MINIO=0 ;;
esac

# --- Docker + MinIO : seulement si MinIO est LOCAL -------------------------
if [[ "$LOCAL_MINIO" == "1" ]]; then
  echo "▶ Docker + MinIO (local)…"
  if ! docker info >/dev/null 2>&1; then
    echo "    Docker non démarré — lancement de Docker Desktop…"
    cmd.exe /c start "" "C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe" >/dev/null 2>&1 || true
    printf "    attente du démon Docker"
    for _ in $(seq 1 60); do
      if docker info >/dev/null 2>&1; then printf " ✓\n"; break; fi
      printf "."; sleep 2
    done
    docker info >/dev/null 2>&1 || {
      printf "\n✗ Docker ne répond pas. Démarre Docker Desktop puis relance.\n" >&2
      exit 1
    }
  fi
  ( cd docker && docker compose up -d )
else
  echo "▶ MinIO distant : ${ENDPOINT} (pas de Docker local)"
fi

# --- RTL-SDR attaché à WSL (usbipd) — Windows/WSL uniquement --------------
echo "▶ RTL-SDR (USB)…"
if lsusb 2>/dev/null | grep -qiE "2838|rtl"; then
  echo "    déjà visible ✓"
else
  usbipd.exe attach --wsl --hardware-id "$RTLSDR_HWID" >/dev/null 2>&1 || true
  sleep 2
  if lsusb 2>/dev/null | grep -qiE "2838|rtl"; then
    echo "    attaché ✓"
  else
    echo "    ⚠ RTL-SDR non détecté (branché ? source synthétique ? autre SDR ?)."
  fi
fi

# --- Pont SoapySDR dans le venv -------------------------------------------
echo "▶ SoapySDR…"
bash setup-soapy.sh

# --- Healthcheck (vérifie le MinIO ciblé, local OU NAS) -------------------
echo "▶ Healthcheck…"
uv run aerolake-healthcheck

# --- Capture --------------------------------------------------------------
echo "▶ Capture (config : $CONFIG)…"
uv run aerolake-capture --config "$CONFIG"
