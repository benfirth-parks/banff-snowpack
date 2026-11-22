"""How agents' settings change as a training run evolves them (ADR-133), for the app's "How agents change" page.

Everything here reads the files a run already writes; nothing is changed and no score is computed:

- ``run_rounds``: every finished round's agents (``rounds/rNN/population.json``) with their full settings, role,
  how each was made and its place on the round's leaderboard; while a run is going, the agents of the round being
  tested now come from the run's event log (``round_started``), unscored.
- ``gene_info``: a family's settings with a plain name, the topic they belong to, range, standard value and whether
  this run may change them (a trimmed run changes only its short list, ADR-123; a switch never changes, ADR-128).
- ``shift``: how far one value sits from the standard value, as a share of the setting's allowed range (the unit
  the drift penalty of ADR-089 counts in), so settings in different units can be drawn on one scale.
- ``drift_table`` / ``value_history``: those shifts round by round for the round's best agent, and one setting's
  values across every agent of every round.
- ``family_tree``: an agent's ancestors inside the run, each with how it was made and what that step changed.
- ``weights_in_force``: the score weights the run was ranked with (frozen into its plan) next to today's.
- ``import_run_archive``: unpack a run's ``.tar.gz`` (a cloud run's checkpoint, ADR-130) into this computer's
  training folder, so a run trained elsewhere can be looked at here.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path, PurePosixPath

import pandas as pd

from snowagent.lab.schemas.genome import AgentFamily, GenomeSpec
from snowagent.lab.services.names import nickname
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.training.loop import committed_rounds, load_round, training_root
from snowagent.lab.ui.genes import plain_name

# A family's blocks of settings, as a forecaster would group them.
TOPICS: dict[str, str] = {
    "forcing": "Weather it reads", "col_forcing": "Weather it reads", "plot_snowfall": "Snowfall at each plot",
    "snowpack_snowfall": "Weather it reads", "snowpack_era5_precip": "Weather it reads",
    "new_snow": "New snow", "settlement": "Settling", "col_settlement": "Settling",
    "col_energy": "Sun, sky and ground heat", "col_water": "Melt water and crusts", "crust": "Melt water and crusts",
    "rcp_crust": "Melt water and crusts", "facets": "Facets and depth hoar",
    "col_metamorphism": "How grains change (facets, depth hoar, rounding)",
    "story_facets": "Facets: what counts for and against", "story_depth_hoar": "Depth hoar: what counts",
    "snowpack_weak_layers": "Facets and weak layers", "snowpack_depth_hoar": "Facets and depth hoar",
    "surface_hoar": "Surface hoar", "col_surface_hoar": "Surface hoar", "story_surface": "Surface hoar and new snow",
    "rcp_surface": "Surface hoar, surface facets and wind slab", "rcp_pack": "The old snow underneath",
    "snowpack_physics": "SNOWPACK physics", "snowpack_new_weak": "Weak layers in new snow",
    "proc_switches": "Processes switched on or off", "proc_strengths": "How strong each process is",
    "col_switches": "Run switches", "sc_dials": "Site traits dials", "sc_shelter": "Shelter from wind",
    "col_output": "How the profile is written", "engine_output": "How the profile is written",
    "uncertainty": "How sure it says it is", "pit": "Using the last pit", "rule_pit": "Using the last pit",
    "analogue": "Past look-alike cases", "blend": "Blend of models",
}
HOW_MADE = {"initial": "start", "mutation": "small random change", "crossover": "mix of two parents",
            "crossover+mutation": "mix of two parents, then a small change",
            "survivor": "carried on unchanged", "seed": "brought in from another run", "file": "from a file"}


def _json(f: Path) -> dict | list | None:
    try:
        return json.loads(f.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def run_spec(spec: GenomeSpec, plan: dict) -> GenomeSpec:
    """The allow-list as the run used it: a trimmed run's short lists of genes (ADR-123) are the only free ones."""
    return spec.trimmed_spec(plan.get("trimmed_genes") or None)


