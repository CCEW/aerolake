"""Iridium Doppler & sky-track engine (decoded data, ADR-011 family).

A Plotly port of the lab's ``doppler_simple.py`` reference script. It turns a
**decoded Iridium acquisition** (the GR-Iridium ``IRA:`` log, or a TSV with
``Satellite_ID / Pos_Geo / Timestamp / Frequency``) plus a **TLE** file into:

  - the **measured Doppler** per satellite (``rx_freq - carrier``), straight
    from the acquisition file;
  - the **predicted Doppler** from the TLE for a *fixed observer* (range-rate
    x carrier / c), via Skyfield/SGP4 — the classic "S-curve" to overlay;
  - the **sky track** (azimuth / elevation over time) for a polar *skyplot*,
    so you literally watch the satellites cross the sky.

Like the rest of ``analysis/``, this is **decoded reference data — never raw IQ**
and it never touches MinIO. The split is the usual one: this module is pure
(parsing + numpy + Plotly figures, unit-tested); the Streamlit app is glue.

The reference values (carrier, UTC offset) and parsing rules are kept identical
to ``doppler_simple.py`` so results match the lab's existing tool.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import plotly.graph_objects as go

from aerolake.gui import theme

# --- Physical / acquisition constants (match doppler_simple.py) -------------
C_MPS = 299_792_458
FC_HZ = 1_626_270_833  # Iridium IRA carrier frequency (Hz)
UTC_OFFSET_H = -4  # local → UTC, only used by the IRA: log format

# A colour-blind-friendly palette (satellites are colour-keyed across figures).
_PALETTE = [
    theme.ACCENT, theme.ACCENT_2, theme.GOOD, theme.BAD, "#a78bfa", "#f472b6",
    "#56B4E9", "#E69F00", "#44AA99", "#999933", "#AA4499", "#117733",
]

# A "track" = one satellite's time series. We keep it as plain dicts (JSON-ish)
# so the whole thing stays trivially serialisable and testable.
Track = dict[str, Any]


# ---------------------------------------------------------------------------
# File reading (glue helper — the parsers below take already-split lines)
# ---------------------------------------------------------------------------

def read_text_lines(path: str) -> list[str]:
    """Read a text file, trying a few encodings (UTF-8/16, BOM, latin-1)."""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-16", "utf-8", "latin-1"):
        try:
            return raw.decode(enc, errors="strict").splitlines()
        except Exception:
            continue
    return raw.decode("latin-1", errors="replace").splitlines()


# ---------------------------------------------------------------------------
# Parsers (pure: list[str] in → dicts out)
# ---------------------------------------------------------------------------

def parse_satmap(lines: list[str]) -> dict[str, str]:
    """Parse a satmap file: maps a numeric ID → ``"IRIDIUM <n>"`` name."""
    satmap: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = (
            re.search(r"^\s*(\d{1,3})\b.*?\bseen:\b.*?\b(IRIDIUM)\s*[_\s-]?(\d{1,3})\b", line, re.I)
            or re.search(r"^\s*(\d{1,3})\b.*?\b(IRIDIUM)\s*[_\s-]?(\d{1,3})\b", line, re.I)
            or re.search(r"^\s*(\d{1,3})\s*[:=>\-]+\s*(IRIDIUM)\s*[_\s-]?(\d{1,3})\b", line, re.I)
        )
        if m:
            satmap[m.group(1).zfill(3)] = f"IRIDIUM {int(m.group(3))}"
    return satmap


def _is_tsv(lines: list[str]) -> bool:
    """Heuristic: does this look like the tab-separated acquisition export?"""
    for line in lines[:5]:
        if "Satellite_ID" in line and "Frequency" in line:
            return True
        parts = line.split("\t")
        if len(parts) >= 7 and re.match(r"^\d{3}$", parts[0].strip()):
            return True
    return False


def parse_measured(
    lines: list[str],
    satmap: dict[str, str] | None = None,
    utc_offset_h: float = 0.0,
) -> dict[str, Track]:
    """Parse measured Doppler per satellite. Auto-detects TSV vs IRA: log.

    Returns ``{label: {"times": [datetime UTC], "doppler": [Hz], "sid": "NNN"}}``.
    Doppler is ``measured_freq - FC_HZ`` (so 0 Hz = on the nominal carrier).

    ``utc_offset_h`` shifts the IRA: log timestamps (the ``p-<epoch>`` field).
    GR-Iridium's epoch is already UTC, so the default is 0; expose it only if a
    particular capture stored *local* time (the lab's original script used -4).
    The TSV branch ignores it (those timestamps are unambiguous UTC epochs).
    """
    data: dict[str, Track] = {}

    if _is_tsv(lines):
        # Columns: Satellite_ID  Spot_Beam  Pos_XYZ  Pos_Geo  Altitude  Timestamp  Frequency …
        # -1 = "not resolved yet" (kept as int so the index ops below stay typed).
        col_sat = col_ts = col_freq = -1
        for raw in lines:
            parts = raw.rstrip("\n").split("\t")
            if "Satellite_ID" in parts[0]:
                hdr = [p.strip().lower() for p in parts]
                try:
                    col_sat = hdr.index("satellite_id")
                    col_ts = hdr.index("timestamp")
                    col_freq = hdr.index("frequency")
                except ValueError:
                    pass
                continue
            if len(parts) < 7:
                continue
            if col_sat < 0:  # no header seen → fall back to fixed columns
                col_sat, col_ts, col_freq = 0, 5, 6
            try:
                sid = parts[col_sat].strip().zfill(3)
                ts_s = float(parts[col_ts].strip())
                rx_hz = float(parts[col_freq].strip())
            except (ValueError, IndexError):
                continue
            if sid == "000":  # 000 = "unknown satellite", skip
                continue
            label = _label_for(sid, satmap)
            t_utc = datetime.fromtimestamp(ts_s, tz=UTC)
            e = data.setdefault(label, {"times": [], "doppler": [], "sid": sid})
            e["times"].append(t_utc)
            e["doppler"].append(rx_hz - FC_HZ)
    else:
        # GR-Iridium SDR "IRA:" log lines.
        re_tag = re.compile(r"^IRA:")
        re_triplet = re.compile(r"p-(\d+)\s+(\d+(?:\.\d+)?)\s+(\d+)")
        re_sat = re.compile(r"sat:\s*(\d+)")
        for raw in lines:
            line = raw.lstrip()
            if not re_tag.match(line):
                continue
            mt = re_triplet.search(line)
            ms = re_sat.search(line)
            if not mt or not ms or ms.group(1) == "000":
                continue
            ts, off_ms, rx_hz = int(mt.group(1)), float(mt.group(2)), int(mt.group(3))
            sid = ms.group(1).zfill(3)
            label = _label_for(sid, satmap)
            t_utc = (
                datetime.fromtimestamp(ts, tz=UTC)
                + timedelta(milliseconds=off_ms)
                + timedelta(hours=utc_offset_h)
            )
            e = data.setdefault(label, {"times": [], "doppler": [], "sid": sid})
            e["times"].append(t_utc)
            e["doppler"].append(rx_hz - FC_HZ)
    return data


def _label_for(sid: str, satmap: dict[str, str] | None) -> str:
    if satmap and sid in satmap:
        return f"IRA_{satmap[sid].replace(' ', '_')}"
    return f"IRA_{sid}"


def window_measured(
    measured: dict[str, Track], start: datetime, end: datetime
) -> dict[str, Track]:
    """Keep only samples with ``start <= t <= end`` (drops now-empty tracks)."""
    out: dict[str, Track] = {}
    for lbl, e in measured.items():
        ts = [t for t in e["times"] if start <= t <= end]
        ds = [d for t, d in zip(e["times"], e["doppler"], strict=True) if start <= t <= end]
        if ts:
            out[lbl] = {"times": ts, "doppler": ds, "sid": e.get("sid", "")}
    return out


def measured_time_span(measured: dict[str, Track]) -> tuple[datetime, datetime] | None:
    """Return ``(first, last)`` timestamp across all tracks, or None if empty."""
    all_t = [t for e in measured.values() for t in e["times"]]
    return (min(all_t), max(all_t)) if all_t else None


# ---------------------------------------------------------------------------
# TLE prediction (Skyfield / SGP4) — fixed observer
# ---------------------------------------------------------------------------

def load_tles(lines: list[str]) -> list[Any]:
    """Parse 2- or 3-line TLE blocks into Skyfield ``EarthSatellite`` objects."""
    from skyfield.api import EarthSatellite, load

    ts = load.timescale()
    sats: list[Any] = []
    clean = [line.strip() for line in lines if line.strip()]
    i = 0
    while i < len(clean):
        if i + 2 < len(clean) and clean[i + 1].startswith("1 ") and clean[i + 2].startswith("2 "):
            name, l1, l2 = clean[i], clean[i + 1], clean[i + 2]
            i += 3
        elif clean[i].startswith("1 ") and i + 1 < len(clean) and clean[i + 1].startswith("2 "):
            name, l1, l2 = f"OBJ_{len(sats) + 1}", clean[i], clean[i + 1]
            i += 2
        else:
            i += 1
            continue
        sats.append(EarthSatellite(l1, l2, name, ts))
    return sats


def predict_doppler(
    sats: list[Any],
    lat: float,
    lon: float,
    alt_m: float,
    t0_utc: datetime,
    t1_utc: datetime,
    dt_s: float = 1.0,
) -> dict[str, Track]:
    """Predict per-satellite Doppler + sky position for a FIXED observer.

    For each satellite above the horizon between ``t0`` and ``t1`` returns
    ``{name: {"times", "doppler_hz", "az_deg", "el_deg"}}``. Doppler is the
    range-rate projected onto the carrier: ``-(v·r̂ / c) · FC_HZ``.
    """
    from skyfield.api import load, wgs84

    ts = load.timescale()
    observer = wgs84.latlon(lat, lon, elevation_m=alt_m)

    n = max(2, math.ceil((t1_utc - t0_utc).total_seconds() / dt_s))
    grid = [t0_utc + timedelta(seconds=k * dt_s) for k in range(n)]
    t_sf = ts.utc(
        [d.year for d in grid], [d.month for d in grid], [d.day for d in grid],
        [d.hour for d in grid], [d.minute for d in grid],
        [d.second + d.microsecond / 1e6 for d in grid],
    )

    out: dict[str, Track] = {}
    for sat in sats:
        topo = (sat - observer).at(t_sf)
        alt, az, _ = topo.altaz()
        mask = alt.degrees > 0.0  # only while the satellite is up
        if not np.any(mask):
            continue
        r = topo.position.m[:, mask]
        v = topo.velocity.m_per_s[:, mask]
        rn = np.linalg.norm(r, axis=0)
        range_rate = np.einsum("ij,ij->j", v, r) / rn  # v·r̂  (m/s, + = receding)
        doppler = -(range_rate / C_MPS) * FC_HZ
        idx = np.nonzero(mask)[0]
        dts = t_sf.utc_datetime()
        out[sat.name] = {
            "times": [dts[k] for k in idx],
            "doppler_hz": doppler.tolist(),
            "az_deg": az.degrees[mask].tolist(),
            "el_deg": alt.degrees[mask].tolist(),
        }
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", s.upper())


def match_prediction(label: str, predicted: dict[str, Track]) -> str | None:
    """Match a measured ``IRA_*`` label to a predicted TLE satellite name."""
    name = (label[4:] if label.startswith("IRA_") else label).replace("_", " ").upper()
    target = _norm(name)
    for k in predicted:
        if _norm(k) == target:
            return k
    for k in predicted:
        nk = _norm(k)
        if nk.startswith(target) or target.startswith(nk):
            return k
    return None


# ---------------------------------------------------------------------------
# Figures (pure: dicts in → Plotly figures out)
# ---------------------------------------------------------------------------

def _display_name(label: str) -> str:
    return label.replace("IRA_", "").replace("_", " ")


def _empty(message: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        template=theme.register_theme(),
        annotations=[dict(text=message, showarrow=False, font=dict(size=16))],
    )
    return fig


def doppler_figure(
    measured: dict[str, Track],
    predicted: dict[str, Track] | None = None,
    cursor_time: datetime | None = None,
) -> go.Figure:
    """Doppler-vs-time: measured points + (optional) TLE-predicted S-curves.

    ``cursor_time`` draws a vertical playhead so the figure can be animated in
    sync with a time slider.
    """
    if not measured:
        return _empty("No measured Doppler in this window.")

    tmpl = theme.register_theme()
    fig = go.Figure()
    for i, (label, m) in enumerate(sorted(measured.items())):
        if not m["times"]:
            continue
        colour = _PALETTE[i % len(_PALETTE)]
        sid = m.get("sid", "")
        disp = f"{sid} — {_display_name(label)}" if sid else _display_name(label)
        # Measured: scattered dots.
        fig.add_trace(
            go.Scattergl(
                x=m["times"], y=m["doppler"], mode="markers", name=disp,
                marker=dict(size=5, color=colour, line=dict(width=0.3, color="#000")),
                legendgroup=disp,
            )
        )
        # Predicted: a smooth line in the same colour (no extra legend entry).
        pk = match_prediction(label, predicted) if predicted else None
        if pk:
            p = predicted[pk]  # type: ignore[index]
            fig.add_trace(
                go.Scatter(
                    x=p["times"], y=p["doppler_hz"], mode="lines",
                    line=dict(color=colour, width=2), opacity=0.85,
                    legendgroup=disp, showlegend=False,
                )
            )

    fig.add_hline(y=0, line=dict(color=theme.TEXT_MUTED, width=1, dash="dash"))
    if cursor_time is not None:
        fig.add_vline(x=cursor_time, line=dict(color=theme.ACCENT_2, width=2))
    fig.update_layout(
        template=tmpl, title="Iridium Doppler — measured (dots) vs TLE prediction (lines)",
        xaxis_title="Time (UTC)", yaxis_title="Doppler shift [Hz]",
        legend_title="ID — Satellite",
    )
    return fig


def skyplot_figure(
    predicted: dict[str, Track], cursor_time: datetime | None = None
) -> go.Figure:
    """Polar skyplot (azimuth / elevation): the satellites crossing the sky.

    Radius = 90 deg minus elevation, so the **zenith is the centre** and the **horizon
    the outer edge**; North is at the top, increasing clockwise. With a
    ``cursor_time`` each satellite shows its position *at that instant* (the
    animated "where is it now"); otherwise the full passes are drawn.
    """
    if not predicted:
        return _empty("No satellites above the horizon (need a TLE).")

    tmpl = theme.register_theme()
    fig = go.Figure()
    for i, (name, p) in enumerate(sorted(predicted.items())):
        colour = _PALETTE[i % len(_PALETTE)]
        az = np.asarray(p["az_deg"], dtype=float)
        el = np.asarray(p["el_deg"], dtype=float)
        # Faint full track of the pass.
        fig.add_trace(
            go.Scatterpolar(
                r=90.0 - el, theta=az, mode="lines",
                line=dict(color=colour, width=1), opacity=0.35,
                name=_display_name(name), legendgroup=name, showlegend=False,
            )
        )
        # Marker: position at the cursor instant (or the latest sample).
        if cursor_time is not None:
            times = p["times"]
            k = min(range(len(times)), key=lambda j: abs((times[j] - cursor_time).total_seconds()))
            # Only show the satellite if it is actually up near that time.
            if abs((times[k] - cursor_time).total_seconds()) > 5.0:
                continue
        else:
            k = len(el) - 1
        fig.add_trace(
            go.Scatterpolar(
                r=[90.0 - el[k]], theta=[az[k]], mode="markers+text",
                marker=dict(size=12, color=colour, line=dict(width=1, color="#000")),
                text=[_display_name(name)], textposition="top center",
                name=_display_name(name), legendgroup=name,
            )
        )

    fig.update_layout(
        template=tmpl, title="Sky track (azimuth / elevation)",
        polar=dict(
            radialaxis=dict(
                range=[0, 90], tickvals=[0, 30, 60, 90],
                ticktext=["90°", "60°", "30°", "0°"], angle=90,
            ),
            angularaxis=dict(direction="clockwise", rotation=90),
        ),
    )
    return fig
