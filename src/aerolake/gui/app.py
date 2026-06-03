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
import subprocess
import time

import numpy as np
import streamlit as st

from aerolake.common.storage import StorageClient, StorageError
from aerolake.consumer.reader import CaptureReader
from aerolake.gui import plots, theme
from aerolake.producer.ingest import ingest_files
from aerolake.scripts.ingest import _resolve_files

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
    """Cheap HEAD-only overview: (sample_rate, total_samples, center_freq, tags).

    Lets us show duration + metrics and bound the time-window slider WITHOUT
    downloading any samples (the capture may be gigabytes).
    """
    storage, reader = _get_clients()
    info = reader.inspect(data_key)
    total_samples = storage.object_size(data_key) // 8  # cf32 = 8 bytes/sample
    sample_rate = float(info.metadata.get("sample-rate", 0.0)) or 1.0
    center_freq = float(info.metadata.get("center-freq", 0.0))
    return sample_rate, total_samples, center_freq, info.tags


@st.cache_data(show_spinner="Building whole-capture overview…")
def _overview(data_key: str, sample_rate: float, n_slices: int = 240, slice_len: int = 4096):
    """Read ``n_slices`` short windows evenly spread across the WHOLE capture.

    Only a few MB are transferred (n_slices x slice_len x 8 bytes) via Range
    reads — enough to render a full-duration spectrogram of a multi-GB capture.
    Returns (slices, times_s).
    """
    storage, _ = _get_clients()
    total = storage.object_size(data_key) // 8
    slice_len = min(slice_len, total)
    starts = np.linspace(0, max(0, total - slice_len), n_slices).astype(int)
    slices, times = [], []
    for s0 in starts:
        raw = storage.download_range(data_key, int(s0) * 8, (int(s0) + slice_len) * 8 - 1)
        slices.append(np.frombuffer(raw, dtype=np.complex64))
        times.append(float(s0) / sample_rate)
    return slices, times


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
# Actions — the *write* side (ingest / curate / delete / stream)
# ---------------------------------------------------------------------------
# Everything above is read-only. The helpers below let the WHOLE workflow run
# from the browser — ingest a capture, curate its quality tag, delete it, push
# it onto the ZeroMQ bus — so you never have to drop back to the terminal during
# a demo. They reuse the *exact* same library functions the CLIs call; the GUI
# stays a thin glue layer (no new logic, no direct S3 access).


def _ingest_panel() -> None:
    """Sidebar form that ingests a real IQ file/folder into MinIO.

    The GUI twin of ``aerolake-ingest``: pick a path (a single IQ file, OR a
    folder of RFSoC ``RX0_pkt_*.bin`` packets), the acquisition parameters, and
    stream it into the lake with ``ingest_files`` (multipart upload — even a
    multi-GB folder of packets won't blow up the RAM).
    """
    with st.sidebar.expander("📥 Ingest a capture", expanded=False):
        # A form batches the inputs: nothing runs until "Ingest" is pressed
        # (without it, Streamlit would re-trigger on every keystroke).
        with st.form("ingest_form"):
            path = st.text_input("Path (file or folder)", placeholder="data/.../captures_Test_1")
            glob_pat = st.text_input("Glob (for folders)", value="RX0_pkt_*.bin")
            signal_type = st.text_input("Signal type", value="iridium")
            c1, c2 = st.columns(2)
            sample_rate = c1.number_input(
                "Sample rate (Hz)", value=400_000.0, step=1000.0, format="%.0f"
            )
            center_freq = c2.number_input(
                "Center freq (Hz)", value=1_626_271_000.0, step=1000.0, format="%.0f"
            )
            datatype = c1.selectbox("Datatype", ["cs32", "cf32", "cu8", "cs16"])
            hardware = c2.text_input("Hardware", value="rfsoc")
            submitted = st.form_submit_button("📥 Ingest", use_container_width=True)
        if not submitted:
            return
        if not path.strip():
            st.warning("Enter a path to an IQ file or a folder of packets.")
            return
        # Resolve a single file or a sorted folder of packets (same as the CLI).
        files = _resolve_files(path.strip(), glob_pat)
        if not files:
            st.error(f"No file(s) found at: {path}")
            return
        storage, _ = _get_clients()
        with st.spinner(f"Ingesting {len(files):,} file(s) → MinIO (multipart)…"):
            try:
                result = ingest_files(
                    file_paths=files,
                    signal_type=signal_type,
                    sample_rate=float(sample_rate),
                    center_freq=float(center_freq),
                    datatype=datatype,
                    hardware=hardware,
                    storage_client=storage,
                )
            except StorageError as exc:
                st.error(f"Storage error: {exc}")
                return
            except (ValueError, OSError) as exc:
                st.error(f"Ingestion failed: {exc}")
                return
        st.success(f"Ingested {result.sample_count:,} samples → {result.data_key}")
        # The catalog changed → drop the cached listing so the new capture shows.
        _list_captures.clear()