def gene_info(spec: GenomeSpec, family: str) -> pd.DataFrame:
    """One row per setting of ``family``: gene, plain name, topic, kind, range, standard value, unit, what it does,
    and ``evolves`` (False for a switch, ADR-128, or a gene a trimmed run keeps fixed, ADR-123)."""
    free = spec.free_genes(family)
    rows = []
    for gene, (block, gs) in spec.family_genes(family).items():
        rows.append({"gene": gene, "setting": plain_name(gene), "topic": TOPICS.get(block, block.replace("_", " ")),
                     "block": block, "kind": gs.kind, "min": gs.min, "max": gs.max, "choices": gs.choices,
                     "standard": gs.default, "unit": gs.unit, "what it does": gs.doc,
                     "fixed": bool(gs.fixed), "evolves": not gs.fixed and (free is None or gene in free)})
    return pd.DataFrame(rows)


def shift(value, info: dict) -> float:
    """How far ``value`` is from the standard value, as a signed share of the allowed range (-1 to 1); a choice is
    1 when it differs from the standard choice and 0 when not."""
    if info["kind"] == "choice" or info["min"] is None or info["max"] is None:
        return 0.0 if value == info["standard"] else 1.0
    span = float(info["max"]) - float(info["min"])
    return (float(value) - float(info["standard"])) / span if span else 0.0


def _last_round_started(events: Path, chunk: int = 1 << 20, limit: int = 64 << 20) -> dict | None:
    """The last ``round_started`` event of a run's event log, read from the end (the log grows to 100 MB or more)."""
    if not events.is_file():
        return None
    with open(events, "rb") as fh:
        fh.seek(0, 2)
        end = pos = fh.tell()
        tail = b""
        while pos > 0 and end - pos < limit:
            step = min(chunk, pos)
            pos -= step
            fh.seek(pos)
            tail = fh.read(step) + tail
            lines = tail.split(b"\n")
            for line in reversed(lines[1:] if pos > 0 else lines):  # the first piece may be a cut line
                if b'"round_started"' in line:
                    try:
                        return json.loads(line)
                    except json.JSONDecodeError:
                        continue
            tail = lines[0] if pos > 0 else b""
    return None


def run_rounds(paths: LabPaths, run_id: str, spec: GenomeSpec, live: bool = True) -> list[dict]:
    """Every finished round of a run: ``{"round", "scored": True, "best": hash, "agents": [...]}``, each agent with
    hash, label, name, family, role, operator, parents, changed (vs its parent), genes (all), rank and composite.
    With ``live``, a round that started but has not finished is added from the event log (``scored``: False, no
    rank; genes rebuilt from the family's standard values and the agent's changes)."""
    d = training_root(paths) / run_id
    out = []
    for r in committed_rounds(d):
        rd = load_round(d, r)
        ranked = {x["genome_hash"]: x for x in rd["leaderboard"].get("ranked", [])}
        agents = []
        for p in rd["population"]:
            lin, g = p["lineage"], p["genome"]
            h = lin["genome_hash"]
            x = ranked.get(h, {})
            agents.append({"hash": h, "label": lin.get("label") or g.get("label"), "name": nickname(h, lin.get("label")),
                           "family": lin["family"], "role": p.get("role"), "operator": lin.get("operator"),
                           "parents": lin.get("parents", []), "changed": lin.get("changed_genes") or {},
                           "from_second": lin.get("from_second_parent") or [], "genes": g["genes"],
                           "rank": x.get("rank"), "composite": x.get("composite")})
        agents.sort(key=lambda a: (a["rank"] is None, a["rank"] or 0))
        out.append({"round": r, "scored": True, "best": rd["round"]["best"]["genome_hash"], "agents": agents})
    if not live:
        return out
    ev = _last_round_started(d / "events.jsonl")
    done = out[-1]["round"] if out else 0
    if ev and int(ev.get("round", 0)) > done:
        agents = []
        for a in ev.get("agents", []):
            fam = a["family"]
            std = {g: gs.default for g, (_b, gs) in spec.family_genes(fam).items()}
            genes = std | {g: v[1] for g, v in (a.get("changed_vs_default") or {}).items()}
            agents.append({"hash": a["genome_hash"], "label": a.get("label"), "name": nickname(a["genome_hash"],
                                                                                              a.get("label")),
                           "family": fam, "role": a.get("role"), "operator": a.get("operator"),
                           "parents": a.get("parents", []), "changed": a.get("changed_genes") or {},
                           "from_second": [], "genes": genes, "rank": None, "composite": None})
        out.append({"round": int(ev["round"]), "scored": False, "best": None, "agents": agents})
    return out


