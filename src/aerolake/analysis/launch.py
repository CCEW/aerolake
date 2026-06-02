"""Launcher for the analysis Streamlit app (``aerolake-analysis``)."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    """Start Streamlit on the bundled analysis app."""
    from streamlit.web import cli as stcli

    app_path = Path(__file__).with_name("app.py")
    sys.argv = ["streamlit", "run", str(app_path)]
    raise SystemExit(stcli.main())


if __name__ == "__main__":
    main()
