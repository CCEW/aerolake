"""Tests for the analysis loader + per-modality plot functions.

We build a tiny multi-group .h5 (GPS + IMU + Iridium) in the same shape as
Wissem's recordings, then check dataset listing, kind detection, the loader,
summaries, and that the right figures build for each kind.
"""

from __future__ import annotations

import h5py
import numpy as np
import plotly.graph_objects as go

from aerolake.analysis import tables

_GPS_COLS = "Timestamp, Latitude, Longitude, Altitude, Satellites, Fix Type"
_IMU_COLS = (
    "Timestamp, Yaw, Pitch, Roll, Mag_X, Mag_Y, Mag_Z, "
    "Accel_X, Accel_Y, Accel_Z, Gyro_X, Gyro_Y, Gyro_Z"
)
_IRI_COLS = (
    "Timestamp, Satellite_ID, X, Y, Z, Lat, Long, Altitude, "
    "Spot_Beam_Number, Frequency, Signal, Noise, SNR"
)


def _make_multimodal(path: str) -> None:
    rng = np.random.default_rng(0)
    with h5py.File(path, "w") as f:
        # GPS
        g = f.create_group("GPS_Analysis")
        gps = np.zeros((20, 6))
        gps[:, 0] = 1.7e18 + np.arange(20) * 1e9
        gps[:, 1] = 45.0 + rng.normal(0, 0.01, 20)   # Latitude
        gps[:, 2] = 1.5 + rng.normal(0, 0.01, 20)    # Longitude
        gps[:, 3] = 100 + rng.normal(0, 5, 20)       # Altitude
        gps[:, 4] = 8                                 # Satellites
        d = g.create_dataset("test_01", data=gps)
        d.attrs["01-Column_Names"] = _GPS_COLS
        # IMU
        gi = f.create_group("IMU_Analysis")
        imu = np.zeros((30, 13))
        imu[:, 0] = 1.7e18 + np.arange(30) * 1e9
        d2 = gi.create_dataset("test_01", data=imu)
        d2.attrs["01-Column_Names"] = _IMU_COLS
        # Iridium
        gr = f.create_group("Iridium_Analysis")
        iri = np.zeros((15, 13))
        iri[:, 0] = 1.7e18 + np.arange(15) * 1e9
        iri[:, 1] = np.array([1, 2, 3] * 5)          # Satellite_ID
        iri[:, 9] = 1.62e9                            # Frequency
        iri[:, 12] = rng.uniform(5, 20, 15)           # SNR
        d3 = gr.create_dataset("test_01", data=iri)
        d3.attrs["01-Column_Names"] = _IRI_COLS


def test_list_datasets_finds_all_groups(tmp_path) -> None:
    p = str(tmp_path / "multi.h5")
    _make_multimodal(p)
    names = tables.list_datasets(p)
    assert "GPS_Analysis/test_01" in names
    assert "IMU_Analysis/test_01" in names
    assert "Iridium_Analysis/test_01" in names


def test_kind_detection(tmp_path) -> None:
    p = str(tmp_path / "multi.h5")
    _make_multimodal(p)
    assert tables.load_table(p, "GPS_Analysis/test_01").kind == tables.KIND_GPS
    assert tables.load_table(p, "IMU_Analysis/test_01").kind == tables.KIND_IMU
    assert tables.load_table(p, "Iridium_Analysis/test_01").kind == tables.KIND_IRIDIUM


def test_loader_parses_columns(tmp_path) -> None:
    p = str(tmp_path / "multi.h5")
    _make_multimodal(p)
    t = tables.load_table(p, "GPS_Analysis/test_01")
    assert t.columns == ["Timestamp", "Latitude", "Longitude", "Altitude",
                         "Satellites", "Fix Type"]
    assert t.n_rows == 20
    assert t.column("Satellites")[0] == 8


def test_summarize_is_kind_aware(tmp_path) -> None:
    p = str(tmp_path / "multi.h5")
    _make_multimodal(p)
    gps = tables.summarize(tables.load_table(p, "GPS_Analysis/test_01"))
    assert gps["max_satellites"] == 8
    iri = tables.summarize(tables.load_table(p, "Iridium_Analysis/test_01"))
    assert iri["satellites"] == 3


def test_figures_for_each_kind(tmp_path) -> None:
    p = str(tmp_path / "multi.h5")
    _make_multimodal(p)
    for ds, n_expected in [
        ("GPS_Analysis/test_01", 3),
        ("IMU_Analysis/test_01", 3),
        ("Iridium_Analysis/test_01", 4),
    ]:
        figs = tables.figures_for(tables.load_table(p, ds))
        assert len(figs) == n_expected
        assert all(isinstance(fig, go.Figure) for _, fig in figs)


def test_generic_over_time_fig(tmp_path) -> None:
    p = str(tmp_path / "multi.h5")
    _make_multimodal(p)
    t = tables.load_table(p, "IMU_Analysis/test_01")
    fig = tables.generic_over_time_fig(t, "Yaw")
    assert isinstance(fig, go.Figure)