def best_agents(rounds: list[dict]) -> list[dict]:
    """The best agent of each scored round (the one the run names best), in round order."""
    out = []
    for rd in rounds:
        if not rd["scored"]:
            continue
        a = next((a for a in rd["agents"] if a["hash"] == rd["best"]), rd["agents"][0] if rd["agents"] else None)
        if a:
            out.append(a | {"round": rd["round"]})
    return out


def drift_table(rounds: list[dict], info: pd.DataFrame, only_moved: bool = True) -> pd.DataFrame:
    """Rows: settings (plain names); columns: rounds; values: the round's best agent's ``shift`` from the standard
    value. ``only_moved`` leaves out settings the best agent never moved."""
    best = best_agents(rounds)
    meta = info.set_index("gene").to_dict("index")
    data = {}
    for a in best:
        data[a["round"]] = {g: shift(a["genes"].get(g, m["standard"]), m) for g, m in meta.items()}
    df = pd.DataFrame(data)
    if df.empty:
        return df
    if only_moved:
        df = df[(df.abs() > 1e-9).any(axis=1)]
    df.index = [meta[g]["setting"] for g in df.index]
    return df


def total_drift(rounds: list[dict], info: pd.DataFrame) -> pd.DataFrame:
    """Per round: the best agent's total distance from the standard settings (ADR-089's drift: the sum of the
    absolute shifts of every setting but the run switches) and how many settings it has moved."""
    meta = info[~info["fixed"]].set_index("gene").to_dict("index")
    rows = []
    for a in best_agents(rounds):
        s = [abs(shift(a["genes"].get(g, m["standard"]), m)) for g, m in meta.items()]
        rows.append({"round": a["round"], "name": a["name"], "drift": sum(s),
                     "settings moved": sum(1 for x in s if x > 1e-9)})
    return pd.DataFrame(rows)


def value_history(rounds: list[dict], gene: str) -> pd.DataFrame:
    """One setting's value for every agent of every round: round, name, label, value, role, operator, best, rank."""
    rows = []
    for rd in rounds:
        for a in rd["agents"]:
            if gene in a["genes"]:
                rows.append({"round": rd["round"], "name": a["name"], "label": a["label"], "value": a["genes"][gene],
                             "role": a["role"], "operator": a["operator"], "best": a["hash"] == rd["best"],
                             "rank": a["rank"], "scored": rd["scored"]})
    return pd.DataFrame(rows)


def agent_index(rounds: list[dict]) -> dict[str, dict]:
    """genome hash -> the agent where it first appeared in the run (with its round)."""
    out: dict[str, dict] = {}
    for rd in rounds:
        for a in rd["agents"]:
            out.setdefault(a["hash"], a | {"round": rd["round"]})
    return out


def family_tree(rounds: list[dict], genome_hash: str, max_nodes: int = 60) -> list[dict]:
    """The agent and its ancestors inside the run, newest first (breadth first, each once): round, name, how it
    was made, its parents (hashes) and what that step changed. A parent from outside the run (a seed) is listed
    with ``outside`` True."""
    idx = agent_index(rounds)
    out, seen, queue = [], set(), [genome_hash]
    while queue and len(out) < max_nodes:
        h = queue.pop(0)
        if h in seen:
            continue
        seen.add(h)
        a = idx.get(h)
        if a is None:
            out.append({"hash": h, "outside": True, "name": "from outside this run", "parents": []})
            continue
        out.append(a | {"outside": False})
        queue.extend(a.get("parents") or [])
    return out


