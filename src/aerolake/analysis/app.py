"""Streamlit viewer for decoded analysis ``.h5`` tables (GPS / IMU / Iridium).

A BONUS tool, separate from the IQ lakehouse: pick a `.h5` file and one of its
datasets (a test run), and the right plots appear for its modality — a GPS
ground track, IMU orientation, or Iridium SNR. This is *decoded reference data*,
NOT raw IQ (for captures use ``aerolake-gui``).

Thin glue over :mod:`aerolake.analysis.tables`. Run with
``uv run --group gui aerolake-analysis``.
"""

from __future__ import annotations

import glob

import streamlit as st

from aerolake.analysis import tables
from aerolake.gui import theme


@st.cache_data
def _datasets(path: str) -> list[str]:
    return tables.list_datasets(path)


@st.cache_data(show_spinner="Loading dataset…")
def _load(path: str, dataset: str) -> tables.AnalysisTable:
    return tables.load_table(path, dataset)


def _run() -> None:
    st.set_page_config(
        page_title="AeroLake — Analysis", page_icon="🛰️", layout="wide"
    )
    st.markdown(theme.STREAMLIT_CSS, unsafe_allow_html=True)
    st.title("🛰️ AeroLake — Analysis viewer")
    st.caption(
        "Decoded GPS / IMU / Iridium tables (reference / ground truth) — NOT raw "
        "IQ. For captures, use aerolake-gui."
    )

    sb = st.sidebar
    sb.header("Source")
    found = sorted(glob.glob("data/*.h5"))
    path = (
        sb.selectbox("HDF5 file (from data/)", found)
        if found
        else sb.text_input("HDF5 file path", value="")
    )
    if not path:
        st.info("Drop .h5 files in data/, or type a path in the sidebar.")
        st.stop()

    try:
        datasets = _datasets(path)
    except (OSError, ValueError) as exc:
        st.error(f"Cannot open {path!r}: {exc}")
        st.stop()
    if not datasets:
        st.warning("No datasets found in this file.")
        st.stop()

    dataset = sb.selectbox("Dataset (test run)", datasets)
    t = _load(path, dataset)
    sb.markdown(f"**Detected type:** `{t.kind}`")

    # Capture setup straight from the HDF5 attributes.
    with st.expander("Capture setup (HDF5 attributes)", expanded=False):
        st.json(t.metadata)

    # Kind-aware headline metrics.
    summary = tables.summarize(t)
    metric_cols = st.columns(len(summary))
    for col, (key, value) in zip(metric_cols, summary.items(), strict=False):
        shown = f"{value:,.1f}" if isinstance(value, float) else f"{value:,}"
        col.metric(key.replace("_", " ").title(), shown)

    # The right plots for this modality (or a generic column picker).
    figs = tables.figures_for(t)
    if figs:
        for tab, (_title, fig) in zip(st.tabs([ti for ti, _ in figs]), figs, strict=True):
            with tab:
                st.plotly_chart(fig, use_container_width=True)
    else:
        st.info(f"Generic view. Columns: {', '.join(t.columns)}")
        choices = [c for c in t.columns if c != "Timestamp"]
        if choices:
            ycol = st.selectbox("Column to plot over time", choices)
            st.plotly_chart(
                tables.generic_over_time_fig(t, ycol), use_container_width=True
            )


_run()
