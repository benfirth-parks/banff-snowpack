"""The group check (ADR-095; owner, 2026-10-09: use the agents' predictions "as a group ... to determine general
characteristics we should be considering, or high uncertainty and inconsistency, to help advise avalanche
forecasting").

A group is a handful of agents chosen for variety: standard SNOWPACK, agents of other kinds, the best agents of
separate training runs, and standard SNOWPACK with nudged weather (more or less snowfall, warmer or colder air) because
storm totals are often the biggest unknown. On each case the group's predictions are pooled into

- a **consensus profile** (``consensus``): the middle snow depth; on 40 relative-depth slices the grain class most
  agents give there, merged into layers; and every weak layer or crust that at least a third of the agents forecast
  (``WEAK_LAYER_FLOOR``), with its presence probability = the share of agents that forecast it, so a layer the
  majority forecast counts as forecast and a split one does not;
- **disagreement measures**: the spread of the agents' snow depths, the share of the profile where they disagree on
  the grain class, and the weak layers they split on.

The check (``run_group_check``) runs every member on a training run's locked test winters through the training cache
(saved predictions are reused; missing ones are computed), scores the consensus profile exactly as an agent is scored,
and asks three questions (``analyse``): is the consensus at least as good as one agent; are the agents more often wrong
where they disagree; and are weak layers most agents agree on really in the pits. Only agents that never trained on
those winters may join (``resolve_members``). Nothing here changes an agent, a score or the training cache's keys
(``lab/services/`` is outside the prediction code hash). Decision support only, never an avalanche forecast.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from snowagent.lab import LAB_DISCLAIMER
from snowagent.lab.agents.common import hardness_code
from snowagent.lab.competition import scoring
from snowagent.lab.genome import default_genome, make_genome, upgrade_genome
from snowagent.lab.ingest.mapping import CONCERN_CLASSES, critical_class
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome
from snowagent.lab.schemas.prediction import (
    BulkState,
    Confidence,
    PredictedLayer,
    Quantiles,
    SnowpackPrediction,
)
from snowagent.lab.schemas.profile import UNKNOWN_GRAIN, CriticalClass
from snowagent.lab.services.names import nickname
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.storage.provenance import new_run_id

CHECK_VERSION = "lab-group-check-1"
SLICES = 40  # relative-depth slices of the consensus structure
GROUP_TOL = 0.10  # weak layers of one kind whose mid-depths lie within this relative depth are one group layer
WEAK_LAYER_FLOOR = 1 / 3  # a group weak layer enters the consensus profile when at least this share forecast it
HARDNESS_MERGE = 0.5  # neighbouring slices of one grain class within this hand-hardness are one layer
SPLIT_RANGE = (1 / 3, 2 / 3)  # a weak layer forecast by this share of the agents is "split"
SUPPORT_BINS = [(0.0, 0.4, "a few agents (under 40%)"), (0.4, 0.7, "about half (40 to 69%)"),
                (0.7, 1.01, "most agents (70% or more)")]
KIND_NAMES = {"surface_hoar": "surface hoar", "facets": "facets", "depth_hoar": "depth hoar", "crust": "crust"}
PERSISTENT_KINDS = ("surface_hoar", "facets", "depth_hoar")
LAYER_COLUMNS = ["case_id", "season", "kind", "top_rel", "bottom_rel", "mid_rel", "support", "agents", "grain",
                 "hardness", "mid_depth_m", "confirmed"]

# Weather nudges: standard SNOWPACK with its measured weather changed by about the size of the doubt in it (the
# gauges' catch and the plots' air temperature). Genes of the SNOWPACK family (config/lab.yaml).
PLOTS = ("bow", "goat", "simp")
NUDGES: dict[str, tuple[str, dict[str, float]]] = {
    "more-snow": ("standard SNOWPACK with 15% more snowfall", {f"sp_precip_mult_{p}": 1.15 for p in PLOTS}),
    "less-snow": ("standard SNOWPACK with 15% less snowfall", {f"sp_precip_mult_{p}": 0.85 for p in PLOTS}),
    "warmer": ("standard SNOWPACK with air 1 degree warmer", {f"sp_ta_offset_{p}_k": 1.0 for p in PLOTS}),
    "colder": ("standard SNOWPACK with air 1 degree colder", {f"sp_ta_offset_{p}_k": -1.0 for p in PLOTS}),
}
OTHER_KINDS = {"hybrid": "blends SNOWPACK with the latest pit", "analogue": "copies similar past weather",
               "persistence": "carries the latest pit forward", "weather_rule": "builds layers from weather rules"}


# --------------------------------------------------------------------------------------------- members


@dataclass
class Member:
    key: str  # "standard", "kind:<family>", "nudge:<name>" or "<training run>/<round>/<rank>"
    name: str  # what the report calls it
    group: str  # "standard", "other kind", "evolved", "weather nudge"
    note: str
    genome: AgentGenome
    run_id: str | None = None

    def record(self) -> dict:
        return {"key": self.key, "name": self.name, "group": self.group, "note": self.note, "run_id": self.run_id,
                "family": self.genome.family.value, "genome_hash": self.genome.genome_hash,
                "agent_id": self.genome.agent_id}


def _training_root(paths: LabPaths) -> Path:
    from snowagent.lab.training.loop import training_root

    return training_root(paths)


def run_plan(paths: LabPaths, run_id: str) -> dict:
    f = _training_root(paths) / run_id / "run.json"
    if not f.is_file():
        raise ValueError(f"no training run {run_id}")
    return json.loads(f.read_text())["plan"]


def unseen_by(plan: dict, seasons: list[str]) -> bool:
    """Whether a training run's agents never learned from ``seasons``: the run locked them (ADR-083) and did not
    start from agents that had seen them (ADR-085)."""
    locked = set(plan.get("locked_seasons") or [])
    return bool(seasons) and set(seasons) <= locked and not set(plan.get("seeded_saw_locked") or []) & set(seasons)


def locked_runs(paths: LabPaths) -> list[str]:
    """Training runs with locked test winters and at least one finished round, newest first: each can supply the
    test cases of a group check."""
    from snowagent.lab.training.loop import committed_rounds

    root = _training_root(paths)
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True):
        try:
            plan = json.loads((d / "run.json").read_text())["plan"]
        except (OSError, ValueError, KeyError):
            continue
        if plan.get("locked_seasons") and plan.get("split_mode", "all") != "loso" and committed_rounds(d):
            out.append(d.name)
    return out


def clean_runs(paths: LabPaths, seasons: list[str]) -> list[str]:
    """Training runs (newest first) whose agents never learned from ``seasons``."""
    out = []
    for run_id in locked_runs(paths):
        try:
            if unseen_by(run_plan(paths, run_id), seasons):
                out.append(run_id)
        except ValueError:
            continue
    return out


def default_members(paths: LabPaths, seasons: list[str], per_run: int = 2) -> list[str]:
    """The suggested group: standard SNOWPACK, the hybrid (another kind of agent that still reads SNOWPACK), the top
    ``per_run`` agents of the last finished round of every training run that never saw ``seasons``, and the four
    weather nudges. The analogue, persistence and weather-rule agents can be added but are left out by default: they
    score well below SNOWPACK on layers, and a weak voter drags a majority vote down."""
    from snowagent.lab.training.loop import committed_rounds

    keys = ["standard", "kind:hybrid"]
    for run_id in clean_runs(paths, seasons):
        rounds = committed_rounds(_training_root(paths) / run_id)
        if rounds:
            keys += [f"{run_id}/{rounds[-1]}/{k}" for k in range(1, per_run + 1)]
    return keys + [f"nudge:{n}" for n in NUDGES]


def nudged_genome(name: str, spec=None) -> AgentGenome:
    base = default_genome(AgentFamily.snowpack, spec)
    return make_genome(AgentFamily.snowpack, dict(base.genes) | NUDGES[name][1], spec, label=f"group-nudge-{name}",
                       origin="file")


def resolve_member(paths: LabPaths, key: str, seasons: list[str], spec=None) -> Member:
    """One member from its key; ``ValueError`` for an agent that learned from ``seasons`` (it would make the test
    look better than it is) or a key that does not exist."""
    if key == "standard":
        return Member(key, "standard SNOWPACK", "standard", "the SNOWPACK model as it stands",
                      default_genome(AgentFamily.snowpack, spec))
    if key.startswith("kind:"):
        fam = key.split(":", 1)[1]
        if fam not in OTHER_KINDS:
            raise ValueError(f"unknown kind of agent {fam!r} (one of {', '.join(OTHER_KINDS)})")
        return Member(key, f"standard {fam.replace('_', ' ')} agent", "other kind", OTHER_KINDS[fam],
                      default_genome(fam, spec))
    if key.startswith("nudge:"):
        name = key.split(":", 1)[1]
        if name not in NUDGES:
            raise ValueError(f"unknown weather nudge {name!r} (one of {', '.join(NUDGES)})")
        return Member(key, NUDGES[name][0], "weather nudge", "tests how much the weather's doubt matters",
                      nudged_genome(name, spec))
    from snowagent.lab.training.loop import load_round

    parts = key.strip("/").split("/")
    if len(parts) != 3:
        raise ValueError(f"member {key!r}: standard, kind:<kind>, nudge:<name> or <training run>/<round>/<rank>")
    run_id, rnd, rank = parts[0], int(parts[1]), int(parts[2])
    plan = run_plan(paths, run_id)
    if not unseen_by(plan, seasons):
        raise ValueError(f"the agents of {run_id} learned from some of the test winters ({', '.join(seasons)}), so "
                         "they cannot take part in a fair test of them")
    rd = load_round(_training_root(paths) / run_id, rnd)
    ranked = rd["leaderboard"]["ranked"]
    if not 1 <= rank <= len(ranked):
        raise ValueError(f"round {rnd} of {run_id} has ranks 1-{len(ranked)}")
    h = ranked[rank - 1]["genome_hash"]
    old = next(AgentGenome.model_validate(x["genome"]) for x in rd["population"]
               if AgentGenome.model_validate(x["genome"]).genome_hash == h)
    name = nickname(old.genome_hash, old.label)  # the name it had in its run, before any upgrade
    return Member(key, name, "evolved", f"rank {rank} of round {rnd} of training run {run_id}",
                  upgrade_genome(old, spec), run_id)


def resolve_members(paths: LabPaths, keys: list[str], seasons: list[str], spec=None) -> tuple[list[Member], list[str]]:
    """The members in order without duplicates (the same genome twice votes once); returns them and notes on the
    keys left out."""
    out, notes, seen = [], [], {}
    for key in keys:
        m = resolve_member(paths, key, seasons, spec)
        h = m.genome.genome_hash
        if h in seen:
            notes.append(f"{m.name} ({m.note}) is the same agent as {seen[h]}, so it votes once")
            continue
        seen[h] = m.name
        out.append(m)
    if len(out) < 3:
        raise ValueError("a group needs at least 3 different agents")
    return out, notes


# --------------------------------------------------------------------------------------------- consensus


@dataclass
class _L:
    """A member's layer on relative depth, with its full grain code and its scored class."""

    top: float
    bottom: float
    grain: str
    hardness: float | None
    kind: CriticalClass
    prob: float

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2


