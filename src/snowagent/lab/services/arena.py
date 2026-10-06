"""The Arena page's data (ADR-078): the event feed of a competition or training run (``events.jsonl``), or, for a run
recorded before the feed existed, the same events rebuilt from the run's own files; and the views' tables at any
replay position. Every number comes from the run's files; nothing is estimated or smoothed."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from snowagent.lab.events import EVENTS_FILE, pit_time, read_events, score_event
from snowagent.lab.storage.paths import LabPaths

LIVE_QUIET_S = 120  # a run without a final file whose feed grew this recently is shown as live
OK_STATUSES = ("ok", "insufficient_data")


@dataclass
class Feed:
    kind: str  # competition or training
    run_id: str
    dir: Path
    events: list[dict] = field(default_factory=list)
    offset: int = 0
    rebuilt: bool = False  # events rebuilt from the run's files (no live feed was recorded)
    plan: dict = field(default_factory=dict)


# --------------------------------------------------------------------------------------------- runs


def arena_runs(paths: LabPaths) -> list[dict]:
    """Competitions and training runs, live ones first, then newest first: kind, run id, directory, live."""
    out = []
    for kind, sub in (("competition", "competitions"), ("training", "training")):
        root = paths.outputs / sub
        if not root.is_dir():
            continue
        for d in root.iterdir():
            if not (d / "run.json").is_file():
                continue
            ev = d / EVENTS_FILE
            mtime = max((ev.stat().st_mtime if ev.is_file() else 0.0), (d / "run.json").stat().st_mtime)
            out.append({"kind": kind, "run_id": d.name, "dir": d, "live": is_live(kind, d), "mtime": mtime,
                        "has_events": ev.is_file()})
    return sorted(out, key=lambda r: (not r["live"], -r["mtime"]))


def is_live(kind: str, d: Path) -> bool:
    from snowagent.lab.services.jobs import ACTIVE, job_for, pid_alive

    if kind == "training":
        try:
            st = json.loads((d / "status.json").read_text())
        except (OSError, ValueError):
            st = {}
        if st.get("state") in ("finished", "stopped", "failed"):
            return False
        if st.get("state") == "running" and pid_alive(st.get("pid")):
            return True
        job = job_for(LabPaths(d.parents[2]), "train", "run_id", d.name)  # still preparing (case library)
        return bool(job and job["state"] in ACTIVE)
    if (d / "leaderboard.json").is_file():
        return False
    job = job_for(LabPaths(d.parents[2]), "compete", "run_id", d.name)  # <root>/outputs/competitions/<run>
    if job is not None:
        return job["state"] in ACTIVE
    ev = d / EVENTS_FILE
    return ev.is_file() and time.time() - ev.stat().st_mtime < LIVE_QUIET_S


# --------------------------------------------------------------------------------------------- feeds


def load_feed(paths: LabPaths, kind: str, run_id: str, previous: Feed | None = None) -> Feed:
    """The run's events; ``previous`` (the same run) is extended from its byte offset (cheap polling)."""
    d = paths.outputs / ("competitions" if kind == "competition" else "training") / run_id
    if previous is not None and previous.run_id == run_id and previous.kind == kind and not previous.rebuilt:
        new, off = read_events(d, previous.offset)
        previous.events.extend(new)
        previous.offset = off
        return previous
    plan = _plan(d)
    if (d / EVENTS_FILE).is_file():
        events, off = read_events(d)
        return Feed(kind, run_id, d, events, off, False, plan)
    if is_live(kind, d):  # started, nothing recorded yet: wait for the feed instead of rebuilding
        return Feed(kind, run_id, d, [], 0, False, plan)
    events = rebuild_competition(d) if kind == "competition" else rebuild_training(d)
    return Feed(kind, run_id, d, events, 0, True, plan)


def _plan(d: Path) -> dict:
    try:
        meta = json.loads((d / "run.json").read_text())
    except (OSError, ValueError):
        return {}
    return meta.get("plan", meta)


def rebuild_competition(d: Path) -> list[dict]:
    """A finished competition's events from its case records, in the order they were written."""
    files = sorted((d / "cases").glob("*.json"), key=lambda f: f.stat().st_mtime_ns) if (d / "cases").is_dir() \
        else []
    out: list[dict] = [{"ev": "run_started", "kind": "competition", "run_id": d.name, "cases": len(files)}]
    for f in files:
        rec = json.loads(f.read_text())
        out += [{"ev": "case_scored", **score_event(r, pit_time=pit_time(rec["case_id"]))} for r in rec["rows"]]
    if (d / "leaderboard.json").is_file():
        out.append({"ev": "run_finished", "kind": "competition", "run_id": d.name})
    return out


