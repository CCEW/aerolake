#!/usr/bin/env bash
# AeroLake GUI stopper — WSL/Linux side.
#
# Stops the background Streamlit GUI started by launch-gui.sh. Started by
# stop-gui.bat (Windows double-click) or runnable directly in WSL:
#   ./stop-gui.sh
#
# The GUI runs detached (nohup + disown), so it has no window to close — this
# is how you shut it down when you no longer need it.
set -uo pipefail

PORT=8501

# Prefer stopping exactly our Streamlit process (precise: won't touch some other
# app that happens to use :8501). Fall back to whatever listens on the port.
if pkill -f "streamlit run src/aerolake/gui/app.py"; then
  echo "[info] stopped the AeroLake GUI"
else
  pid="$(ss -ltnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' | head -1 || true)"
  if [ -n "${pid:-}" ]; then
    kill "$pid" 2>/dev/null && echo "[info] stopped process $pid on :$PORT"
  else
    echo "[info] nothing to stop — GUI is not running"
  fi
fi
