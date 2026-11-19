"""Stability check (ADR-127): do a model's stability numbers point at the layers that broke in the pits' stability
tests?

SNOWPACK already writes three stability numbers for every layer of its profile files (lab template
``terrain_column.ini``, ``OUT_STAB = TRUE``): SK38 (code 0533, the skier stability index: layer strength over the
stress a skier adds on a 38 degree slope; low = weak), the critical crack length (0606, metres; short = a crack
spreads easily) and RTA (0607, the relative threshold sum approach: 0 to 1, high = many signs of a weak layer).
The agents' engine reader keeps none of them. This check runs the engine again for every case whose withheld pit
holds a stability test that broke at a known depth, reads those three numbers at the valid time, adds a fourth,
the "lemons" count of the model's own layers (McCammon and Schweizer 2002: six signs of a weak layer), and asks for
each number:

1. **Weakest spot.** Does the layer the number calls weakest lie within ``TOL_M`` of a layer that broke? Compared
   with chance: the share of the column that lies that close to a broken layer (what a pick at a random depth
   would score).
2. **Flags.** Of the layers that broke, what share does the number flag as weak (SK38 <= 1, crack length <= 0.4 m,
   RTA >= 0.8, 5 or more lemons), and what share of the rest of the column does it flag too?

Both for every layer that broke, and again for the layers that broke easily and suddenly (a compression test in 20
taps or fewer with a sudden planar, collapse or pop break; an extended column test or propagation saw test that
propagated), which are the ones linked to avalanches.

Choices (ADR-127): model depths are stretched to the pit's measured snow depth (the question is where in the column,
not how deep the snow is; the depth score covers that); the column looked at ends at ``COLUMN_M`` or just below the
deepest break, whichever is deeper (compression test columns rarely go deeper); the locked test winters are left out
unless asked. Nothing here is tuned: the thresholds are the published ones, fixed before any result.

The module sits in ``lab/services`` (outside the prediction-code hash, ``training.cache.CODE_EXCLUDE``), so adding
it re-runs no forecast and rebuilds no case. It reads the withheld pit's tests and depth from the lab's own tables:
the evaluator's side, never shown to an agent. Decision support only, not an avalanche forecast.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from snowagent.lab import LAB_DISCLAIMER
from snowagent.lab.agents.snowpack import VisiblePackageEngine
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.storage.provenance import new_run_id

CHECK_VERSION = "lab-stability-check-1"
TOL_M = 0.10  # a model layer this close (after stretching) to a break counts as the broken layer
COLUMN_M = 1.2  # compression test columns rarely go deeper; the column looked at ends here or below the deepest break
EASY_TAPS = 20  # a compression test break in this many taps or fewer is "easy" (easy and moderate ratings)
SUDDEN = {"SP", "SC", "PC", "Q1"}  # sudden planar, sudden collapse, pop, quality 1 (CAA OGRS)
RESISTANT = {"RP", "BRK", "PC/RP", "Q2", "Q3", "SDN", "RES"}  # sudden pop is PC; resistant planar, break, quality 2-3
TYPE_ALIASES = {"COMPRESSION": "CT", "EXTENDEDCOLUMN": "ECT", "DEEPTAP": "DT", "PROPAGATIONSAW": "PST",
                "RUTSCHBLOCK": "RB", "SHOVEL": "ST", "SHOVELSHEAR": "ST"}
TAP_TESTS = ("CT", "DT")
BOOTSTRAP = 2000
LOCKED_SEASONS = 3  # the training loop's locked test winters (``training.loop.LOCKED_SEASONS``): the newest three

# the numbers looked at: name -> (words, which way is weak, published threshold for "weak")
INDICATORS: dict[str, tuple[str, str, float]] = {
    "sk38": ("SK38 (skier stability index)", "low", 1.0),
    "crack": ("critical crack length", "low", 0.4),
    "rta": ("RTA (structural index)", "high", 0.8),
    "lemons": ("lemons count (six weak-layer signs)", "high", 5.0),
}
PERSISTENT_CLASSES = {4, 5, 6, 9}  # SNOWPACK grain classes (Swiss code, first digit): FC, DH, SH, FCxr


# --------------------------------------------------------------------------------------------- the pits' tests


@dataclass
class Break:
    """One layer that broke in a pit's stability test."""

    depth_m: float  # from the surface
    kind: str  # CT, ECT, DT, PST, RB, ST
    taps: float | None
    sudden: bool | None  # None: break type not recorded
    easy_sudden: bool

    def record(self) -> dict:
        return {"depth_m": round(self.depth_m, 3), "kind": self.kind, "taps": self.taps, "sudden": self.sudden,
                "easy_sudden": self.easy_sudden}