def member_layers(pred: SnowpackPrediction) -> list[_L]:
    """A prediction's layers on relative depth, surface to ground, each with the class the critical-layer score gives
    it (under scoring version 4 a facet layer without a slab, a harder bed or a hardness jump is not a weak layer)."""
    cols = scoring.predicted_columns(pred)
    if not cols:
        return []
    kinds = [c.critical for c in (scoring.structural(cols) if scoring.by_structure() else cols)]
    layers = sorted(pred.layers, key=lambda ly: ly.top_depth_m.p50)
    return [_L(c.top, c.bottom, (ly.grain_form[0] if ly.grain_form else UNKNOWN_GRAIN), c.hardness, k, c.prob)
            for c, ly, k in zip(cols, layers, kinds, strict=True)]


@dataclass
class GroupLayer:
    """A weak layer or crust of the group: the agents that forecast one of that kind near the same depth."""

    kind: str
    top: float  # relative depth (0 surface, 1 ground)
    bottom: float
    members: list[int]
    support: float  # share of the agents that answered
    grain: str
    hardness: float | None

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2

    def record(self, hs: float | None = None) -> dict:
        d = {"kind": self.kind, "top_rel": round(self.top, 4), "bottom_rel": round(self.bottom, 4),
             "mid_rel": round(self.mid, 4), "support": round(self.support, 4), "agents": len(self.members),
             "grain": self.grain, "hardness": hardness_code(self.hardness)}
        if hs:
            d |= {"mid_depth_m": round(self.mid * hs, 3)}
        return d


