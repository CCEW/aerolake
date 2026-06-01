"""Analysis of decoded Iridium ``.h5`` tables (a BONUS, outside the IQ lakehouse).

These ``.h5`` files are **not** raw IQ — they are the *output* of the GR-Iridium
Toolkit: a table of detected bursts (timestamp, satellite, position, frequency,
signal/noise/SNR). This module loads that table and turns it into Plotly
figures, so it's a self-contained "analysis" capability sitting *alongside* the
core capture pipeline (it does not touch MinIO or SigMF).

Same pure-vs-glue split as the rest of the project: the loader + the figure
builders here are pure (testable on a synthetic table); the Streamlit app
(``iridium_app.py``) is thin glue.

Expected layout (from Wissem's recordings)
-------------------------------------------
One HDF5 dataset shaped ``(n_bursts, 13)`` whose ``*-Column_Names`` attribute
lists: ``Timestamp, Satellite_ID, X, Y, Z, Lat, Long, Altitude,
Spot_Beam_Number, Frequency, Signal, Noise, SNR``. Timestamps are Unix
nanoseconds. Other attributes record the capture setup (sample rate, centre
frequency, hardware…).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import plotly.graph_objects as go

from aerolake.gui import theme


@dataclass(frozen=True)
class IridiumAnalysis:
    """A loaded Iridium analysis table + its capture metadata."""

    dataset_name: str
    columns: list[str]
    data: np.ndarray              # shape (n_bursts, n_columns), float64
    metadata: dict[str, Any]      # the HDF5 attributes (capture setup, dates…)

    @property
    def n_bursts(self) -> int:
        return int(self.data.shape[0])

    def column(self, name: str) -> np.ndarray:
        """Return the column named ``name`` (raises if absent)."""
        if name not in self.columns:
            raise KeyError(f"No column {name!r}; have {self.columns}")
        return self.data[:, self.columns.index(name)]

    def time_seconds(self) -> np.ndarray:
        """Timestamps as seconds elapsed from the first burst (ns → s)."""
        ts = self.column("Timestamp")
        return (ts - ts.min()) / 1e9


def _clean(value: Any) -> Any:
    """Turn HDF5/numpy scalars into plain Python for display/JSON."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def load_iridium_analysis(path: str, dataset: str | None = None) -> IridiumAnalysis:
    """Load an Iridium analysis ``.h5`` into an :class:`IridiumAnalysis`.

    By default loads the first dataset found; pass ``dataset`` to pick one.
    Column names come from the dataset's ``*-Column_Names`` attribute (falling
    back to generic ``col0…`` if absent).
    """
    import h5py  # imported lazily so the core package needn't depend on h5py

    with h5py.File(path, "r") as f:
        names: list[str] = []
        f.visititems(
            lambda n, o: names.append(n) if isinstance(o, h5py.Dataset) else None
        )
        if not names:
            raise ValueError(f"No dataset found in {path!r}")
        name = dataset or names[0]
        dset = f[name]
        data = np.asarray(dset[()], dtype=np.float64)
        if data.ndim == 1:  # defensive: make it 2-D (n, 1)
            data = data.reshape(-1, 1)
        attrs = {k: _clean(v) for k, v in dset.attrs.items()}

    # Column names live in the attribute whose key ends with "Column_Names".
    col_attr = next(
        (v for k, v in attrs.items() if "Column_Names" in k), None
    )
    if col_attr:
        columns = [c.strip() for c in str(col_attr).split(",")]
    else:
        columns = [f"col{i}" for i in range(data.shape[1])]

    return IridiumAnalysis(
        dataset_name=name, columns=columns, data=data, metadata=attrs
    )


def summarize(a: IridiumAnalysis) -> dict[str, Any]:
    """Compute headline numbers for a quick textual overview."""
    snr = a.column("SNR")
    freq = a.column("Frequency")
    sats = a.column("Satellite_ID")
    t = a.time_seconds()
    return {
        "bursts": a.n_bursts,
        "satellites": int(np.unique(sats).size),
        "duration_s": float(t.max()) if a.n_bursts else 0.0,
        "snr_mean": float(np.mean(snr)) if a.n_bursts else 0.0,
        "snr_min": float(np.min(snr)) if a.n_bursts else 0.0,
        "snr_max": float(np.max(snr)) if a.n_bursts else 0.0,
        "freq_min": float(np.min(freq)) if a.n_bursts else 0.0,
        "freq_max": float(np.max(freq)) if a.n_bursts else 0.0,
    }


# ---------------------------------------------------------------------------
# Figure builders (pure)
# ---------------------------------------------------------------------------

def snr_over_time_fig(a: IridiumAnalysis) -> go.Figure:
    """SNR of each detected burst over the recording, coloured by SNR."""
    tmpl = theme.register_theme()
    fig = go.Figure(
        go.Scattergl(
            x=a.time_seconds(),
            y=a.column("SNR"),
            mode="markers",
            marker=dict(
                color=a.column("SNR"),
                colorscale=theme.HEATMAP_COLORSCALE,
                size=4,
                opacity=0.7,
                colorbar=dict(title="SNR"),
            ),
        )
    )
    fig.update_layout(
        template=tmpl,
        title="SNR over time",
        xaxis_title="Time (s from start)",
        yaxis_title="SNR (dB)",
    )
    return fig


def bursts_per_satellite_fig(a: IridiumAnalysis) -> go.Figure:
    """How many bursts were detected per satellite."""
    tmpl = theme.register_theme()
    sats, counts = np.unique(a.column("Satellite_ID"), return_counts=True)
    fig = go.Figure(
        go.Bar(
            x=[str(int(s)) for s in sats],
            y=counts,
            marker_color=theme.ACCENT,
        )
    )
    fig.update_layout(
        template=tmpl,
        title="Bursts per satellite",
        xaxis_title="Satellite ID",
        yaxis_title="Burst count",
    )
    return fig


def snr_histogram_fig(a: IridiumAnalysis) -> go.Figure:
    """Distribution of burst SNR."""
    tmpl = theme.register_theme()
    fig = go.Figure(go.Histogram(x=a.column("SNR"), marker_color=theme.ACCENT_2))
    fig.update_layout(
        template=tmpl,
        title="SNR distribution",
        xaxis_title="SNR (dB)",
        yaxis_title="Count",
    )
    return fig


def frequency_over_time_fig(a: IridiumAnalysis) -> go.Figure:
    """Burst frequency over time, coloured by SNR — shows the Iridium channels."""
    tmpl = theme.register_theme()
    fig = go.Figure(
        go.Scattergl(
            x=a.time_seconds(),
            y=a.column("Frequency"),
            mode="markers",
            marker=dict(
                color=a.column("SNR"),
                colorscale=theme.HEATMAP_COLORSCALE,
                size=4,
                opacity=0.7,
                colorbar=dict(title="SNR"),
            ),
        )
    )
    fig.update_layout(
        template=tmpl,
        title="Burst frequency over time",
        xaxis_title="Time (s from start)",
        yaxis_title="Frequency (Hz)",
    )
    return fig
