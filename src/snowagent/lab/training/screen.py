"""Two-stage evaluation of new physics genomes, ``snowagent lab train --screen-cases K`` (milestone 5, ADR-072).

A genome whose SNOWPACK physics genes are new to the run needs a fresh engine run on every case (ADR-070). With
``--screen-cases K`` such a child is first scored on a fixed stratified sample of K training cases; only a child
whose leaderboard composite on that sample beats the worst survivor's on the same sample (strictly) is scored on all
cases and ranked. The others are recorded with their sample score (role ``screened_out``) and cannot survive. The
survivors, children of the cheap families and output-only children (physics already run) skip the screen: they cost
milliseconds per case. Off by default, so the owner's loop is unchanged unless asked.

The sample is fixed for the run and stored in its plan: per stratum (plot x case type) a share of K proportional to
the stratum's size (largest remainder, at least one case per stratum when K allows), picked by systematic sampling
over the stratum sorted by season and case id with a seeded offset, so it spreads over the seasons. It depends only
on the cases and the seed.
"""

from __future__ import annotations

import math

import numpy as np


def screen_sample(refs, k: int, seed: int) -> list[str]:
    """Case ids of the stratified sample (see the module doc)."""
    if k <= 0:
        raise ValueError("--screen-cases must be positive")
    if k >= len(refs):
        return [r.case_id for r in refs]
    strata: dict[tuple[str, str], list] = {}
    for r in refs:
        m = r.manifest
        strata.setdefault((str(m.site_code), str(getattr(m.case_type, "value", m.case_type))), []).append(r)
    keys = sorted(strata)
    n = len(refs)
    quota = {s: k * len(strata[s]) / n for s in keys}
    alloc = {s: int(math.floor(q)) for s, q in quota.items()}
    if k >= len(keys):
        for s in keys:
            alloc[s] = max(1, alloc[s])
    while sum(alloc.values()) > k:  # the floor of one per stratum overshot: take from the largest allocations
        s = max(keys, key=lambda x: (alloc[x] - quota[x], alloc[x]))
        alloc[s] -= 1
    for s in sorted(keys, key=lambda x: (-(quota[x] - math.floor(quota[x])), x)):
        if sum(alloc.values()) >= k:
            break
        if alloc[s] < len(strata[s]):
            alloc[s] += 1
    rng = np.random.default_rng(np.random.SeedSequence([int(seed), 72]))
    out = []
    for s in keys:
        items = sorted(strata[s], key=lambda r: (r.manifest.season, r.case_id))
        m = min(alloc[s], len(items))
        if not m:
            continue
        u = float(rng.random())
        idx = sorted({min(len(items) - 1, int((i + u) * len(items) / m)) for i in range(m)})
        out += [items[i].case_id for i in idx]
    return sorted(out)


def screen_threshold(sample_ranked: list[dict], survivors: list[str]) -> float | None:
    """The worst survivor's leaderboard composite on the sample (None if no survivor was scored there)."""
    vals = [r["composite_exact"] for r in sample_ranked
            if r["genome_hash"] in survivors and r.get("composite_exact") is not None]
    return min(vals) if vals else None