def group_layers(layers: list[list[_L]], n: int, tol: float = GROUP_TOL) -> list[GroupLayer]:
    """Every member's forecast weak layers and crusts (presence probability at least 0.5) gathered by kind: a layer
    joins a group layer of its kind whose mean mid-depth is within ``tol`` and to which its agent has not yet
    contributed, else starts one. Support = agents in it / ``n``."""
    items = [(i, ly) for i, ls in enumerate(layers) for ly in ls
             if ly.kind in CONCERN_CLASSES and ly.prob >= scoring.PRESENT_P]
    out: list[GroupLayer] = []
    for kind in scoring.EVENT_CLASSES:
        groups: list[list[tuple[int, _L]]] = []
        for i, ly in sorted(((i, ly) for i, ly in items if ly.kind == kind), key=lambda t: t[1].mid):
            best = None
            for g in groups:
                mean = float(np.mean([x.mid for _, x in g]))
                if abs(ly.mid - mean) <= tol and all(j != i for j, _ in g):
                    if best is None or abs(ly.mid - mean) < best[0]:
                        best = (abs(ly.mid - mean), g)
            if best is None:
                groups.append([(i, ly)])
            else:
                best[1].append((i, ly))
        for g in groups:
            hard = [x.hardness for _, x in g if x.hardness is not None]
            grain = Counter(x.grain for _, x in g).most_common(1)[0][0]
            out.append(GroupLayer(kind.value, float(np.median([x.top for _, x in g])),
                                  float(np.median([x.bottom for _, x in g])), sorted(i for i, _ in g), len(g) / n,
                                  grain, float(np.median(hard)) if hard else None))
    return sorted(out, key=lambda g: g.mid)


