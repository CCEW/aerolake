"""Streamlit viewer for decoded Iridium ``.h5`` analysis tables (bonus).

Thin glue over :mod:`aerolake.analysis.iridium`: pick a `.h5`, see the capture
setup, headline numbers, and four plots. This is *analysis* of already-decoded
Iridium bursts — NOT the raw-IQ capture explorer (that's ``aerolake-gui``).

Run with: ``uv run --group gui aerolake-iridium`` (or
``uv run --group gui streamlit run src/aerolake/analysis/iridium_app.py``).
"""

from __future__ import annotations

import streamlit as st

from aerolake.analysis import iridium
from aerolake.gui import theme


@st.cache_data(show_spinner="Loading .h5…")
def _load(path: str) -> iridium.IridiumAnalysis:
    return iridium.load_iridium_analysis(path)


def _run() -> None:
    st.set_page_config(
        page_title="AeroLake — Iridium Analysis", page_icon="🛰️", layout="wide"
    )
    st.markdown(theme.STREAMLIT_CSS, unsafe_allow_html=True)
    st.title("🛰️ AeroLake — Iridium Analysis")
    st.caption(
        "Decoded Iridium bursts (GR-Iridium Toolkit output) — reference/ground "
        "truth, NOT raw IQ. For captures, use aerolake-gui."
    )

    path = st.sidebar.text_input("HDF5 file path", value="data/static_test.h5")
    if not path:
        st.info("Enter the path to an Iridium analysis .h5 file in the sidebar.")
        st.stop()
    try:
        analysis = _load(path)
    except (OSError, ValueError, KeyError) as exc:
        st.error(f"Could not load {path!r}: {exc}")
        st.stop()

    # Capture setup straight from the HDF5 attributes.
    with st.expander("Capture setup (HDF5 attributes)", expanded=True):
        st.json(analysis.metadata)

    s = iridium.summarize(analysis)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bursts", f"{s['bursts']:,}")
    c2.metric("Satellites", s["satellites"])
    c3.metric("Duration", f"{s['duration_s']:.0f} s")
    c4.metric("SNR mean", f"{s['snr_mean']:.1f} dB")

    t1, t2, t3, t4 = st.tabs(
        ["📈 SNR over time", "🛰️ Per satellite", "📊 SNR dist.", "📻 Freq over time"]
    )
    with t1:
        st.plotly_chart(iridium.snr_over_time_fig(analysis), use_container_width=True)
    with t2:
        st.plotly_chart(
            iridium.bursts_per_satellite_fig(analysis), use_container_width=True
        )
    with t3:
        st.plotly_chart(iridium.snr_histogram_fig(analysis), use_container_width=True)
    with t4:
        st.plotly_chart(
            iridium.frequency_over_time_fig(analysis), use_container_width=True
        )


_run()
