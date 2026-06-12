#!/usr/bin/env bash
# Bridge the system SoapySDR binding into the uv venv.
# Re-run this after every `uv sync` (which recreates .venv and drops the .pth).
set -euo pipefail
PTH=".venv/lib/python3.14/site-packages/_system_soapysdr.pth"
echo "/usr/lib/python3/dist-packages" > "$PTH"
echo "SoapySDR bridge installed -> $PTH"
uv run python -c "import SoapySDR; print('SoapySDR', SoapySDR.getAPIVersion(), 'OK')"
