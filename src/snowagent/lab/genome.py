"""Genome construction and the variation operators the evolution loop (milestone 4) calls (ADR-061).

- ``default_genome(family)``: every gene at its configured default.
- ``load_genome(path)`` / ``save_genome``: JSON files, validated against the allow-list.
- ``mutate(genome, strength, rng)``: each gene changes with probability ``strength`` (at least one always does);
  a number moves by a normal step of sd ``strength`` x half its range, reflected at the bounds and clipped; an
  integer is rounded; a choice switches to another choice.
- ``crossover(a, b, rng)``: block crossover. Same family: each of the family's blocks comes whole from ``a`` or
  ``b`` (probability 1/2). Different families: the child keeps ``a``'s family (by convention the better-ranked
  parent) and takes from ``b`` only blocks both families carry (forcing, new_snow, pit, uncertainty ...), each
  with probability 1/2; with no shared block the child equals ``a``'s genes.

Both are pure and deterministic for a given ``numpy.random.Generator`` state (or integer seed) and always return a
genome inside the bounds (validated). Children record ``origin`` and their parents' genome hashes (lineage).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from snowagent.lab.schemas.genome import AgentFamily, AgentGenome, GenomeSpec, default_spec

BLEND = ("snowpack_weight", "persistence_weight", "rule_weight")


def _spec(spec: GenomeSpec | None) -> GenomeSpec:
    return spec or default_spec()


def _rng(rng: np.random.Generator | int | None) -> np.random.Generator:
    return rng if isinstance(rng, np.random.Generator) else np.random.default_rng(rng)


def make_genome(family: AgentFamily | str, genes: dict, spec: GenomeSpec | None = None, **meta) -> AgentGenome:
    return AgentGenome.model_validate({"family": AgentFamily(family), "genes": genes, **meta},
                                      context={"spec": _spec(spec)})


def default_genome(family: AgentFamily | str, spec: GenomeSpec | None = None, label: str | None = None
                   ) -> AgentGenome:
    s = _spec(spec)
    genes = {g: gs.default for g, (_b, gs) in s.family_genes(family).items()}
    return make_genome(family, genes, s, label=label or f"{AgentFamily(family).value}-default")


def default_genomes(spec: GenomeSpec | None = None) -> list[AgentGenome]:
    """One default genome per family (the milestone-3 competition's entrants)."""
    return [default_genome(f, spec) for f in AgentFamily]


def load_genome(path: Path, spec: GenomeSpec | None = None) -> AgentGenome:
    d = json.loads(Path(path).read_text())
    d.setdefault("origin", "file")
    return AgentGenome.model_validate(d, context={"spec": _spec(spec)})


def save_genome(genome: AgentGenome, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(genome.model_dump_json(indent=1))
    return path


def _repair(family: AgentFamily, genes: dict, spec: GenomeSpec) -> dict:
    """Family constraints the bounds cannot express: a hybrid needs one blend weight above 0 (else the
    SNOWPACK weight returns to its default)."""
    if family == AgentFamily.hybrid and not any(genes[w] > 0 for w in BLEND):
        genes["snowpack_weight"] = spec.family_genes(family)["snowpack_weight"][1].default
    return genes


def _reflect(v: float, lo: float, hi: float) -> float:
    w = hi - lo
    t = (v - lo) % (2 * w)
    return float(np.clip(lo + (t if t <= w else 2 * w - t), lo, hi))


def mutate(genome: AgentGenome, strength: float, rng: np.random.Generator | int | None,
           spec: GenomeSpec | None = None) -> AgentGenome:
    """A mutated copy (see the module doc). ``strength`` in (0, 1]."""
    if not 0 < strength <= 1:
        raise ValueError("mutation strength must be in (0, 1]")
    s, r = _spec(spec), _rng(rng)
    allowed = s.family_genes(genome.family)
    names = list(allowed)
    pick = r.random(len(names)) < strength
    if not pick.any():
        pick[int(r.integers(len(names)))] = True
    genes = dict(genome.genes)
    for name, chosen in zip(names, pick, strict=True):
        gs = allowed[name][1]
        step = r.normal()  # drawn for every gene so a gene's draw does not depend on which others mutate
        alt = r.random()
        if not chosen:
            continue
        if gs.kind == "choice":
            others = [c for c in gs.choices if c != genes[name]]
            genes[name] = others[min(int(alt * len(others)), len(others) - 1)]
            continue
        v = _reflect(float(genes[name]) + step * strength * (gs.max - gs.min) / 2, gs.min, gs.max)
        genes[name] = int(np.clip(round(v), gs.min, gs.max)) if gs.kind == "int" else v
    return make_genome(genome.family, _repair(genome.family, genes, s), s, origin="mutation",
                       parents=[genome.genome_hash])


def shared_blocks(a: AgentFamily | str, b: AgentFamily | str, spec: GenomeSpec | None = None) -> list[str]:
    s = _spec(spec)
    fb = set(s.families[AgentFamily(b)])
    return [blk for blk in s.families[AgentFamily(a)] if blk in fb]


def crossover(a: AgentGenome, b: AgentGenome, rng: np.random.Generator | int | None,
              spec: GenomeSpec | None = None) -> AgentGenome:
    """Block crossover (see the module doc); the child has ``a``'s family."""
    s, r = _spec(spec), _rng(rng)
    genes = dict(a.genes)
    for blk in s.families[a.family]:
        take_b = r.random() < 0.5
        if take_b and blk in s.families[b.family]:
            for g in s.blocks[blk]:
                genes[g] = b.genes[g]
    return make_genome(a.family, _repair(a.family, genes, s), s, origin="crossover",
                       parents=[a.genome_hash, b.genome_hash])


def gene_table(spec: GenomeSpec | None = None) -> list[dict]:
    """Every gene of every family with block, kind, range, unit and default (docs, UI)."""
    s = _spec(spec)
    rows = []
    for fam in AgentFamily:
        for g, (blk, gs) in s.family_genes(fam).items():
            rows.append({"family": fam.value, "block": blk, "gene": g, "kind": gs.kind, "min": gs.min,
                         "max": gs.max, "choices": gs.choices, "default": gs.default, "unit": gs.unit, "doc": gs.doc})
    return rows
