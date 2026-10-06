"""SNOWPACK physics genes (milestone 5, ADR-070): allow-list and validation, io.ini rendering, defaults that reproduce
the incumbent (fake engine, and the real engine when installed), forcing genes, and the engine cache keyed by the
physics genes only."""

from __future__ import annotations

import dataclasses
import re
import shutil
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.engine import snowpack as sp  # noqa: E402
from snowagent.lab.agents import make_agent  # noqa: E402
from snowagent.lab.agents.common import AgentUnavailable  # noqa: E402
from snowagent.lab.agents.physics import (  # noqa: E402
    INCUMBENT_INI,
    INCUMBENT_RAIN_SNOW,
    INI_KEYS,
    PHYSICS_BLOCK,
    EnginePhysics,
    PhysicsError,
    check_spec,
    engine_physics,
    set_ini_key,
)
from snowagent.lab.agents.snowpack import (  # noqa: E402
    FakeEngine,
    FixedEngineResult,
    LabEngineSettings,
    VisiblePackageEngine,
    engine_forcing,
)
from snowagent.lab.benchmark import builder  # noqa: E402
from snowagent.lab.benchmark.loader import case_dirs, load_visible_case  # noqa: E402
from snowagent.lab.competition.runner import CachingBackend  # noqa: E402
from snowagent.lab.genome import (  # noqa: E402
    default_genome,
    load_genome,
    make_genome,
    mutate,
    save_genome,
    upgrade_genome,
)
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome, GenomeSpec, default_spec  # noqa: E402
from snowagent.lab.settings import load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from snowagent.lab.training.cache import DiskEngineCache, TrainingCache  # noqa: E402
from snowagent.spatial_forcing.builder import ForcingConfig  # noqa: E402
from tests.unit.lab_fixtures import write_synthetic_lab  # noqa: E402
from tests.unit.test_lab_benchmark import CONFIG  # noqa: E402

TEMPLATE = Path(sp.DEFAULT_TEMPLATE)
HAS_ENGINE = shutil.which("snowpack") is not None or Path("/opt/snowpack/bin/snowpack").exists()


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("physics")
    cfg = load_lab_config(CONFIG)
    paths = LabPaths(tmp / "lab")
    write_synthetic_lab(paths.root, tmp / "checkout", cfg)
    builder.build_cases(paths, cfg, tmp / "checkout", exclude_flagged=True)
    return {d.name: d for d in case_dirs(paths)}


def genes(**over) -> dict:
    return dict(default_genome(AgentFamily.snowpack).genes) | over


def _template_value(key: str) -> str | None:
    m = re.search(rf"(?m)^{key}\s*=\s*(\S+)", TEMPLATE.read_text())
    return m[1] if m else None


# --------------------------------------------------------------------------------------------- allow-list


def test_physics_block_is_carried_by_snowpack_and_hybrid_only_and_fully_mapped():
    spec = default_spec()
    check_spec(spec)
    for fam, blocks in spec.families.items():
        assert (PHYSICS_BLOCK in blocks) == (fam in (AgentFamily.snowpack, AgentFamily.hybrid)), fam
    block = spec.blocks[PHYSICS_BLOCK]
    sites = {s.lower() for s in load_lab_config(CONFIG).sites}
    assert {g for g in block if g.startswith("sp_precip_mult_")} == {f"sp_precip_mult_{s}" for s in sites}
    assert set(INI_KEYS) <= set(block)
    for fam in (AgentFamily.snowpack, AgentFamily.hybrid):  # the size guard still holds
        g = default_genome(fam)
        assert len(g.genes) <= spec.max_genes and len(g.model_dump_json()) <= spec.max_bytes


def test_an_unmapped_physics_gene_or_engine_key_is_refused():
    spec = default_spec()
    blocks = dict(spec.blocks)
    blocks[PHYSICS_BLOCK] = dict(blocks[PHYSICS_BLOCK]) | {
        "sp_thermal_conductivity": blocks[PHYSICS_BLOCK]["sp_wind_mult"]}
    bad = GenomeSpec(max_genes=spec.max_genes, max_bytes=spec.max_bytes, blocks=blocks, families=spec.families)
    with pytest.raises(PhysicsError, match="sp_thermal_conductivity"):
        check_spec(bad)
    with pytest.raises(PhysicsError, match="allow-list"):
        EnginePhysics(ini=(("SnowpackAdvanced", "THRESH_RAIN", "2.0"),))
    with pytest.raises(PhysicsError, match="allow-list"):
        EnginePhysics(ini=(("Snowpack", "HN_DENSITY", "FIXED"),))  # right key, wrong section


