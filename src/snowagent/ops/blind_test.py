"""Blind live test, scoring (ADR-090; the freeze is ADR-086).

Agents frozen in the lab app before a winter's pits exist (``blind_test/<season>/*.json`` on the ``site-agents``
branch) are scored by the daily update on that winter's pits, but only on pits observed after their freeze time,
beside standard SNOWPACK on the same cases and under the scoring version recorded at the freeze. Nothing about a
pit can have reached an agent frozen before it was dug, so this is the one test that cannot leak.

Each daily run checks the live season's pits first. With no frozen agent, or no pit observed after the earliest
freeze, or no new pit since the last scoring, it does nothing else. Otherwise it imports the checkout into a separate
lab data root (``data/lab_blind``, never the owner's), builds the lab's cases (next pit and 72 h forecast) for just
those pits with the live season enabled, and scores the frozen agents and standard SNOWPACK on the cases not scored
yet. Scores are appended to ``archive/blind_test/<season>/scores.jsonl`` (tracked, append-only: a score once written
is never changed) and summarised in ``web/data/blind_test.json``, which the lab app reads (``blind_results/<season>.json``
on the ``site-agents`` branch, pushed with the same plumbing as Send to site)."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from snowagent.ops.site_agents import branch_files

ARCHIVE = Path("archive/blind_test")
LAB_ROOT = Path("data/lab_blind")
OBSERVED = Path("data/interim/obs/observed_profiles.jsonl")
RESULTS_FOLDER = "blind_results"
STANDARD = "standard SNOWPACK"
LABEL = ("Blind test: agents frozen before these pits were dug, scored on them afterwards. Research and decision "
         "support only, not an avalanche forecast.")
KEY = ("case_id", "agent_id", "scoring_version")
KEEP = ("composite", "snow_depth", "layer_structure", "critical_layers", "uncertainty", "robustness", "status",
        "depth_error_m", "depth_covered")


def _utc(s) -> pd.Timestamp:
    t = pd.Timestamp(s)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def season_pits(season: str, observed: Path = OBSERVED, season_start: str = "09-15") -> list[dict]:
    """The live season's pits (lab profile id, time, plot) from the daily update's observed-profile file, which the
    lab import reads (the lab keeps its profile ids)."""
    from snowagent.lab.settings import season_key

    out = []
    if not Path(observed).is_file():
        return out
    for line in Path(observed).read_text().splitlines():
        try:
            r = json.loads(line)
            t = _utc(r.get("obs_time_utc"))
        except (ValueError, TypeError):
            continue
        if pd.isna(t):
            continue  # an undated record: never scored
        if season_key(t.to_pydatetime(), season_start) == season:
            out.append({"id": r.get("profile_id"), "time": t.isoformat(), "plot": r.get("site_key")})
    return sorted(out, key=lambda x: x["time"])


def load_store(path: Path) -> pd.DataFrame:
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.is_file() else []
    return pd.DataFrame(rows)


def append_store(path: Path, rows: list[dict]) -> int:
    """Append rows whose key is not in the store yet (a score once written is never changed)."""
    have = load_store(path)
    seen = set(map(tuple, have[list(KEY)].astype(str).values)) if not have.empty else set()
    new = [r for r in rows if tuple(str(r[k]) for k in KEY) not in seen]
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            for r in new:
                f.write(json.dumps(r, sort_keys=True, default=str) + "\n")
    return len(new)


def summarise(store: pd.DataFrame, entries: list[tuple[str, object]], season: str, now: datetime) -> dict:
    """Per frozen agent: its composite and standard SNOWPACK's on the same cases (pits after its freeze, its
    scoring version), cases won and lost, and per plot."""
    from snowagent.lab.competition.runner import leaderboard
    from snowagent.lab.settings import load_lab_config

    weights = load_lab_config().scoring_weights
    out = []
    for aid, e in entries:
        rec = {"id": aid, "name": e.name, "frozen_utc": e.frozen_utc, "scoring_version": e.scoring_version,
               "git_commit": e.git_commit, "source": e.source.model_dump(), "cases": 0, "composite": None,
               "standard_composite": None, "difference": None, "won": 0, "lost": 0, "by_plot": {}}
        if not store.empty:
            mine = store[(store["agent_id"] == aid) & (store["scoring_version"] == e.scoring_version)
                         & (store["pit_utc"].map(_utc) > _utc(e.frozen_utc))]
            std = store[(store["agent_id"] == "standard") & (store["scoring_version"] == e.scoring_version)
                        & store["case_id"].isin(mine["case_id"])]
            both = sorted(set(mine["case_id"]) & set(std["case_id"]))
            if both:
                a, b = mine[mine["case_id"].isin(both)], std[std["case_id"].isin(both)]
                ca, cb = _composite(a, weights, leaderboard), _composite(b, weights, leaderboard)
                pa = a.set_index("case_id")["composite"].astype(float)
                pb = b.set_index("case_id")["composite"].astype(float).reindex(pa.index)
                rec |= {"cases": len(both), "composite": ca, "standard_composite": cb,
                        "difference": None if ca is None or cb is None else round(ca - cb, 4),
                        "won": int((pa > pb).sum()), "lost": int((pa < pb).sum())}
                for plot, g in a.groupby("site_code"):
                    gb = b[b["case_id"].isin(g["case_id"])]
                    rec["by_plot"][plot] = {"cases": len(g), "composite": _composite(g, weights, leaderboard),
                                            "standard_composite": _composite(gb, weights, leaderboard)}
        out.append(rec)
    return {"label": LABEL, "season": season, "generated_utc": now.isoformat(timespec="seconds"), "entries": out}


def _composite(df: pd.DataFrame, weights, leaderboard) -> float | None:
    d = df.assign(family="x", label="x", runtime_s=0.0, agent_id="x")
    for k in ("snow_depth", "layer_structure", "critical_layers", "uncertainty", "robustness"):
        if k not in d:
            d[k] = math.nan
    rows = leaderboard(d, weights)
    c = rows[0]["composite"] if rows else None
    return None if c is None else round(float(c), 4)


def score_new(entries: list[tuple[str, object]], pit_ids: list[str], store_path: Path, lab_root: Path,
              repo: Path, engine=None, workers: int = 2, cfg=None, do_import: bool = True) -> dict:
    """Import the checkout into ``lab_root``, build the cases of ``pit_ids`` with the live season enabled, and score
    each frozen agent (on cases whose pit came after its freeze) and standard SNOWPACK under each entry's scoring
    version, skipping what the store already has. Returns counts."""
    from snowagent.lab.benchmark.builder import CaseBuildError, build_cases
    from snowagent.lab.competition.runner import EngineSpec, select_cases
    from snowagent.lab.genome import default_genome
    from snowagent.lab.services.data import import_data
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training.evaluate import EvalContext, case_refs, evaluate_population

    paths = LabPaths(lab_root)
    cfg = cfg or _live_config(entries)
    if do_import:
        import_data(repo, paths, cfg)
    try:
        build_cases(paths, cfg, repo, profile_ids=pit_ids)
    except CaseBuildError:
        return {"cases": 0, "scored": 0}
    refs = case_refs(select_cases(paths, cfg.splits.case_set()))
    refs = [r for r in refs if r.manifest.target_profile_id in set(pit_ids)]
    have = load_store(store_path)
    seen = set(map(tuple, have[list(KEY)].astype(str).values)) if not have.empty else set()
    std = default_genome("snowpack", cfg.genome)
    scored = 0
    for version in sorted({e.scoring_version for _, e in entries}):
        group = [(aid, e) for aid, e in entries if e.scoring_version == version]
        genomes, names = [std], {std.agent_id: ("standard", STANDARD)}
        for aid, e in group:
            g = e.checked_genome(cfg.genome)
            genomes.append(g)
            names[g.agent_id] = (aid, e.name)
        earliest = min(_utc(e.frozen_utc) for _, e in group)
        todo = [r for r in refs if _utc(r.manifest.valid_time) > earliest
                and any((r.manifest.case_id, names[g.agent_id][0], version) not in seen for g in genomes)]
        if not todo:
            continue
        ctx = EvalContext(paths=paths, case_set=cfg.splits.case_set(), seed=0, weights=cfg.scoring_weights,
                          config_hash=cfg.config_hash(), engine=engine or EngineSpec(kind="auto", source_root=str(repo)),
                          scoring_version=version)
        res = evaluate_population(todo, genomes, ctx, workers)
        by_case = {r.manifest.case_id: r.manifest for r in todo}
        rows = []
        for x in res.scores.to_dict("records"):
            m = by_case[x["case_id"]]
            aid, name = names[x["agent_id"]]
            frozen = next((e.frozen_utc for i, e in group if i == aid), None)
            if frozen is not None and _utc(m.valid_time) <= _utc(frozen):
                continue  # this pit came before that agent's freeze: it does not count for it
            rows.append({"case_id": x["case_id"], "agent_id": aid, "name": name, "scoring_version": version,
                         "site_code": m.site_code.value, "case_type": m.case_type.value,
                         "pit_utc": _utc(m.valid_time).isoformat(), "as_of_utc": _utc(m.as_of_time).isoformat(),
                         "scored_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                         **{k: (None if isinstance(x.get(k), float) and math.isnan(x[k]) else x.get(k))
                            for k in KEEP}})
        scored += append_store(store_path, rows)
    return {"cases": len(refs), "scored": scored}


def _live_config(entries):
    """The lab config with the entries' season(s) enabled (mode all), so its pits become scorable cases. The
    configured seasons are unchanged everywhere else."""
    from snowagent.lab.settings import load_lab_config

    cfg = load_lab_config()
    seasons = sorted({e.season for _, e in entries} | set(cfg.splits.all_seasons))
    splits = cfg.splits.model_copy(update={"mode": cfg.splits.mode.__class__("all"), "all_seasons": seasons})
    return cfg.model_copy(update={"splits": splits})


def run_blind_test(y: int, out_dir: Path, now: pd.Timestamp | None = None, repo: Path = Path("."),
                   files: dict[str, bytes] | None = None, observed: Path = OBSERVED, archive: Path = ARCHIVE,
                   lab_root: Path = LAB_ROOT, engine=None, publish: bool = True, cfg=None,
                   do_import: bool = True) -> dict:
    """The daily step: score what is new, write the summary, publish it for the lab app. Never raises for one bad
    entry file (skipped with a warning)."""
    season = f"{y}-{y + 1}"
    if files is None:
        files = branch_files(repo, folder=f"blind_test/{season}")
    try:
        import pyarrow  # noqa: F401  the lab's tables (the lab extra)
    except ImportError:  # a note only when there is something to score
        note = [{"level": "info", "source": "blind_test",
                 "message": "blind test not scored: the lab extra is not installed (pip install -e '.[dev,lab]')"}]
        return {"season": season, "entries": 0, "pits": 0, "scored": 0, "warnings": note if files else []}
    from snowagent.lab.services.blind_test import parse_entries

    now = (now or pd.Timestamp.now(tz="UTC")).to_pydatetime()
    entries, skipped = parse_entries(files)
    entries = [(aid, e) for aid, e in entries if e.season == season]
    warnings = [{"level": "info", "source": "blind_test", "message": f"blind-test entry {s} skipped"}
                for s in skipped]
    store_path = Path(archive) / season / "scores.jsonl"
    state_path = Path(archive) / season / "state.json"
    res = {"season": season, "entries": len(entries), "pits": 0, "scored": 0, "warnings": warnings}
    if entries:
        earliest = min(_utc(e.frozen_utc) for _, e in entries)
        pits = [p for p in season_pits(season, observed) if _utc(p["time"]) > earliest]
        res["pits"] = len(pits)
        state = {"pits": pits, "entries": sorted(aid for aid, _ in entries)}
        old = json.loads(state_path.read_text()) if state_path.is_file() else None
        if pits and state != old:
            ids = [p["id"] for p in pits if p["id"]]
            if ids:
                res |= score_new(entries, ids, store_path, lab_root, repo, engine, cfg=cfg, do_import=do_import)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state, indent=1) + "\n")
    summary = summarise(load_store(store_path), entries, season, now)
    out = Path(out_dir) / "blind_test.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=1) + "\n")
    if publish and entries:
        try:
            from snowagent.lab.services.blind_test import results

            same = (results(repo, season) or {}) | {"generated_utc": None} == summary | {"generated_utc": None}
            if not same:  # one commit when the results change, not one a day
                _publish(repo, season, summary)
        except Exception as exc:  # the app's copy is a convenience; the site file and the archive are the record
            warnings.append({"level": "info", "source": "blind_test",
                             "message": f"blind-test results not pushed to the site-agents branch: {exc}"[:300]})
    return res


def _publish(repo: Path, season: str, summary: dict) -> None:
    from snowagent.lab.services.site_send import _commit, _git

    blob = _git(repo, "hash-object", "-w", "--stdin", stdin=(json.dumps(summary, indent=1) + "\n").encode())

    def add(index: Path) -> None:
        _git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{RESULTS_FOLDER}/{season}.json",
             index=index)

    _commit(repo, add, f"Blind test {season}: results {summary['generated_utc']}")
