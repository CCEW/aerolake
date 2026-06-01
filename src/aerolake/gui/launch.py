"""Launcher for the AeroLake Streamlit GUI.

Exposed as the ``aerolake-gui`` console script. Streamlit is normally started
with ``streamlit run <file>``; this thin wrapper locates ``app.py`` inside the
installed package and invokes Streamlit's own CLI programmatically, so the user
can simply run ``aerolake-gui`` (via ``uv run --group gui aerolake-gui``).
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    """Start Streamlit on the bundled app.py."""
    # Import inside the function so the optional `streamlit` dependency is only
    # required when actually launching the GUI — importing aerolake.gui.launch
    # (e.g. when Python enumerates entry points) must not fail if the gui group
    # isn't installed.
    from streamlit.web import cli as stcli

    app_path = Path(__file__).with_name("app.py")
    # Streamlit reads its target from argv, exactly as if we'd typed
    # `streamlit run /path/to/app.py` on the command line.
    sys.argv = ["streamlit", "run", str(app_path)]
    raise SystemExit(stcli.main())


if __name__ == "__main__":
    main()
