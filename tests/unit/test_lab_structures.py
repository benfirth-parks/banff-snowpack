"""More genome structures on the column model: process switches, layer life stories and weather recipes (ADR-120)
and the site scorecard (ADR-122). Each predicts every synthetic case in the contract from the visible case only, is opt-in in training, and
leaves the column model itself unchanged."""

from __future__ import annotations

import json

import numpy as np
import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.agents import ProcessAgent, RecipeAgent, ScorecardAgent, StoryAgent, make_agent  # noqa: E402
from snowagent.lab.agents import column as colmod  # noqa: E402
from snowagent.lab.agents import process as procmod  # noqa: E402
from snowagent.lab.agents import recipe as recmod  # noqa: E402
from snowagent.lab.agents import scorecard as scmod  # noqa: E402
from snowagent.lab.agents import story as stomod  # noqa: E402
from snowagent.lab.agents.common import block_defaults, stamp_prediction  # noqa: E402
from snowagent.lab.benchmark import builder  # noqa: E402
from snowagent.lab.benchmark.loader import case_dirs, load_visible_case, read_manifest  # noqa: E402
from snowagent.lab.genome import default_genome, default_genomes, make_genome, mutate  # noqa: E402
from snowagent.lab.schemas.genome import STANDARD_FAMILIES, AgentFamily  # noqa: E402
from snowagent.lab.services.training import PRESETS  # noqa: E402
from snowagent.lab.settings import load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from snowagent.lab.training.loop import fill_initial, slot_families  # noqa: E402
from tests.unit.lab_fixtures import write_synthetic_lab  # noqa: E402
from tests.unit.test_lab_benchmark import CONFIG  # noqa: E402

NEW = {AgentFamily.process: ProcessAgent, AgentFamily.process_melt: ProcessAgent, AgentFamily.story: StoryAgent,
       AgentFamily.recipe: RecipeAgent,
       AgentFamily.scorecard: ScorecardAgent}
SWITCHES = [f"proc_sw_{k}" for k in ("kinetics", "crust", "wet", "surface", "hoar", "wind", "ground", "round")]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("structures")
    cfg = load_lab_config(CONFIG)
    paths = LabPaths(tmp / "lab")
    write_synthetic_lab(paths.root, tmp / "checkout", cfg)
    builder.build_cases(paths, cfg, tmp / "checkout", exclude_flagged=True)
    return {d.name: d for d in case_dirs(paths)}


@pytest.mark.parametrize("family", list(NEW))
def test_each_structure_predicts_every_case_in_the_contract(built, family):
    genome = default_genome(family)
    assert len(genome.genes) <= 72  # size guard, ADR-154
    agent = make_agent(genome)
    assert isinstance(agent, NEW[family])
    ok = 0
    for name, d in built.items():
        case = load_visible_case(d)
        p = agent.predict(case, seed=1)
        assert p.case_id == case.case_key and p.agent_id == agent.agent_id
        if p.status == "ok":
            ok += 1
            hs = p.bulk_state.snow_depth_m
            assert 0 <= hs.p10 <= hs.p50 <= hs.p90
            tops = [ly.top_depth_m.p50 for ly in p.layers]
            assert tops == sorted(tops) and all(ly.bottom_depth_m.p50 <= hs.p50 + 1e-6 for ly in p.layers)
            assert p.model_metadata["family"] == family.value
        stamp_prediction(p, read_manifest(d))
        text = json.dumps(p.model_dump(mode="json"))
        assert "bow_summit_syn" not in text and "2024-" not in text and "2023-" not in text, name
    assert ok


def test_the_structures_are_opt_in_and_get_presets():
    for family in NEW:
        assert family not in STANDARD_FAMILIES
        assert family not in {g.family for g in default_genomes()}
        assert slot_families([default_genome(family)])[-1] == family
        filled = fill_initial([default_genome(family)], 6, 0, 0.3, None)
        assert len({g.genome_hash for g in filled}) == 6 and all(g.family == family for g in filled)
        assert any(p.get("initial") == [family.value] for p in PRESETS.values())
    assert slot_families(None) == list(STANDARD_FAMILIES)


