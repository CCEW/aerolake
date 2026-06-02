"""AeroLake capture explorer — Streamlit web GUI.

A browser-based viewer for the captures stored in MinIO. Pick a capture in the
sidebar and the main panel shows its spectrum, spectrogram and IQ
constellation, plus its tags and quality verdict — all without downloading a
single byte more than once (reads are cached).

This module is deliberately **thin**: every bit of real logic lives elsewhere
and is reused here, so the app is just "glue":

  - data access goes through ``CaptureReader`` / ``StorageClient`` (the same
    chokepoint the CLIs use — the GUI never talks to S3 directly);
  - all DSP/figures come from the pure functions in ``aerolake.gui.plots``;
  - all styling comes from ``aerolake.gui.theme``.

Run it with: ``uv run --group gui aerolake-gui`` (or
``uv run --group gui streamlit run src/aerolake/gui/app.py``).

Note: this file is exercised by hand (Streamlit), not by the unit tests — the
testable logic was intentionally pushed down into plots.py.
"""

from __future__ import annotations

import json

import streamlit as st

from aerolake.common.storage import StorageClient, StorageError
from aerolake.consumer.reader import CaptureReader
from aerolake.gui import plots, theme

# ---------------------------------------------------------------------------
# Cached resources / data
# ---------------------------------------------------------------------------
# Streamlit re-runs this whole script top-to-bottom on every widget change.
# Caching is therefore essential: without it we'd rebuild the S3 client and
# re-download the capture on every click.

@st.cache_resource
def _get_clients() -> tuple[StorageClient, CaptureReader]:
    """Build the storage client + reader once and reuse across re-runs.

    ``cache_resource`` is for non-serialisable singletons (network clients).
    """
    storage = StorageClient()
    return storage, CaptureReader(storage)


@st.cache_data(show_spinner="Listing captures…")
def _list_captures(prefix: str) -> list[str]:
    """List complete-capture keys under a prefix (cached per prefix)."""
    _, reader = _get_clients()
    return reader.list_captures(prefix=prefix)


@st.cache_data(show_spinner="Inspecting capture…")
def _capture_overview(data_key: str):
    """Cheap HEAD-only overview: (sample_rate, total_samples, tags, metadata).

    Lets us show the total duration and bound the time-window slider WITHOUT
    downloading any samples (the capture may be gigabytes).
    """
    storage, reader = _get_clients()
    info = reader.inspect(data_key)
    total_samples = storage.object_size(data_key) // 8  # cf32 = 8 bytes/sample
    sample_rate = float(info.metadata.get("sample-rate", 0.0)) or 1.0
    return sample_rate, total_samples, info.tags, info.metadata


@st.cache_data(show_spinner="Reading window…")
def _load_segment(data_key: str, start_s: float, duration_s: float):
    """Read ONLY a time window via an HTTP Range request (partial read).

    This is what makes the explorer usable on multi-GB captures: we never load
    more than ``duration_s`` of samples, and ``start_s`` lets you seek anywhere.
    """
    _, reader = _get_clients()
    content = reader.read_segment(data_key, start_s=start_s, duration_s=duration_s)
    return content.samples, content.sigmf_meta, content.info.tags, content.info.metadata


@st.cache_data
def _load_quality_report(data_key: str) -> dict | None:
    """Return the parsed quality_report.json for a capture, or None if absent."""
    storage, _ = _get_clients()
    report_key = data_key.rsplit("/", 1)[0] + "/quality_report.json"
    if not storage.object_exists(report_key):
        return None
    return json.loads(storage.download_bytes(report_key).decode("utf-8"))


# ---------------------------------------------------------------------------
# Small presentation helpers
# ---------------------------------------------------------------------------

def _explain_box(view_key: str) -> None:
    """Render the plain-language explanation for a view as a styled callout."""
    st.markdown(
        f"<div class='aerolake-explain'>{plots.EXPLANATIONS[view_key]}</div>",
        unsafe_allow_html=True,
    )


