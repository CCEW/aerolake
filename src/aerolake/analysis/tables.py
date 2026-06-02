"""Loader + plots for the decoded analysis ``.h5`` tables (GPS / IMU / Iridium).

These HDF5 files (produced by the GR-Iridium Toolkit + ublox/VN100 scripts)
bundle several analysis **modalities** as groups, each holding one or more
test-run datasets:

  - ``GPS_Analysis``     — navigation fixes: Timestamp, Latitude, Longitude,
                           Altitude, Satellites, Fix Type (+ Velocity sometimes)
  - ``IMU_Analysis``     — VN100 inertial: Yaw/Pitch/Roll, Mag/Accel/Gyro XYZ
  - ``Iridium_Analysis`` — decoded Iridium bursts: Satellite_ID, Frequency, SNR…

**None of these is raw IQ** — they are *decoded results* (reference / ground
truth). So this stays a separate analysis tool, entirely outside the IQ
lakehouse (it never touches MinIO or SigMF).

Design: a generic loader (any dataset → :class:`AnalysisTable`) + pure Plotly
figure builders. ``figures_for`` returns the right set of figures for a table's
detected ``kind``, so the Streamlit app is thin glue.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import plotly.graph_objects as go

from aerolake.gui import theme

KIND_GPS = "gps"
KIND_IMU = "imu"
KIND_IRIDIUM = "iridium"
KIND_GENERIC = "generic"


@dataclass(frozen=True)
class AnalysisTable:
    """One loaded analysis dataset (a single test run) + its attributes."""

    path: str
    dataset: str               # full HDF5 path, e.g. 'GPS_Analysis/test_01_…'
    kind: str                  # gps / imu / iridium / generic
    columns: list[str]
    data: np.ndarray           # shape (n_rows, n_cols), float64
    metadata: dict[str, Any]

    @property
    def n_rows(self) -> int:
        return int(self.data.shape[0])

    def has_column(self, name: str) -> bool:
        return name in self.columns

    def column(self, name: str) -> np.ndarray:
        if name not in self.columns:
            raise KeyError(f"No column {name!r}; have {self.columns}")
        return self.data[:, self.columns.index(name)]

    def time_seconds(self) -> np.ndarray:
        """Timestamps as seconds from the first row (ns → s)."""
        ts = self.column("Timestamp")
        return (ts - ts.min()) / 1e9 if ts.size else ts


def _clean(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def _kind_from_group(dataset_path: str) -> str:
    """Detect the modality from the dataset's group (first path segment)."""
    group = dataset_path.split("/", 1)[0].lower()
    if group.startswith("gps"):
        return KIND_GPS
    if group.startswith("imu"):
        return KIND_IMU
    if group.startswith("iridium"):
        return KIND_IRIDIUM
    return KIND_GENERIC


def list_datasets(path: str) -> list[str]:
    """Return every dataset path inside the HDF5 file (e.g. 'GPS_Analysis/…')."""
    import h5py

    names: list[str] = []
    with h5py.File(path, "r") as f:
        f.visititems(
            lambda n, o: names.append(n) if isinstance(o, h5py.Dataset) else None
        )
    return sorted(names)


def load_table(path: str, dataset: str | None = None) -> AnalysisTable:
    """Load one dataset (the first if ``dataset`` is None) into an AnalysisTable."""
    import h5py

    with h5py.File(path, "r") as f:
        names: list[str] = []
        f.visititems(
            lambda n, o: names.append(n) if isinstance(o, h5py.Dataset) else None
        )
        if not names:
            raise ValueError(f"No dataset found in {path!r}")
        name = dataset or sorted(names)[0]
        dset = f[name]
        data = np.asarray(dset[()], dtype=np.float64)
        if data.ndim == 1:
            data = data.reshape(-1, 1)
        attrs = {k: _clean(v) for k, v in dset.attrs.items()}

    col_attr = next((v for k, v in attrs.items() if "Column_Names" in k), None)
    if col_attr:
        columns = [c.strip() for c in str(col_attr).split(",")]
    else:
        columns = [f"col{i}" for i in range(data.shape[1])]

    return AnalysisTable(
        path=path,
        dataset=name,
        kind=_kind_from_group(name),
        columns=columns,
        data=data,
        metadata=attrs,
    )