def test_process_with_every_switch_off_is_the_column_model(built):
    off = make_genome(AgentFamily.process, default_genome(AgentFamily.process).genes | {k: "off" for k in SWITCHES})
    column = default_genome(AgentFamily.column)
    for name in ("BOW_20240110T1900Z_NP", "BOW_20240110T1900Z_H72"):
        case = load_visible_case(built[name])
        a, _ = colmod.simulate(case, column.genes)
        ext = procmod.ProcessExt(procmod.genes_of(off))
        b, _ = colmod.simulate(case, procmod.genes_of(off), ext)
        assert not any(ext.on.values())
        assert a.cls == b.cls
        assert np.allclose(a.dz, b.dz) and np.allclose(a.T, b.T)


def test_process_switches_are_genes_that_mutate():
    g = default_genome(AgentFamily.process)
    assert all(g.genes[k] == "on" for k in SWITCHES)
    rng = np.random.default_rng(3)
    seen = set()
    for _ in range(60):
        g2 = mutate(g, 0.5, rng, None)
        seen |= {k for k in SWITCHES if g2.genes[k] == "off"}
    assert len(seen) >= 4  # mutation turns processes off as well as changing strengths
    fixed = block_defaults(*procmod.FIXED_BLOCKS)
    assert not set(fixed) & set(g.genes)  # the column's own settings are not in this genome


def _snow(n=10, T=-5.0, rho=200.0, n_hist=0):
    c = colmod.Column.empty(n_hist)
    for _ in range(n):
        c.append(0.03, 0.03 * rho, T, "RG", "RG", new=False)
    return c


def test_process_crust_switch_speeds_faceting_under_a_crust():
    base = procmod.genes_of(default_genome(AgentFamily.process))
    progress = {}
    for sw in ("on", "off"):
        g = base | {k: "off" for k in SWITCHES} | {"proc_sw_crust": sw}
        c = _snow()
        c.T = np.linspace(-2.0, -6.0, 10)  # about 15 K/m
        c.cls[5] = c.code[5] = "MFcr"
        ext = procmod.ProcessExt(g)
        for _ in range(48):
            ext.metamorphism(g, c, -6.5, -1.5, colmod.Hour(ta=-6.0, vw=1.0, rh=0.7, sw=0.0, lw=200.0))
        progress[sw] = c.fac[4]
    assert progress["on"] > progress["off"] > 0


def test_story_scores_follow_their_weights():
    g = stomod.genes_of(default_genome(AgentFamily.story))
    h = np.zeros((3, stomod.N_HIST))
    h[1, stomod.GRAD] = 144.0  # twice the gradient dose that alone makes facets
    h[2, stomod.GRAD], h[2, stomod.DENSE] = 144.0, 720.0
    s = stomod.facet_score(g, h)
    assert s[0] < 0 <= s[1] and s[2] < s[1]
    assert (stomod.facet_score(g | {"sty_w_grad": 0.0}, h) < 0).all()
    h[1, stomod.BIG] = 500.0
    d = stomod.depth_hoar_score(g, h)
    assert d[0] < 0 <= d[1]


def test_story_history_makes_facets_and_buries_surface_hoar():
    g = stomod.genes_of(default_genome(AgentFamily.story))
    strong, weak = _snow(n_hist=stomod.N_HIST), _snow(n_hist=stomod.N_HIST)
    strong.T = np.linspace(-1.0, -12.0, 10)
    weak.T = np.linspace(-4.0, -5.0, 10)
    es, ew = stomod.StoryExt(), stomod.StoryExt()
    night = colmod.Hour(ta=-12.0, vw=1.0, rh=0.8, sw=0.0, lw=180.0, ts=-16.0)
    for _ in range(400):
        es.metamorphism(g, strong, -13.0, 0.0, night)
        ew.metamorphism(g, weak, -5.0, -4.0, night)
    assert {"FC", "DH"} & set(strong.cls) and not ({"FC", "DH"} & set(weak.cls))
    ext = stomod.StoryExt()
    for _ in range(int(g["sty_sh_hours"]) + 1):
        assert ext.hoar_growth(g, night, -13.0, 0.0002) == 0.0  # no physical hoar element
    n = len(weak)
    ext.before_snowfall(g, weak, night)
    assert len(weak) == n + 1 and weak.cls[-1] == "SH" and ext.hoar_h == 0.0
    ext.before_snowfall(g, weak, night)  # no hoar weather since: nothing more
    assert len(weak) == n + 1


