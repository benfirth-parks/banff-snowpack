"""The column model (ADR-118): the lab's own snowpack model, no SNOWPACK. It predicts every synthetic case in the
contract from the visible case only, starts from the latest pit, conserves mass, makes crusts from melt and rain,
facets under a strong gradient, and is opt-in in training (default populations and family slots unchanged)."""

from __future__ import annotations

import json

import numpy as np
import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.agents import ColumnAgent, make_agent  # noqa: E402
from snowagent.lab.agents import column as colmod  # noqa: E402
from snowagent.lab.agents.common import latest_season_pit, pit_snow_depth, stamp_prediction  # noqa: E402
from snowagent.lab.benchmark import builder  # noqa: E402
from snowagent.lab.benchmark.loader import case_dirs, load_visible_case, read_manifest  # noqa: E402
from snowagent.lab.genome import default_genome, default_genomes  # noqa: E402
from snowagent.lab.schemas.genome import STANDARD_FAMILIES, AgentFamily  # noqa: E402
from snowagent.lab.settings import load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from snowagent.lab.training.loop import fill_initial, slot_families  # noqa: E402
from tests.unit.lab_fixtures import write_synthetic_lab  # noqa: E402
from tests.unit.test_lab_benchmark import CONFIG  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("column")
    cfg = load_lab_config(CONFIG)
    paths = LabPaths(tmp / "lab")
    write_synthetic_lab(paths.root, tmp / "checkout", cfg)
    builder.build_cases(paths, cfg, tmp / "checkout", exclude_flagged=True)
    return {d.name: d for d in case_dirs(paths)}


def _genes(**kw):
    g = default_genome(AgentFamily.column)
    return g.model_copy(update={"genes": g.genes | kw})


def test_column_predicts_every_case_in_the_contract(built):
    agent = make_agent(default_genome(AgentFamily.column))
    assert isinstance(agent, ColumnAgent)
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
            assert p.model_metadata["family"] == "column"
        stamp_prediction(p, read_manifest(d))
        text = json.dumps(p.model_dump(mode="json"))
        assert "bow_summit_syn" not in text and "2024-" not in text and "2023-" not in text, name
    assert ok


def test_column_starts_from_the_latest_pit(built):
    case = load_visible_case(built["BOW_20240110T1900Z_NP"])
    pit = latest_season_pit(case)
    col, notes = colmod.simulate(case, default_genome(AgentFamily.column).genes)
    assert notes["start"] == "pit"
    pit_codes = {ly.grain_primary for ly in pit.layers}
    assert pit_codes & set(col.code)  # pit layers carried with their grain forms
    assert col.hs > 0.5 * pit_snow_depth(pit)


def test_more_precipitation_means_deeper_snow_without_a_sensor(built):
    case = load_visible_case(built["BOW_20240110T1900Z_H72"])
    lo = colmod.simulate(case, _genes(col_precip_mult_bow=0.6, col_sensor_add_m=0.08).genes)[0].hs
    hi = colmod.simulate(case, _genes(col_precip_mult_bow=1.6, col_sensor_add_m=0.08).genes)[0].hs
    assert hi >= lo


def _snow(n=20, T=-5.0, rho=200.0):
    c = colmod.Column.empty()
    for _ in range(n):
        c.append(0.03, 0.03 * rho, T, "RG", "RG", new=False)
    return c


def test_rain_on_cold_snow_refreezes_into_a_crust_and_keeps_mass():
    g = default_genome(AgentFamily.column).genes
    c = _snow()
    m0 = c.ice.sum() + c.water.sum()
    colmod._percolate(g, c, 6.0)  # 6 mm of rain on -5 degC snow
    assert c.ice.sum() + c.water.sum() == pytest.approx(m0 + 6.0)
    assert c.frz[-1] > 0
    for _ in range(48):  # the held water refreezes as the snow cools again
        colmod._conduct(g, c, -8.0, 0.0)
        colmod._phase(c)
    assert c.water[-1] == pytest.approx(0.0, abs=1e-6)
    colmod._metamorphism(g, c, -8.0, 0.0)
    assert c.cls[-1] == "MFcr"


def test_strong_gradient_makes_facets_weak_gradient_does_not():
    g = default_genome(AgentFamily.column).genes
    strong = _snow(n=10)
    strong.T = np.linspace(-1.0, -12.0, 10)  # 0.3 m, about 37 K/m
    weak = _snow(n=10)
    weak.T = np.linspace(-4.0, -5.0, 10)
    for _ in range(400):
        colmod._metamorphism(g, strong, -13.0, 0.0)
        colmod._metamorphism(g, weak, -5.0, -4.0)
    assert "FC" in strong.cls or "DH" in strong.cls
    assert not ({"FC", "DH"} & set(weak.cls))


def test_conduction_relaxes_towards_the_boundaries():
    g = default_genome(AgentFamily.column).genes
    c = _snow(n=10, T=-10.0)
    for _ in range(24 * 20):
        colmod._conduct(g, c, -10.0, 0.0)
    assert c.T[0] > c.T[-1] and -10.0 <= c.T.min() and c.T.max() <= 0.0