def _at(layers: list[_L], z: float) -> _L | None:
    for ly in layers:
        if ly.top <= z < ly.bottom or (z == 1.0 and ly.bottom >= 1.0):
            return ly
    return None


def structure_slices(layers: list[list[_L]], n: int, slices: int = SLICES) -> list[dict]:
    """Per relative-depth slice: the grain class most agents give there (ties: the class seen first from the top
    agent order), its full grain code, the agreement (agents giving it / ``n``) and the median hardness."""
    out = []
    for z in (np.arange(slices) + 0.5) / slices:
        here = [ly for ls in layers if (ly := _at(ls, float(z))) is not None]
        majors = Counter(scoring.major_class(x.grain) for x in here)
        if not majors:
            out.append({"z": float(z), "major": "?", "grain": UNKNOWN_GRAIN, "agree": 0.0, "hardness": None})
            continue
        major, count = majors.most_common(1)[0]
        grain = Counter(x.grain for x in here if scoring.major_class(x.grain) == major).most_common(1)[0][0]
        hard = [x.hardness for x in here if x.hardness is not None]
        out.append({"z": float(z), "major": major, "grain": grain, "agree": count / n,
                    "hardness": float(np.median(hard)) if hard else None})
    return out


def _merge(slices: list[dict]) -> list[dict]:
    """Neighbouring slices of one grain class and similar hardness as one layer (relative depths)."""
    out: list[dict] = []
    step = 1 / len(slices) if slices else 0
    for s in slices:
        top, bottom = s["z"] - step / 2, s["z"] + step / 2
        last = out[-1] if out else None
        if last and last["major"] == s["major"] and (
                last["hardness"] is None or s["hardness"] is None or abs(last["hardness"] - s["hardness"]) <= HARDNESS_MERGE):
            last["bottom"] = bottom
            last["agree"].append(s["agree"])
            if s["hardness"] is not None:
                last["hard"].append(s["hardness"])
            continue
        out.append({"top": top, "bottom": bottom, "major": s["major"], "grain": s["grain"], "agree": [s["agree"]],
                    "hard": [s["hardness"]] if s["hardness"] is not None else [],
                    "hardness": s["hardness"]})
    for ly in out:
        ly["prob"] = float(np.mean(ly["agree"]))
        ly["hardness"] = float(np.mean(ly["hard"])) if ly["hard"] else None
    return out


@dataclass
class GroupView:
    """The group's answer on one case: the consensus prediction, its weak layers and how much the agents disagree."""

    prediction: SnowpackPrediction
    layers: list[GroupLayer] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _depth_quantiles(preds: list[SnowpackPrediction]) -> Quantiles:
    p50s = [p.bulk_state.snow_depth_m.p50 for p in preds]
    pooled = [v for p in preds for v in (p.bulk_state.snow_depth_m.p10, p.bulk_state.snow_depth_m.p50,
                                         p.bulk_state.snow_depth_m.p90)]
    mid = float(np.median(p50s))
    return Quantiles(p10=max(0.0, min(mid, float(np.percentile(pooled, 10)))), p50=mid,
                     p90=max(mid, float(np.percentile(pooled, 90))))


def _predicted(i: int, top: float, bottom: float, hs: Quantiles, grain: str, hardness: float | None, prob: float,
               kind: CriticalClass | None = None) -> PredictedLayer:
    """A consensus layer from relative depths; its depth quantiles follow the snow-depth quantiles."""
    def q(rel: float) -> Quantiles:
        lo = max(hs.p10, 0.5 * hs.p50)
        return Quantiles(p10=rel * lo, p50=rel * hs.p50, p90=rel * hs.p90)

    cls = kind or critical_class(grain)[0]
    return PredictedLayer(name=f"G{i + 1:02d}", top_depth_m=q(top), bottom_depth_m=q(bottom),
                          grain_form=[grain] if grain and grain != UNKNOWN_GRAIN else [],
                          hardness=hardness_code(hardness), probability_present=float(np.clip(prob, 0.0, 1.0)),
                          is_layer_of_concern=cls in CONCERN_CLASSES, critical_class=cls,
                          confidence=float(np.clip(prob, 0.0, 1.0)))


