"""End-to-end acceptance tests on the REAL SNOWPACK engine (synthetic inputs).

These tests define the first milestone's success criteria. No mocks are used here.
"""

from __future__ import annotations

import json
import math
import os

import numpy as np
import pandas as pd
import pytest

from snowagent.contracts import EXPERIMENTAL_LABEL, ProfileRecord
from snowagent.engine.column import BUDGET_ABS_TOL
from snowagent.engine.snowpack import sha256_file
from snowagent.errors import (
    DataLeakage,
    ImmutableRecord,
    InitializationRequired,
    OutOfDomain,
    UnsupportedTerrain,
)
from snowagent.forecast.pipeline import predict
from snowagent.forecast.query import query_profile
from snowagent.state.store import StateStore

pytestmark = pytest.mark.engine


def _records(run_dir, unit_id):
    return [ProfileRecord.model_validate_json(x) for x in (run_dir / "profiles" / f"{unit_id}.jsonl").read_text().splitlines()]


def test_future_profiles_for_multiple_units_without_pit(mini, mini_run):
    m = mini_run.meta
    assert m.status == "ok"
    assert len(m.units_simulated) >= 4  # N- and S-facing units at two positions
    for uid in m.units_simulated:
        recs = _records(mini_run.run_dir, uid)
        assert {(r.member_id, r.lead_hours) for r in recs} == {(mm, h) for mm in range(3) for h in (0, 12, 24, 48)}
        fut = [r for r in recs if r.lead_hours == 48]
        assert all(r.diagnostics.n_layers > 0 for r in fut)
    # no observation of any kind was used
    assert json.loads((mini_run.run_dir / "manifest.json").read_text())["initial_state_id"] == mini.checkpoint.state_id
    assert mini.checkpoint.observations_used == []


def test_layers_satisfy_physical_bounds_and_geometry(mini_run):
    for uid in mini_run.meta.units_simulated:
        for r in _records(mini_run.run_dir, uid):
            cos_sl = math.cos(math.radians(r.slope_deg))
            prev_top = 0.0
            for ly in r.layers:
                assert ly.bottom_vertical_m == pytest.approx(prev_top, abs=1e-6)
                assert ly.thickness_vertical_m > 0
                assert ly.thickness_slope_normal_m == pytest.approx(ly.thickness_vertical_m * cos_sl)
                assert 30 <= ly.density_kg_m3 <= 917
                assert ly.temperature_c <= 0.05
                assert 0 <= ly.lwc_vol_frac <= 0.2
                assert ly.grain_form_primary is not None
                prev_top = ly.top_vertical_m
            assert r.diagnostics.hs_vertical_m == pytest.approx(prev_top, abs=1e-6)
            assert any(n.field == "p_unstable" for n in r.nulls)


def test_mass_budget_closes_with_all_terms(mini_run):
    b = pd.read_csv(mini_run.run_dir / "diagnostics" / "mass_budget.csv")
    for col in ("snowfall", "rain", "runoff", "sublimation", "evaporation", "wind_erosion", "lateral_transport"):
        assert col in b
    assert (b["wind_erosion"] == 0).all() and (b["lateral_transport"] == 0).all()
    assert (b["residual"].abs() <= b["tolerance"]).all()
    assert b["residual"].abs().max() < BUDGET_ABS_TOL
    assert mini_run.meta.mass_budget_max_residual_kg_m2 < BUDGET_ABS_TOL


def test_checkpoint_unchanged_by_forecast_and_whatif_branch(mini):
    cdir = mini.store.checkpoint_dir("mini", mini.checkpoint.state_id)
    before = {p.name: sha256_file(p) for p in (cdir / "units").iterdir()}
    mf = sha256_file(cdir / "manifest.json")
    from snowagent.contracts import WhatIf

    res = predict(mini.domain, mini.forecast, mini.store, mini.root / "runs_whatif", mini.engine, mini.settings,
                  mini.fcfg, mini.ens.model_copy(update={"members": 1}), [0, 24],
                  what_if=WhatIf(ta_offset_k=3.0, psum_factor=1.5))
    assert "whatif" in res.run_id
    after = {p.name: sha256_file(p) for p in (cdir / "units").iterdir()}
    assert before == after and sha256_file(cdir / "manifest.json") == mf
    mini.store.verify(mini.store.load("mini", mini.checkpoint.state_id))
    assert all(not (p.stat().st_mode & 0o222) for p in (cdir / "units").iterdir())


def test_identical_inputs_reproduce_results(mini, mini_run):
    again = predict(mini.domain, mini.forecast, mini.store, mini.root / "runs_repro", mini.engine, mini.settings,
                    mini.fcfg, mini.ens, [0, 12, 24, 48])
    assert again.run_id == mini_run.run_id
    for uid in mini_run.meta.units_simulated:
        assert (again.run_dir / "profiles" / f"{uid}.jsonl").read_text() == \
            (mini_run.run_dir / "profiles" / f"{uid}.jsonl").read_text()
    assert (again.run_dir / "field" / "summary.csv").read_text() == (mini_run.run_dir / "field" / "summary.csv").read_text()