@pytest.mark.parametrize("gene,value", [("sp_hn_density_fixed_kg_m3", 20.0), ("sp_roughness_length_m", 0.5),
                                        ("sp_precip_mult_bow", 3.0), ("sp_viscosity_model", "CALIBRATION"),
                                        ("sp_hn_density_parameterization", "VANKAMPENHOUT"),
                                        ("sp_hoar_thresh_rh", float("nan"))])
def test_out_of_range_or_excluded_values_are_rejected(gene, value):
    with pytest.raises(ValueError, match=gene):
        make_genome(AgentFamily.snowpack, genes(**{gene: value}))
    with pytest.raises(ValueError):
        engine_physics(genes(**{gene: value}), "BOW")


def test_unknown_or_missing_physics_genes_are_rejected():
    with pytest.raises(ValueError, match="unknown genes"):
        make_genome(AgentFamily.snowpack, genes(sp_metamorphism_model="NIED"))
    g = genes()
    g.pop("sp_wind_mult")
    with pytest.raises(ValueError, match="missing genes"):
        make_genome(AgentFamily.snowpack, g)
    with pytest.raises(ValueError, match="unknown genes"):  # other families carry no physics genes
        make_genome(AgentFamily.persistence, dict(default_genome(AgentFamily.persistence).genes) | {"sp_wind_mult": 1.0})


# --------------------------------------------------------------------------------------------- defaults


def test_gene_defaults_are_the_incumbent_settings():
    block = default_spec().blocks[PHYSICS_BLOCK]
    for gene, (_sec, key) in INI_KEYS.items():
        d = block[gene].default
        assert (d if isinstance(d, str) else f"{float(d):.6g}") == INCUMBENT_INI[key], gene
        tv = _template_value(key)
        if tv is not None:  # where the template sets the key, the incumbent value is the template's
            assert tv == INCUMBENT_INI[key] or float(tv) == float(INCUMBENT_INI[key]), key
    mid, width = block["sp_rain_snow_mid_c"].default, block["sp_rain_snow_width_k"].default
    assert (mid - width / 2, mid + width / 2) == pytest.approx(INCUMBENT_RAIN_SNOW)
    assert (ForcingConfig().phase_t_snow_c, ForcingConfig().phase_t_rain_c) == INCUMBENT_RAIN_SNOW


def test_default_genes_render_the_incumbent_ini_and_forcing_exactly():
    base = sp.EngineSettings(prof_days_between=1.0, snow_days_between=3650.0, first_backup=400.0)
    for fam in (AgentFamily.snowpack, AgentFamily.hybrid):
        for site in ("BOW", "GOAT", "SIMP"):
            phys = engine_physics(default_genome(fam).genes, site)
            assert phys.is_default and phys.key == "default" and phys == EnginePhysics()
            assert phys.forcing_config() == ForcingConfig()
            s = LabEngineSettings.from_base(base, 0.0, phys)
            assert s.render("X") == base.render("X") and s.config_hash() == base.config_hash()
    # a genome without the block (a milestone-4 record) is the incumbent too
    assert engine_physics({"hardness_merge_tol": 0.5}, "BOW") == EnginePhysics()


def test_changed_genes_render_verified_keys_in_their_sections():
    base = sp.EngineSettings()
    phys = engine_physics(genes(sp_roughness_length_m=0.004, sp_hoar_thresh_ta_c=0.5,
                                sp_hn_density_parameterization="ZWART"), "BOW")
    text = LabEngineSettings.from_base(base, 0.0, phys).render("X")
    sections = {}
    current = None
    for ln in text.splitlines():
        if ln.strip().startswith("["):
            current = ln.strip()[1:-1]
        elif "=" in ln and not ln.lstrip().startswith((";", "#")):
            sections.setdefault(current, {})[ln.split("=")[0].strip()] = ln.split("=", 1)[1].strip()
    assert sections["Snowpack"]["ROUGHNESS_LENGTH"] == "0.004"  # replaced in place
    assert sections["SnowpackAdvanced"]["HOAR_THRESH_TA"] == "0.5"  # appended to its section
    assert sections["SnowpackAdvanced"]["HN_DENSITY_PARAMETERIZATION"] == "ZWART"
    assert text.count("ROUGHNESS_LENGTH") == 1 and "[Filters]" in text
    # the numerical retry (dataclasses.replace) keeps the physics
    retry = dataclasses.replace(LabEngineSettings.from_base(base, 0.0, phys), calculation_step_min=5.0)
    assert "HOAR_THRESH_TA = 0.5" in retry.render("X")