def step_text(agent: dict, info: pd.DataFrame, idx: dict[str, dict]) -> str:
    """'small random change of Omar Balmoral: faceting hours 72 → 64' in plain words."""
    names = info.set_index("gene")["setting"].to_dict()
    op = agent.get("operator") or "initial"
    parents = [idx[p]["name"] if p in idx else "an agent from outside this run" for p in agent.get("parents") or []]
    how = HOW_MADE.get(op, op)
    if parents:
        how += " of " + " and ".join(parents)
    ch = agent.get("changed") or {}
    if ch:
        bits = [f"{names.get(g, plain_name(g))} {_fmt(v[0])} → {_fmt(v[1])}" for g, v in list(ch.items())[:6]]
        how += ": " + "; ".join(bits) + (f" (and {len(ch) - 6} more)" if len(ch) > 6 else "")
    elif op in ("mutation", "crossover"):
        how += " (no setting changed)"
    return how


def _fmt(v) -> str:
    return f"{v:.3g}" if isinstance(v, float) else str(v)


WEIGHT_NAMES = {"snow_depth": "Snow depth", "layer_structure": "Layering", "critical_layers": "Weak layers",
                "uncertainty": "Honest uncertainty", "robustness": "Works everywhere"}


def weights_in_force(plan: dict, current: dict | None) -> pd.DataFrame:
    """The score weights the run ranked agents with (frozen into its plan) and today's, as percentages."""
    used = plan.get("scoring_weights") or {}
    keys = list(dict.fromkeys([*WEIGHT_NAMES, *used, *(current or {})]))
    return pd.DataFrame([{"part of the score": WEIGHT_NAMES.get(k, k.replace("_", " ")),
                          "this run": round(100 * used[k]) if k in used else None,
                          "today": round(100 * current[k]) if current and k in current else None} for k in keys])


def run_families(rounds: list[dict]) -> list[str]:
    """The families in a run, the latest best agent's first."""
    fams = list(dict.fromkeys(a["family"] for rd in rounds for a in rd["agents"]))
    best = best_agents(rounds)
    if best and best[-1]["family"] in fams:
        fams.remove(best[-1]["family"])
        fams.insert(0, best[-1]["family"])
    return [f for f in fams if f in AgentFamily.__members__]


# ------------------------------------------------------------------------------------------- run archives


class ArchiveError(ValueError):
    pass


def import_run_archive(paths: LabPaths, archive: Path, overwrite: bool = False) -> str:
    """Unpack a training run's ``.tar.gz`` (one top folder holding ``run.json`` and ``rounds/``, as the cloud
    checkpoints of ADR-130 are) into this computer's training folder; returns the run id. Refuses anything else:
    members outside the one folder, links, devices, or a run of that name already here (unless ``overwrite``)."""
    root = training_root(paths)
    try:
        tf = tarfile.open(archive, "r:*")
    except (tarfile.TarError, OSError) as exc:
        raise ArchiveError(f"not a run archive: {exc}") from exc
    with tf:
        members = tf.getmembers()
        tops = {PurePosixPath(m.name).parts[0] for m in members if PurePosixPath(m.name).parts}
        if len(tops) != 1:
            raise ArchiveError("the archive should hold one run folder")
        run_id = tops.pop()
        if run_id in (".", "..") or not run_id.strip():
            raise ArchiveError("the archive should hold one run folder")
        names = set()
        for m in members:
            p = PurePosixPath(m.name)
            if p.is_absolute() or ".." in p.parts:
                raise ArchiveError(f"unsafe path in the archive: {m.name}")
            if not (m.isfile() or m.isdir()):
                raise ArchiveError(f"the archive holds a link or special file: {m.name}")
            names.add("/".join(p.parts[1:]))
        if "run.json" not in names or not any(n.startswith("rounds/") for n in names):
            raise ArchiveError("this is not a training run (no run.json and rounds folder); a run's cache archive "
                               "(…-cache.tar.gz) is not needed here")
        dest = root / run_id
        if dest.exists() and not overwrite:
            raise ArchiveError(f"a run called {run_id} is already here")
        root.mkdir(parents=True, exist_ok=True)
        tf.extractall(root, members=members, filter="data")
    status = _json(dest / "status.json")
    if isinstance(status, dict) and status.get("state") == "running":  # a checkpoint taken mid-run is not going here
        status |= {"state": "stopped", "message": "copied from another computer"}
        (dest / "status.json").write_text(json.dumps(status, indent=1))
    return run_id