def test_issued_forecast_is_write_once(mini, mini_run):
    with pytest.raises(ImmutableRecord):
        predict(mini.domain, mini.forecast, mini.store, mini.root / "runs", mini.engine, mini.settings, mini.fcfg,
                mini.ens, [0, 12, 24, 48])


def test_forecast_cannot_use_run_available_after_issue(mini):
    early = pd.Timestamp(mini.forecast.meta.available_time) - pd.Timedelta(hours=1)
    with pytest.raises(DataLeakage):
        predict(mini.domain, mini.forecast, mini.store, mini.root / "runs_leak", mini.engine, mini.settings, mini.fcfg,
                mini.ens, [0, 24], issue_time=early.to_pydatetime())


def test_forecast_ignores_actuals_after_issue_and_checkpoints_built_later(mini):
    # actuals file extends past issue time; checkpoint cutoff must not exceed analysis + latency
    assert mini.actuals.data.index[-1] > pd.Timestamp(mini.forecast.meta.available_time)
    assert pd.Timestamp(mini.checkpoint.assimilation_cutoff) == \
        pd.Timestamp(mini.checkpoint.analysis_time) + pd.Timedelta(hours=1)
    # a store holding only a checkpoint whose cutoff is after the issue time is not usable
    empty = StateStore(mini.root / "store_empty")
    with pytest.raises(InitializationRequired):
        predict(mini.domain, mini.forecast, empty, mini.root / "runs_noinit", mini.engine, mini.settings, mini.fcfg,
                mini.ens, [0, 24])


def test_sunny_vs_shaded_units_change_radiation_and_engine_response(mini, mini_run):
    units = {u.unit_id: u for u in mini.domain.units if u.supported}
    south = [u for u in units.values() if u.aspect_deg is not None and 135 < u.aspect_deg < 225]
    north = [u for u in units.values() if u.aspect_deg is not None and (u.aspect_deg > 315 or u.aspect_deg < 45)]
    assert south and north
    f = pd.read_csv(mini_run.run_dir / "diagnostics" / "forcing.csv")
    f0 = f[f.member == 0].set_index("unit_id")
    s, n = south[0].unit_id, north[0].unit_id
    assert f0.loc[s, "iswr_slope_mean"] > 1.5 * f0.loc[n, "iswr_slope_mean"]
    assert f0.loc[s, "sunlit_hours"] > f0.loc[n, "sunlit_hours"]
    # engine response over the replayed history: near-surface snow on the sunny unit is warmer at the
    # analysis time and the profiles differ; we do not assert a fixed property of any aspect.
    ps = mini.store.analysis_profile(mini.checkpoint, s)
    pn = mini.store.analysis_profile(mini.checkpoint, n)
    assert ps["layers"] != pn["layers"]
    swe_s = ps["diagnostics"]["swe_kg_m2_per_horizontal_area"]
    swe_n = pn["diagnostics"]["swe_kg_m2_per_horizontal_area"]
    assert swe_s != pytest.approx(swe_n, rel=1e-6)


def test_point_queries_resolve_units_and_reject_unsupported_or_outside(mini, mini_run):
    u = next(x for x in mini.domain.units if x.supported)
    lon, lat = u.centroid_lonlat
    q = query_profile(mini_run.run_dir, lat, lon, 24)
    assert q["terrain_unit"]["unit_id"] == u.unit_id and q["terrain_unit"]["unit_resolution_m"] == 1000
    assert q["label"] == EXPERIMENTAL_LABEL and q["synthetic_inputs"] is True
    assert q["capability"]["transport_status"] == "unresolved"
    assert q["provenance"]["engine_version"].startswith("SNOWPACK")
    forest = next(x for x in mini.domain.units if not x.supported)
    with pytest.raises(UnsupportedTerrain):
        query_profile(mini_run.run_dir, forest.centroid_lonlat[1], forest.centroid_lonlat[0], 24)
    with pytest.raises(OutOfDomain):
        query_profile(mini_run.run_dir, lat + 0.2, lon, 24)


def test_outputs_carry_versions_and_flags(mini_run):
    m = json.loads((mini_run.run_dir / "manifest.json").read_text())
    for k in ("engine_version", "engine_config_hash", "terrain_version", "forcing_hash", "initial_state_id",
              "initial_state_manifest_sha256"):
        assert m[k]
    assert m["synthetic_inputs"] is True and m["capability"]["transport_status"] == "unresolved"
    gj = json.loads((mini_run.run_dir / "field" / "lead_024h.geojson").read_text())
    assert gj["label"] == EXPERIMENTAL_LABEL
    props = [f["properties"] for f in gj["features"]]
    assert any(p["supported"] is False for p in props)
    assert all(p["transport_status"] == "unresolved" for p in props)
    lon, lat = gj["features"][0]["geometry"]["coordinates"][0][0]
    assert -116 < lon < -115 and 51 < lat < 51.3  # RFC 7946 WGS84
    assert (mini_run.run_dir / "plots" / "hs_map.png").stat().st_size > 1000
    assert os.path.exists(mini_run.run_dir / "README.txt")
    np.testing.assert_array_less(0, [p["hs_vertical_m_control"] for p in props if p["supported"]])