def _upper(x) -> str:
    return re.sub(r"\s+", "", str(x or "")).upper()


def parse_test(payload: dict) -> Break | None:
    """A stability-test observation's payload -> the break it records, or None (no break, no depth, no result).

    The SnowPro 3.x files keep a compression test's result only in the raw text ("Compression H - Hard 21 SC");
    the pit reader leaves ``result`` empty for them, so it is read from that text here (ADR-127)."""
    kind = TYPE_ALIASES.get(_upper(payload.get("type")), _upper(payload.get("type")))
    raw = str(payload.get("raw") or "")
    result = _upper(payload.get("result"))
    taps = payload.get("score")
    fc = _upper(payload.get("fracture_character")) or None
    m = re.match(r"\s*Compression\s+([NEMHV])\b\s*(?:-\s*[A-Za-z ]+?)?\s*(\d+)?\b", raw)
    if not result and m and kind in ("CT", ""):
        kind = "CT"
        result = "CTN" if m.group(1) == "N" else f"CT{m.group(1)}{m.group(2) or ''}"
        if taps is None and m.group(2):
            taps = float(m.group(2))
        if fc is None:
            f = re.search(r"\b(SP|SC|PC|RP|BRK|Q1|Q2|Q3)\b", raw.upper())
            fc = f.group(1) if f else None
    depth = payload.get("depth_m")
    if not result or depth is None or not math.isfinite(float(depth)) or float(depth) < 0:
        return None
    if kind in TAP_TESTS:
        if result.startswith(("CTN", "DTN", "CTX")):
            return None  # no break
        if result.startswith(("CTV", "DTV")):
            taps = 0.0  # broke while the column was cut: weaker than one tap
        if taps is None:
            t = re.search(r"(\d+)", result)
            taps = float(t.group(1)) if t else None
        sudden = True if fc in SUDDEN else False if fc in RESISTANT else None
        easy = taps is not None and taps <= EASY_TAPS
        return Break(float(depth), kind, taps, sudden, bool(sudden and easy))
    if kind == "ECT":
        if "ECTX" in result:
            return None
        prop = "ECTP" in result
        return Break(float(depth), kind, taps, prop, prop)
    if kind == "PST":
        prop = "END" in result
        return Break(float(depth), kind, None, prop, prop)
    if kind in ("RB", "ST"):
        return Break(float(depth), kind, taps, None, False)
    return None


def pit_breaks(payloads: list[dict]) -> list[Break]:
    """A pit's breaks, one per depth (tests within 2 cm of each other broke the same layer; the record keeps the
    most telling: easy and sudden if any test says so)."""
    out: list[Break] = []
    for b in sorted((x for x in map(parse_test, payloads) if x is not None), key=lambda x: x.depth_m):
        if out and abs(out[-1].depth_m - b.depth_m) <= 0.02:
            o = out[-1]
            out[-1] = Break(o.depth_m, o.kind, o.taps if o.taps is not None else b.taps,
                            o.sudden if b.sudden is None else bool(o.sudden) or b.sudden,
                            o.easy_sudden or b.easy_sudden)
        else:
            out.append(b)
    return out


def tests_by_profile(paths: LabPaths) -> dict[str, list[dict]]:
    """The lab's stability-test payloads by profile id (evaluator side: the withheld pits' tests too)."""
    from snowagent.lab.storage.tables import read_table

    obs = read_table(paths.observations)
    out: dict[str, list[dict]] = {}
    if not len(obs):
        return out
    for r in obs[obs["observation_type"] == "stability_test"].to_dict("records"):
        out.setdefault(r["profile_id"], []).append(json.loads(r.get("payload_json") or "{}"))
    return out


# --------------------------------------------------------------------------------------------- the model's numbers

PRO_STABILITY = {"0501": "height_cm", "0512": "grain_size_mm", "0513": "grain_type", "0534": "hardness",
                 "0533": "sk38", "0606": "crack", "0607": "rta"}