def consensus(preds: list[SnowpackPrediction], n_members: int | None = None, agent_id: str = "group") -> GroupView:
    """Pool the members' predictions of one case (see the module doc). Members that returned insufficient data are
    left out of the vote but counted in ``n_members`` (default: all of ``preds``), so their silence is doubt."""
    if not preds:
        raise ValueError("no prediction to pool")
    ok = [p for p in preds if p.status == "ok" and p.bulk_state is not None]
    n = max(n_members or len(preds), len(ok))
    first = preds[0]
    base = {"case_id": first.case_id, "agent_id": agent_id, "issued_at": first.issued_at, "valid_at": first.valid_at,
            "site_code": first.site_code, "scenario": first.scenario, "label": LAB_DISCLAIMER}
    if len(ok) < 2:
        pred = SnowpackPrediction(**base, status="insufficient_data", confidence=Confidence(overall=0.0),
                                  insufficient_data_reason=f"only {len(ok)} of {n} agents answered",
                                  model_metadata={"family": "group", "members": n, "answered": len(ok)})
        return GroupView(pred, [], {"answered": len(ok), "members": n})
    hs = _depth_quantiles(ok)
    p50s = np.array([p.bulk_state.snow_depth_m.p50 for p in ok])
    spread = float(np.percentile(p50s, 90) - np.percentile(p50s, 10)) if len(p50s) > 1 else 0.0
    layers = [member_layers(p) for p in ok]
    glayers = group_layers(layers, n)
    slices = structure_slices(layers, n)
    bulk = _merge(slices) if hs.p50 > 0 else []
    weak = [g for g in glayers if g.support >= WEAK_LAYER_FLOOR - 1e-9]
    cols: list[tuple[float, float, str, float | None, float, CriticalClass | None]] = [
        (b["top"], b["bottom"], b["grain"], b["hardness"], b["prob"], None) for b in bulk]
    for g in weak:
        kind = CriticalClass(g.kind)
        host = next((i for i, c in enumerate(cols) if c[0] <= g.mid < c[1]), None)
        if host is not None and critical_class(cols[host][2])[0] == kind:  # a thick layer of that kind already
            t, b, gr, h, p, k = cols[host]
            cols[host] = (t, b, gr, h, max(p, g.support), k)
            continue
        top, bottom = g.top, max(g.bottom, g.top + 0.005)
        cut = []
        for t, b, gr, h, p, k in cols:  # cut the layers it overlaps around it
            if b <= top or t >= bottom:
                cut.append((t, b, gr, h, p, k))
                continue
            if t < top:
                cut.append((t, top, gr, h, p, k))
            if b > bottom:
                cut.append((bottom, b, gr, h, p, k))
        cols = sorted([c for c in cut if c[1] - c[0] > 1e-4] + [(top, bottom, g.grain, g.hardness, g.support, kind)],
                      key=lambda c: (c[0], c[1]))
    out_layers = [_predicted(i, t, b, hs, gr, h, p, k) for i, (t, b, gr, h, p, k) in enumerate(cols)] \
        if hs.p50 > 0 else []
    split = [g for g in glayers if g.kind in PERSISTENT_KINDS and SPLIT_RANGE[0] - 1e-9 <= g.support < SPLIT_RANGE[1]]
    stats = {"answered": len(ok), "members": n, "depth_p50_m": round(hs.p50, 4),
             "depth_spread_m": round(spread, 4),
             "depth_spread_rel": round(spread / hs.p50, 4) if hs.p50 > 0 else None,
             "structure_disagreement": round(1 - float(np.mean([s["agree"] for s in slices])), 4),
             "weak_layers_agreed": sum(1 for g in glayers if g.kind in PERSISTENT_KINDS and g.support >= SPLIT_RANGE[1]),
             "weak_layers_split": len(split)}
    pred = SnowpackPrediction(
        **base, bulk_state=BulkState(snow_depth_m=hs), layers=out_layers,
        confidence=Confidence(overall=round(1 - stats["structure_disagreement"], 4),
                              main_limits=[f"{len(split)} weak layer(s) the agents split on"] if split else []),
        model_metadata={"family": "group", "members": n, "answered": len(ok), "check_version": CHECK_VERSION})
    return GroupView(pred, glayers, stats)


# --------------------------------------------------------------------------------------------- verification


def confirm(glayers: list[GroupLayer], obs: list[scoring.Col], tol: float = scoring.REL_TOL) -> list[bool]:
    """Per group layer: whether the pit has a layer of its kind within ``tol`` relative depth (the scorer's
    tolerance; ``obs`` as the scorer sees the pit)."""
    return [any(o.critical.value == g.kind and abs(o.mid - g.mid) <= tol for o in obs) for g in glayers]


