"""Real-engine process tests: upstream example, restart equivalence, controlled radiation and phase cases."""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from snowagent.engine import snowpack as sp
from snowagent.engine.column import mass_budget, prepare_and_run
from snowagent.engine.profiles import convert_profile
from snowagent.spatial_forcing.builder import ForcingConfig, build_unit_forcing
from snowagent.state.replay import advance, initialize_from_history

pytestmark = pytest.mark.engine

UPSTREAM = Path("/opt/snowpack-src/snowpack-model/Source/snowpack")

# Documented replay-vs-restart tolerances (docs/decisions.md ADR-006)
TOL_HS_M = 0.01
TOL_SWE = 1.0
TOL_RHO_MEAN = 5.0
TOL_T_MEAN = 0.2


@pytest.mark.slow
def test_upstream_mst96_example_matches_reference(tmp_path, engine):
    if not UPSTREAM.exists():
        pytest.skip("upstream SNOWPACK source tree not present")
    from snowagent.engine.upstream import run_mst96_example

    r = run_mst96_example(UPSTREAM, tmp_path / "mst96")
    assert r["passed"], r


def _grid(layers, attr, z):
    out = np.full(len(z), np.nan)
    for ly in layers:
        out[(z >= ly["bottom_vertical_m"]) & (z < ly["top_vertical_m"])] = ly[attr]
    return out


def test_replay_and_checkpoint_restart_agree(mini, tmp_path):
    from snowagent.state.store import StateStore

    mid = pd.Timestamp(mini.checkpoint.analysis_time) - pd.Timedelta(days=12)
    store = StateStore(tmp_path / "store")
    cp_mid = initialize_from_history(mini.domain, mini.actuals, store, mid.to_pydatetime(), mini.engine,
                                     mini.settings, mini.fcfg, "snow_free")
    cp_end = advance(mini.domain, cp_mid, mini.actuals, store, mini.checkpoint.analysis_time, mini.engine,
                     mini.settings, mini.fcfg, issue_time=mini.forecast.meta.available_time)
    assert cp_end.parent_state_id == cp_mid.state_id and cp_end.analysis_time == mini.checkpoint.analysis_time
    for u in [x for x in mini.domain.units if x.supported]:
        a = mini.store.analysis_profile(mini.checkpoint, u.unit_id)  # continuous replay
        b = store.analysis_profile(cp_end, u.unit_id)  # replay + restart
        assert abs(a["diagnostics"]["hs_vertical_m"] - b["diagnostics"]["hs_vertical_m"]) <= TOL_HS_M
        assert abs(a["diagnostics"]["swe_kg_m2_per_slope_area"] - b["diagnostics"]["swe_kg_m2_per_slope_area"]) <= TOL_SWE
        z = np.arange(0, min(a["diagnostics"]["hs_vertical_m"], b["diagnostics"]["hs_vertical_m"]), 0.01)
        for attr, tol in (("density_kg_m3", TOL_RHO_MEAN), ("temperature_c", TOL_T_MEAN)):
            d = np.abs(_grid(a["layers"], attr, z) - _grid(b["layers"], attr, z))
            assert np.nanmean(d) <= tol, (u.unit_id, attr, np.nanmean(d))


def _controlled_column(engine, tmp_path, unit, src, name):
    uf = build_unit_forcing(src, unit.centroid_lonlat[1], unit.centroid_lonlat[0], unit.elevation_m, unit,
                            ForcingConfig())
    out = prepare_and_run(engine, sp.EngineSettings(), tmp_path / name, unit, uf.smet, src.index[-1].to_pydatetime(),
                          snowfree_start=(src.index[0] + pd.Timedelta(hours=6)).to_pydatetime())
    met = sp.parse_met(out.met)
    _, profs = sp.parse_pro(out.pro)
    return uf, met, profs


def _src(days, ta_c, psum_hours, ghi_scale=1.0):
    from tests.unit.test_forcing import src

    s = src(days=days, start="2026-02-01T01:00:00Z", ghi_scale=ghi_scale, ta_c=ta_c)
    s.loc[s.index[:psum_hours], "psum"] = 1.5
    s.loc[s.index[:psum_hours], "iswr"] = 0.0
    return s