def read_stability(pro: Path, valid: pd.Timestamp) -> dict:
    """The profile of a SNOWPACK ``.pro`` file nearest ``valid``, as layers from the surface down: depths (m),
    grain size, grain class, hand hardness and the three stability numbers (None where the engine wrote -999)."""
    blocks: list[tuple[pd.Timestamp, dict]] = []
    in_data = False
    with open(pro) as fh:
        for raw in fh:
            line = raw.strip()
            if line.startswith("["):
                in_data = line == "[DATA]"
                continue
            if not in_data or not line:
                continue
            code, _, rest = line.partition(",")
            if code == "0500":
                t = pd.Timestamp(datetime.strptime(rest.strip(), "%d.%m.%Y %H:%M:%S")).tz_localize("UTC")
                blocks.append((t, {}))
            elif blocks and code in PRO_STABILITY:
                blocks[-1][1][PRO_STABILITY[code]] = rest.split(",")[1:]
    if not blocks:
        raise ValueError(f"no profiles in {pro}")
    t, d = min(blocks, key=lambda b: abs((b[0] - valid).total_seconds()))
    h = [float(x) for x in d.get("height_cm", [])]
    n = len(h)

    def col(name: str) -> list[float | None]:
        vals = d.get(name) or []
        out = []
        for i in range(n):
            try:
                v = float(vals[i])
            except (IndexError, ValueError):
                v = None
            out.append(None if v is None or v <= -999 or not math.isfinite(v) else v)
        return out

    hs = h[-1] / 100.0 if n else 0.0
    gs, gt, hd, sk, rc, rta = (col(c) for c in ("grain_size_mm", "grain_type", "hardness", "sk38", "crack", "rta"))
    layers = []
    for i in range(n - 1, -1, -1):  # the file lists elements from the ground up
        bottom_h = h[i - 1] if i else 0.0
        if h[i] <= 0 or h[i] <= bottom_h:
            continue
        layers.append({"top_m": round(hs - h[i] / 100.0, 4), "bottom_m": round(hs - max(bottom_h, 0.0) / 100.0, 4),
                       "grain_size_mm": gs[i], "grain_class": int(gt[i] // 100) if gt[i] else None,
                       "hardness": abs(hd[i]) if hd[i] is not None else None, "sk38": sk[i], "crack": rc[i],
                       "rta": rta[i]})
    return {"snow_depth_m": hs, "profile_time": t.isoformat(), "layers": layers}


def add_lemons(layers: list[dict]) -> None:
    """The lemons count of each model layer (McCammon and Schweizer 2002), from its own grain class, grain size,
    hardness and depth and the differences with the layer above and below (the larger): persistent grains, grain
    size >= 1.25 mm, hardness <= 1.3 (F+), grain size difference >= 0.75 mm, hardness difference >= 1.7, top between
    0.18 and 0.94 m deep. A sign the layer has no value for does not count."""
    for i, ly in enumerate(layers):
        nb = [layers[j] for j in (i - 1, i + 1) if 0 <= j < len(layers)]

        def diff(key: str, ly=ly, nb=nb) -> float:
            v = ly.get(key)
            return max((abs(v - o[key]) for o in nb if v is not None and o.get(key) is not None), default=0.0)

        gs, hd = ly.get("grain_size_mm"), ly.get("hardness")
        ly["lemons"] = float(sum([
            ly.get("grain_class") in PERSISTENT_CLASSES,
            gs is not None and gs >= 1.25,
            hd is not None and hd <= 1.3,
            diff("grain_size_mm") >= 0.75,
            diff("hardness") >= 1.7,
            0.18 <= ly["top_m"] <= 0.94,
        ]))


class StabilityEngine(VisiblePackageEngine):
    """The agents' SNOWPACK run (``VisiblePackageEngine``, unchanged) that also reads the stability numbers of its
    final profile, before the run directory is removed (``stability`` after each ``simulate``)."""

    stability: dict | None = None

    def _profile(self, out, valid: pd.Timestamp):
        self.stability = read_stability(Path(out.pro), valid)
        return super()._profile(out, valid)


# --------------------------------------------------------------------------------------------- scoring one pit


def _weak(name: str, v: float | None) -> bool:
    if v is None:
        return False
    _w, way, thr = INDICATORS[name]
    return v <= thr if way == "low" else v >= thr


def _near(ly: dict, d: float) -> bool:
    return ly["top_m"] - TOL_M <= d <= ly["bottom_m"] + TOL_M


def score_pit(model: dict, breaks: list[Break], pit_hs_m: float | None) -> dict:
    """One model profile against one pit's breaks: per number, for all breaks and for the easy, sudden ones, whether
    its weakest spot is at a break (``hit``: a share when several layers tie), the chance of that for a random depth
    (``chance``), how many breaks it flags and what share of the rest of the column it flags."""
    layers = [dict(x) for x in model["layers"]]
    hs = float(model.get("snow_depth_m") or 0.0)
    stretch = pit_hs_m / hs if pit_hs_m and hs > 0.05 else 1.0
    for ly in layers:
        ly["top_m"], ly["bottom_m"] = ly["top_m"] * stretch, ly["bottom_m"] * stretch
    add_lemons(layers)  # after stretching: its depth sign is on the pit's scale
    out: dict = {"stretch": round(stretch, 3), "model_layers": len(layers)}
    for subset, targets in (("all", breaks), ("easy_sudden", [b for b in breaks if b.easy_sudden])):
        if not targets:
            continue
        floor = max(COLUMN_M, max(b.depth_m for b in targets) + TOL_M)
        col = [ly for ly in layers if ly["top_m"] < floor and ly["bottom_m"] > ly["top_m"]]
        if not col:
            continue
        thick = np.array([min(ly["bottom_m"], floor) - ly["top_m"] for ly in col])
        total = float(thick.sum())
        near = np.array([any(_near(ly, b.depth_m) for b in targets) for ly in col])
        res: dict = {"breaks": len(targets), "chance": float(thick[near].sum() / total) if total > 0 else 0.0}
        for name, (_w, way, _t) in INDICATORS.items():
            vals = [ly.get(name) for ly in col]
            have = [i for i, v in enumerate(vals) if v is not None]
            if not have:
                continue
            best = min(vals[i] for i in have) if way == "low" else max(vals[i] for i in have)
            tied = [i for i in have if vals[i] == best]
            hit = float(np.average(near[tied], weights=thick[tied])) if thick[tied].sum() > 0 else float(near[tied[0]])
            found = sum(any(_near(col[i], b.depth_m) and _weak(name, vals[i]) for i in have) for b in targets)
            rest = [i for i in have if not near[i]]
            rest_t = float(thick[rest].sum())
            flagged = float(sum(thick[i] for i in rest if _weak(name, vals[i])) / rest_t) if rest_t > 0 else 0.0
            res[name] = {"hit": hit, "found": int(found), "false_flag_share": flagged,
                         "weakest_depth_m": round(float(np.mean([col[i]["top_m"] for i in tied])), 3)}
        out[subset] = res
    return out


# --------------------------------------------------------------------------------------------- the agents


@dataclass
class Agent:
    key: str  # "standard", a standard model's name, or "<training run>/<round>/<rank>"
    name: str
    genome: object  # AgentGenome

    def record(self) -> dict:
        return {"key": self.key, "name": self.name, "genome_hash": self.genome.genome_hash,
                "family": self.genome.family.value}


def resolve_agent(paths: LabPaths, key: str, spec=None) -> Agent:
    """An agent whose SNOWPACK runs are read: ``standard`` (the SNOWPACK model as it stands), a standard model by
    name (``genome.NAMED_MODELS``) or ``<training run>/<round>/<rank>``. Only agents that run SNOWPACK (the
    snowpack and hybrid families) have stability numbers."""
    from snowagent.lab.genome import NAMED_MODELS, default_genome, named_genome, upgrade_genome
    from snowagent.lab.schemas.genome import AgentFamily, AgentGenome
    from snowagent.lab.services.names import nickname

    if key == "standard":
        return Agent(key, "standard SNOWPACK", default_genome(AgentFamily.snowpack, spec))
    if key in NAMED_MODELS:
        return Agent(key, key, named_genome(key, spec))
    from snowagent.lab.training.loop import load_round, training_root

    parts = key.strip("/").split("/")
    if len(parts) != 3:
        raise ValueError(f"agent {key!r}: standard, a standard model's name or <training run>/<round>/<rank>")
    run_id, rnd, rank = parts[0], int(parts[1]), int(parts[2])
    run_dir = training_root(paths) / run_id
    if not (run_dir / "run.json").is_file():
        raise ValueError(f"no training run {run_id}")
    rd = load_round(run_dir, rnd)
    ranked = rd["leaderboard"]["ranked"]
    if not 1 <= rank <= len(ranked):
        raise ValueError(f"round {rnd} of {run_id} has ranks 1-{len(ranked)}")
    h = ranked[rank - 1]["genome_hash"]
    old = next(AgentGenome.model_validate(x["genome"]) for x in rd["population"]
               if AgentGenome.model_validate(x["genome"]).genome_hash == h)
    g = upgrade_genome(old, spec)
    if g.family not in (AgentFamily.snowpack, AgentFamily.hybrid):
        raise ValueError(f"{nickname(old.genome_hash, old.label)} does not run SNOWPACK, so it has no stability "
                         "numbers")
    return Agent(key, nickname(old.genome_hash, old.label), g)


def agent_choices(paths: LabPaths, spec=None, runs: int = 6, per_run: int = 3,
                  label: Callable[[str], str] | None = None) -> dict[str, str]:
    """Agent key -> how the app names it: standard SNOWPACK, the standard models, and the top ``per_run`` agents of
    the last finished round of the newest ``runs`` training runs that have SNOWPACK agents. Promotion-check folds
    are not training runs of their own and are left out (``training.real_runs``); the same agent offered twice (the
    standard genome kept in a run, a survivor of several runs) is offered once, under its first name."""
    from snowagent.lab.genome import NAMED_MODELS
    from snowagent.lab.services.training import real_runs
    from snowagent.lab.training.loop import committed_rounds, training_root

    out = {"standard": "standard SNOWPACK"} | {k: f"standard model: {k}" for k in NAMED_MODELS}
    seen = {resolve_agent(paths, k, spec).genome.genome_hash for k in out}
    used = 0
    for run_id in real_runs(paths):
        if used >= runs:
            break
        rounds = committed_rounds(training_root(paths) / run_id)
        if not rounds:
            continue
        found = False
        for k in range(1, per_run + 1):
            key = f"{run_id}/{rounds[-1]}/{k}"
            try:
                a = resolve_agent(paths, key, spec)
            except (ValueError, KeyError, IndexError, StopIteration, FileNotFoundError):
                continue
            found = True
            if a.genome.genome_hash in seen:
                continue
            seen.add(a.genome.genome_hash)
            when = label(run_id) if label else run_id
            out[key] = f"{a.name} (number {k} of the run of {when})"
        used += found
    return out


def engine_profile(case_dir: Path, genes: dict, work_dir: Path | None = None, segments_root: Path | None = None,
                   segments_context: str = "") -> dict:
    """One SNOWPACK run of a case with these physics genes (the agents' own run), and its stability profile."""
    from snowagent.lab.agents.physics import engine_physics
    from snowagent.lab.agents.segments import SegmentStore
    from snowagent.lab.benchmark.loader import load_visible_case

    case = load_visible_case(case_dir)
    seg = SegmentStore(segments_root, segments_context) if segments_root else None
    eng = StabilityEngine(work_dir=work_dir, segments=seg)
    phys = engine_physics(genes, str(getattr(case.site_code, "value", case.site_code)))
    eng.simulate(case, phys)
    if eng.stability is None:
        raise ValueError("the run wrote no profile")
    return eng.stability


Profiler = Callable[[Path, dict], dict]  # (case dir, physics genes) -> stability profile (tests pass a fake)


def _task(args: tuple) -> tuple[str, str, dict]:
    agent_key, case_id, case_dir, genes, out_file, work_dir, seg_root, seg_ctx = args
    try:
        prof = engine_profile(Path(case_dir), genes, Path(work_dir), Path(seg_root), seg_ctx)
        rec = {"ok": True, "profile": prof}
    except Exception as exc:  # noqa: BLE001 - one failed run is reported, never the end of the check
        rec = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:400]}
    Path(out_file).write_text(json.dumps(rec))
    return agent_key, case_id, rec