def _quality_badge(quality: str) -> str:
    """Return a coloured Markdown badge for a quality tag value."""
    colour = {
        "validated": theme.GOOD,
        "rejected": theme.BAD,
        "raw": theme.WARN,
        "archived": theme.TEXT_MUTED,
    }.get(quality, theme.TEXT_MUTED)
    return f":material/sensors: <span style='color:{colour};font-weight:600'>{quality}</span>"


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def _run() -> None:
    st.set_page_config(
        page_title="AeroLake — Capture Explorer",
        page_icon="📡",
        layout="wide",
    )
    # Inject the aerospace dark CSS for the page chrome.
    st.markdown(theme.STREAMLIT_CSS, unsafe_allow_html=True)
    st.title("📡 AeroLake — Capture Explorer")

    # --- Sidebar: selection + view controls -------------------------------
    st.sidebar.header("Capture")
    prefix = st.sidebar.text_input(
        "Prefix filter", value="", placeholder="e.g. gnss_l1/"
    )

    # Listing hits MinIO; a failure here usually means MinIO is down / .env is
    # wrong. Surface it clearly instead of a raw traceback.
    try:
        keys = _list_captures(prefix)
    except StorageError as exc:
        st.error(
            f"Could not reach storage: {exc}\n\n"
            "Is MinIO running (`docker compose up -d` in docker/) and is your "
            "`.env` pointing at it?"
        )
        st.stop()

    if not keys:
        st.info(f"No complete captures found under prefix {prefix!r}.")
        st.stop()

    data_key = st.sidebar.selectbox("Select a capture", keys)

    st.sidebar.divider()
    st.sidebar.header("Display")
    # "Explain mode" shows plain-language captions under each view so a
    # non-specialist understands what they're looking at. Experts can turn it
    # off for a denser view.
    explain = st.sidebar.toggle(
        "🔰 Explain mode",
        value=True,
        help="Plain-language captions under each chart (turn off if you're an expert).",
    )

    st.sidebar.divider()
    st.sidebar.header("Parameters")
    # FFT size trades frequency resolution (larger) vs smoothness/speed.
    nperseg = st.sidebar.select_slider(
        "FFT size", options=[256, 512, 1024, 2048, 4096], value=1024
    )
    max_points = st.sidebar.slider(
        "Constellation points", 1000, 20000, value=5000, step=1000
    )

    # --- Time window (partial read) ---------------------------------------
    # We load only a window of the capture via a Range request, so even a
    # multi-GB recording opens instantly and you can seek through its duration.
    overview_sr, total_samples, _tags_o, _meta_o = _capture_overview(data_key)
    total_duration = total_samples / overview_sr if overview_sr else 0.0

    st.sidebar.divider()
    st.sidebar.header("Time window")
    st.sidebar.caption(f"Total: {total_duration:,.1f} s — {total_samples:,} samples")
    window_s = st.sidebar.select_slider(
        "Window (s)", options=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0], value=2.0
    )
    max_start = max(0.0, round(total_duration - window_s, 1))
    start_s = (
        st.sidebar.slider("Start (s)", 0.0, max_start, 0.0, step=0.5)
        if max_start > 0
        else 0.0
    )

    # --- Load just that window --------------------------------------------
    samples, sigmf_meta, tags, _metadata = _load_segment(data_key, start_s, window_s)
    sample_rate = float(sigmf_meta.get("global", {}).get("core:sample_rate", 0.0))
    center_freq = float(
        sigmf_meta.get("captures", [{}])[0].get("core:frequency", 0.0)
    )

    # --- Header strip: key facts + quality verdict ------------------------
    info_col, quality_col = st.columns([3, 1])
    with info_col:
        st.markdown(f"**Key:** `{data_key}`")
        st.markdown(
            f"**Signal type:** {tags.get('signal-type', '—')} &nbsp;|&nbsp; "
            f"**Hardware:** {tags.get('hardware', '—')} &nbsp;|&nbsp; "
            f"**Window:** {start_s:.1f}-{start_s + window_s:.1f}s of "
            f"{total_duration:.0f}s &nbsp;|&nbsp; **Samples:** {len(samples):,}",
            unsafe_allow_html=True,
        )
    with quality_col:
        st.markdown(
            f"**Quality:** {_quality_badge(tags.get('quality', '—'))}",
            unsafe_allow_html=True,
        )

    # Quick metrics row from the SigMF metadata.
    m1, m2, m3 = st.columns(3)
    m1.metric("Sample rate", f"{sample_rate / 1e6:.3f} MS/s")
    m2.metric("Center freq", f"{center_freq / 1e6:.3f} MHz")
    m3.metric("Datatype", sigmf_meta.get("global", {}).get("core:datatype", "—"))

    # --- Quality report (if one was written by validate) ------------------
    report = _load_quality_report(data_key)
    if report is not None:
        verdict = "✅ valid" if report.get("is_valid") else "❌ rejected"
        with st.expander(f"Quality report — {verdict}", expanded=False):
            failed = report.get("failed_checks", [])
            if failed:
                st.write("**Failed checks:**")
                for f in failed:
                    st.write(f"- {f}")
            # Show the raw measured metrics as a compact table.
            st.json(
                {
                    k: report[k]
                    for k in (
                        "clipping_ratio",
                        "rms_power_dbfs",
                        "invalid_count",
                        "dc_offset_i",
                        "dc_offset_q",
                        "sample_completeness",
                        "metadata_valid",
                    )
                    if k in report
                }
            )

    # --- Plain-language one-line summary ----------------------------------
    # A quick "what does this signal look like?" sentence anyone can read,
    # computed from the spectrum (peak frequency + height above noise).
    summary = plots.describe_signal(samples, sample_rate, center_freq)
    st.markdown(
        f"<div class='aerolake-summary'>📡 {summary}</div>",
        unsafe_allow_html=True,
    )
    st.write("")  # a little breathing room

    # --- The plots, one per tab -------------------------------------------
    # Tabs keep the page tidy (no endless scrolling). Each tab optionally leads
    # with its plain-language explanation when Explain mode is on.
    tab_spectrum, tab_spectrogram, tab_constellation = st.tabs(
        ["📈 Spectrum", "🌈 Spectrogram", "✴️ Constellation"]
    )
    with tab_spectrum:
        if explain:
            _explain_box("spectrum")
        st.plotly_chart(
            plots.spectrum_figure(samples, sample_rate, center_freq, nperseg=nperseg),
            use_container_width=True,
        )
    with tab_spectrogram:
        if explain:
            _explain_box("spectrogram")
        st.plotly_chart(
            plots.spectrogram_figure(samples, sample_rate, center_freq),
            use_container_width=True,
        )
    with tab_constellation:
        if explain:
            _explain_box("constellation")
        st.plotly_chart(
            plots.constellation_figure(samples, max_points=max_points),
            use_container_width=True,
        )


# Streamlit executes the module top-level on each run, so we just call _run().
_run()
