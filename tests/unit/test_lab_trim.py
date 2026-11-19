"""Trimmed genomes (ADR-123): a sensitivity screen on training winters ranks a family's genes, the configuration
holds each family's short list, and a trimmed training run changes only those genes. Untrimmed runs are unchanged."""

from __future__ import annotations

import json
import shutil

import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.benchmark import builder  # noqa: E402
from snowagent.lab.competition.runner import EngineSpec  # noqa: E402
from snowagent.lab.genome import default_genome, mutate  # noqa: E402
from snowagent.lab.schemas.genome import AgentFamily, GeneSpec, default_spec  # noqa: E402
from snowagent.lab.services.training import PRESETS, training_command  # noqa: E402
from snowagent.lab.settings import load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from snowagent.lab.training import sensitivity  # noqa: E402
from snowagent.lab.training.loop import (  # noqa: E402
    TrainOptions,
    _opts_from_plan,
    load_round,
    run_training,
    trimmed_lists,
)
from tests.unit.lab_fixtures import write_multiseason_lab  # noqa: E402
from tests.unit.test_lab_benchmark import CONFIG  # noqa: E402

SEASONS = ("2021-2022", "2022-2023", "2023-2024")
STRUCTURES = ("column", "process", "recipe", "story", "scorecard")
SHORT = ["col_facet_hours", "col_sh_growth_m_h"]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("trim")
    cfg = load_lab_config(CONFIG)
    paths = LabPaths(root / "lab")
    write_multiseason_lab(paths.root, root / "checkout", cfg, SEASONS)
    builder.build_cases(paths, cfg, root / "checkout", exclude_flagged=True)
    return cfg, root


@pytest.fixture()
def lab(built, tmp_path):
    cfg, root = built
    shutil.copytree(root / "lab", tmp_path / "lab")
    return cfg, LabPaths(tmp_path / "lab")


def _changed(a: dict, b: dict) -> set[str]:
    return {k for k in a if a[k] != b[k]}


def test_a_trimmed_spec_mutates_only_the_short_list():
    spec = default_spec()
    base = default_genome(AgentFamily.column, spec)
    trimmed = spec.trimmed_spec({"column": SHORT})
    assert trimmed.free_genes("column") == frozenset(SHORT) and trimmed.free_genes("story") is None
    seen = set()
    for strength in (0.02, 0.3, 1.0):
        for seed in range(20):
            child = mutate(base, strength, seed, trimmed)
            diff = _changed(base.genes, child.genes)
            assert diff and diff <= set(SHORT)  # at least one gene always changes, never a fixed one
            seen |= diff
    assert seen == set(SHORT)
    grand = mutate(mutate(base, 1.0, 1, spec), 1.0, 2, trimmed)  # a parent that is off its defaults elsewhere
    parent = mutate(base, 1.0, 1, spec)
    assert _changed(parent.genes, grand.genes) <= set(SHORT)  # fixed genes keep the parent's values


def test_an_untrimmed_run_mutates_as_before():
    spec = default_spec()
    assert spec.free_genes("column") is None and spec.trimmed_spec(None) is spec and spec.trimmed_spec({}) is spec
    base = default_genome(AgentFamily.column, spec)
    lists_only = spec.model_copy(update={"trimmed": {AgentFamily.column: SHORT}})  # lists in the config, no --trim
    for seed in range(5):
        a, b = mutate(base, 0.2, seed, spec), mutate(base, 0.2, seed, lists_only)
        assert a.genome_hash == b.genome_hash
    assert len(_changed(base.genes, mutate(base, 1.0, 0, spec).genes)) > len(SHORT)


def test_a_short_list_must_name_genes_of_its_family():
    spec = default_spec()
    for bad in (["sty_w_grad"], [], ["col_facet_hours", "col_facet_hours"]):
        with pytest.raises(ValueError, match="trimmed"):
            spec.trimmed_spec({"column": bad})


def test_the_configuration_has_a_short_list_for_each_structure():
    spec = load_lab_config(CONFIG).genome
    assert not spec.trim  # the file never turns trimming on by itself
    for fam in STRUCTURES:
        genes, allowed = spec.trimmed[AgentFamily(fam)], spec.family_genes(fam)
        assert 1 <= len(genes) <= sensitivity.KEEP < len(allowed)
        assert [g for g in allowed if g in genes] == genes  # in the family's gene order
    site = [g for g in spec.trimmed[AgentFamily.scorecard] if g.startswith("sc_")]
    assert 1 <= len(site) <= sensitivity.RESERVED[AgentFamily.scorecard][1]
    assert trimmed_lists(spec, [default_genome("story", spec)]) == {"story": spec.trimmed[AgentFamily.story]}
    with pytest.raises(ValueError, match="no trimmed gene list for snowpack"):
        trimmed_lists(spec, [default_genome("snowpack", spec), default_genome("story", spec)])


def test_each_structure_has_a_trimmed_kind_of_run(tmp_path):
    spec = load_lab_config(CONFIG).genome
    trimmed = {name: p for name, p in PRESETS.items() if p.get("trim")}
    assert sorted(p["initial"][0] for p in trimmed.values()) == sorted(STRUCTURES)
    for name, p in trimmed.items():
        assert name.endswith(", trimmed") and spec.trimmed.get(AgentFamily(p["initial"][0]))
        full = next(q for n, q in PRESETS.items() if not q.get("trim") and q.get("initial") == p["initial"])
        assert {k: v for k, v in p.items() if k != "trim"} == full  # the same budget as the full run
    cmd = training_command(LabPaths(tmp_path), CONFIG, "r", rounds=1, population=4, survivors=2,
                           mutation_strength=0.2, crossover_share=0.25, seed=0, workers=1, trim=True)
    assert "--trim" in cmd
    assert "--trim" not in training_command(LabPaths(tmp_path), CONFIG, "r", rounds=1, population=4, survivors=2,
                                            mutation_strength=0.2, crossover_share=0.25, seed=0, workers=1)