def missed_by_all(layers: list[list[_L]], obs: list[scoring.Col], tol: float = scoring.REL_TOL) -> int:
    """Pit weak layers (persistent kinds) that no agent forecast near their depth."""
    fc = [ly for ls in layers for ly in ls if ly.kind.value in PERSISTENT_KINDS and ly.prob >= scoring.PRESENT_P]
    return sum(1 for o in obs if o.critical.value in PERSISTENT_KINDS
               and not any(f.kind == o.critical and abs(f.mid - o.mid) <= tol for f in fc))


def case_view(m, truth, member_preds: list[SnowpackPrediction | None], weights) -> tuple[dict, list[dict]]:
    """Score the group's consensus on one case like an agent, with its disagreement measures and weak layers.
    ``member_preds``: one per member (None when the member could not run there). Returns the case row and its group
    layer rows (with ``confirmed`` when the pit has full layers)."""
    preds = [p for p in member_preds if p is not None]
    view = consensus(preds, n_members=len(member_preds))
    s = scoring.score_case(view.prediction, truth, m.target_scope)
    s.pop("status", None)
    row = {"case_id": m.case_id, "season": m.season, "site_code": str(getattr(m.site_code, "value", m.site_code)),
           "case_type": m.case_type.value, "target_scope": m.target_scope.value,
           "status": view.prediction.status, **view.stats, **s,
           "composite": scoring.case_composite(s, weights)}
    full = m.target_scope.value == "full_profile" and bool(truth.layers)
    rows = []
    if view.prediction.status == "ok":
        obs = scoring.structural(scoring.truth_columns(truth)) if scoring.by_structure() \
            else scoring.truth_columns(truth)
        ok = [member_layers(p) for p in preds if p.status == "ok" and p.bulk_state is not None]
        hits = confirm(view.layers, obs) if full else [None] * len(view.layers)
        for g, hit in zip(view.layers, hits, strict=True):
            rows.append({"case_id": m.case_id, "season": m.season, **g.record(view.stats.get("depth_p50_m")),
                         "confirmed": hit})
        if full:
            row["pit_weak_layers"] = sum(1 for o in obs if o.critical.value in PERSISTENT_KINDS)
            row["pit_weak_missed_by_all"] = missed_by_all(ok, obs)
    return row, rows


def _thirds(df: pd.DataFrame, by: str, cols: dict[str, str]) -> list[dict]:
    """Cases split into thirds by ``by`` (low, middle, high), with the mean of each column of ``cols``."""
    d = df.dropna(subset=[by])
    if len(d) < 6:
        return []
    ranks = d[by].rank(method="first")
    cuts = pd.qcut(ranks, 3, labels=["low", "middle", "high"])
    out = []
    for lab in ("low", "middle", "high"):
        part = d[cuts == lab]
        out.append({"disagreement": lab, "cases": int(len(part)), "range": (float(part[by].min()), float(part[by].max())),
                    **{k: (float(pd.to_numeric(part[c], errors="coerce").mean()) if c in part else math.nan)
                       for k, c in cols.items()}})
    return out


def _spearman(a: pd.Series, b: pd.Series) -> float | None:
    d = pd.concat([a, b], axis=1).dropna()
    if len(d) < 6 or d.iloc[:, 0].nunique() < 2 or d.iloc[:, 1].nunique() < 2:
        return None
    return float(d.iloc[:, 0].rank().corr(d.iloc[:, 1].rank()))


