"""The next round's population (ADR-066): the survivors unchanged, then children by the existing operators.

From round 2 a population of ``size`` is the ``survivors`` (the top agents of the previous round, unchanged, in rank
order) followed by ``size - survivors`` children: ``round(children x crossover_share)`` crossovers of survivor pairs
(with two survivors: a x b, b x a, a x b, ...; the child keeps the first parent's family, ADR-061) and the rest
mutations, dealt to the survivors in rank order (s1, s2, s1, ...). Every child must be new to the run: a child
whose genome hash was already evaluated in the run or drawn this round is re-drawn (up to ``max_redraws`` times).
A crossover that keeps returning a parent (two families sharing no block, or identical blocks) is mutated after
``CROSS_MUTATE_AFTER`` draws ("crossover+mutation"). The random stream is ``SeedSequence([seed, round])``, so a
round's population depends only on the seed, the round and the previous round's ranking (deterministic, resumable).

Each genome carries a lineage record: operator, parents (genome hashes), the genes that differ from the first
parent (``changed_genes``: gene -> [parent value, child value]), the genes taken from the second parent, and the
genes that differ from the family default.
"""

from __future__ import annotations

from itertools import cycle

import numpy as np

from snowagent.lab.genome import crossover, default_genome, mutate
from snowagent.lab.schemas.genome import AgentGenome, GenomeSpec

CROSS_MUTATE_AFTER = 10


class DuplicateGenomes(RuntimeError):
    """No new genome could be drawn within ``max_redraws``."""


def round_rng(seed: int, round_no: int) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence([int(seed), int(round_no)]))


def gene_diff(a: dict, b: dict) -> dict[str, list]:
    """Genes whose value differs (floats at 12 significant digits): gene -> [a value, b value]."""
    def same(x, y) -> bool:
        if isinstance(x, float) or isinstance(y, float):
            return f"{float(x):.12g}" == f"{float(y):.12g}"
        return x == y

    return {g: [a[g], b[g]] for g in a if g in b and not same(a[g], b[g])}


def vs_default(g: AgentGenome, spec: GenomeSpec) -> dict[str, list]:
    return gene_diff(default_genome(g.family, spec).genes, g.genes)


def lineage_record(g: AgentGenome, round_no: int, operator: str, spec: GenomeSpec, parents: list[AgentGenome],
                   strength: float | None = None) -> dict:
    rec = {"genome_hash": g.genome_hash, "agent_id": g.agent_id, "family": g.family.value, "label": g.display_name,
           "round": round_no, "operator": operator, "parents": [p.genome_hash for p in parents],
           "parent_agent_ids": [p.agent_id for p in parents], "changed_genes": {}, "from_second_parent": [],
           "changed_vs_default": vs_default(g, spec)}
    if parents:
        rec["changed_genes"] = gene_diff(parents[0].genes, g.genes)
    if len(parents) > 1:
        rec["from_second_parent"] = sorted(k for k, (_a, v) in rec["changed_genes"].items()
                                           if k in parents[1].genes and gene_diff({k: v}, {k: parents[1].genes[k]})
                                           == {})
    if strength is not None and "mutation" in operator:
        rec["mutation_strength"] = strength
    return rec


def initial_records(genomes: list[AgentGenome], spec: GenomeSpec) -> list[dict]:
    return [lineage_record(g, 1, "initial", spec, []) for g in genomes]


def children(survivors: list[AgentGenome], n: int, round_no: int, seed: int, strength: float,
             crossover_share: float, spec: GenomeSpec, seen: set[str], max_redraws: int = 100
             ) -> list[tuple[AgentGenome, dict]]:
    """``n`` new genomes with their lineage records (see the module doc)."""
    if not survivors:
        raise ValueError("no survivor to vary")
    rng = round_rng(seed, round_no)
    taken = set(seen) | {s.genome_hash for s in survivors}
    n_cross = int(round(n * crossover_share)) if len(survivors) > 1 else 0
    n_mut = n - n_cross
    plan: list[tuple[str, tuple[AgentGenome, ...]]] = []
    mut_parents = cycle(survivors)
    plan += [("mutation", (next(mut_parents),)) for _ in range(n_mut)]
    pairs = [(a, b) for i, a in enumerate(survivors) for b in survivors[i + 1:]]
    oriented = [p for a, b in pairs for p in ((a, b), (b, a))]
    cross_parents = cycle(oriented) if oriented else None
    plan += [("crossover", next(cross_parents)) for _ in range(n_cross)] if cross_parents else []
    out: list[tuple[AgentGenome, dict]] = []
    counters = {"mutation": 0, "crossover": 0}
    for op, parents in plan:
        child, used = None, op
        for attempt in range(max_redraws):
            if op == "mutation":
                cand = mutate(parents[0], strength, rng, spec)
                used = "mutation"
            else:
                cand = crossover(parents[0], parents[1], rng, spec)
                used = "crossover"
                if cand.genome_hash in taken and attempt >= CROSS_MUTATE_AFTER:
                    cand = mutate(cand, strength, rng, spec).model_copy(
                        update={"origin": "crossover", "parents": [p.genome_hash for p in parents]})
                    used = "crossover+mutation"
            if cand.genome_hash not in taken:
                child = cand
                break
        if child is None:
            raise DuplicateGenomes(f"round {round_no}: no new {op} of {[p.agent_id for p in parents]} in "
                                   f"{max_redraws} draws")
        counters[op] += 1
        tag = "m" if op == "mutation" else "x"
        child = child.model_copy(update={"label": f"r{round_no:02d}-{tag}{counters[op]:02d}-{child.family.value}"})
        taken.add(child.genome_hash)
        out.append((child, lineage_record(child, round_no, used, spec, list(parents), strength)))
    return out


def family_slot_children(bests: list[AgentGenome], round_no: int, seed: int, strength: float, spec: GenomeSpec,
                         seen: set[str], max_redraws: int = 100) -> list[tuple[AgentGenome, dict]]:
    """``--family-slots`` (ADR-073): one mutant of each family's best agent so far (``bests``, one per family, in
    family order). Its own random stream (``SeedSequence([seed, round, 1])``), so the owner's children of the same
    round are unchanged by the option. Children must be new to the run, as every child."""
    rng = np.random.default_rng(np.random.SeedSequence([int(seed), int(round_no), 1]))
    out: list[tuple[AgentGenome, dict]] = []
    taken = set(seen)
    for i, parent in enumerate(bests, start=1):
        child = None
        for _ in range(max_redraws):
            cand = mutate(parent, strength, rng, spec)
            if cand.genome_hash not in taken:
                child = cand
                break
        if child is None:
            raise DuplicateGenomes(f"round {round_no}: no new family-slot mutation of {parent.agent_id} in "
                                   f"{max_redraws} draws")
        child = child.model_copy(update={"label": f"r{round_no:02d}-f{i:02d}-{child.family.value}"})
        taken.add(child.genome_hash)
        rec = lineage_record(child, round_no, "mutation", spec, [parent], strength) | {"slot": "family"}
        out.append((child, rec))
    return out