def test_physics_is_normalised_per_plot():
    a = engine_physics(genes(sp_precip_mult_goat=1.3), "BOW")
    assert a.key == "default"  # Goat's Eye's factor does not change a Bow Summit run
    assert engine_physics(genes(sp_precip_mult_goat=1.3), "GOAT").precip_mult == 1.3
    fixed = engine_physics(genes(sp_hn_density="FIXED", sp_hn_density_parameterization="ZWART"), "BOW")
    assert ("SnowpackAdvanced", "HN_DENSITY_FIXEDVALUE", "100") in fixed.ini
    assert not any(k == "HN_DENSITY_PARAMETERIZATION" for _s, k, _v in fixed.ini)  # unused when FIXED
    assert fixed.key == engine_physics(genes(sp_hn_density="FIXED"), "BOW").key
    para = engine_physics(genes(sp_hn_density_fixed_kg_m3=180.0), "BOW")
    assert para.key == "default"  # the fixed value is unused when PARAMETERIZED
    rs = engine_physics(genes(sp_rain_snow_mid_c=0.5, sp_rain_snow_width_k=1.0), "SIMP")
    assert rs.rain_snow_c == (0.0, 1.0) and rs.forcing_config().phase_t_rain_c == 1.0


def test_set_ini_key_refuses_a_missing_section_or_a_duplicate_key():
    with pytest.raises(PhysicsError, match="no \\[Nowhere\\]"):
        set_ini_key("[A]\nX = 1\n", "Nowhere", "X", "2")
    with pytest.raises(PhysicsError, match="more than once"):
        set_ini_key("[A]\nX = 1\nX = 2\n", "A", "X", "3")


def test_forcing_genes_change_measured_hours_only(built):
    case = load_visible_case(built["BOW_20240110T1900Z_H72"])  # measured hours, then an archived GFS run
    f0, _ = engine_forcing(case, 1.15)
    f1, _ = engine_forcing(case, 1.15 * 1.2, wind_mult=1.5)
    wet = f0["psum"] > 0
    assert set((f1["psum"][wet] / f0["psum"][wet]).round(9).unique()) == {1.2, 1.0}  # measured x1.2, GFS raw
    windy = f0["vw"] > 0
    assert set((f1["vw"][windy] / f0["vw"][windy]).round(9).unique()) == {1.5, 1.0}
    f2, _ = engine_forcing(case, 1.15, wind_mult=1.0)
    assert f2.equals(f0)


def test_mutation_and_crossover_reach_physics_genes_and_stay_valid():
    g = default_genome(AgentFamily.snowpack)
    seen = set()
    for seed in range(50):
        m = mutate(g, 0.3, seed)
        seen |= {k for k in m.genes if k.startswith("sp_") and m.genes[k] != g.genes[k]}
        engine_physics(m.genes, "BOW")  # always renderable
    assert {"sp_hn_density_parameterization", "sp_roughness_length_m", "sp_precip_mult_bow"} <= seen


def test_a_milestone4_genome_still_validates_and_upgrades_to_default_physics(tmp_path):
    old = {"schema_version": "lab-genome-2", "family": "snowpack", "label": "r10-m01-snowpack",
           "genes": {"hardness_merge_tol": 0.40466847549237306, "depth_spread_frac": 0.16179175939708001,
                     "depth_spread_floor_m": 0.1973746885658839, "boundary_spread_m": 0.020352367300036832,
                     "presence_confidence": 0.8486328326126795}}
    g = AgentGenome.model_validate(old)
    assert g.agent_id == "snowpack-793c87b12d"  # the milestone-4 winner's id is unchanged
    f = tmp_path / "w.json"
    f.write_text(g.model_dump_json())
    up = load_genome(f)
    assert up.schema_version == "lab-genome-3" and up.parents == [g.genome_hash]
    assert {k: up.genes[k] for k in old["genes"]} == old["genes"]
    assert engine_physics(up.genes, "BOW").is_default
    assert load_genome(f, upgrade=False) == g and upgrade_genome(up) is up
    save_genome(up, tmp_path / "u.json")
    assert load_genome(tmp_path / "u.json").genome_hash == up.genome_hash


# --------------------------------------------------------------------------------------------- the agents


def test_default_genes_reproduce_the_incumbent_prediction_with_a_fake_engine(built):
    case = load_visible_case(built["BOW_20240110T1900Z_H72"])

    class Milestone4Engine(FakeEngine):  # what milestone 4 called: simulate(case), no physics
        def simulate(self, case, physics=None):
            assert physics is None or physics.is_default
            return super().simulate(case)

    for fam in (AgentFamily.snowpack, AgentFamily.hybrid):
        new = make_agent(default_genome(fam), backend=FakeEngine()).predict(case, 0)
        old = make_agent(default_genome(fam), backend=Milestone4Engine()).predict(case, 0)
        assert new.model_dump() == old.model_dump()
        assert "physics_key" not in new.model_metadata