def analyse(cases: pd.DataFrame, members: pd.DataFrame, glayers: pd.DataFrame, member_info: list[dict]) -> dict:
    """The three questions on the scored cases (``cases``: the group's rows; ``members``: one row per member and
    case with ``composite``; ``glayers``: the group layers with ``confirmed``). Every number is a plain count or mean;
    the verdicts follow fixed rules (ADR-095)."""
    res: dict = {"cases": int(len(cases))}
    g = cases.set_index("case_id")["composite"]
    board = []
    for info in member_info:
        mc = members[members["agent_id"] == info["agent_id"]].set_index("case_id")["composite"]
        both = pd.concat([g, mc], axis=1, keys=["group", "agent"]).dropna()
        diff = both["group"] - both["agent"]
        board.append({"name": info["name"], "kind": info["group"], "agent_id": info["agent_id"], "cases": int(len(both)),
                      "agent": float(both["agent"].mean()) if len(both) else math.nan,
                      "group": float(both["group"].mean()) if len(both) else math.nan,
                      "group_wins": int((diff > 0.005).sum()), "group_losses": int((diff < -0.005).sum())})
    res["board"] = board
    res["group_mean"] = float(g.mean()) if len(g) else math.nan
    std = next((b for b in board if b["kind"] == "standard"), None)
    best = max((b for b in board if not math.isnan(b["agent"])), key=lambda b: b["agent"], default=None)
    res["standard"], res["best_member"] = std, best
    # 1. better than one agent?
    if std and std["cases"]:
        d = std["group"] - std["agent"]
        res["vs_standard"] = "better" if d > 0.005 and std["group_wins"] > std["group_losses"] else \
            "worse" if d < -0.005 and std["group_losses"] > std["group_wins"] else "about the same"
    if best and best["cases"]:
        d = best["group"] - best["agent"]
        res["vs_best"] = "better" if d > 0.005 else "worse" if d < -0.005 else "about the same"
    # 2. disagreement where they are wrong?
    c = cases[cases["status"] == "ok"].copy()
    c["abs_depth_error_m"] = pd.to_numeric(c.get("depth_error_m"), errors="coerce").abs()
    c["covered"] = c.get("depth_covered")
    res["depth_thirds"] = _thirds(c, "depth_spread_m", {"abs_error_m": "abs_depth_error_m", "covered": "covered",
                                                        "snow_depth": "snow_depth"})
    full = c[c["target_scope"] == "full_profile"].dropna(subset=["layer_structure"]) if "layer_structure" in c else c[:0]
    res["structure_thirds"] = _thirds(full, "structure_disagreement",
                                      {"layer_structure": "layer_structure", "critical_layers": "critical_layers"})
    res["rho_depth"] = _spearman(c["depth_spread_m"], c["abs_depth_error_m"])
    res["rho_structure"] = _spearman(full["structure_disagreement"], -full["layer_structure"]) if len(full) else None
    res["disagreement_useful"] = _useful(res["depth_thirds"], "abs_error_m", higher_is_worse=True), \
        _useful(res["structure_thirds"], "layer_structure", higher_is_worse=False)
    # 3. agreed weak layers really there?
    rel = []
    gl = glayers[glayers["confirmed"].notna()] if len(glayers) else glayers
    for kinds, label in ((PERSISTENT_KINDS, "weak layers"), (("crust",), "crusts")):
        part = gl[gl["kind"].isin(kinds)] if len(gl) else gl
        for lo, hi, name in SUPPORT_BINS:
            b = part[(part["support"] >= lo) & (part["support"] < hi)] if len(part) else part
            n = int(len(b))
            rel.append({"what": label, "agreement": name, "layers": n,
                        "in_the_pit": int(b["confirmed"].astype(bool).sum()) if n else 0,
                        "share": float(b["confirmed"].astype(bool).mean()) if n else math.nan})
    res["reliability"] = rel
    wl = [r for r in rel if r["what"] == "weak layers"]
    res["agreement_useful"] = _rises(wl)
    if "pit_weak_layers" in cases:
        res["pit_weak_layers"] = int(pd.to_numeric(cases["pit_weak_layers"], errors="coerce").fillna(0).sum())
        res["pit_weak_missed_by_all"] = int(pd.to_numeric(cases["pit_weak_missed_by_all"],
                                                          errors="coerce").fillna(0).sum())
    return res


def _useful(thirds: list[dict], col: str, higher_is_worse: bool) -> str:
    """'yes' when the most-disagreed third is clearly worse than the least (by a quarter of the low third's error,
    or 0.05 of a score) and the middle third lies between or with the worse end; 'weak' when only the ends differ that
    way; 'no' otherwise; 'too few cases' under 6."""
    if not thirds:
        return "too few cases"
    lo, mid, hi = (t[col] for t in thirds)
    if any(math.isnan(x) for x in (lo, hi)):
        return "too few cases"
    gap = (hi - lo) if higher_is_worse else (lo - hi)
    need = 0.25 * abs(lo) if higher_is_worse else 0.05
    if gap <= max(need, 1e-9):
        return "no"
    ordered = (lo <= mid <= hi) if higher_is_worse else (lo >= mid >= hi)
    return "yes" if ordered or math.isnan(mid) else "weak"


def _rises(rows: list[dict]) -> str:
    """'yes' when weak layers most agents forecast are found in the pits more often than those only a few forecast,
    by at least 10 points, with at least 5 layers in each of those two groups; 'too few' otherwise if either has
    fewer; else 'no'."""
    few, most = rows[0], rows[-1]
    if few["layers"] < 5 or most["layers"] < 5:
        return "too few"
    return "yes" if most["share"] - few["share"] >= 0.10 else "no"


# --------------------------------------------------------------------------------------------- the check


def checks_root(paths: LabPaths) -> Path:
    return paths.outputs / "group_checks"