def rebuild_training(d: Path) -> list[dict]:
    """A training run's events from its committed rounds (case order within a round, not completion order)."""
    from snowagent.lab.training.loop import committed_rounds, load_round, round_dir

    out: list[dict] = [{"ev": "run_started", "kind": "training", "run_id": d.name}]
    for r in committed_rounds(d):
        rd = load_round(d, r)
        out.append({"ev": "round_started", "round": r, "agents": [
            {"agent_id": p["lineage"]["agent_id"], "label": p["lineage"]["label"], "family": p["lineage"]["family"],
             "genome_hash": p["lineage"]["genome_hash"], "role": p["role"], "operator": p["lineage"]["operator"],
             "parents": p["lineage"]["parents"], "changed_genes": p["lineage"].get("changed_genes"),
             "changed_vs_default": p["lineage"].get("changed_vs_default")} for p in rd["population"]]})
        info = rd["round"]
        if info.get("screen", {}).get("agents"):
            out.append({"ev": "screen", "round": r, "threshold": info["screen"].get("threshold"),
                        "agents": info["screen"]["agents"]})
        f = round_dir(d, r) / "scores.parquet"
        if f.is_file():
            df = pd.read_parquet(f).astype(object).where(lambda x: pd.notna(x), None)
            out += [{"ev": "case_scored", **score_event(row, round=r, pit_time=pit_time(row["case_id"]))}
                    for row in df.to_dict("records")]
        out.append({"ev": "round_committed", "round": r, "best": info["best"], "gap": info["gap"],
                    "survivors_next": info["survivors_next"], "wall_s": info.get("wall_s"),
                    "ranked": [{k: x.get(k) for k in ("rank", "agent_id", "label", "family", "genome_hash",
                                                      "composite")} for x in rd["leaderboard"]["ranked"]]})
    if (d / "summary.json").is_file():
        out.append({"ev": "run_finished", "kind": "training", "run_id": d.name})
    return out


# --------------------------------------------------------------------------------------------- views


def scored_frame(feed: Feed) -> pd.DataFrame:
    """Every ``case_scored`` event in feed order (``seq``); a later event of the same agent, case and round (a
    resumed run) replaces the earlier one."""
    rows = [e for e in feed.events if e.get("ev") == "case_scored"]
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["seq", "case_id", "agent_id", "label", "family", "composite", "round"])
    df["seq"] = range(len(df))
    if "round" not in df:
        df["round"] = 1
    df["round"] = df["round"].fillna(1).astype(int)
    df["composite"] = pd.to_numeric(df["composite"], errors="coerce")
    return df.drop_duplicates(["round", "case_id", "agent_id"], keep="last").sort_values("seq")


def race_table(upto: pd.DataFrame) -> pd.DataFrame:
    """Per agent: mean case composite over its scored cases so far, cases scored and skipped."""
    if upto.empty:
        return pd.DataFrame(columns=["agent_id", "label", "family", "genome_hash", "mean", "cases", "skipped"])
    g = upto.groupby("agent_id", sort=False)
    out = pd.DataFrame({"label": g["label"].last(), "family": g["family"].last(),
                        "genome_hash": g["genome_hash"].last() if "genome_hash" in upto else None,
                        "mean": g["composite"].mean(), "cases": g["composite"].count(),
                        "skipped": g["status"].apply(lambda s: int((s == "skipped").sum()))
                        if "status" in upto else 0}).reset_index()
    return out.sort_values(["mean", "label"], ascending=[False, True], na_position="last").reset_index(drop=True)


def rounds_of(feed: Feed) -> dict[int, dict]:
    """Training: per round, its agents (``round_started``), screen and commit records."""
    out: dict[int, dict] = {}
    for e in feed.events:
        r = e.get("round")
        if r is None:
            continue
        rec = out.setdefault(int(r), {"agents": [], "screen": None, "committed": None})
        if e["ev"] == "round_started":
            rec["agents"] = e.get("agents") or []
        elif e["ev"] == "screen":
            rec["screen"] = e
        elif e["ev"] == "round_committed":
            rec["committed"] = e
    return out


def finished(feed: Feed) -> bool:
    return any(e.get("ev") == "run_finished" for e in feed.events)


__all__ = ["Feed", "arena_runs", "finished", "is_live", "load_feed", "race_table", "rebuild_competition",
           "rebuild_training", "rounds_of", "scored_frame"]
