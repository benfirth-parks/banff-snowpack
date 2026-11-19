"""Gene sensitivity screen (ADR-123): which settings of a family move the score at all.

With a few hundred correlated pits, a genome of 30 to 45 settings gives evolution more room than the data can
judge. The screen ranks a family's genes so that a trimmed run (``lab train --trim``) evolves only a short list
and keeps the rest at the values they start with.

Method, fixed before any result was read:

- cases: a stratified sample (plot x case type, ``screen_sample``) of the training winters only. The locked test
  winters (ADR-083) are split off first and never loaded; another area's cases (the Jasper lab, ADR-117) are not in
  this lab's case set at all.
- one gene at a time from the family's default genome: a numeric gene is moved halfway to its lower bound and
  halfway to its upper bound, a choice gene to each other choice. Every probe genome predicts every sampled case.
- a gene's sensitivity is the largest absolute change of the mean case composite among its probes. It measures
  whether the gene matters, not whether moving it helps.
- ``choose`` keeps the ``keep`` most sensitive genes at or above ``floor`` (ties by name). A family in ``RESERVED``
  gives a fixed number of those places to named blocks (the site scorecard: two site genes, however they rank,
  because a scorecard without a site gene is the column model).

Limits: one gene at a time misses genes that matter only in combination, the sample is a part of the training
cases, and choosing genes on the training winters is itself a choice made on data. The honest test of a trimmed
run is the same as of a full one: the locked winters and the out-of-area check."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from snowagent.lab import LAB_DISCLAIMER
from snowagent.lab.competition.runner import EngineSpec, select_cases
from snowagent.lab.genome import default_genome, make_genome
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome, GeneSpec, GenomeSpec
from snowagent.lab.settings import LabConfig
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.training.cache import ENGINE_FAMILIES, TrainingCache
from snowagent.lab.training.evaluate import CaseRef, EvalContext, case_refs, evaluate_population
from snowagent.lab.training.screen import screen_sample

SENSITIVITY_VERSION = "sensitivity-1"
KEEP = 12
FLOOR = 0.0005  # a gene whose probes move the mean composite (0 to 1) by less than this is not kept
# family -> (blocks, places): that many of the kept genes come from these blocks
RESERVED: dict[AgentFamily, tuple[tuple[str, ...], int]] = {
    AgentFamily.scorecard: (("sc_dials", "sc_shelter"), 2),
}


def probe_values(gs: GeneSpec) -> list:
    """The values a gene is tried at: halfway from its default to each bound, or each other choice."""
    if gs.kind == "choice":
        return [c for c in gs.choices or [] if c != gs.default]
    d = float(gs.default)
    out: list = []
    for v in (d - (d - gs.min) / 2, d + (gs.max - d) / 2):  # type: ignore[operator]
        v = int(round(v)) if gs.kind == "int" else float(f"{v:.12g}")
        if v != gs.default and v not in out:
            out.append(v)
    return out


def probe_genomes(family: AgentFamily | str, spec: GenomeSpec) -> tuple[AgentGenome, list[tuple[str, object, AgentGenome]]]:
    """The family's default genome and one genome per (gene, probe value), all else at the defaults."""
    base = default_genome(family, spec)
    out = []
    for gene, (_block, gs) in spec.family_genes(family).items():
        if gs.fixed:  # ADR-128: a run-level switch is not screened
            continue
        for v in probe_values(gs):
            out.append((gene, v, make_genome(family, base.genes | {gene: v}, spec, label=f"probe-{len(out):03d}")))
    return base, out


def training_refs(paths: LabPaths, locked_seasons: int, cases: int | None, seed: int = 0, case_set: str = "all"
                  ) -> tuple[list[CaseRef], list[str]]:
    """The sampled training cases and the locked seasons left out (never loaded or scored)."""
    from snowagent.lab.training.loop import split_locked

    train, _locked, seasons = split_locked(select_cases(paths, case_set), locked_seasons)
    refs = case_refs(train)
    if cases and cases < len(refs):
        keep = set(screen_sample(refs, cases, seed))
        refs = [r for r in refs if r.case_id in keep]
    if any(r.manifest.season in set(seasons) for r in refs):
        raise RuntimeError("a locked season reached the sensitivity screen")
    return refs, seasons


