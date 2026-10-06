"""Agent genome (ADR-061): allow-listed, bounded genes per family, stable hash, size guard, and the mutation and
crossover operators the evolution loop will call (deterministic for a seed, always inside the bounds)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from snowagent.lab.genome import (
    crossover,
    default_genome,
    default_genomes,
    gene_table,
    load_genome,
    make_genome,
    mutate,
    save_genome,
    shared_blocks,
)
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome, default_spec
from snowagent.lab.settings import load_lab_config

REPO = Path(__file__).resolve().parents[2]


def _in_bounds(g: AgentGenome) -> None:
    for name, (_blk, s) in default_spec().family_genes(g.family).items():
        v = g.genes[name]
        if s.kind == "choice":
            assert v in s.choices, name
        else:
            assert s.min <= v <= s.max, (name, v)
            assert isinstance(v, int) if s.kind == "int" else isinstance(v, float), name


def test_config_spec_documents_every_gene_and_every_family_has_a_default():
    cfg = load_lab_config(REPO / "config/lab.yaml")
    assert cfg.genome == default_spec()
    rows = gene_table()
    assert {r["family"] for r in rows} == {f.value for f in AgentFamily}
    assert all(r["unit"] and r["doc"] for r in rows)
    sizes = {f: sum(r["family"] == f.value for r in rows) for f in AgentFamily}
    assert all(1 <= n <= cfg.genome.max_genes for n in sizes.values()) and sizes[AgentFamily.weather_rule] > 20
    for g in default_genomes():
        _in_bounds(g)
        assert g.origin == "default" and g.label == f"{g.family.value}-default"


def test_validation_rejects_unknown_missing_out_of_range_and_wrong_type():
    g = default_genome("weather_rule")
    genes = dict(g.genes)
    with pytest.raises(ValidationError, match="unknown genes"):
        make_genome("weather_rule", {**genes, "learning_rate": 0.1})
    with pytest.raises(ValidationError, match="unknown genes"):  # a gene of another family
        make_genome("weather_rule", {**genes, "k": 5})
    missing = dict(genes)
    missing.pop("facet_hours")
    with pytest.raises(ValidationError, match="missing genes"):
        make_genome("weather_rule", missing)
    with pytest.raises(ValidationError, match="outside"):
        make_genome("weather_rule", {**genes, "precipitation_factor": 2.5})
    with pytest.raises(ValidationError, match="not a number"):
        make_genome("weather_rule", {**genes, "precipitation_factor": "1.0"})
    a = default_genome("analogue")
    with pytest.raises(ValidationError, match="not an integer"):
        make_genome("analogue", {**a.genes, "k": 2.5})
    with pytest.raises(ValidationError, match="not one of"):
        make_genome("analogue", {**a.genes, "kernel": "gaussian"})
    h = default_genome("hybrid")
    with pytest.raises(ValidationError, match="blend weight"):
        make_genome("hybrid", {**h.genes, "snowpack_weight": 0.0, "persistence_weight": 0.0, "rule_weight": 0.0})
    with pytest.raises(ValidationError):
        AgentGenome(family="ml_oracle", genes={})


def test_a_genome_holds_no_profile_data():
    """Size guard and scalar-only genes: a stored profile, a list, a date or an oversized label cannot fit."""
    g = default_genome("persistence")
    pit = [{"top_depth_m": 0.0, "bottom_depth_m": 0.2, "grain_primary": "FC"}] * 40
    with pytest.raises(ValidationError, match="not scalar"):
        make_genome("persistence", {**g.genes, "pit_trust": pit})
    with pytest.raises(ValidationError, match="not scalar"):
        make_genome("persistence", {**g.genes, "pit_trust": {"2024-01-10": 1.5}})
    with pytest.raises(ValidationError):
        make_genome("persistence", g.genes, label="x" * 65)
    with pytest.raises(ValidationError):
        make_genome("persistence", g.genes, label="BOW 2024-01-10 pit")  # free text is not a label
    with pytest.raises(ValidationError):
        make_genome("persistence", g.genes, parents=["a" * 64] * 3)
    with pytest.raises(ValidationError):
        AgentGenome.model_validate({**g.model_dump(), "profiles": pit})  # no other field exists
    spec = default_spec().model_copy(update={"max_bytes": 256})
    with pytest.raises(ValidationError, match="max_bytes"):
        AgentGenome.model_validate(g.model_dump(), context={"spec": spec})
    assert len(g.model_dump_json()) <= default_spec().max_bytes


def test_genome_hash_is_stable_and_ignores_label_and_lineage(tmp_path):
    g = default_genome("weather_rule")
    same = make_genome("weather_rule", dict(reversed(list(g.genes.items()))), label="other",
                       parents=["b" * 64], origin="mutation")
    assert same.genome_hash == g.genome_hash and len(g.genome_hash) == 64
    assert g.agent_id == f"weather_rule-{g.genome_hash[:10]}"
    nearly = make_genome("weather_rule", {**g.genes, "facet_hours": g.genes["facet_hours"] + 1e-15})
    assert nearly.genome_hash == g.genome_hash  # canonical floats
    other = make_genome("weather_rule", {**g.genes, "facet_hours": 80.0})
    assert other.genome_hash != g.genome_hash
    assert make_genome("weather_rule", {**g.genes, "facet_hours": 72}).genome_hash == g.genome_hash  # int -> float
    f = save_genome(g, tmp_path / "g.json")
    back = load_genome(f)
    assert back.genome_hash == g.genome_hash and back.origin == "default"
    d = json.loads(f.read_text())
    d.pop("origin")
    f.write_text(json.dumps(d))
    assert load_genome(f).origin == "file"


@pytest.mark.parametrize("family", list(AgentFamily))
def test_mutation_stays_in_bounds_is_deterministic_and_records_its_parent(family):
    g = default_genome(family)
    for seed in range(40):
        for strength in (0.05, 0.3, 1.0):
            m = mutate(g, strength, np.random.default_rng(seed))
            _in_bounds(m)
            assert m.family == family and m.origin == "mutation" and m.parents == [g.genome_hash]
    a = mutate(g, 0.5, np.random.default_rng(7))
    assert mutate(g, 0.5, np.random.default_rng(7)).genome_hash == a.genome_hash
    assert mutate(g, 0.5, 7).genome_hash == a.genome_hash  # an integer seed is the same generator
    assert a.genome_hash != g.genome_hash  # at least one gene changes
    chain = g
    for i in range(200):  # repeated strong mutation never leaves the bounds
        chain = mutate(chain, 1.0, i)
    _in_bounds(chain)
    with pytest.raises(ValueError):
        mutate(g, 0.0, 1)


def test_mutation_keeps_a_hybrid_blend_valid():
    h = make_genome("hybrid", {**default_genome("hybrid").genes, "snowpack_weight": 0.0, "persistence_weight": 0.0,
                               "rule_weight": 0.01})
    for seed in range(200):
        m = mutate(h, 1.0, seed)
        assert any(m.genes[w] > 0 for w in ("snowpack_weight", "persistence_weight", "rule_weight"))


def test_block_crossover_within_a_family():
    a = mutate(default_genome("weather_rule"), 1.0, 1)
    b = mutate(default_genome("weather_rule"), 1.0, 2)
    spec = default_spec()
    kids = [crossover(a, b, np.random.default_rng(s)) for s in range(30)]
    for k in kids:
        _in_bounds(k)
        assert k.parents == [a.genome_hash, b.genome_hash] and k.origin == "crossover"
        for blk in spec.families[AgentFamily.weather_rule]:  # each block comes whole from one parent
            src = {"a" if all(k.genes[g] == a.genes[g] for g in spec.blocks[blk]) else "",
                   "b" if all(k.genes[g] == b.genes[g] for g in spec.blocks[blk]) else ""} - {""}
            assert src, blk
    assert len({k.genome_hash for k in kids}) > 5
    assert crossover(a, b, 3).genome_hash == crossover(a, b, np.random.default_rng(3)).genome_hash


def test_cross_family_crossover_keeps_a_and_takes_only_shared_blocks():
    a = mutate(default_genome("hybrid"), 1.0, 11)
    b = mutate(default_genome("weather_rule"), 1.0, 12)
    shared = shared_blocks("hybrid", "weather_rule")
    assert shared == ["forcing", "new_snow", "uncertainty"]
    spec = default_spec()
    seen_b = set()
    for s in range(30):
        k = crossover(a, b, s)
        assert k.family == AgentFamily.hybrid and set(k.genes) == set(a.genes)
        _in_bounds(k)
        for blk in spec.families[AgentFamily.hybrid]:
            if blk not in shared:
                assert all(k.genes[g] == a.genes[g] for g in spec.blocks[blk])
            elif all(k.genes[g] == b.genes[g] for g in spec.blocks[blk]):
                seen_b.add(blk)
    assert seen_b == set(shared)
    # no shared block: the child carries a's genes
    s1, an = default_genome("snowpack"), default_genome("analogue")
    assert shared_blocks("analogue", "persistence") == ["uncertainty"]
    assert crossover(an, mutate(default_genome("persistence"), 1.0, 1), 0).family == AgentFamily.analogue
    assert shared_blocks("snowpack", "analogue") == ["uncertainty"]
    assert crossover(s1, an, 5).genes.keys() == s1.genes.keys()
