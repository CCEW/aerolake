"""Aerospace "mission-control" theme for the AeroLake GUI.

Centralises every colour, font and styling choice so the look is consistent
across all plots and easy to tweak in one place. Two outputs:

  - a registered Plotly *template* ("aerolake") the plot functions apply;
  - a CSS block the Streamlit app injects for the page chrome (fonts, gradient
    background, HUD-style panels, instrument-readout metric cards).

The aesthetic target: a dark "mission control" console — deep-space navy
backdrop, cyan as the primary signal accent, amber for warnings, techy display
fonts (Orbitron/Rajdhani) for headings and a monospace face for numeric
readouts so values look like telemetry.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

# --- Palette --------------------------------------------------------------
# Deep-space navy backgrounds, cyan primary, amber secondary. Tuned for strong
# contrast on a dark backdrop (readable when projected in a meeting room).

BACKGROUND = "#05080f"     # page backdrop — near-black space navy
BACKGROUND_2 = "#0a1020"   # gradient partner for a subtle depth effect
PANEL = "#0d1426"          # plot/card surface
PANEL_BORDER = "#1c2c44"   # HUD-style hairline border around panels
GRID = "#15233a"           # subtle gridlines

TEXT = "#e8f1fb"           # primary text — cool white
TEXT_MUTED = "#7d93ad"     # secondary text / axis titles

ACCENT = "#2dd4ff"         # cyan — primary trace + accents
ACCENT_GLOW = "rgba(45,212,255,0.18)"  # translucent cyan for the spectrum halo
ACCENT_2 = "#ffb020"       # amber — secondary / highlights
GOOD = "#34d399"           # green — "validated"
BAD = "#ff5d5d"            # red — "rejected"
WARN = "#ffd23f"           # yellow — "raw"

# Spectrogram heatmap scale: "Inferno" (black→purple→orange→yellow) reads like a
# thermal/energy map and pops on the dark theme while staying perceptually sound.
HEATMAP_COLORSCALE = "Inferno"

# Font stacks (Google Fonts loaded via the CSS @import below; each falls back to
# a system font if the network fetch is blocked).
FONT_DISPLAY = "Orbitron, 'Segoe UI', sans-serif"   # headings — spacey
FONT_BODY = "Rajdhani, 'Segoe UI', sans-serif"       # UI / axis labels
FONT_MONO = "'Roboto Mono', 'Consolas', monospace"   # numeric readouts

TEMPLATE_NAME = "aerolake"


def _build_template() -> go.layout.Template:
    """Construct the Plotly layout template encoding the palette + fonts."""
    return go.layout.Template(
        layout=go.Layout(
            paper_bgcolor="rgba(0,0,0,0)",   # transparent: blend into the panel
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=TEXT, family=FONT_BODY, size=14),
            title=dict(font=dict(color=TEXT, family=FONT_DISPLAY, size=18)),
            xaxis=dict(
                gridcolor=GRID,
                zerolinecolor=GRID,
                linecolor=PANEL_BORDER,
                title=dict(font=dict(color=TEXT_MUTED, family=FONT_BODY)),
                tickfont=dict(color=TEXT_MUTED, family=FONT_MONO, size=11),
            ),
            yaxis=dict(
                gridcolor=GRID,
                zerolinecolor=GRID,
                linecolor=PANEL_BORDER,
                title=dict(font=dict(color=TEXT_MUTED, family=FONT_BODY)),
                tickfont=dict(color=TEXT_MUTED, family=FONT_MONO, size=11),
            ),
            colorway=[ACCENT, ACCENT_2, GOOD, BAD, WARN],
            margin=dict(l=70, r=30, t=50, b=55),
            hoverlabel=dict(font=dict(family=FONT_MONO, size=12)),
        )
    )


def register_theme() -> str:
    """Register the AeroLake template with Plotly and return its name.

    Idempotent — safe to call from every plot function.
    """
    pio.templates[TEMPLATE_NAME] = _build_template()
    return TEMPLATE_NAME


# CSS injected by the Streamlit app. Themes everything Plotly doesn't control:
# fonts, the gradient page background, the sidebar, headings, and the metric
# "instrument" cards. Kept here so all styling lives in one file.
STREAMLIT_CSS = f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700&family=Rajdhani:wght@500;600;700&family=Roboto+Mono:wght@400;500&display=swap');

    /* Page backdrop: a subtle deep-space gradient. */
    .stApp {{
        background: radial-gradient(1200px 600px at 70% -10%, {BACKGROUND_2} 0%, {BACKGROUND} 60%);
        color: {TEXT};
        font-family: {FONT_BODY};
    }}

    /* Headings in the spacey display font. */
    h1, h2, h3 {{ font-family: {FONT_DISPLAY}; color: {TEXT}; letter-spacing: 1px; }}
    h1 {{
        text-transform: uppercase;
        border-bottom: 2px solid {ACCENT};
        padding-bottom: 0.35rem;
        text-shadow: 0 0 18px {ACCENT_GLOW};
    }}

    /* Sidebar styled as a control panel. */
    section[data-testid="stSidebar"] {{
        background: {PANEL};
        border-right: 1px solid {PANEL_BORDER};
    }}
    section[data-testid="stSidebar"] h2 {{
        text-transform: uppercase;
        font-size: 0.95rem;
        color: {ACCENT};
        letter-spacing: 2px;
    }}

    /* Metric cards → instrument readouts: bordered panel, mono value. */
    div[data-testid="stMetric"] {{
        background: {PANEL};
        border: 1px solid {PANEL_BORDER};
        border-left: 3px solid {ACCENT};
        border-radius: 6px;
        padding: 12px 16px;
    }}
    div[data-testid="stMetricLabel"] {{
        text-transform: uppercase;
        letter-spacing: 1.5px;
        color: {TEXT_MUTED};
    }}
    div[data-testid="stMetricValue"] {{
        font-family: {FONT_MONO};
        color: {ACCENT};
    }}

    /* Tabs: uppercase, spaced — console tabs. */
    button[data-baseweb="tab"] {{ letter-spacing: 1px; text-transform: uppercase; }}

    /* "Explain" callout box for the pedagogical captions. */
    .aerolake-explain {{
        background: {PANEL};
        border: 1px solid {PANEL_BORDER};
        border-left: 3px solid {ACCENT_2};
        border-radius: 6px;
        padding: 12px 16px;
        margin-bottom: 12px;
        color: {TEXT};
        font-size: 0.95rem;
        line-height: 1.5;
    }}
    .aerolake-explain b {{ color: {ACCENT_2}; }}

    /* Plain-language signal summary banner. */
    .aerolake-summary {{
        font-family: {FONT_MONO};
        background: {PANEL};
        border: 1px solid {PANEL_BORDER};
        border-radius: 6px;
        padding: 10px 16px;
        color: {ACCENT};
    }}
</style>
"""