# --------------------------------------------------------------------------------------------- the check


def checks_root(paths: LabPaths) -> Path:
    return paths.outputs / "stability_checks"


def _slug(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", key)[:120]


def eligible_cases(paths: LabPaths, include_locked: bool = False, plots: list[str] | None = None,
                   limit: int | None = None) -> tuple[list[dict], list[str]]:
    """The cases of the ``all`` set whose withheld pit has a break at a known depth, and the locked winters left
    out. Each: case id and dir, plot, winter, case type, the pit's depth and its breaks."""
    from snowagent.lab.benchmark.loader import case_dirs, read_manifest
    from snowagent.lab.storage.tables import read_table

    tests = tests_by_profile(paths)
    prof = read_table(paths.profiles, columns=["profile_id", "snow_depth_m"])
    hs = dict(zip(prof["profile_id"], prof["snow_depth_m"], strict=False))
    rows = []
    for d in case_dirs(paths, "all"):
        m = read_manifest(d)
        site = str(getattr(m.site_code, "value", m.site_code))
        if plots and site not in plots:
            continue
        payloads = [p for pid in [m.target_profile_id, *m.target_copies] for p in tests.get(pid, [])]
        breaks = pit_breaks(payloads)
        if not breaks:
            continue
        h = hs.get(m.target_profile_id)
        rows.append({"case_id": m.case_id, "case_dir": str(d), "site": site, "season": m.season,
                     "case_type": m.case_type.value, "pit_hs_m": float(h) if h is not None and h == h else None,
                     "breaks": [b.record() for b in breaks]})
    seasons = sorted({r["season"] for r in rows} | {read_manifest(d).season for d in case_dirs(paths, "all")})
    locked = [] if include_locked else seasons[-LOCKED_SEASONS:] if len(seasons) >= LOCKED_SEASONS + 2 else []
    rows = [r for r in rows if r["season"] not in locked]
    rows.sort(key=lambda r: (r["season"], r["site"], r["case_id"]))
    return (rows[:limit] if limit else rows), locked


def _breaks(rec: list[dict]) -> list[Break]:
    return [Break(**b) for b in rec]


def _rate(rows: list[dict], subset: str, name: str) -> dict | None:
    sel = [r for r in rows if subset in r["score"] and name in r["score"][subset]]
    if not sel:
        return None
    hit = float(np.mean([r["score"][subset][name]["hit"] for r in sel]))
    chance = float(np.mean([r["score"][subset]["chance"] for r in sel]))
    nb = sum(r["score"][subset]["breaks"] for r in sel)
    found = sum(r["score"][subset][name]["found"] for r in sel)
    ff = float(np.mean([r["score"][subset][name]["false_flag_share"] for r in sel]))
    return {"pits": len(sel), "hit": hit, "chance": chance, "breaks": nb, "found_share": found / nb if nb else None,
            "false_flag_share": ff}


def _by_winter_range(rows: list[dict], subset: str, name: str, seed: int = 0) -> tuple[float, float] | None:
    """90% range of (hit rate - chance) from resampling whole winters (a winter's pits are not independent)."""
    sel = [r for r in rows if subset in r["score"] and name in r["score"][subset]]
    winters = sorted({r["season"] for r in sel})
    if len(winters) < 3:
        return None
    per = {w: np.array([r["score"][subset][name]["hit"] - r["score"][subset]["chance"] for r in sel
                        if r["season"] == w]) for w in winters}
    rng = np.random.default_rng(seed)
    stats = []
    for _ in range(BOOTSTRAP):
        pick = rng.choice(winters, size=len(winters), replace=True)
        stats.append(float(np.concatenate([per[w] for w in pick]).mean()))
    lo, hi = np.percentile(stats, [5, 95])
    return float(lo), float(hi)


def summarise(rows: list[dict], agents: list[Agent]) -> dict:
    """Pooled answers per agent, number and subset; per plot for the weakest-spot hit rate."""
    out: dict = {}
    for a in agents:
        mine = [r for r in rows if r["agent"] == a.key and r.get("score")]
        res: dict = {"pits": len({r["case_id"] for r in mine}), "subsets": {}, "by_plot": {}}
        for subset in ("all", "easy_sudden"):
            res["subsets"][subset] = {}
            for name in INDICATORS:
                rate = _rate(mine, subset, name)
                if rate is not None:
                    rate["range90"] = _by_winter_range(mine, subset, name)
                    res["subsets"][subset][name] = rate
        for site in sorted({r["site"] for r in mine}):
            res["by_plot"][site] = {name: _rate([r for r in mine if r["site"] == site], "all", name)
                                    for name in INDICATORS}
        out[a.key] = res
    return out


def run_stability_check(paths: LabPaths, cfg, agent_keys: list[str] | None = None, *, workers: int = 1,
                        include_locked: bool = False, plots: list[str] | None = None, limit: int | None = None,
                        check_id: str | None = None, log: Callable[[str], None] = print,
                        profiler: Profiler | None = None) -> dict:
    """Run (or resume) a stability check and write ``result.json`` and ``report.md`` under its folder. Each run of
    an agent on a case is saved as it finishes, so the same check id resumes where it stopped. ``profiler`` replaces
    the SNOWPACK run (tests)."""
    from snowagent.lab.agents.segments import SegmentStore
    from snowagent.lab.training.cache import code_hash, engine_files_hash, sha

    started = datetime.now(UTC)
    keys = agent_keys or ["standard"]
    agents = [resolve_agent(paths, k, cfg.genome) for k in keys]
    check_id = check_id or new_run_id("stability-check", salt=json.dumps(keys))
    cdir = checks_root(paths) / check_id
    cases, locked = eligible_cases(paths, include_locked, plots, limit)
    if not cases:
        raise ValueError("no case's withheld pit has a stability test that broke at a known depth (build the "
                         "benchmark cases first)")
    plan = {"check_version": CHECK_VERSION, "check_id": check_id, "agents": [a.record() for a in agents],
            "cases": len(cases), "locked_seasons_left_out": locked, "plots": plots, "limit": limit,
            "tolerance_m": TOL_M, "column_m": COLUMN_M, "easy_taps": EASY_TAPS,
            "thresholds": {k: v[2] for k, v in INDICATORS.items()}, "started_utc": started.isoformat()}
    (cdir / "runs").mkdir(parents=True, exist_ok=True)
    (cdir / "plan.json").write_text(json.dumps(plan, indent=1))
    log(f"stability check {check_id}: {len(cases)} pits x {len(agents)} agents"
        + (f"; locked winters left out: {', '.join(locked)}" if locked else ""))
    seg_root = cdir / "segments"
    seg_ctx = sha({"code": code_hash(), "files": engine_files_hash(), "check": CHECK_VERSION})
    work = cdir / "work"
    work.mkdir(exist_ok=True)
    todo, done = [], {}
    for a in agents:
        adir = cdir / "runs" / _slug(a.key)
        adir.mkdir(exist_ok=True)
        for c in cases:
            f = adir / f"{c['case_id']}.json"
            if f.is_file():
                try:
                    done[(a.key, c["case_id"])] = json.loads(f.read_text())
                    continue
                except json.JSONDecodeError:
                    pass
            todo.append((a.key, c["case_id"], c["case_dir"], dict(a.genome.genes), str(f), str(work),
                         str(seg_root), seg_ctx))
    if done:
        log(f"{len(done)} runs kept from before; {len(todo)} to go")
    t0 = datetime.now(UTC)
    n = 0

    def note(key: str, cid: str, rec: dict) -> None:
        nonlocal n
        n += 1
        done[(key, cid)] = rec
        if n == 1 or n % 10 == 0 or n == len(todo):
            el = (datetime.now(UTC) - t0).total_seconds()
            left = el / n * (len(todo) - n)
            log(f"{n}/{len(todo)} runs done ({el / 60:.0f} min so far, about {left / 60:.0f} min left)"
                + ("" if rec["ok"] else f"; failed: {rec['error'][:120]}"))

    if profiler is not None or workers <= 1:
        for t in todo:
            if profiler is not None:
                try:
                    rec = {"ok": True, "profile": profiler(Path(t[2]), t[3])}
                except Exception as exc:  # noqa: BLE001
                    rec = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:400]}
                Path(t[4]).write_text(json.dumps(rec))
                note(t[0], t[1], rec)
            else:
                note(*_task(t))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for fut in as_completed([pool.submit(_task, t) for t in todo]):
                note(*fut.result())
    SegmentStore(seg_root).clear()
    rows = []
    for a in agents:
        for c in cases:
            rec = done.get((a.key, c["case_id"])) or {"ok": False, "error": "not run"}
            row = {k: c[k] for k in ("case_id", "site", "season", "case_type", "pit_hs_m")} | {"agent": a.key}
            if rec.get("ok"):
                row["score"] = score_pit(rec["profile"], _breaks(c["breaks"]), c["pit_hs_m"])
            else:
                row["error"] = rec.get("error")
            rows.append(row)
    failed = sum(1 for r in rows if "error" in r)
    result = plan | {"finished_utc": datetime.now(UTC).isoformat(), "work_s": (datetime.now(UTC) - started)
                     .total_seconds(), "failed_runs": failed, "summary": summarise(rows, agents),
                     "winters": sorted({c["season"] for c in cases}),
                     "breaks": sum(len(c["breaks"]) for c in cases),
                     "easy_sudden_breaks": sum(b["easy_sudden"] for c in cases for b in c["breaks"]),
                     "label": LAB_DISCLAIMER}
    (cdir / "rows.json").write_text(json.dumps(rows))
    (cdir / "result.json").write_text(json.dumps(result, indent=1, default=str))
    (cdir / "report.md").write_text("\n".join(report_lines(result)) + "\n")
    import shutil

    shutil.rmtree(work, ignore_errors=True)
    log(f"done: {len(rows) - failed} runs scored, {failed} failed; report {cdir / 'report.md'}")
    return result