def test_column_is_opt_in_for_training():
    assert AgentFamily.column not in STANDARD_FAMILIES
    assert AgentFamily.column not in {g.family for g in default_genomes()}
    assert slot_families(None) == list(STANDARD_FAMILIES)
    assert slot_families([default_genome(AgentFamily.column)])[-1] == AgentFamily.column
    filled = fill_initial([default_genome(AgentFamily.column)], 6, 0, 0.3, None)
    assert len(filled) == 6 and len({g.genome_hash for g in filled}) == 6
    assert all(g.family == AgentFamily.column for g in filled)
    assert fill_initial([default_genome(AgentFamily.column)], 6, 0, 0.3, None) == filled  # seeded


def test_a_run_without_snowpack_agents_shows_standard_snowpack_on_the_locked_winters():
    from snowagent.lab.genome import named_genome
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.training.loop import LOCKED_REFERENCE, TrainOptions, _extensions, locked_best

    cfg = load_lab_config(CONFIG)
    col = TrainOptions.from_config(cfg, initial=[default_genome(AgentFamily.column)])
    assert _extensions(col, [], (["2025-2026"], ["c1"]))["locked_reference"] == LOCKED_REFERENCE
    named_genome(LOCKED_REFERENCE)  # a standard model the lab knows
    std = TrainOptions.from_config(cfg)
    assert "locked_reference" not in _extensions(std, [], (["2025-2026"], ["c1"]))
    assert "locked_reference" not in _extensions(col, [], ([], []))
    info = {"locked_test": {"agents": [{"composite": 0.5}, {"composite": 0.6, "reference": True}]}}
    assert locked_best(info) == 0.5  # the bar does not count as the run's own best


# --- ADR-128: the old-facets switch -------------------------------------------------------------------------------


def _pit_facets(n=10, T=-3.0, rho=250.0):
    c = colmod.Column.empty()
    for _ in range(n):
        c.append(0.03, 0.03 * rho, T, "FC", "FC", hard0=2.0, rho0=rho, fac=1.0, new=False)
    return c


def _run_weak_gradient(g, c, hours=2000):
    c.T = np.full(len(c), -0.5)
    for _ in range(hours):  # near 0 degC and no gradient: facets round back unless the switch keeps them
        colmod._metamorphism(g, c, -0.5, -0.5)


def test_old_facets_switch_keeps_the_pits_facets_from_rounding():
    off = default_genome(AgentFamily.column).genes
    assert off["col_old_facets"] == "off"
    on = off | {"col_old_facets": "on"}
    a, b = _pit_facets(), _pit_facets()
    _run_weak_gradient(off, a)
    _run_weak_gradient(on, b)
    assert "RG" in a.cls  # as before: the pit's facets round back
    assert set(b.cls) == {"FC"}  # switch on: they stay facets
    new = _pit_facets()
    new.new[:] = True  # facets the model made itself still round with the switch on
    _run_weak_gradient(on, new)
    assert "RG" in new.cls


def test_old_facets_switch_keeps_the_observers_hardness(built):
    off = default_genome(AgentFamily.column).genes
    on = off | {"col_old_facets": "on"}
    c = _pit_facets()
    c.dz = c.dz * 0.7  # the layers settled: denser than when observed
    case = load_visible_case(next(iter(built.values())))
    h_off = [ly.hardness_index for ly in colmod.column_layers(c, off, case) if ly.grain == "FC"]
    h_on = [ly.hardness_index for ly in colmod.column_layers(c, on, case) if ly.grain == "FC"]
    assert h_on and all(h == pytest.approx(2.0) for h in h_on)
    assert all(h > 2.0 for h in h_off)  # without it settling hardens them


def test_switch_off_keeps_every_existing_column_id_and_on_is_a_new_agent():
    from snowagent.lab.genome import make_genome

    g = default_genome(AgentFamily.column)
    old_file = {k: v for k, v in g.genes.items() if k != "col_old_facets"}  # a genome saved before ADR-128
    legacy = make_genome(AgentFamily.column, old_file)
    assert legacy.genes["col_old_facets"] == "off" and legacy.genome_hash == g.genome_hash
    assert g.agent_id == "column-b042288198"  # the id the column default has had since ADR-118
    on = make_genome(AgentFamily.column, dict(g.genes) | {"col_old_facets": "on"})
    assert on.genome_hash != g.genome_hash


def test_fixed_switch_is_never_mutated_crossed_screened_or_counted_as_drift():
    from snowagent.lab.genome import _spec, crossover, make_genome, mutate
    from snowagent.lab.training.loop import drift
    from snowagent.lab.training.sensitivity import probe_genomes

    base = default_genome(AgentFamily.column)
    on = make_genome(AgentFamily.column, dict(base.genes) | {"col_old_facets": "on"})
    for seed in range(30):
        assert mutate(on, 1.0, seed).genes["col_old_facets"] == "on"
        assert mutate(base, 1.0, seed).genes["col_old_facets"] == "off"
        assert crossover(on, base, seed).genes["col_old_facets"] == "on"
    # the switch draws no random numbers: mutants and crossovers are the ones made before it existed (hashes
    # computed with the code of main before ADR-128), so a seeded run without the switch repeats exactly
    m = mutate(base, 0.3, 7)
    m2 = mutate(m, 0.5, 3)
    assert (m.genome_hash[:10], m2.genome_hash[:10], crossover(m, m2, 11).genome_hash[:10]) == (
        "d1f1ea4829", "ab96aaf4a2", "2be2f2af37")
    assert drift(on) == pytest.approx(0.0)
    _, probes = probe_genomes(AgentFamily.column, _spec(None))
    assert all(gene != "col_old_facets" for gene, _v, _g in probes)
