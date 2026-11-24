"""The site's main model (ADR-137): an evolved lab column model run for the site, its configuration, the season as
runs restarted from each pit, the display layers, and the daily build's files."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from snowagent.lab.agents import column as cm
from snowagent.lab.genome import default_genome
from snowagent.lab.schemas.benchmark import VisibleLayer, VisiblePit
from snowagent.ops import main_model as mm

REPO = Path(__file__).resolve().parents[2]


def _model(family: str = "process", name: str = "Carver Daniels") -> mm.MainModel:
    g = json.loads(default_genome(family).model_dump_json())
    return mm.MainModel(name=name, genome=g, chosen_utc="2026-11-22T12:27:32+00:00")


def test_the_configured_main_model_is_a_valid_process_genome():
    m = mm.load_main(REPO / "config" / "site_main_model.json")
    g = m.checked_genome()
    assert m.name == "Carver Daniels" and g.family.value == "process"
    # Carver Daniels (w129-process-seeded r14-x04-process, 2975778ee39e) with old facets kept (ADR-142) and facets
    # by hours (ADR-143) and depth hoar only at the base (ADR-154)
    assert g.genes["col_old_facets"] == "on" and g.genes["proc_sw_duration"] == "on" and g.genes["col_dh_base"] == "on"
    assert g.genome_hash.startswith("dbf18bab4b")
    assert m.source.genome_name == "Carver Daniels"


def test_only_column_model_families_can_be_the_main_model():
    with pytest.raises(ValueError, match="must be one of"):
        _model("snowpack").checked_genome()
    assert _model("column").checked_genome().family.value == "column"
    assert _model("process_melt").checked_genome().family.value == "process_melt"  # the next Carver's kind
    g, _ext, _v = mm.model_parts(_model("process_melt").checked_genome())
    assert "col_albedo_fresh" in g and "proc_sw_kinetics" in g
    assert mm.load_main(Path("/nonexistent.json")) is None


def test_precipitation_takes_the_labs_factors_per_source():
    src = pd.Series(["sunshine_village_ab_env", "era5:2004m", "gfs_day1", "gfs025"])
    f = mm.lab_psum_factors(src, {"psum_factor": 0.9, "era5_psum_factor": 1.3})
    assert f.tolist() == [0.9, 1.3, 1.0, 1.0]


def _wx(hours: int = 96, ta: float = -8.0, psum: float = 0.0) -> cm.Forcing:
    t = np.arange(hours, dtype=float)
    n = len(t)
    return cm.Forcing(t=t, ta=np.full(n, ta), rh=np.full(n, 0.8), vw=np.full(n, 2.0), sw=np.zeros(n),
                      lw=np.full(n, 230.0), psum=np.full(n, psum), measured=np.ones(n, dtype=bool), hs=None,
                      dw=np.zeros(n))


def _pit(t_rel_h: float, hs: float = 1.0) -> VisiblePit:
    layers = [VisibleLayer(top_depth_m=0.0, bottom_depth_m=hs / 2, grain_primary="RG", hardness_index=2.0),
              VisibleLayer(top_depth_m=hs / 2, bottom_depth_m=hs, grain_primary="FC", hardness_index=3.0)]
    return VisiblePit(pit_key="pit_01", t_rel_h=t_rel_h, day_of_year=60, season_offset=0, available_rel_h=t_rel_h,
                      availability_assumption="observed_at", snow_depth_m=hs, layers=layers)


def test_the_season_restarts_from_each_pit_and_nothing_before_it_sees_the_pit():
    times = pd.date_range("2027-01-01", periods=96, freq="h", tz="UTC")
    wx = _wx(psum=0.5)  # snowing: the column grows from bare ground until the pit
    m = _model()
    g, new_ext, _v = mm.model_parts(m.checked_genome())
    rec = {"profile_id": "P1", "obs_time_utc": "2027-01-03T00:00:00+00:00"}
    nowcast, hs, used = mm.step_season(wx, times, [(rec, _pit(48.0, hs=1.5))], g, new_ext, "GOAT",
                                       lambda ts: ts.hour % 6 == 0)
    assert used == [{"id": "P1", "t": "2027-01-03T00:00"}]
    by_t = {p["t"]: p for p in nowcast}
    assert by_t["2027-01-02T18"]["hs"] < 100  # before the pit: the snow-free run's own depth
    assert by_t["2027-01-03T00"]["hs"] >= 149  # from the pit's hour: the pit's 1.5 m
    assert hs.iloc[47] < 1.0 and hs.iloc[48] >= 1.49
    grains = {r[2] for r in by_t["2027-01-03T06"]["L"]}
    assert "FC" in grains  # the pit's facets carry on


def test_display_layers_are_heights_above_ground_with_flags():
    m = _model()
    g, new_ext, _v = mm.model_parts(m.checked_genome())
    pit = _pit(0.0, hs=1.0)
    *_, (col, _n, _i) = cm.run_hours(_wx(hours=3), pit, g, new_ext(), "GOAT", 2.0)
    rows = mm.site_rows(col, g, pit)
    assert rows[0][0] == pytest.approx(col.hs * 100, abs=0.2) and rows[-1][1] == 0.0
    assert all(r[0] > r[1] for r in rows) and all(len(r) == 8 for r in rows)
    fc = [r for r in rows if r[2] == "FC"]
    assert fc and all(r[7] & 2 for r in fc) and all(r[5] <= 0.0 for r in rows)


def test_the_daily_build_writes_the_files_and_keeps_a_plots_last_run_on_failure(tmp_path, monkeypatch):
    cfg = tmp_path / "main.json"
    cfg.write_text(_model().model_dump_json())
    calls = {"n": 0}

    def fake(plot, y, model, now=None, spec=None, observed=None):
        calls["n"] += 1
        if plot == "simpson" and calls["n"] > 3:
            raise RuntimeError("no forcing")
        return {"id": model.checked_genome().agent_id, "name": model.name, "nowcast": [{"t": "2026-11-21T12"}]}

    monkeypatch.setattr(mm, "run_main_season", fake)
    out = tmp_path / "data"
    res = mm.build_main(2026, out, pd.Timestamp("2026-11-22T13:00Z"), config=cfg)
    assert set(res["plots"]) == {"bow_summit", "goats_eye", "simpson", "parker_ridge"} and not res["warnings"]
    index = json.loads((out / "main_model.json").read_text())
    assert index["model"]["name"] == "Carver Daniels" and "not an avalanche forecast" in index["label"]
    f = out / "simpson" / "2026-2027_main.json"
    assert json.loads(f.read_text())["main"]["name"] == "Carver Daniels"
    res = mm.build_main(2026, out, pd.Timestamp("2026-11-23T13:00Z"), config=cfg)  # simpson fails now
    assert any("Simpson" in w["message"] and "keeps its last run" in w["message"] for w in res["warnings"])
    assert f.exists() and "simpson" not in res["plots"]
    res = mm.build_main(2026, out, config=tmp_path / "gone.json")  # no main model: SNOWPACK first again
    assert res["model"] is None and not (out / "main_model.json").exists()


def test_the_site_index_lists_the_main_run_and_skips_side_files(tmp_path, monkeypatch):
    from snowagent.web import build

    monkeypatch.chdir(REPO)
    d = tmp_path / "goats_eye"
    d.mkdir()
    (d / "2026-2027.json").write_text(json.dumps({"season": "2026-2027", "mode": "live", "pits": []}))
    (d / "2026-2027_main.json").write_text(json.dumps({"season": "2026-2027", "main": {}}))
    (d / "2026-2027_agents.json").write_text(json.dumps({"season": "2026-2027", "agents": []}))
    idx = build.write_index(tmp_path, pd.Timestamp("2026-11-22T13:00Z"))
    goat = next(s for s in idx["sites"] if s["id"] == "goats_eye")
    assert [s["season"] for s in goat["seasons"]] == ["2026-2027"]
    assert goat["seasons"][0]["main"] == "data/goats_eye/2026-2027_main.json"