def summarize(t: AnalysisTable) -> dict[str, Any]:
    """Kind-aware headline numbers for the metrics row."""
    out: dict[str, Any] = {"rows": t.n_rows}
    if t.has_column("Timestamp") and t.n_rows:
        out["duration_s"] = float(t.time_seconds().max())
    if t.kind == KIND_IRIDIUM and t.n_rows:
        out["satellites"] = int(np.unique(t.column("Satellite_ID")).size)
        out["snr_mean"] = float(np.mean(t.column("SNR")))
    elif t.kind == KIND_GPS and t.n_rows:
        out["max_satellites"] = int(np.max(t.column("Satellites")))
        if t.has_column("Altitude"):
            out["alt_max"] = float(np.max(t.column("Altitude")))
    return out


# ---------------------------------------------------------------------------
# Figure builders (pure). One small helper to cut boilerplate.
# ---------------------------------------------------------------------------

def _lines_over_time(t: AnalysisTable, cols: list[str], title: str, ytitle: str) -> go.Figure:
    """Plot several columns against elapsed time as overlaid lines."""
    tmpl = theme.register_theme()
    secs = t.time_seconds()
    fig = go.Figure()
    for c in cols:
        if t.has_column(c):
            fig.add_trace(go.Scattergl(x=secs, y=t.column(c), mode="lines", name=c))
    fig.update_layout(
        template=tmpl, title=title,
        xaxis_title="Time (s from start)", yaxis_title=ytitle,
    )
    return fig


def _scatter_colored(x, y, color, title, xtitle, ytitle, cbar) -> go.Figure:
    tmpl = theme.register_theme()
    fig = go.Figure(
        go.Scattergl(
            x=x, y=y, mode="markers",
            marker=dict(color=color, colorscale=theme.HEATMAP_COLORSCALE,
                        size=5, opacity=0.7, colorbar=dict(title=cbar)),
        )
    )
    fig.update_layout(template=tmpl, title=title, xaxis_title=xtitle, yaxis_title=ytitle)
    return fig


# --- GPS ---

def gps_map_fig(t: AnalysisTable) -> go.Figure:
    """Interactive OpenStreetMap of the ground track, coloured by altitude.

    Uses Plotly's MapLibre ``Scattermap`` with the free ``open-street-map``
    style — no API token needed (tiles are fetched by the browser).
    """
    tmpl = theme.register_theme()
    lat = t.column("Latitude")
    lon = t.column("Longitude")
    color = t.column("Altitude") if t.has_column("Altitude") else None

    fig = go.Figure(
        go.Scattermap(
            lat=lat,
            lon=lon,
            mode="markers+lines",
            marker=dict(
                size=7,
                color=color,
                colorscale=theme.HEATMAP_COLORSCALE,
                showscale=color is not None,
                colorbar=dict(title="Alt (m)"),
            ),
            line=dict(color=theme.ACCENT, width=2),
        )
    )
    # Center on the track; pick a zoom from the coordinate span (heuristic).
    center_lat = float(np.mean(lat)) if lat.size else 0.0
    center_lon = float(np.mean(lon)) if lon.size else 0.0
    span = max(float(np.ptp(lat)), float(np.ptp(lon)), 1e-3) if lat.size else 1.0
    zoom = float(np.clip(11.0 - np.log2(span / 0.01), 3.0, 16.0))
    fig.update_layout(
        template=tmpl,
        title="GPS track (OpenStreetMap)",
        map=dict(
            style="open-street-map",
            center=dict(lat=center_lat, lon=center_lon),
            zoom=zoom,
        ),
        margin=dict(l=0, r=0, t=40, b=0),
    )
    return fig


def gps_track_fig(t: AnalysisTable) -> go.Figure:
    """Ground track: longitude vs latitude, coloured by altitude."""
    color = t.column("Altitude") if t.has_column("Altitude") else None
    fig = _scatter_colored(
        t.column("Longitude"), t.column("Latitude"), color,
        "GPS ground track", "Longitude (°)", "Latitude (°)", "Alt (m)",
    )
    fig.update_yaxes(scaleanchor="x", scaleratio=1)  # roughly equal aspect
    return fig


