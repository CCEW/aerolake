"""Tests for the Iridium Doppler/skyplot engine (aerolake.analysis.doppler).

Parsers are tested on synthetic TSV + IRA: log lines; the TLE prediction is
exercised with a real (historical) ISS TLE over a 24 h window so at least one
pass is visible from Montreal — all offline (Skyfield uses bundled timescale).
"""

from __future__ import annotations

from datetime import UTC, datetime

import plotly.graph_objects as go

from aerolake.analysis import doppler

# --- synthetic decoded acquisition (TSV) ---------------------------------
# Columns: Satellite_ID  Spot_Beam  Pos_XYZ  Pos_Geo  Altitude  Timestamp  Frequency
_T0 = 1_580_300_000.0  # a Unix epoch (UTC)
_TSV = [
    "Satellite_ID\tSpot_Beam\tPos_XYZ\tPos_Geo\tAltitude\tTimestamp\tFrequency",
    f"042\tB1\t1,2,3\t45.5,-73.5\t780000\t{_T0:.1f}\t{doppler.FC_HZ + 1000}",
    f"042\tB1\t1,2,3\t45.5,-73.5\t780000\t{_T0 + 1:.1f}\t{doppler.FC_HZ + 1200}",
    f"107\tB2\t4,5,6\t46.0,-73.0\t781000\t{_T0:.1f}\t{doppler.FC_HZ - 3000}",
    f"000\tB0\t0,0,0\t0,0\t0\t{_T0:.1f}\t{doppler.FC_HZ}",  # unknown sat → skipped
]

_SATMAP = ["042 seen: IRIDIUM 119", "107  IRIDIUM_140"]


def test_parse_satmap() -> None:
    m = doppler.parse_satmap(_SATMAP)
    assert m["042"] == "IRIDIUM 119"
    assert m["107"] == "IRIDIUM 140"


def test_parse_measured_tsv_doppler_and_labels() -> None:
    measured = doppler.parse_measured(_TSV, doppler.parse_satmap(_SATMAP))
    # sat 000 is dropped; 042 and 107 remain, named via satmap.
    assert set(measured) == {"IRA_IRIDIUM_119", "IRA_IRIDIUM_140"}
    s42 = measured["IRA_IRIDIUM_119"]
    assert s42["sid"] == "042"
    assert s42["doppler"] == [1000.0, 1200.0]  # rx - carrier
    assert len(s42["times"]) == 2
    assert s42["times"][0] == datetime.fromtimestamp(_T0, tz=UTC)
    assert measured["IRA_IRIDIUM_140"]["doppler"] == [-3000.0]


def test_parse_measured_ira_log() -> None:
    lines = [
        f"IRA: p-1580300000 12.5 {doppler.FC_HZ + 500}  sat:042 beam:1",
        "garbage line",
        f"IRA: p-1580300001 0 {doppler.FC_HZ} sat:000",  # sat 000 skipped
    ]
    measured = doppler.parse_measured(lines)
    assert set(measured) == {"IRA_042"}
    assert measured["IRA_042"]["doppler"] == [500.0]


def test_window_and_span() -> None:
    measured = doppler.parse_measured(_TSV)
    span = doppler.measured_time_span(measured)
    assert span is not None
    start, _end = span
    # Window to just the first instant → 107 (only at _T0) stays, 042's 2nd drops.
    win = doppler.window_measured(measured, start, start)
    assert all(len(e["times"]) >= 1 for e in win.values())
    assert doppler.measured_time_span({}) is None


def test_match_prediction() -> None:
    preds = {"IRIDIUM 119": {"times": [], "doppler_hz": [], "az_deg": [], "el_deg": []}}
    assert doppler.match_prediction("IRA_IRIDIUM_119", preds) == "IRIDIUM 119"
    assert doppler.match_prediction("IRA_999", preds) is None


# --- TLE prediction (Skyfield/SGP4, real ISS TLE, offline) ----------------

_ISS_TLE = [
    "ISS (ZARYA)",
    "1 25544U 98067A   20029.51835301  .00000874  00000-0  23545-4 0  9994",
    "2 25544  51.6443 234.6473 0004482  86.4117  19.5045 15.49228129210004",
]


def test_predict_doppler_returns_consistent_tracks() -> None:
    sats = doppler.load_tles(_ISS_TLE)
    assert len(sats) == 1
    t0 = datetime(2020, 1, 29, 0, 0, tzinfo=UTC)
    t1 = datetime(2020, 1, 30, 0, 0, tzinfo=UTC)
    # Montreal observer (same as the lab script). Over 24 h the ISS passes over.
    pred = doppler.predict_doppler(sats, 45.49476, -73.56304, 30.0, t0, t1, dt_s=60.0)
    assert isinstance(pred, dict) and pred  # at least one visible pass
    for track in pred.values():
        n = len(track["times"])
        assert n == len(track["doppler_hz"]) == len(track["az_deg"]) == len(track["el_deg"])
        assert all(e > 0.0 for e in track["el_deg"])  # only above the horizon


# --- figures --------------------------------------------------------------

def _fake_pred() -> dict:
    times = [datetime(2020, 1, 29, 12, 0, s, tzinfo=UTC) for s in range(0, 30, 10)]
    return {
        "IRIDIUM 119": {
            "times": times,
            "doppler_hz": [3000.0, 0.0, -3000.0],
            "az_deg": [10.0, 90.0, 170.0],
            "el_deg": [5.0, 45.0, 8.0],
        }
    }


def test_doppler_figure_builds_with_and_without_prediction() -> None:
    measured = doppler.parse_measured(_TSV, doppler.parse_satmap(_SATMAP))
    assert isinstance(doppler.doppler_figure(measured), go.Figure)
    fig = doppler.doppler_figure(
        measured, _fake_pred(), cursor_time=datetime.fromtimestamp(_T0, tz=UTC)
    )
    assert isinstance(fig, go.Figure)
    assert isinstance(doppler.doppler_figure({}), go.Figure)  # empty → placeholder


def test_skyplot_figure_builds() -> None:
    pred = _fake_pred()
    assert isinstance(doppler.skyplot_figure(pred), go.Figure)
    cursor = datetime(2020, 1, 29, 12, 0, 10, tzinfo=UTC)
    assert isinstance(doppler.skyplot_figure(pred, cursor_time=cursor), go.Figure)
    assert isinstance(doppler.skyplot_figure({}), go.Figure)  # empty → placeholder
