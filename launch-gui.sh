#!/usr/bin/env bash
# AeroLake GUI launcher — WSL/Linux side.
#
# Started by launch-gui.bat (Windows double-click) or runnable directly in WSL:
#   ./launch-gui.sh
#
# Starts the Streamlit GUI detached (survives the launching window closing) if
# nothing is already listening on :8501. Kept as a real script — NOT an inline
# `bash -lc "..."` string — so there is no cmd->wsl->bash quote/escape mangling.
set -euo pipefail

PORT=8501

# Run from the directory this script lives in, so the app path is relative and
# no absolute path is hardcoded (works wherever the repo is cloned).
cd "$(dirname "$(readlink -f "$0")")"

# Locate uv: PATH first, then the common install locations (a fresh machine may
# not have uv on the login PATH — e.g. it lives in ~/.local/bin or a conda env).
UV="$(command -v uv || true)"
if [ -z "$UV" ]; then
  for c in "$HOME/.local/bin/uv" "$HOME/radioconda/bin/uv" "$HOME/miniconda3/bin/uv" /usr/local/bin/uv; do
    if [ -x "$c" ]; then UV="$c"; break; fi
  done
fi
if [ -z "$UV" ]; then
  echo "[error] uv not found. Install uv or add it to PATH." >&2
  exit 1
fi

# Already up? Nothing to do.
if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
  echo "[info] GUI already listening on :$PORT"
  exit 0
fi

echo "[info] starting AeroLake GUI with $UV ..."
# Detach so it outlives this shell: nohup + background + disown. Logs to /tmp.
nohup "$UV" run --extra gui streamlit run src/aerolake/gui/app.py \
  --server.headless true \
  --server.address 0.0.0.0 \
  --server.port "$PORT" \
  >/tmp/aerolake-gui.log 2>&1 &
disown
# Give WSL a moment to fully spawn the detached child before wsl.exe returns
# (otherwise the session can be torn down before the child is established).
sleep 1
echo "[info] GUI launching; log at /tmp/aerolake-gui.log"