def _set_quality(data_key: str, new_quality: str) -> None:
    """Change the quality tag (read-merge-write, NEVER a blind replace).

    ``update_tags`` REPLACES the whole tag set, so we MUST read the existing
    tags and merge — otherwise we'd wipe signal-type / hardware / recorder
    (the classic ADR-003 footgun).
    """
    storage, _ = _get_clients()
    tags = storage.get_object_tags(data_key)  # read…
    tags["quality"] = new_quality  # …merge the one field…
    storage.update_tags(data_key, tags)  # …write the full set back.
    _capture_overview.clear()  # tags are cached → refresh them.


def _delete_capture(data_key: str) -> int:
    """Delete every object of a capture (data + meta + quality report).

    A capture is a *folder* of objects under ``…/{session}/`` — we remove them
    all so no orphan bytes or stale report linger. Returns the count deleted.
    """
    storage, _ = _get_clients()
    session_prefix = data_key.rsplit("/", 1)[0] + "/"
    deleted = 0
    for key in list(storage.list_objects(prefix=session_prefix)):
        storage.delete_object(key)
        deleted += 1
    _list_captures.clear()
    _capture_overview.clear()
    return deleted


def _stream_status() -> subprocess.Popen[bytes] | None:
    """Return the running stream subprocess, or None if it isn't (still) alive.

    ``poll()`` is None only while the child is still running; once it exits we
    treat the slot as free again.
    """
    proc = st.session_state.get("stream_proc")
    if proc is not None and proc.poll() is None:
        return proc
    return None


