"""Streamlit viewer for decoded analysis data (GPS / IMU / Iridium + Doppler).

A BONUS tool, separate from the IQ lakehouse. Two modes (sidebar):

  - **📊 HDF5 tables** — pick a ``.h5`` file + dataset; the right plots appear
    for its modality (GPS ground track, IMU orientation, Iridium SNR).
  - **🛰️ Doppler / Skyplot** — point at a decoded Iridium acquisition (+ a TLE)
    and watch the **measured vs predicted Doppler** S-curves and a **skyplot**,
    with a time cursor you can animate (the satellites cross the sky).

All of this is *decoded reference data*, NOT raw IQ (for captures use
``aerolake-gui``). Thin glue over :mod:`aerolake.analysis.tables` and
:mod:`aerolake.analysis.doppler`. Run: ``uv run --group gui aerolake-analysis``.
"""

from __future__ import annotations

import glob
from datetime import datetime, timedelta

import streamlit as st

from aerolake.analysis import doppler, tables
from aerolake.gui import theme


@st.cache_data
def _datasets(path: str) -> list[str]:
    return tables.list_datasets(path)


@st.cache_data(show_spinner="Loading dataset…")
def _load(path: str, dataset: str) -> tables.AnalysisTable:
    return tables.load_table(path, dataset)


@st.cache_data(show_spinner="Parsing acquisition…")
def _parse_acq(acq_path: str, satmap_path: str, utc_offset_h: float) -> dict:
    """Parse measured Doppler (+ optional satmap for names). Cached per paths."""
    satmap = doppler.parse_satmap(doppler.read_text_lines(satmap_path)) if satmap_path else {}
    return doppler.parse_measured(
        doppler.read_text_lines(acq_path), satmap, utc_offset_h=utc_offset_h
    )


@st.cache_data(show_spinner="Propagating TLE (Skyfield)…")
def _predict(
    tle_path: str,
    lat: float,
    lon: float,
    alt: float,
    t0_iso: str,
    t1_iso: str,
    wanted: tuple[str, ...],
) -> dict:
    """Predict Doppler/sky-track for the satellites seen in the acquisition."""
    sats = doppler.load_tles(doppler.read_text_lines(tle_path))
    if wanted:  # keep only the satellites we actually measured (else keep all)
        sats = [s for s in sats if s.name.upper() in wanted] or sats
    return doppler.predict_doppler(
        sats, lat, lon, alt, datetime.fromisoformat(t0_iso), datetime.fromisoformat(t1_iso)
    )


def _pick_file(sb, label: str, found: list[str], *, optional: bool = False, guess: str = "") -> str:
    """A selectbox over discovered files (or a free-text path if none found)."""
    if not found:
        return sb.text_input(label, value="")
    opts = (["(none)"] if optional else []) + found
    idx = 0
    if guess:  # preselect the file whose name contains the hint
        for i, o in enumerate(opts):
            if guess.lower() in o.lower():
                idx = i
                break
    choice = sb.selectbox(label, opts, index=idx)
    return "" if choice == "(none)" else choice


# ---------------------------------------------------------------------------
# Mode: Doppler / Skyplot
# ---------------------------------------------------------------------------