def test_physics_genes_change_the_engine_profile_and_output_genes_do_not(built):
    case = load_visible_case(built["BOW_20240110T1900Z_H72"])
    eng = FakeEngine()
    backend = CachingBackend(eng)
    g0 = default_genome(AgentFamily.snowpack)
    out_only = make_genome(AgentFamily.snowpack, dict(g0.genes) | {"presence_confidence": 0.9})
    phys = make_genome(AgentFamily.snowpack, dict(g0.genes) | {"sp_precip_mult_bow": 1.4})
    p0 = make_agent(g0, backend=backend).predict(case, 0)
    make_agent(out_only, backend=backend).predict(case, 0)
    assert eng.calls == 1  # the output-only mutant shares the profile
    p2 = make_agent(phys, backend=backend).predict(case, 0)
    assert eng.calls == 2 and p2.model_metadata["physics_key"] != "default"
    assert p2.bulk_state.snow_depth_m.p50 != p0.bulk_state.snow_depth_m.p50
    hyb = make_genome(AgentFamily.hybrid, dict(default_genome(AgentFamily.hybrid).genes) | {"sp_precip_mult_bow": 1.4})
    make_agent(hyb, backend=backend).predict(case, 0)
    assert eng.calls == 2  # the hybrid with the same physics reuses the SNOWPACK agent's profile


def test_a_reused_site_run_serves_the_incumbent_physics_only(built):
    case = load_visible_case(built["BOW_20240110T1900Z_H72"])
    res = FakeEngine().simulate(case)
    phys = engine_physics(genes(sp_precip_mult_bow=1.2), "BOW")
    assert FixedEngineResult(res).simulate(case, EnginePhysics()) is res
    with pytest.raises(AgentUnavailable, match="incumbent's physics"):
        FixedEngineResult(res).simulate(case, phys)
    assert FixedEngineResult(res, FakeEngine()).simulate(case, phys).physics_key == phys.key


def test_engine_cache_is_keyed_by_the_physics_genes_only(built, tmp_path):
    case = load_visible_case(built["BOW_20240110T1900Z_H72"])
    disk = DiskEngineCache(FakeEngine(), TrainingCache(tmp_path / "cache"), "fake", case_hash="ab" * 32)
    k0 = disk.key_for(case)
    assert disk.key_for(case, EnginePhysics()) == k0
    assert disk.key_for(case, engine_physics(genes(presence_confidence=0.95, hardness_merge_tol=1.0), "BOW")) == k0
    assert disk.key_for(case, engine_physics(genes(sp_precip_mult_simp=1.4), "BOW")) == k0
    kp = disk.key_for(case, engine_physics(genes(sp_roughness_length_m=0.004), "BOW"))
    assert kp != k0
    r1 = disk.simulate(case, engine_physics(genes(sp_roughness_length_m=0.004), "BOW"))
    assert disk.hit is False
    r2 = disk.simulate(case, engine_physics(genes(sp_roughness_length_m=0.004, depth_spread_frac=0.3), "BOW"))
    assert disk.hit is True and r2 == r1 and disk.hits == 1 and len(disk.misses) == 1
    pk = engine_physics(genes(sp_roughness_length_m=0.004), "BOW").key
    assert disk.cache.engine_cached_for("ab" * 32, pk) is True
    assert disk.cache.engine_cached_for("ab" * 32) is None  # the incumbent's physics was never run here


# --------------------------------------------------------------------------------------------- the real engine


@pytest.mark.skipif(not HAS_ENGINE, reason="SNOWPACK binary not installed")
def test_real_engine_default_physics_reproduces_the_incumbent_and_a_physics_gene_changes_it(built, tmp_path):
    case = load_visible_case(built["BOW_20240110T1900Z_H72"])
    try:
        ref = VisiblePackageEngine(work_dir=tmp_path).simulate(case)  # the milestone-3/4 call
    except AgentUnavailable as exc:
        pytest.skip(str(exc))
    same = VisiblePackageEngine(work_dir=tmp_path).simulate(
        case, engine_physics(default_genome(AgentFamily.snowpack).genes, "BOW"))
    assert same == ref and same.physics_key == "default"
    fixed = VisiblePackageEngine(work_dir=tmp_path).simulate(
        case, engine_physics(genes(sp_hn_density="FIXED", sp_hn_density_fixed_kg_m3=200.0), "BOW"))
    assert fixed.physics_key != "default" and fixed.config_hash != ref.config_hash
    assert fixed.layers != ref.layers
    rho = [ly.density_kg_m3 for ly in fixed.layers if ly.density_kg_m3]
    assert rho and np.isfinite(rho).all()
