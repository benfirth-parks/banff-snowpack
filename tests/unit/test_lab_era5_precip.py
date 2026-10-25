"""ADR-106: reanalysis (ERA5) precipitation hours take the plot's own factor, not its gauge's."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import yaml

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.agents import snowpack as spa  # noqa: E402
from snowagent.lab.agents.physics import ERA5_BLOCK, EnginePhysics, PhysicsError, engine_physics  # noqa: E402
from snowagent.lab.agents.snowpack import PLOT_FORCING, VisiblePackageEngine, engine_forcing  # noqa: E402
from snowagent.lab.benchmark.loader import load_visible_case  # noqa: E402
from snowagent.lab.genome import NAMED_MODELS, default_genome, named_genome, upgrade_genome  # noqa: E402
from snowagent.lab.schemas.genome import (  # noqa: E402
    GENOME_SCHEMA,
    LEGACY_BLOCKS,
    AgentFamily,
    AgentGenome,
    default_spec,
)
from snowagent.lab.services.names import nickname  # noqa: E402
from snowagent.lab.services.reports import plain_change  # noqa: E402
from tests.unit.test_lab_physics import built, genes  # noqa: E402, F401


def test_the_gene_is_a_physics_choice_whose_default_is_the_incumbent():
    spec = default_spec()
    assert spec.blocks[ERA5_BLOCK]["sp_era5_precip"].default == "gauge_factor"
    for fam in (AgentFamily.snowpack, AgentFamily.hybrid):
        assert ERA5_BLOCK in spec.families[fam]
    assert engine_physics(genes(), "GOAT") == EnginePhysics() and EnginePhysics().key == "default"
    p = engine_physics(genes(sp_era5_precip="era5_factor"), "GOAT")
    assert p.era5_precip == "era5_factor" and not p.is_default and p.key != "default"
    assert p.as_dict()["era5_precip"] == "era5_factor" and "era5_precip" not in EnginePhysics().as_dict()
    with pytest.raises(PhysicsError):
        EnginePhysics(era5_precip="radar")


def test_a_lab_genome_7_agent_upgrades_to_the_gauge_factor_and_runs_as_before():
    now = default_genome(AgentFamily.snowpack)
    later = {g for b in LEGACY_BLOCKS["lab-genome-7"] for g in default_spec().blocks[b]}
    old = AgentGenome.model_validate({"schema_version": "lab-genome-7", "family": "snowpack",
                                      "genes": {k: v for k, v in now.genes.items() if k not in later}})
    up = upgrade_genome(old)
    assert up.schema_version == GENOME_SCHEMA == "lab-genome-8" and up.genes["sp_era5_precip"] == "gauge_factor"
    assert up.parents == [old.genome_hash] and engine_physics(up.genes, "GOAT").is_default


def test_the_snowfall_fix_model_is_the_new_snow_check_with_one_change():
    fix, nsc = named_genome("snowpack-era5-fix"), named_genome("snowpack-new-snow-check")
    assert list(NAMED_MODELS)[-1] == "snowpack-era5-fix"
    assert {k for k in fix.genes if fix.genes[k] != nsc.genes[k]} == {"sp_era5_precip"}
    assert fix.genes["sp_era5_precip"] == "era5_factor"
    assert nickname(fix.genome_hash, fix.label) == "SNOWPACK with sensors, new-snow check and snowfall fix"
    assert "own factor" in plain_change("sp_era5_precip", "gauge_factor", "era5_factor")


def test_only_goats_eye_gets_another_factor_for_reanalysis_hours():
    plots = yaml.safe_load(PLOT_FORCING.read_text())["plots"]
    assert plots["goats_eye"]["psum_factor"] == 0.9 and plots["goats_eye"]["era5_psum_factor"] == 1.3
    for p in ("bow_summit", "simpson"):  # unchanged there (ADR-106)
        assert plots[p]["era5_psum_factor"] == plots[p]["psum_factor"]


def _mixed_sources(case):
    """The case with 1 mm in every observed hour, its precipitation from ERA5 in every other hour."""
    hours = [h.model_copy(update={"precipitation_mm": 1.0, "sources": h.sources | {
        "precipitation_mm": "era5_cell_2004m" if i % 2 else "sunshine_village_ab_env"}})
             for i, h in enumerate(case.weather_observed)]
    return case.model_copy(update={"weather_observed": hours})


def test_reanalysis_hours_take_their_own_factor_and_gauge_hours_keep_theirs(built):  # noqa: F811
    case = _mixed_sources(load_visible_case(built["BOW_20240110T1900Z_H72"]))
    base, _ = engine_forcing(case, 0.9)
    fixed, _ = engine_forcing(case, 0.9, era5_psum_factor=1.3)
    ref = spa.reference_time(case.as_of_day_of_year)
    obs = {(pd.Timestamp(ref) + pd.Timedelta(hours=float(h.t_rel_h))).round("h"): h.sources["precipitation_mm"]
           for h in case.weather_observed}
    era5 = [t for t, s in obs.items() if s.startswith("era5") and t in fixed.index]
    gauge = [t for t, s in obs.items() if not s.startswith("era5") and t in fixed.index]
    assert era5 and gauge
    assert np.allclose(base.loc[era5 + gauge, "psum"], 0.9)
    assert np.allclose(fixed.loc[era5, "psum"], 1.3) and np.allclose(fixed.loc[gauge, "psum"], 0.9)
    other = [c for c in base.columns if c != "psum"]
    assert base[other].equals(fixed[other])


def test_the_run_passes_the_plots_reanalysis_factor_only_with_the_gene(built, monkeypatch):  # noqa: F811
    case = load_visible_case(built["BOW_20240110T1900Z_H72"])
    seen = []

    class Stop(Exception):
        pass

    def fake_forcing(case, psum_factor, **kw):
        seen.append((psum_factor, kw.get("era5_psum_factor")))
        raise Stop

    monkeypatch.setattr(spa, "engine_forcing", fake_forcing)
    monkeypatch.setattr(spa, "_plots", lambda path: {case.site.plot_id: {"psum_factor": 0.9, "era5_psum_factor": 1.3}})
    monkeypatch.setattr(VisiblePackageEngine, "engine", lambda self: SimpleNamespace(binary="x", version_string="v"))
    for g in (genes(), genes(sp_era5_precip="era5_factor"), genes(sp_era5_precip="era5_factor", sp_precip_mult_bow=1.1)):
        with pytest.raises(Stop):
            VisiblePackageEngine().simulate(case, engine_physics(g, "BOW"))
    assert seen[0] == (0.9, None) and seen[1] == (0.9, 1.3)
    assert seen[2][0] == pytest.approx(0.99) and seen[2][1] == pytest.approx(1.43)  # the plot gene multiplies both
