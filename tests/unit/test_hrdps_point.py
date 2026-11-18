"""HRDPS point extracts -> hourly forcing, and the HRDPS + GFS blend (ADR-116)."""

import numpy as np
import pandas as pd
import pytest

from snowagent.forecast.hrdps_point import LAPSE_K_PER_M, blend_hourly, hrdps_hourly


def _run(max_lead=48, point="p", elev=2000.0):
    leads = np.arange(max_lead + 1)
    return pd.DataFrame({
        "run_utc": "2026-11-17T00:00:00+00:00", "lead_h": leads, "point": point,
        "tmp2m_k": 270.0 + 0.1 * leads, "rh2m_pct": 80.0, "wind10m_ms": 3.0, "wdir10m_deg": 250.0,
        "pres_pa": 80000.0, "apcp_kgm2": 0.5 * leads, "dswrf_jm2": 3600.0 * 100 * leads,
        "dlwrf_jm2": 3600.0 * 250 * leads, "snow_kgm2": 0.5 * leads, "model_elev_m": [elev] + [None] * max_lead})


def test_hourly_deaccumulates_and_keeps_instantaneous():
    h, elev = hrdps_hourly(_run(), "p")
    assert elev == 2000.0
    assert len(h) == 48 and h.index[0] == pd.Timestamp("2026-11-17T01:00Z")
    assert np.allclose(h["psum"], 0.5) and np.allclose(h["iswr"], 100) and np.allclose(h["ilwr"], 250)
    assert h["ta"].iloc[0] == pytest.approx(270.1) and np.allclose(h["rh"], 0.8)
    assert list(h.columns) == ["ta", "rh", "vw", "dw", "psum", "iswr", "ilwr"]


def test_negative_accumulation_noise_is_clipped():
    df = _run()
    df.loc[df.lead_h == 10, "apcp_kgm2"] = 4.0  # below lead 9 (4.5)
    h, _ = hrdps_hourly(df, "p")
    assert (h["psum"] >= 0).all()


def _gfs(elev=1800.0, hours=72):
    idx = pd.date_range("2026-11-17T01:00Z", periods=hours, freq="h")
    return pd.DataFrame({"ta": 265.0, "rh": 0.9, "vw": 5.0, "dw": 200.0, "iswr": 50.0, "ilwr": 200.0,
                         "psum": 0.2}, index=idx), elev


def test_blend_hrdps_then_gfs_tail_at_hrdps_height():
    out, elev, info = blend_hourly(hrdps_hourly(_run(), "p"), _gfs())
    assert elev == 2000.0 and len(out) == 72 and info["source"] == "HRDPS+GFS"
    assert info["hrdps_hours"] == 48 and info["gfs_hours"] == 24
    assert out["psum"].iloc[47] == pytest.approx(0.5) and out["psum"].iloc[48] == pytest.approx(0.2)
    assert out["ta"].iloc[48] == pytest.approx(265.0 + LAPSE_K_PER_M * 200.0)


def test_blend_falls_back_to_gfs_alone():
    g = _gfs()
    out, elev, info = blend_hourly(None, g)
    assert info["source"] == "GFS" and elev == 1800.0 and len(out) == 72
    with pytest.raises(ValueError):
        blend_hourly(None, None)


def test_site_forecast_weather_follows_forecast_source(tmp_path, monkeypatch):
    from snowagent.web.build import _forecast_weather
    from tests.unit.lab_fixtures import write_gfs

    monkeypatch.chdir(tmp_path)
    issue = pd.Timestamp("2026-11-17T00:00Z")
    write_gfs(tmp_path, issue.isoformat(), ["p"])
    g, elev, info = _forecast_weather(issue, "p", "gfs")
    assert info == {"source": "GFS"} and len(g) == 72
    g, elev, info = _forecast_weather(issue, "p", "hrdps")  # no HRDPS run: GFS alone
    assert info["source"] == "GFS" and len(g) == 72
    d = tmp_path / "archive/forecasts/hrdps"
    d.mkdir(parents=True)
    _run().to_csv(d / "hrdps_2026111700.csv", index=False)
    g, elev, info = _forecast_weather(issue, "p", "hrdps")
    assert info["source"] == "HRDPS+GFS" and info["hrdps_hours"] == 48 and elev == 2000.0 and len(g) == 72
    with pytest.raises(ValueError):
        _forecast_weather(issue, "p", "ecmwf")