def test_controlled_sunny_vs_shaded_response(engine, tmp_path):
    from tests.unit.test_forcing import unit

    s = _src(days=12, ta_c=-3.0, psum_hours=48)
    sunny = unit(aspect=180, horizon=0, uid="sunny")
    shaded = unit(aspect=0, horizon=25, uid="shaded")
    uf_s, met_s, prof_s = _controlled_column(engine, tmp_path, sunny, s, "sunny")
    uf_n, met_n, prof_n = _controlled_column(engine, tmp_path, shaded, s, "shaded")
    # identical source weather, same slope/elevation: only terrain orientation/horizon differ
    assert uf_s.smet["ISWR"].sum() > 3 * uf_n.smet["ISWR"].sum()
    ab_s = met_s["Net absorbed shortwave radiation"].sum()
    ab_n = met_n["Net absorbed shortwave radiation"].sum()
    assert ab_s > ab_n
    day = met_s.index.hour.isin(range(17, 23))
    assert met_s.loc[day, "Modeled surface temperature"].mean() > met_n.loc[day, "Modeled surface temperature"].mean()
    ls, ds, _, _ = convert_profile(prof_s[-1], sunny.slope_deg, "sunny")
    ln, dn, _, _ = convert_profile(prof_n[-1], shaded.slope_deg, "shaded")
    assert np.mean([x.temperature_c for x in ls]) > np.mean([x.temperature_c for x in ln])


def test_controlled_elevation_phase_response(engine, tmp_path):
    from tests.unit.test_forcing import unit

    s = _src(days=4, ta_c=2.5, psum_hours=36)  # source at 2000 m: near the rain-snow transition
    s.loc[s.index[:36], "ilwr"] = 305.0  # overcast, precipitating sky (physically consistent longwave)
    low = unit(slope=0, aspect=0, svf=1.0, elev=1700, uid="low")
    high = unit(slope=0, aspect=0, svf=1.0, elev=2700, uid="high")
    s_low = s.copy()
    uf_l = build_unit_forcing(s_low, 51.2, -115.7, 2000, low, ForcingConfig())
    uf_h = build_unit_forcing(s, 51.2, -115.7, 2000, high, ForcingConfig())
    assert uf_l.components["liquid_fraction"].mean() > uf_h.components["liquid_fraction"].mean()
    res = {}
    for name, u, uf in (("low", low, uf_l), ("high", high, uf_h)):
        rd = tmp_path / name
        out = prepare_and_run(engine, sp.EngineSettings(), rd, u, uf.smet, s.index[-1].to_pydatetime(),
                              snowfree_start=(s.index[0] + pd.Timedelta(hours=6)).to_pydatetime())
        met = sp.parse_met(out.met)
        _, profs = sp.parse_pro(out.pro)
        _, diag, _, _ = convert_profile(profs[-1], 0.0, name)
        res[name] = (met, diag, mass_budget(met, 0.0, 0.0, diag.swe_kg_m2_per_slope_area))
        shutil.rmtree(rd)
    assert res["low"][2]["rain"] > res["high"][2]["rain"]
    assert res["low"][2]["snowfall"] < res["high"][2]["snowfall"]
    assert res["low"][1].swe_kg_m2_per_slope_area < res["high"][1].swe_kg_m2_per_slope_area
    for _, _, b in res.values():
        assert abs(b["residual"]) <= b["tolerance"], b
    assert math.isfinite(res["low"][1].hs_vertical_m)


def test_numerical_abort_is_retried_once_and_recorded(engine, tmp_path):
    """Deterministic case (clear-sky ILWR during heavy mixed-phase precipitation at 2300 m) that makes
    SNOWPACK abort at a 15-min step. The adapter must retry once at 5 min and record it; with retries
    disabled the failure must surface as an explicit error (never a fabricated profile)."""
    from snowagent.errors import EngineRunFailed
    from tests.unit.test_forcing import unit

    s = _src(days=4, ta_c=2.5, psum_hours=36)
    u = unit(slope=0, aspect=0, svf=1.0, elev=2300, uid="edge")
    uf = build_unit_forcing(s, 51.2, -115.7, 2000, u, ForcingConfig())
    start = (s.index[0] + pd.Timedelta(hours=6)).to_pydatetime()
    with pytest.raises(EngineRunFailed):
        prepare_and_run(engine, sp.EngineSettings(), tmp_path / "noretry", u, uf.smet, s.index[-1].to_pydatetime(),
                        snowfree_start=start, allow_retry=False)
    out = prepare_and_run(engine, sp.EngineSettings(), tmp_path / "retry", u, uf.smet, s.index[-1].to_pydatetime(),
                          snowfree_start=start)
    assert out.extra["calculation_step_min"] == "5" and "numerical_retry" in out.extra