def list_group_checks(paths: LabPaths) -> list[str]:
    root = checks_root(paths)
    if not root.is_dir():
        return []
    return [d.name for d in sorted(root.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
            if (d / "result.json").is_file()]


def load_group_check(paths: LabPaths, check_id: str) -> dict:
    d = checks_root(paths) / check_id
    res = json.loads((d / "result.json").read_text())
    res["cases_df"] = pd.read_parquet(d / "cases.parquet")
    res["layers_df"] = pd.read_parquet(d / "group_layers.parquet")
    res["members_df"] = pd.read_parquet(d / "member_scores.parquet")
    return res


def run_group_check(paths: LabPaths, cfg, test_run: str, member_keys: list[str] | None = None, workers: int = 1,
                    check_id: str | None = None, log: Callable[[str], None] = print) -> dict:
    """Score the group on ``test_run``'s locked test winters (see the module doc) and store the result under
    ``outputs/group_checks/<check id>/``: ``result.json``, ``cases.parquet`` (the group per case),
    ``group_layers.parquet`` and ``member_scores.parquet``. Saved predictions are reused; the rest are computed
    (and saved) as training would."""
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.competition.truth import TruthNotScorable, scoring_truth
    from snowagent.lab.training.cache import TrainingCache
    from snowagent.lab.training.evaluate import EvalContext, build_library, evaluate_population
    from snowagent.lab.training.loop import TrainOptions, prepare

    t0 = datetime.now(UTC)
    _, plan, _refs, refs = prepare(paths, cfg, TrainOptions.from_config(cfg), test_run, resume=True)
    seasons = list(plan.get("locked_seasons") or [])
    if not seasons or not refs:
        raise ValueError(f"training run {test_run} has no locked test winters")
    keys = member_keys or default_members(paths, seasons)
    members, notes = resolve_members(paths, keys, seasons, cfg.genome)
    for n in notes:
        log(f"note: {n}")
    log(f"group check on {len(refs)} cases of the locked winters {', '.join(seasons)} of {test_run}, "
        f"{len(members)} agents: " + "; ".join(m.name for m in members))
    ctx = EvalContext(paths=paths, case_set=plan["case_set"], seed=plan["seed"], weights=cfg.scoring_weights,
                      config_hash=cfg.config_hash(), engine=EngineSpec(**plan["engine"]),
                      library_file=build_library(paths, plan["case_set"], TrainingCache(paths.outputs / "cache"),
                                                 exclude_seasons=seasons))

    def progress(done: int, total: int) -> None:
        log(f"cases computed: {done}/{total}")

    genomes = [m.genome for m in members]
    ev = evaluate_population(refs, genomes, ctx, workers=workers, progress=progress)
    log(f"predictions: {ev.hits} saved ones reused, {ev.misses} computed ({ev.wall_s:.0f} s)")
    member_rows = ev.scores
    case_rows, layer_rows = [], []
    for ref in refs:
        try:
            truth = scoring_truth(ref.case_dir, ref.manifest).truth_profile
        except (TruthNotScorable, FileNotFoundError):
            continue
        preds = []
        for g in genomes:
            e = ctx.cache.get(ctx.key(g, ref))
            p = (e or {}).get("prediction")
            preds.append(SnowpackPrediction.model_validate(p) if p is not None else None)
        if sum(p is not None for p in preds) == 0:
            continue
        row, lrows = case_view(ref.manifest, truth, preds, cfg.scoring_weights)
        case_rows.append(row)
        layer_rows += lrows
    cases = pd.DataFrame(case_rows)
    glayers = pd.DataFrame(layer_rows) if layer_rows else pd.DataFrame(columns=LAYER_COLUMNS)
    info = [m.record() for m in members]
    res = analyse(cases, member_rows, glayers, info)
    check_id = check_id or new_run_id("group-check", salt=json.dumps([test_run, keys]))
    d = checks_root(paths) / check_id
    d.mkdir(parents=True, exist_ok=True)
    cases.to_parquet(d / "cases.parquet", index=False)
    glayers.astype({"confirmed": "object"}).to_parquet(d / "group_layers.parquet", index=False)
    keep = [c for c in ("case_id", "agent_id", "label", "family", "season", "site_code", "status", "composite",
                        "snow_depth", "layer_structure", "critical_layers", "uncertainty", "depth_error_m")
            if c in member_rows]
    member_rows[keep].to_parquet(d / "member_scores.parquet", index=False)
    out = {"check_id": check_id, "check_version": CHECK_VERSION, "test_run": test_run, "seasons": seasons,
           "scoring_version": scoring.SCORING_VERSION, "members": info, "member_keys": keys, "notes": notes,
           "created_at": t0.isoformat(timespec="seconds"),
           "runtime_s": round((datetime.now(UTC) - t0).total_seconds(), 1), "evaluation": ev.summary(),
           "label": LAB_DISCLAIMER, **res}
    (d / "result.json").write_text(json.dumps(_clean(out), indent=1, default=_json_default))
    log(f"group check {check_id} written to {d}")
    return out


def _clean(o):
    """NaN as null, so result.json is strict JSON."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating)) and math.isnan(float(o)):
        return None
    return o


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if math.isnan(float(o)) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, tuple):
        return list(o)
    return str(o)