def test_a_trimmed_training_run_changes_only_its_short_list(lab):
    cfg, paths = lab
    base = default_genome(AgentFamily.column, cfg.genome)
    opts = TrainOptions.from_config(cfg, rounds=3, population=5, survivors=2, engine=EngineSpec(kind="none"),
                                    initial=[base], trimmed_genes={"column": SHORT})
    res = run_training(paths, cfg, opts, run_id="t", log=lambda _m: None)
    plan = json.loads((res.run_dir / "run.json").read_text())["plan"]
    assert plan["trimmed_genes"] == {"column": SHORT}
    again = _opts_from_plan(plan)
    assert again.trim and again.trimmed_genes == {"column": SHORT}  # a resume or a promotion check trims the same
    n = 0
    for r in (1, 2, 3):
        for p in load_round(res.run_dir, r)["population"]:
            assert _changed(base.genes, p["genome"]["genes"]) <= set(SHORT)
            n += p["genome"]["genes"] != base.genes
    assert n >= 8  # the starting mutants and every child differ from the default
    full = TrainOptions.from_config(cfg, rounds=1, population=5, survivors=2, engine=EngineSpec(kind="none"),
                                    initial=[base])
    res2 = run_training(paths, cfg, full, run_id="f", log=lambda _m: None)
    plan2 = json.loads((res2.run_dir / "run.json").read_text())["plan"]
    assert "trimmed_genes" not in plan2  # an untrimmed plan keeps its form
    assert any(len(_changed(base.genes, p["genome"]["genes"])) > len(SHORT)
               for p in load_round(res2.run_dir, 1)["population"])


def test_probe_values_are_halfway_to_each_bound():
    f = GeneSpec(kind="float", min=0.0, max=10.0, default=4.0, unit="-", doc="x")
    assert sensitivity.probe_values(f) == [2.0, 7.0]
    at_bound = GeneSpec(kind="float", min=0.0, max=3.0, default=0.0, unit="-", doc="x")
    assert sensitivity.probe_values(at_bound) == [1.5]
    i = GeneSpec(kind="int", min=1, max=4, default=2, unit="-", doc="x")
    assert sensitivity.probe_values(i) == [3]  # the rounded lower probe is the default itself: left out
    c = GeneSpec(kind="choice", choices=["on", "off"], default="on", unit="-", doc="x")
    assert sensitivity.probe_values(c) == ["off"]


def test_choose_keeps_the_most_sensitive_genes_and_reserves_site_genes():
    spec = default_spec()
    genes = list(spec.family_genes("scorecard").items())
    rows = [{"gene": g, "block": blk, "sensitivity": 0.02 if blk.startswith("sc_") and i % 2 else 0.01 + i / 1e4,
             "probes": []} for i, (g, (blk, _s)) in enumerate(genes)]
    rows[0]["sensitivity"] = 0.0001  # below the floor: never kept
    res = {"family": "scorecard", "genes": rows}
    kept = sensitivity.choose(res, spec, keep=6)
    assert len(kept) == 6 and rows[0]["gene"] not in kept
    assert sum(g.startswith("sc_") for g in kept) == 2  # two places for site genes, although more rank higher
    assert kept == [g for g, _ in genes if g in kept]
    col = {"family": "column", "genes": [{"gene": g, "block": b, "sensitivity": 0.0, "probes": []}
                                         for g, (b, _s) in spec.family_genes("column").items()]}
    assert sensitivity.choose(col, spec) == []  # nothing moves the score: nothing to evolve


def test_the_screen_runs_on_training_winters_only(lab, monkeypatch):
    cfg, paths = lab
    refs, locked = sensitivity.training_refs(paths, locked_seasons=1, cases=None)
    assert locked == [SEASONS[-1]] and refs and SEASONS[-1] not in {r.manifest.season for r in refs}
    few, _ = sensitivity.training_refs(paths, locked_seasons=1, cases=2)
    assert len(few) == 2 and {r.case_id for r in few} <= {r.case_id for r in refs}
    real = sensitivity.probe_genomes

    def first_probes(family, spec):
        base, probes = real(family, spec)
        return base, [p for p in probes if p[0] in ("snow_mult_bow", "sty_w_grad")]

    monkeypatch.setattr(sensitivity, "probe_genomes", first_probes)
    res = sensitivity.screen_family(paths, cfg, "story", cases=2, locked_seasons=1)
    assert res["cases"] == 2 and res["locked_seasons_left_out"] == [SEASONS[-1]] and res["probes"] == 4
    assert SEASONS[-1] not in res["seasons"]
    by = {g["gene"]: g for g in res["genes"]}
    assert len(by) == len(cfg.genome.family_genes("story")) and len(by["snow_mult_bow"]["probes"]) == 2
    assert by["snow_mult_bow"]["sensitivity"] > 0  # more or less snowfall at the plot moves the depth score
    assert by["sty_hold"]["sensitivity"] == 0.0 and not by["sty_hold"]["probes"]
    assert not list(paths.outputs.glob("sensitivity-*"))  # the throwaway cache is removed
    with pytest.raises(ValueError, match="needs SNOWPACK"):
        sensitivity.screen_family(paths, cfg, "snowpack", cases=2)
    assert any("snow_mult_bow" in line for line in sensitivity.format_table(res, ["snow_mult_bow"]))
