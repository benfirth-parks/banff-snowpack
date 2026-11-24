"""Genome construction and the variation operators the evolution loop (milestone 4) calls (ADR-061).

- ``default_genome(family)``: every gene at its configured default.
- ``named_genome(name)``: a standard model, the default SNOWPACK genome with a named change (``NAMED_MODELS``).
- ``load_genome(path)`` / ``save_genome``: JSON files, validated against the allow-list.
- ``mutate(genome, strength, rng)``: each gene changes with probability ``strength`` (at least one always does);
  a number moves by a normal step of sd ``strength`` x half its range, reflected at the bounds and clipped; an
  integer is rounded; a choice switches to another choice. With a trimmed run's spec (ADR-123,
  ``GenomeSpec.trimmed_spec``) only the family's short list of genes can change; the others keep the parent's values.
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

from snowagent.lab.schemas.genome import (
    GENOME_SCHEMA,
    STANDARD_FAMILIES,
    AgentFamily,
    AgentGenome,
    GenomeSpec,
    default_spec,
)

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


# Standard models besides the family defaults: name -> (family, genes changed from its defaults, plain description)
NAMED_MODELS: dict[str, tuple[AgentFamily, dict, str]] = {
    "column-old-facets": (AgentFamily.column, {"col_old_facets": "on"},
                          "the column model with the last pit's facets and depth hoar kept soft and faceted (ADR-128); "
                          "a training run started from it keeps the switch on"),
    "process-old-facets": (AgentFamily.process, {"col_old_facets": "on"},
                           "the process switches model with the last pit's facets and depth hoar kept soft and faceted "
                           "(ADR-128 switch, ADR-142); a training run started from it keeps the switch on"),
    "process-facet-hours": (AgentFamily.process, {"col_old_facets": "on", "proc_sw_duration": "on",
                                                  "proc_facet_excess_cap": 0.2, "col_facet_hours": 24.0,
                                                  "proc_sw_kinetics": "off"},
                            "process-old-facets, with faceting counted by the hours above the faceting gradient "
                            "(about 120 h make facets) and no temperature or density brake (ADR-143)"),
    "snowpack-era5-fix-dh-base": (AgentFamily.snowpack, {"sp_snowfall_source": "snow_depth", "sp_new_weak_factor": 0.4,
                                                         "sp_era5_precip": "era5_factor", "sp_dh_base": "on"},
                                  "snowpack-era5-fix, with depth hoar only at the base: SNOWPACK's depth hoar more "
                                  "than 0.5 m above the ground or above the bottom half of the snowpack is reported "
                                  "as facets (ADR-154)"),
    "process-dh-base": (AgentFamily.process, {"col_dh_base": "on"},
                        "the process switches model with depth hoar only within 0.5 m of the ground and in the "
                        "bottom half of the snowpack (ADR-154); a training run started from it keeps the switch on"),
    "snowpack-snow-depth": (AgentFamily.snowpack, {"sp_snowfall_source": "snow_depth"},
                            "standard SNOWPACK with snowfall from the plot's snow depth sensor (ADR-100)"),
    "snowpack-new-snow-check": (AgentFamily.snowpack, {"sp_snowfall_source": "snow_depth", "sp_new_weak_factor": 0.4},
                                "snowpack-snow-depth, with weak layers formed in snow since the last pit given "
                                "probability 0.28 instead of 0.7 (ADR-103)"),
    "snowpack-era5-fix": (AgentFamily.snowpack, {"sp_snowfall_source": "snow_depth", "sp_new_weak_factor": 0.4,
                                                 "sp_era5_precip": "era5_factor"},
                          "snowpack-new-snow-check, with reanalysis (ERA5) precipitation hours given the plot's own "
                          "ERA5 factor instead of its gauge's (ADR-106)"),
}


def named_genome(name: str, spec: GenomeSpec | None = None) -> AgentGenome:
    """A standard model by name (``NAMED_MODELS``), labelled with its name."""
    if name not in NAMED_MODELS:
        raise ValueError(f"unknown model {name!r} (one of {', '.join(NAMED_MODELS)})")
    fam, change, _doc = NAMED_MODELS[name]
    base = default_genome(fam, spec)
    return make_genome(fam, dict(base.genes) | change, spec, label=name)


def default_genomes(spec: GenomeSpec | None = None) -> list[AgentGenome]:
    """One default genome per standard family (the milestone-3 competition's entrants; the opt-in column family,
    ADR-118, is not one)."""
    return [default_genome(f, spec) for f in STANDARD_FAMILIES]


def load_genome(path: Path, spec: GenomeSpec | None = None, upgrade: bool = True) -> AgentGenome:
    """A genome file; one of an earlier schema version (e.g. a milestone-4 winner) is upgraded (``upgrade_genome``)
    unless ``upgrade`` is False."""
    d = json.loads(Path(path).read_text())
    d.setdefault("origin", "file")
    g = AgentGenome.model_validate(d, context={"spec": _spec(spec)})
    return upgrade_genome(g, spec) if upgrade else g


def upgrade_genome(genome: AgentGenome, spec: GenomeSpec | None = None) -> AgentGenome:
    """A genome of an earlier schema version with the blocks added since at their defaults (milestone 5: the SNOWPACK
    physics genes, whose defaults are the incumbent's physics, so it behaves exactly as before). Its parent is the
    old genome (lineage); the current version is returned unchanged."""
    if genome.schema_version == GENOME_SCHEMA:
        return genome
    s = _spec(spec)
    genes = {g: gs.default for g, (_b, gs) in s.family_genes(genome.family).items()} | dict(genome.genes)
    return make_genome(genome.family, genes, s, label=genome.label, origin="file", parents=[genome.genome_hash])


# ADR-136: a family whose genes are a superset of another's; a genome of the smaller family joins a run of the larger
# one with the extra genes at their defaults, so it predicts exactly as before
WIDER_FAMILY: dict[AgentFamily, AgentFamily] = {AgentFamily.process: AgentFamily.process_melt}


def as_family(genome: AgentGenome, family: AgentFamily | str, spec: GenomeSpec | None = None) -> AgentGenome:
    """``genome`` as a genome of ``family``, whose genes must include all of its genes: the others at their
    defaults. Its parent is the original genome (lineage); the label is kept."""
    s, family = _spec(spec), AgentFamily(family)
    if genome.family == family:
        return genome
    allowed = s.family_genes(family)
    own = s.family_genes(genome.family)
    # a fixed gene of the narrower family left at its default (an ADR-152 weather switch off) is simply dropped
    extra = sorted(k for k in set(genome.genes) - set(allowed)
                   if not (k in own and own[k][1].fixed and genome.genes[k] == own[k][1].default))
    if extra:
        raise ValueError(f"a {genome.family} genome cannot become {family}: {family} lacks {extra}")
    genes = {g: gs.default for g, (_b, gs) in allowed.items()} | {k: v for k, v in genome.genes.items() if k in allowed}
    return make_genome(family, genes, s, label=genome.label, origin="file", parents=[genome.genome_hash])


def widen_seeds(seeds: list[AgentGenome], initial: list[AgentGenome] | None,
                spec: GenomeSpec | None = None) -> list[AgentGenome]:
    """Seeds from an earlier run, each turned into the wider family (``WIDER_FAMILY``) when the new run starts with
    that family and not with the seed's own; the others unchanged."""
    fams = {g.family for g in initial or []}
    out = []
    for g in seeds:
        wide = WIDER_FAMILY.get(g.family)
        out.append(as_family(g, wide, spec) if wide in fams and g.family not in fams else g)
    return out


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
    names = [n for n, (_b, gs) in allowed.items() if not gs.fixed]  # ADR-128: switches never mutate (and draw nothing)
    pick = r.random(len(names)) < strength
    free = s.free_genes(genome.family)  # ADR-123: a trimmed run changes only its short list of genes
    pool = [i for i, n in enumerate(names) if free is None or n in free]
    if free is not None:
        pick &= np.isin(np.arange(len(names)), pool)
    if not pick.any():
        pick[pool[int(r.integers(len(pool)))]] = True
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
        if all(gs.fixed for gs in s.blocks[blk].values()):
            continue  # ADR-128: a block of switches keeps a's values and draws nothing
        take_b = r.random() < 0.5
        if take_b and blk in s.families[b.family]:
            for g, gs in s.blocks[blk].items():
                if not gs.fixed:
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