def screen_family(paths: LabPaths, cfg: LabConfig, family: AgentFamily | str, *, cases: int | None = 150,
                  locked_seasons: int | None = None, seed: int = 0, workers: int = 1,
                  engine: EngineSpec | None = None, progress: Callable[[int, int], None] | None = None) -> dict:
    """Run the screen for one family (see the module doc). Predictions go to a throwaway cache."""
    from snowagent.lab.training.loop import LOCKED_SEASONS

    family = AgentFamily(family)
    engine = engine or EngineSpec(kind="none")
    if family in ENGINE_FAMILIES and engine.kind == "none":
        raise ValueError(f"the {family.value} family needs SNOWPACK: pass an engine")
    t0 = time.time()
    refs, seasons = training_refs(paths, LOCKED_SEASONS if locked_seasons is None else locked_seasons, cases, seed)
    if not refs:
        raise ValueError("no training case to screen on (build the cases first)")
    base, probes = probe_genomes(family, cfg.genome)
    tmp = Path(tempfile.mkdtemp(prefix="sensitivity-", dir=paths.outputs if paths.outputs.is_dir() else None))
    try:
        ctx = EvalContext(paths=paths, case_set="all", seed=seed, weights=cfg.scoring_weights,
                          config_hash=cfg.config_hash(), engine=engine)
        ctx.cache = TrainingCache(tmp)
        df = evaluate_population(refs, [base] + [g for _, _, g in probes], ctx, workers, progress).scores
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    scored = df[df["status"] != "skipped"]
    mean = scored.groupby("agent_id")["composite"].mean().to_dict()
    fails = scored[scored["status"] != "ok"].groupby("agent_id").size().to_dict()
    b = float(mean[base.agent_id])
    blocks = {g: blk for g, (blk, _s) in cfg.genome.family_genes(family).items()}
    genes: dict[str, dict] = {g: {"gene": g, "block": blocks[g], "sensitivity": 0.0, "probes": []} for g in blocks}
    for gene, value, g in probes:
        delta = float(mean.get(g.agent_id, float("nan"))) - b
        genes[gene]["probes"].append({"value": value, "delta": round(delta, 6),
                                      "failures": int(fails.get(g.agent_id, 0))})
        if delta == delta:
            genes[gene]["sensitivity"] = round(max(genes[gene]["sensitivity"], abs(delta)), 6)
    ranked = sorted(genes.values(), key=lambda x: (-x["sensitivity"], x["gene"]))
    ids = sorted(r.case_id for r in refs)
    return {"version": SENSITIVITY_VERSION, "family": family.value, "label": LAB_DISCLAIMER,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"), "cases": len(ids),
            "case_ids_hash": hashlib.sha256(json.dumps(ids).encode()).hexdigest()[:16],
            "seasons": sorted({r.manifest.season for r in refs}), "locked_seasons_left_out": seasons, "seed": seed,
            "config_hash": cfg.config_hash(), "base_composite": round(b, 6), "probes": len(probes),
            "wall_s": round(time.time() - t0, 1), "genes": ranked}


def choose(result: dict, spec: GenomeSpec, keep: int = KEEP, floor: float = FLOOR) -> list[str]:
    """The genes a trimmed run evolves, in the family's gene order (see the module doc)."""
    family = AgentFamily(result["family"])
    ranked = [g for g in sorted(result["genes"], key=lambda x: (-x["sensitivity"], x["gene"]))
              if g["sensitivity"] >= floor]
    blocks, places = RESERVED.get(family, ((), 0))
    held = [g["gene"] for g in ranked if g["block"] in blocks][:places]
    rest = [g["gene"] for g in ranked if g["block"] not in blocks][:max(keep - len(held), 0)]
    picked = set(held) | set(rest)
    return [g for g in spec.family_genes(family) if g in picked]


def format_table(result: dict, chosen: list[str]) -> list[str]:
    """Plain lines: rank, gene, sensitivity in points of 100, the change at each probe, kept or fixed."""
    out = [f"{result['family']}: {result['cases']} training-winter cases, default composite "
           f"{100 * result['base_composite']:.2f}, {result['probes']} probes, {result['wall_s']:.0f} s"]
    for i, g in enumerate(result["genes"], start=1):
        moves = ", ".join(f"{p['value']}: {100 * p['delta']:+.2f}" for p in g["probes"])
        out.append(f"  {i:>2} {'keep ' if g['gene'] in chosen else 'fixed'} {g['gene']:<30} "
                   f"{100 * g['sensitivity']:5.2f}  ({moves})")
    return out


__all__ = ["FLOOR", "KEEP", "RESERVED", "SENSITIVITY_VERSION", "choose", "format_table", "probe_genomes",
           "probe_values", "screen_family", "training_refs"]
