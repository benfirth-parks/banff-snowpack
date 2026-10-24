"""ADR-100: the snowfall source gene (SNOWPACK driven by the plot's snow depth sensor until the forecast starts)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.engine import snowpack as sp  # noqa: E402
from snowagent.lab.agents.common import AgentUnavailable  # noqa: E402
from snowagent.lab.agents.physics import (  # noqa: E402
    SNOWFALL_BLOCK,
    EnginePhysics,
    PhysicsError,
    engine_physics,
)
from snowagent.lab.agents.snowpack import (  # noqa: E402
    HS_MIN_COVER,
    LabEngineSettings,
    VisiblePackageEngine,
    despike_hs,
    measured_snow_depth,
)
from snowagent.lab.benchmark.loader import load_visible_case  # noqa: E402
from snowagent.lab.genome import NAMED_MODELS, default_genome, named_genome, upgrade_genome  # noqa: E402
from snowagent.lab.schemas.genome import GENOME_SCHEMA, AgentFamily, AgentGenome, default_spec  # noqa: E402
from snowagent.lab.services.names import nickname  # noqa: E402
from snowagent.lab.services.reports import plain_change  # noqa: E402
from tests.unit.test_lab_physics import HAS_ENGINE, built, genes  # noqa: E402, F401

UTC = "UTC"


def test_the_snowfall_gene_is_a_physics_choice_whose_default_is_the_incumbent():
    spec = default_spec()
    assert spec.blocks[SNOWFALL_BLOCK]["sp_snowfall_source"].default == "precipitation"
    for fam in (AgentFamily.snowpack, AgentFamily.hybrid):
        assert SNOWFALL_BLOCK in spec.families[fam]
    assert engine_physics(genes(), "BOW") == EnginePhysics() and EnginePhysics().key == "default"
    p = engine_physics(genes(sp_snowfall_source="snow_depth"), "BOW")
    assert p.snowfall_source == "snow_depth" and not p.is_default and p.ini == ()
    assert p.as_dict()["snowfall_source"] == "snow_depth" and "snowfall_source" not in EnginePhysics().as_dict()
    assert p.key != "default"
    with pytest.raises(PhysicsError):
        EnginePhysics(snowfall_source="radar")


def test_a_lab_genome_5_agent_still_validates_and_upgrades_to_precipitation():
    g6 = default_genome(AgentFamily.snowpack)
    old = AgentGenome.model_validate({"schema_version": "lab-genome-5", "family": "snowpack",
                                      "genes": {k: v for k, v in g6.genes.items() if k != "sp_snowfall_source"}})
    assert engine_physics(old.genes, "GOAT").is_default
    up = upgrade_genome(old)
    assert up.schema_version == GENOME_SCHEMA == "lab-genome-6" and up.genes["sp_snowfall_source"] == "precipitation"


def test_the_named_snow_depth_model_is_standard_snowpack_with_one_change():
    g = named_genome("snowpack-snow-depth")
    base = default_genome(AgentFamily.snowpack)
    assert {k for k in g.genes if g.genes[k] != base.genes[k]} == {"sp_snowfall_source"}
    assert g.label == "snowpack-snow-depth" in NAMED_MODELS
    assert nickname(g.genome_hash, g.label) == "SNOWPACK with snow depth sensors"
    assert "snow depth sensor" in plain_change("sp_snowfall_source", "precipitation", "snow_depth")


def test_driven_settings_switch_the_engine_to_measured_heights_and_default_settings_are_unchanged():
    base = sp.EngineSettings()
    plain = LabEngineSettings.from_base(base).render("S")
    driven = LabEngineSettings.from_base(base, snow_depth_driven=True).render("S")
    assert plain == LabEngineSettings.from_base(base, physics=EnginePhysics()).render("S")
    for key, val in (("ENFORCE_MEASURED_SNOW_HEIGHTS", "TRUE"), ("THRESH_RH", "0"), ("THRESH_DTEMP_AIR_SNOW", "100")):
        assert f"{key} = {val}" in driven and f"{key} = {val}" not in plain


def test_the_forcing_file_carries_hs_only_when_given(tmp_path):
    idx = pd.date_range("2024-01-01", periods=3, freq="h", tz=UTC)
    df = pd.DataFrame({f: 1.0 for f in sp.SMET_FIELDS}, index=idx)
    sp.write_smet_forcing(tmp_path / "a.smet", "S", 51.0, -116.0, 2000.0, df)
    sp.write_smet_forcing(tmp_path / "b.smet", "S", 51.0, -116.0, 2000.0, df.assign(HS=[0.1, np.nan, 0.3]))
    a, b = (tmp_path / "a.smet").read_text(), (tmp_path / "b.smet").read_text()
    assert "fields = timestamp TA RH VW DW ISWR ILWR PSUM PSUM_PH TSG\n" in a
    assert "TSG HS\n" in b and b.splitlines()[-2].endswith(f"{sp.NODATA:.5f}")


def _hours(t0: str, values, step_h: int = 1) -> pd.Series:
    return pd.Series(values, index=pd.date_range(t0, periods=len(values), freq=f"{step_h}h", tz=UTC), dtype=float)


def test_despiking_drops_stuck_and_spiking_readings_and_keeps_real_snowfall():
    hs = _hours("2024-01-01", [0.30, 0.31, 2.21, 2.21, 2.21, 0.33, 0.40, 0.48, 0.47])
    kept = despike_hs(hs)
    assert kept.max() < 0.5 and len(kept) == 6
    assert len(despike_hs(_hours("2024-01-01", [1.2, 1.2]))) == 0  # a season does not start under 1.2 m of snow


def _case(readings: dict[float, float | None], qc: str = "ok"):
    """A stand-in case: observed hours at t_rel_h (hours before as-of) with snow depth readings."""
    hours = [SimpleNamespace(t_rel_h=t, snow_depth_m=v, qc={"snow_depth_m": qc}) for t, v in sorted(readings.items())]
    return SimpleNamespace(weather_observed=hours)


def test_measured_depth_switches_at_00_utc_bridges_gaps_and_needs_enough_readings():
    ref = pd.Timestamp("2024-01-10T19:00", tz=UTC)
    t = np.arange(-24 * 20, 0, 1.0)  # 20 days of hourly readings up to as-of
    readings = {x: 0.01 * (x + 480) / 24 for x in t}  # 1 cm a day from bare ground
    for x in t[100:130]:  # a 30-hour gap
        readings[x] = None
    index = pd.date_range(ref - pd.Timedelta(hours=480), ref + pd.Timedelta(hours=72), freq="h")
    start, end = index[0] + pd.Timedelta(hours=6), index[-1]
    hs, switch = measured_snow_depth(_case(readings), index, ref, start, end)
    assert switch == pd.Timestamp("2024-01-10T00:00", tz=UTC)
    assert hs[index <= switch].notna().all() and hs[index > switch].isna().all()
    step = hs[index <= switch].diff().dropna()
    assert (step >= 0).all() and step.max() < 0.001  # the 30-hour gap is bridged by a straight line, no jump
    # mostly missing: precipitation throughout
    thin = {x: (v if i % 3 == 0 else None) for i, (x, v) in enumerate(readings.items())}
    assert 1 / 3 < HS_MIN_COVER and measured_snow_depth(_case(thin), index, ref, start, end) == (None, None)
    # readings the station flagged are not used
    assert measured_snow_depth(_case(readings, qc="suspect"), index, ref, start, end) == (None, None)


def test_a_snow_depth_run_splits_at_the_switch_and_drives_only_the_segments_before_it(built, monkeypatch, tmp_path):  # noqa: F811
    """The engine steps are stubbed: the split, the settings and the forcing each segment gets are what is tested."""
    case = load_visible_case(built["BOW_20240110T1900Z_H72"])
    calls = []

    def fake_run(self, eng, settings, run_dir, unit, f, t, sno, seg_start):
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "out.sno").write_text("state")
        calls.append({"driven": settings.snow_depth_driven, "hs": "HS" in f, "end": t, "start": seg_start,
                      "enforce": "ENFORCE_MEASURED_SNOW_HEIGHTS = TRUE" in settings.render("S")})
        (run_dir / "input").mkdir(exist_ok=True)
        (run_dir / "input" / f"{sid[0]}.smet").write_text("x")
        return SimpleNamespace(sno=str(run_dir / "out.sno"))

    from snowagent.baseline.run import plot_unit
    from snowagent.engine.column import station_id

    site = case.site
    sid = [station_id(plot_unit(site.plot_id, site.latitude, site.longitude, site.elevation_m))]
    monkeypatch.setattr(VisiblePackageEngine, "engine", lambda self: SimpleNamespace(binary="x", version_string="v"))
    monkeypatch.setattr(VisiblePackageEngine, "_run", fake_run)
    monkeypatch.setattr(VisiblePackageEngine, "_model_hs", staticmethod(lambda out: 50.0))
    monkeypatch.setattr(VisiblePackageEngine, "_update", staticmethod(
        lambda state, o, model_hs, init, rec: (Path(init).write_text("pit"), rec.update(method="layers"))))
    monkeypatch.setattr(VisiblePackageEngine, "_profile", lambda self, out, valid: (1.0, [], 0.0))

    std = VisiblePackageEngine(work_dir=tmp_path).simulate(case, engine_physics(genes(), "BOW"))
    n_std = len(calls)
    assert not any(c["driven"] or c["hs"] or c["enforce"] for c in calls)
    calls.clear()
    res = VisiblePackageEngine(work_dir=tmp_path).simulate(case, engine_physics(genes(sp_snowfall_source="snow_depth"),
                                                                                 "BOW"))
    assert res.notes["snowfall"] == "snow depth sensor" and "snowfall" not in std.notes
    assert len(calls) in (n_std, n_std + 1)  # one more segment unless the switch falls on a pit update
    assert all(c["driven"] == c["hs"] == c["enforce"] for c in calls)
    assert calls[0]["driven"] and not calls[-1]["driven"]
    switch = max(c["end"] for c in calls if c["driven"])
    assert switch.hour == 0 and calls[-1]["start"] >= switch


@pytest.mark.skipif(not HAS_ENGINE, reason="SNOWPACK binary not installed")
def test_real_engine_follows_the_sensor_until_the_switch(built, tmp_path):  # noqa: F811
    case = load_visible_case(built["BOW_20240110T1900Z_H72"])
    eng = VisiblePackageEngine(work_dir=tmp_path, keep=True, steer=False)
    try:
        ref = eng.simulate(case)
        res = eng.simulate(case, engine_physics(genes(sp_snowfall_source="snow_depth"), "BOW"))
    except AgentUnavailable as exc:
        pytest.skip(str(exc))
    assert res.notes["snowfall"] == "snow depth sensor"
    sensor = res.notes["snow_depth_switch"]
    obs = [h.snow_depth_m for h in case.weather_observed if h.snow_depth_m is not None]
    assert abs(sensor["hs_model_cm"] / 100 - obs[-1]) < 0.15  # the model tracked the sensor (2 cm a day here)
    assert res.physics_key != ref.physics_key and res.snow_depth_m != ref.snow_depth_m