def test_recipe_runs_from_the_pit_with_no_heat_physics(built):
    g = recmod.genes_of(default_genome(AgentFamily.recipe))
    case = load_visible_case(built["BOW_20240110T1900Z_NP"])
    col, notes = recmod.simulate(case, g)
    assert notes["start"] == "pit" and col.hs > 0 and "hoar:" in notes["recipes"]
    assert (col.water == 0).all()  # no water flow
    lo = recmod.simulate(case, g | {"rcp_sh_hours": 2.0})[1]["recipes"]
    hi = recmod.simulate(case, g | {"rcp_sh_hours": 100.0})[1]["recipes"]
    count = lambda s: int(dict(x.split(":") for x in s.split(","))["hoar"])  # noqa: E731
    assert count(lo) >= count(hi)


def test_recipe_element_helpers_keep_mass():
    c = _snow(n=3)
    m0, h0 = c.ice.sum(), c.hs
    recmod._split_top(c, 0.01)
    assert len(c) == 4 and c.dz[-1] == pytest.approx(0.01)
    c.cls[1] = "DF"
    recmod._insert_above(c, 1, 0.005, 0.5)
    assert len(c) == 5 and c.cls[2] == "DF" and c.dz[2] == pytest.approx(0.005)
    assert c.ice.sum() == pytest.approx(m0 + 0.5) and c.hs == pytest.approx(h0 + 0.005)


def test_scorecard_default_is_the_column_model_and_has_no_plot_gene(built):
    genome = default_genome(AgentFamily.scorecard)
    assert not [k for k in genome.genes if any(p in k for p in ("bow", "goat", "simp", "park"))]
    assert all(v == 0.0 for k, v in genome.genes.items() if k.startswith("sc_") and k != "sc_shelter_scale")
    column = default_genome(AgentFamily.column)
    for name in ("BOW_20240110T1900Z_NP", "BOW_20240110T1900Z_H72"):
        case = load_visible_case(built[name])
        a, _ = colmod.simulate(case, column.genes)
        g, sums = scmod.genes_for(genome, "BOW")
        b, _ = colmod.simulate(case, g, scmod.ScorecardExt(g, "BOW", sums))
        assert a.cls == b.cls and np.allclose(a.dz, b.dz) and np.allclose(a.T, b.T)


def test_scorecard_traits_are_dated_and_set_the_dials():
    import yaml

    raw = yaml.safe_load(scmod.SITE_TRAITS.read_text())
    assert raw["final_at"] == "2026-11-19T00:57:45Z"  # the owner's sheet, final before any training
    assert set(raw["plots"]) == {"BOW", "GOAT", "SIMP", "PARK"}
    idx = {p: scmod.site_indices(p) for p in raw["plots"]}
    assert all(0.0 <= v <= 1.0 for d in idx.values() for v in d.values())
    assert idx["BOW"]["ground"] == 1.0 and idx["SIMP"]["ground"] == 0.0
    assert idx["SIMP"]["exposure"] > idx["PARK"]["exposure"] > idx["GOAT"]["exposure"]
    assert scmod.site_indices("BALD") is None
    base = default_genome(AgentFamily.scorecard)
    genome = make_genome(AgentFamily.scorecard, base.genes | {"sc_hoar_ground": 0.7, "sc_wind_exposure": 0.5,
                                                               "sc_shelter_wind": 1.0, "sc_lee_snow": 0.5})
    g_bow, s_bow = scmod.genes_for(genome, "BOW")
    g_simp, s_simp = scmod.genes_for(genome, "SIMP")
    g_none, s_none = scmod.genes_for(genome, "BALD")
    assert g_bow["col_sh_growth_m_h"] > g_none["col_sh_growth_m_h"] > g_simp["col_sh_growth_m_h"]  # damp, cold ground
    assert all(v == 0.0 for v in s_none.values())
    simp, none = scmod.ScorecardExt(g_simp, "SIMP", s_simp), scmod.ScorecardExt(g_none, "BALD", s_none)
    assert none.weather(g_none, 5.0, 1.0, 225.0) == (5.0, 1.0)  # a plot without traits: every dial at 1
    sw_wind, sw_snow = simp.weather(g_simp, 10.0, 1.0, 225.0)  # Simpson is open to the south-west
    nw_wind, nw_snow = simp.weather(g_simp, 10.0, 1.0, 315.0)  # and sheltered from the north-west
    assert sw_wind > nw_wind and sw_snow < nw_snow
    assert simp.shelter(0.0) == pytest.approx(simp.shelter(360.0))