def _doppler_view() -> None:
    sb = st.sidebar
    sb.header("Doppler source")
    found = [f for f in sorted(glob.glob("data/iridium_doppler/*")) if "." in f]
    acq = _pick_file(sb, "Acquisition file", found, guess="acquisition")
    tle = _pick_file(sb, "TLE file (for prediction)", found, optional=True, guess="iridium-next")
    satmap = _pick_file(sb, "Satmap (optional)", found, optional=True, guess="satmap")

    sb.divider()
    sb.subheader("Observer (fixed)")
    lat = sb.number_input("Latitude", value=45.49476, format="%.5f")
    lon = sb.number_input("Longitude", value=-73.56304, format="%.5f")
    alt = sb.number_input("Altitude (m)", value=30.0, step=1.0)
    # GR-Iridium 'IRA:' timestamps are already UTC → 0. Only change if a capture
    # stored local time (the lab's original script assumed -4 for EDT).
    utc_off = sb.number_input("IRA: UTC offset (h)", value=0.0, step=1.0)
    max_sats = sb.slider("Max satellites (by # measurements)", 1, 20, value=6)

    if not acq:
        st.info(
            "Dépose l'acquisition décodée (+ TLE + satmap) dans "
            "`data/iridium_doppler/`, ou tape un chemin. Le **TLE** est nécessaire "
            "pour la courbe prédite (S-curve) et le skyplot."
        )
        st.stop()

    try:
        measured = _parse_acq(acq, satmap, utc_off)
    except (OSError, ValueError) as exc:
        st.error(f"Cannot parse {acq!r}: {exc}")
        st.stop()

    # Keep the N most-measured satellites so the plot stays readable.
    if len(measured) > max_sats:
        keep = sorted(measured, key=lambda k: -len(measured[k]["times"]))[:max_sats]
        measured = {k: measured[k] for k in keep}

    span = doppler.measured_time_span(measured)
    if not measured or span is None:
        st.warning("No measured Doppler found — check the file format (TSV or IRA: log).")
        st.stop()
    start, end = span
    total_s = max(0.0, (end - start).total_seconds())

    # Headline metrics.
    n_pts = sum(len(e["times"]) for e in measured.values())
    m1, m2, m3 = st.columns(3)
    m1.metric("Satellites seen", len(measured))
    m2.metric("Measurements", f"{n_pts:,}")
    m3.metric("Time span", f"{total_s:,.0f} s")

    # TLE prediction (optional but needed for the predicted curve + skyplot).
    predicted: dict = {}
    if tle:
        wanted = tuple(
            sorted({lbl.replace("IRA_", "").replace("_", " ").upper() for lbl in measured})
        )
        try:
            predicted = _predict(
                tle,
                float(lat),
                float(lon),
                float(alt),
                (start - timedelta(seconds=30)).isoformat(),
                (end + timedelta(seconds=30)).isoformat(),
                wanted,
            )
        except Exception as exc:  # surface any Skyfield/TLE error to the UI
            st.warning(f"TLE prediction failed: {exc}")
    else:
        st.info("Ajoute un **TLE** pour la prédiction (S-curve) et le skyplot.")

    # --- Time cursor + ▶ animation (st.fragment → only the plots refresh) ---
    if "dop_pos" not in st.session_state:
        st.session_state.dop_pos = 0.0
    st.session_state.dop_pos = min(st.session_state.dop_pos, total_s)

    playing = sb.toggle(
        "▶ Animate (time cursor)",
        value=False,
        help="Sweep a time cursor across the Doppler curves and skyplot.",
    )
    if playing:
        sb.caption("▶ Playing… (the cursor advances on its own)")
        refresh: float | None = 0.3
    else:
        st.session_state.dop_pos = (
            sb.slider(
                "Time cursor (s from start)",
                0.0,
                total_s,
                value=st.session_state.dop_pos,
                step=max(1.0, round(total_s / 200, 1)),
            )
            if total_s > 0
            else 0.0
        )
        refresh = None
    step = max(1.0, round(total_s / 150, 1))  # cursor advance per tick

    @st.fragment(run_every=refresh)
    def _view() -> None:
        pos = min(st.session_state.dop_pos, total_s)
        cursor = start + timedelta(seconds=pos)
        left, right = st.columns([3, 2])
        with left:
            st.plotly_chart(
                doppler.doppler_figure(measured, predicted, cursor_time=cursor),
                use_container_width=True,
            )
        with right:
            st.plotly_chart(
                doppler.skyplot_figure(predicted, cursor_time=cursor),
                use_container_width=True,
            )
        st.caption(f"🕒 t = {cursor:%Y-%m-%d %H:%M:%S} UTC  (+{pos:.0f}s / {total_s:.0f}s)")

        if playing and total_s > 0:  # advance the cursor, wrap at the end (loop)
            nxt = pos + step
            st.session_state.dop_pos = 0.0 if nxt > total_s else round(nxt, 1)

    _view()


# ---------------------------------------------------------------------------
# Mode: HDF5 tables (original viewer)
# ---------------------------------------------------------------------------


def _tables_view() -> None:
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

    with st.expander("Capture setup (HDF5 attributes)", expanded=False):
        st.json(t.metadata)

    summary = tables.summarize(t)
    metric_cols = st.columns(len(summary))
    for col, (key, value) in zip(metric_cols, summary.items(), strict=False):
        shown = f"{value:,.1f}" if isinstance(value, float) else f"{value:,}"
        col.metric(key.replace("_", " ").title(), shown)

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
            st.plotly_chart(tables.generic_over_time_fig(t, ycol), use_container_width=True)


def _run() -> None:
    st.set_page_config(page_title="AeroLake — Analysis", page_icon="🛰️", layout="wide")
    st.markdown(theme.STREAMLIT_CSS, unsafe_allow_html=True)
    st.title("🛰️ AeroLake — Analysis viewer")
    st.caption(
        "Decoded GPS / IMU / Iridium data (reference / ground truth) — NOT raw IQ. "
        "For captures, use aerolake-gui."
    )

    mode = st.sidebar.radio("View", ["📊 HDF5 tables", "🛰️ Doppler / Skyplot"])
    st.sidebar.divider()
    if mode.startswith("🛰️"):
        _doppler_view()
    else:
        _tables_view()


_run()
