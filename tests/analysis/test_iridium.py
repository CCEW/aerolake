"""Tests for the Iridium analysis loader + pure plot functions.

We build a tiny synthetic .h5 in the same layout as Wissem's recordings, then
check the loader, the summary, and that each figure builder returns a Plotly
figure.
"""

from __future__ import annotations

import h5py
import numpy as np
import plotly.graph_objects as go

from aerolake.analysis import iridium

_COLS = (
    "Timestamp, Satellite_ID, X, Y, Z, Lat, Long, Altitude, "
    "Spot_Beam_Number, Frequency, Signal, Noise, SNR"
)


def _make_h5(path: str, n: int = 10) -> None:
    rng = np.random.default_rng(0)
    data = np.zeros((n, 13), dtype=np.float64)
    data[:, 0] = 1.7e18 + np.arange(n) * 1e9          # Timestamp (ns), 1 s apart
    data[:, 1] = np.array([1, 2, 1, 3, 2, 1, 2, 3, 1, 2])[:n]  # Satellite_ID
    data[:, 9] = 1.62e9 + rng.normal(0, 1e5, n)       # Frequency (Hz)
    data[:, 12] = rng.uniform(5, 20, n)               # SNR (dB)
    with h5py.File(path, "w") as f:
        grp = f.create_group("Iridium_Analysis")
        dset = grp.create_dataset("test_01", data=data)
        dset.attrs["01-Column_Names"] = _COLS
        dset.attrs["05-Sample Rate (MHz)"] = np.int64(10)
        dset.attrs["06-Centre Frequency (MHz)"] = np.int64(1622)


def test_load_parses_columns_and_metadata(tmp_path) -> None:
    p = str(tmp_path / "iridium.h5")
    _make_h5(p, n=10)

    a = iridium.load_iridium_analysis(p)

    assert a.n_bursts == 10
    assert a.columns[0] == "Timestamp"
    assert a.columns[-1] == "SNR"
    assert len(a.columns) == 13
    assert a.metadata["05-Sample Rate (MHz)"] == 10
    assert a.metadata["06-Centre Frequency (MHz)"] == 1622


def test_column_and_time_helpers(tmp_path) -> None:
    p = str(tmp_path / "iridium.h5")
    _make_h5(p, n=10)
    a = iridium.load_iridium_analysis(p)

    assert a.column("SNR").shape == (10,)
    t = a.time_seconds()
    assert t[0] == 0.0            # relative to first burst
    assert t[-1] == 9.0           # 9 s after start (1 s spacing)


def test_summarize(tmp_path) -> None:
    p = str(tmp_path / "iridium.h5")
    _make_h5(p, n=10)
    a = iridium.load_iridium_analysis(p)

    s = iridium.summarize(a)
    assert s["bursts"] == 10
    assert s["satellites"] == 3        # IDs 1, 2, 3
    assert s["duration_s"] == 9.0
    assert s["snr_min"] <= s["snr_mean"] <= s["snr_max"]


def test_figure_builders_return_plotly(tmp_path) -> None:
    p = str(tmp_path / "iridium.h5")
    _make_h5(p, n=10)
    a = iridium.load_iridium_analysis(p)

    for fig in (
        iridium.snr_over_time_fig(a),
        iridium.bursts_per_satellite_fig(a),
        iridium.snr_histogram_fig(a),
        iridium.frequency_over_time_fig(a),
    ):
        assert isinstance(fig, go.Figure)