def gps_altitude_fig(t: AnalysisTable) -> go.Figure:
    return _lines_over_time(t, ["Altitude"], "Altitude over time", "Altitude (m)")


def gps_satellites_fig(t: AnalysisTable) -> go.Figure:
    return _lines_over_time(
        t, ["Satellites", "Fix Type"], "Satellites & fix type over time", "Count / type"
    )


# --- IMU ---

def imu_orientation_fig(t: AnalysisTable) -> go.Figure:
    return _lines_over_time(t, ["Yaw", "Pitch", "Roll"], "Orientation (Euler)", "Degrees")


def imu_accel_fig(t: AnalysisTable) -> go.Figure:
    return _lines_over_time(
        t, ["Accel_X", "Accel_Y", "Accel_Z"], "Accelerometer", "Accel (m/s²)"
    )


def imu_gyro_fig(t: AnalysisTable) -> go.Figure:
    return _lines_over_time(t, ["Gyro_X", "Gyro_Y", "Gyro_Z"], "Gyroscope", "Rate (rad/s)")


# --- Iridium ---

def iridium_snr_over_time_fig(t: AnalysisTable) -> go.Figure:
    return _scatter_colored(
        t.time_seconds(), t.column("SNR"), t.column("SNR"),
        "SNR over time", "Time (s from start)", "SNR (dB)", "SNR",
    )


def iridium_bursts_per_satellite_fig(t: AnalysisTable) -> go.Figure:
    tmpl = theme.register_theme()
    sats, counts = np.unique(t.column("Satellite_ID"), return_counts=True)
    fig = go.Figure(go.Bar(x=[str(int(s)) for s in sats], y=counts, marker_color=theme.ACCENT))
    fig.update_layout(template=tmpl, title="Bursts per satellite",
                      xaxis_title="Satellite ID", yaxis_title="Burst count")
    return fig


def iridium_snr_histogram_fig(t: AnalysisTable) -> go.Figure:
    tmpl = theme.register_theme()
    fig = go.Figure(go.Histogram(x=t.column("SNR"), marker_color=theme.ACCENT_2))
    fig.update_layout(template=tmpl, title="SNR distribution",
                      xaxis_title="SNR (dB)", yaxis_title="Count")
    return fig


def iridium_frequency_over_time_fig(t: AnalysisTable) -> go.Figure:
    return _scatter_colored(
        t.time_seconds(), t.column("Frequency"), t.column("SNR"),
        "Burst frequency over time", "Time (s from start)", "Frequency (Hz)", "SNR",
    )


# Which figures to show for each kind: list of (title, builder).
_FIGURES: dict[str, list[tuple[str, Callable[[AnalysisTable], go.Figure]]]] = {
    KIND_GPS: [
        ("🗺️ Map", gps_map_fig),
        ("📍 Track (X/Y)", gps_track_fig),
        ("⛰️ Altitude", gps_altitude_fig),
        ("🛰️ Satellites/Fix", gps_satellites_fig),
    ],
    KIND_IMU: [
        ("🧭 Orientation", imu_orientation_fig),
        ("📈 Accelerometer", imu_accel_fig),
        ("🌀 Gyroscope", imu_gyro_fig),
    ],
    KIND_IRIDIUM: [
        ("📈 SNR/time", iridium_snr_over_time_fig),
        ("🛰️ Per satellite", iridium_bursts_per_satellite_fig),
        ("📊 SNR dist.", iridium_snr_histogram_fig),
        ("📻 Freq/time", iridium_frequency_over_time_fig),
    ],
}


def generic_over_time_fig(t: AnalysisTable, column: str) -> go.Figure:
    """Fallback: plot any single column against elapsed time (unknown kinds)."""
    return _lines_over_time(t, [column], f"{column} over time", column)


def figures_for(t: AnalysisTable) -> list[tuple[str, go.Figure]]:
    """Build the appropriate set of (title, figure) for this table's kind.

    For an unknown ('generic') kind, returns an empty list — the caller can
    fall back to a generic column picker.
    """
    return [(title, builder(t)) for title, builder in _FIGURES.get(t.kind, [])]