def list_checks(paths: LabPaths) -> list[str]:
    root = checks_root(paths)
    if not root.is_dir():
        return []
    return sorted((d.name for d in root.iterdir() if (d / "result.json").is_file()), reverse=True)


def load_check(paths: LabPaths, check_id: str) -> dict:
    return json.loads((checks_root(paths) / check_id / "result.json").read_text())


# --------------------------------------------------------------------------------------------- plain words


def pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.0f}%"


def verdict(rate: dict) -> str:
    """Whether a number's weakest spot finds the broken layer more often than chance, from the winter resampling."""
    rng = rate.get("range90")
    if rng is None:
        return "too few winters to tell"
    lo, hi = rng
    if lo > 0:
        return "better than chance"
    if hi < 0:
        return "worse than chance"
    return "no better than chance"


def report_lines(result: dict) -> list[str]:
    """The check in plain words for a person who does not read the code (ADR-127)."""
    from snowagent.lab.services.activity import minutes

    names = {a["key"]: a["name"] for a in result["agents"]}
    out = [f"# Stability check {result['check_id']}", "", f"_{LAB_DISCLAIMER}_", "",
           f"Pits: {result['cases']} forecasts of pits with a stability test that broke at a known depth "
           f"({result['breaks']} broken layers, {result['easy_sudden_breaks']} of them easy and sudden), "
           f"winters {result['winters'][0]} to {result['winters'][-1]}."]
    if result.get("locked_seasons_left_out"):
        out.append(f"The locked test winters ({', '.join(result['locked_seasons_left_out'])}) were left out, so "
                   "they remain a clean final test.")
    out += ["", "**How to read it.** For each pit we look at the layer each number calls weakest. If it is within "
            f"{int(TOL_M * 100)} cm of a layer that broke in the pit's test, that is a hit. \"Chance\" is how often a "
            "pick at a random depth would hit, because a pit with many broken layers is easy to hit by luck. "
            "\"Found\" is the share of broken layers the number flags as weak by its published threshold, and "
            "\"also flags\" is the share of the rest of the column it flags too (lower is better).", ""]
    for key, res in result["summary"].items():
        out += [f"## {names.get(key, key)}", ""]
        for subset, title in (("all", "Every layer that broke"),
                              ("easy_sudden", "Layers that broke easily and suddenly")):
            rates = res["subsets"].get(subset) or {}
            if not rates:
                continue
            some = next(iter(rates.values()))
            out += [f"**{title}** ({some['pits']} pits, {some['breaks']} layers)", "",
                    "| Number | Weakest spot hits | Chance | Verdict | Found | Also flags |",
                    "| --- | --- | --- | --- | --- | --- |"]
            for name, r in rates.items():
                out.append(f"| {INDICATORS[name][0]} | {pct(r['hit'])} | {pct(r['chance'])} | {verdict(r)} | "
                           f"{pct(r['found_share'])} | {pct(r['false_flag_share'])} |")
            out.append("")
        if res["by_plot"]:
            out += ["**Weakest spot hits by plot** (every layer that broke)", "",
                    "| Plot | Pits | " + " | ".join(INDICATORS[n][0] for n in INDICATORS) + " | Chance |",
                    "| --- | --- | " + " | ".join("---" for _ in INDICATORS) + " | --- |"]
            for site, rates in res["by_plot"].items():
                first = next((r for r in rates.values() if r), None)
                if first is None:
                    continue
                out.append(f"| {site} | {first['pits']} | "
                           + " | ".join(pct(r['hit']) if r else "-" for r in rates.values())
                           + f" | {pct(first['chance'])} |")
            out.append("")
    if result.get("failed_runs"):
        out.append(f"{result['failed_runs']} SNOWPACK runs failed and are left out (their errors are in rows.json).")
    if result.get("work_s") is not None:
        out.append(f"Computing time: {minutes(result['work_s'])}.")
    return out


__all__ = ["INDICATORS", "Break", "StabilityEngine", "add_lemons", "agent_choices", "eligible_cases", "list_checks", "load_check",
           "parse_test", "pit_breaks", "read_stability", "report_lines", "resolve_agent", "run_stability_check",
           "score_pit", "summarise", "verdict"]