def _actions_panel(data_key: str, tags: dict) -> None:
    """Sidebar panel to stream / curate / delete the SELECTED capture."""
    with st.sidebar.expander("🎛️ Actions on this capture", expanded=False):
        # --- 📡 ZeroMQ streaming (runs as a background process) -----------
        st.caption("📡 ZeroMQ stream")
        bind = st.text_input("Bind address", value="tcp://*:5555", key="stream_bind")
        duration = st.number_input(
            "Stream duration (s)",
            value=10.0,
            min_value=1.0,
            step=5.0,
            key="stream_dur",
            help="A bounded window (partial read) keeps the demo snappy.",
        )
        proc = _stream_status()
        if proc is None:
            if st.button("▶ Start stream", use_container_width=True, key="stream_start"):
                # Launch the CLI as a *separate* process so it streams in the
                # background (at the recorded cadence) without blocking the GUI.
                try:
                    st.session_state.stream_proc = subprocess.Popen(
                        [
                            "aerolake-stream",
                            "--key",
                            data_key,
                            "--bind",
                            bind,
                            "--duration",
                            str(duration),
                        ]
                    )
                except OSError as exc:
                    st.error(f"Could not start stream: {exc}")
                else:
                    st.rerun()
        else:
            st.success(f"Streaming on {bind} (PID {proc.pid})")
            if st.button("⏹ Stop stream", use_container_width=True, key="stream_stop"):
                proc.terminate()
                st.session_state.stream_proc = None
                st.rerun()

        st.divider()
        # --- 🏷️ Quality curation (cheap tag flip, read-merge-write) -------
        st.caption(f"🏷️ Quality — currently **{tags.get('quality', '—')}**")
        q1, q2, q3 = st.columns(3)
        if q1.button("✅ Validate", use_container_width=True):
            _set_quality(data_key, "validated")
            st.rerun()
        if q2.button("❌ Reject", use_container_width=True):
            _set_quality(data_key, "rejected")
            st.rerun()
        if q3.button("↩ Raw", use_container_width=True):
            _set_quality(data_key, "raw")
            st.rerun()

        st.divider()
        # --- 🗑️ Destructive: delete the whole capture ---------------------
        st.caption("🗑️ Danger zone")
        confirm = st.checkbox("Yes, delete this capture", key="confirm_delete")
        if st.button("🗑️ Delete capture", disabled=not confirm, use_container_width=True):
            n = _delete_capture(data_key)
            st.success(f"Deleted {n} object(s).")
            st.rerun()


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

    # --- Sidebar: ingest a new capture (always available, even on an empty
    #     bucket — it's how you add the very first capture from the browser) --
    _ingest_panel()

    # --- Sidebar: selection + view controls -------------------------------
    st.sidebar.header("Capture")
    prefix = st.sidebar.text_input("Prefix filter", value="", placeholder="e.g. gnss_l1/")

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
    nperseg = st.sidebar.select_slider("FFT size", options=[256, 512, 1024, 2048, 4096], value=1024)
    max_points = st.sidebar.slider("Constellation points", 1000, 20000, value=5000, step=1000)

    # --- Cheap overview (no samples loaded): duration, metrics, tags ------
    sample_rate, total_samples, center_freq, tags = _capture_overview(data_key)
    total_duration = total_samples / sample_rate if sample_rate else 0.0

    # --- Sidebar: write-side actions on the selected capture --------------
    _actions_panel(data_key, tags)

    st.sidebar.divider()
    st.sidebar.header("Time window")
    st.sidebar.caption(f"Total: {total_duration:,.1f} s — {total_samples:,} samples")
    # Overview mode = a coarse spectrogram of the WHOLE capture (strided reads).
    overview_mode = st.sidebar.toggle(
        "🔭 Whole-capture overview",
        value=False,
        help="Coarse spectrogram across the FULL duration (cheap strided reads).",
    )

    # --- Header strip (from cheap metadata — no samples yet) --------------
    info_col, quality_col = st.columns([3, 1])
    with info_col:
        st.markdown(f"**Key:** `{data_key}`")
        st.markdown(
            f"**Signal type:** {tags.get('signal-type', '—')} &nbsp;|&nbsp; "
            f"**Hardware:** {tags.get('hardware', '—')} &nbsp;|&nbsp; "
            f"**Total:** {total_duration:,.0f} s &nbsp;|&nbsp; "
            f"**Samples:** {total_samples:,}",
            unsafe_allow_html=True,
        )
    with quality_col:
        st.markdown(
            f"**Quality:** {_quality_badge(tags.get('quality', '—'))}",
            unsafe_allow_html=True,
        )
    m1, m2, m3 = st.columns(3)
    m1.metric("Sample rate", f"{sample_rate / 1e6:.3f} MS/s")
    m2.metric("Center freq", f"{center_freq / 1e6:.3f} MHz")
    m3.metric("Duration", f"{total_duration:,.0f} s")

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

    if overview_mode:
        # Whole-capture waterfall: only a few MB read across the full duration.
        slices, times = _overview(data_key, sample_rate)
        st.markdown(
            f"<div class='aerolake-summary'>🔭 Whole-capture overview — "
            f"{len(slices)} slices across {total_duration:,.0f} s</div>",
            unsafe_allow_html=True,
        )
        st.write("")
        tab_sg, tab_sp = st.tabs(["🌈 Full spectrogram", "📈 Average spectrum"])
        with tab_sg:
            if explain:
                _explain_box("spectrogram")
            st.plotly_chart(
                plots.overview_spectrogram_figure(slices, sample_rate, center_freq, times),
                use_container_width=True,
            )
        with tab_sp:
            st.plotly_chart(
                plots.overview_spectrum_figure(slices, sample_rate, center_freq),
                use_container_width=True,
            )
        return

    # --- Detailed window mode ---------------------------------------------
    window_s = st.sidebar.select_slider(
        "Window (s)", options=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0], value=2.0
    )
    max_start = max(0.0, round(total_duration - window_s, 1))

    # --- ▶ Animated playback ----------------------------------------------
    # "Animated playback" auto-advances the time window so the spectrum and
    # spectrogram scroll through the whole capture, like a moving playhead.
    # Streamlit has no real event loop, so we fake it: hold the playhead in
    # session_state, render the window, then (if playing) bump the playhead and
    # ask Streamlit to re-run after a short pause (see the bottom of this file).
    if "play_pos" not in st.session_state:
        st.session_state.play_pos = 0.0
    playing = st.sidebar.toggle(
        "▶ Animated playback",
        value=False,
        help="Auto-advance the window through the whole capture (a moving playhead).",
    )
    # The slider is keyless and takes its value FROM the playhead, so the
    # auto-advance can move it; dragging it by hand updates the playhead too.
    pos0 = min(st.session_state.play_pos, max_start)
    start_s = (
        st.sidebar.slider("Start (s)", 0.0, max_start, value=pos0, step=0.5)
        if max_start > 0
        else 0.0
    )
    st.session_state.play_pos = start_s
    samples, _sigmf_meta, _tags, _metadata = _load_segment(data_key, start_s, window_s)

    summary = plots.describe_signal(samples, sample_rate, center_freq)
    st.markdown(
        f"<div class='aerolake-summary'>📡 {summary} &nbsp;"
        f"(window {start_s:.1f}-{start_s + window_s:.1f}s)</div>",
        unsafe_allow_html=True,
    )
    st.write("")

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

    # --- ▶ Animated playback: advance the playhead and re-run -------------
    # Done LAST, after the figures are drawn: move the window forward by one
    # window-length, wrap to 0 at the end (so it loops), pause briefly, then
    # re-run. The cached Range reads make each step cheap.
    if playing and max_start > 0:
        nxt = start_s + window_s
        st.session_state.play_pos = 0.0 if nxt > max_start else round(nxt, 1)
        time.sleep(0.6)
        st.rerun()


# Streamlit executes the module top-level on each run, so we just call _run().
_run()
