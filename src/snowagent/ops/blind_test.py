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
HRDPS_ID = "standard_hrdps"  # ADR-116: standard SNOWPACK on HRDPS forecast weather, beside the frozen agents
HRDPS_NAME = "standard SNOWPACK on HRDPS weather"
HRDPS_START = "2026-09-28T00:00:00+00:00"  # the first archived HRDPS run (ADR-116)
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


def summarise(store: pd.DataFrame, entries: list[tuple[str, object]], season: str, now: datetime,
              outside_start: str | None = None, hrdps_start: str | None = None) -> dict:
    """Per frozen agent: its composite and standard SNOWPACK's on the same cases (pits after its freeze, its
    scoring version), cases won and lost, and per plot. With ``outside_start``, the same for each outside forecast
    (``outside``, ADR-112) under the current scoring version, on pits after its first snapshot."""
    from snowagent.lab.competition.scoring import SCORING_VERSION
    from snowagent.lab.settings import load_lab_config
    from snowagent.ops.snowpackbc import AGENTS, SOURCE

    weights = load_lab_config().scoring_weights
    out = []
    for aid, e in entries:
        rec = {"id": aid, "name": e.name, "frozen_utc": e.frozen_utc, "scoring_version": e.scoring_version,
               "git_commit": e.git_commit, "source": e.source.model_dump()}
        out.append(rec | _compare(store, aid, e.scoring_version, e.frozen_utc, weights))
    res = {"label": LABEL, "season": season, "generated_utc": now.isoformat(timespec="seconds"), "entries": out}
    if outside_start:
        res["outside"] = [{"id": aid, "name": name, "source": SOURCE, "from_utc": outside_start,
                           "scoring_version": SCORING_VERSION}
                          | _compare(store, aid, SCORING_VERSION, outside_start, weights)
                          for aid, name in AGENTS.values()]
    if hrdps_start and not store.empty and (store["agent_id"] == HRDPS_ID).any():
        res["hrdps"] = {"id": HRDPS_ID, "name": HRDPS_NAME, "from_utc": hrdps_start,
                        "scoring_version": SCORING_VERSION} | _compare(store, HRDPS_ID, SCORING_VERSION, hrdps_start,
                                                                      weights)
    return res


def _compare(store: pd.DataFrame, aid: str, version: str, after: str, weights) -> dict:
    """An agent's composite and standard SNOWPACK's on the cases both have (pits after ``after``, one scoring
    version), cases won and lost, and per plot."""
    from snowagent.lab.competition.runner import leaderboard

    rec = {"cases": 0, "composite": None, "standard_composite": None, "difference": None, "won": 0, "lost": 0,
           "by_plot": {}}
    if store.empty:
        return rec
    mine = store[(store["agent_id"] == aid) & (store["scoring_version"] == version)
                 & (store["pit_utc"].map(_utc) > _utc(after))]
    std = store[(store["agent_id"] == "standard") & (store["scoring_version"] == version)
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
    return rec


def _composite(df: pd.DataFrame, weights, leaderboard) -> float | None:
    d = df.assign(family="x", label="x", runtime_s=0.0, agent_id="x")
    for k in ("snow_depth", "layer_structure", "critical_layers", "uncertainty", "robustness"):
        if k not in d:
            d[k] = math.nan
    rows = leaderboard(d, weights)
    c = rows[0]["composite"] if rows else None
    return None if c is None else round(float(c), 4)


def score_new(entries: list[tuple[str, object]], pit_ids: list[str], store_path: Path, lab_root: Path,
              repo: Path, engine=None, workers: int = 2, cfg=None, do_import: bool = True,
              outside: dict | None = None, hrdps_root: Path | None = None) -> dict:
    """Import the checkout into ``lab_root``, build the cases of ``pit_ids`` with the live season enabled, and score
    each frozen agent (on cases whose pit came after its freeze) and standard SNOWPACK under each entry's scoring
    version, skipping what the store already has. With ``outside`` (``{"archive", "season", "start"}``, ADR-112),
    SnowpackBC's saved forecasts are scored too, beside standard SNOWPACK, under the current scoring version, on
    cases whose pit came after the first snapshot. Returns counts."""
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
    from snowagent.lab.competition.scoring import SCORING_VERSION

    current = bool(outside) or hrdps_root is not None  # standard SNOWPACK under the current version is the yardstick
    versions = {e.scoring_version for _, e in entries} | ({SCORING_VERSION} if current else set())
    for version in sorted(versions):
        group = [(aid, e) for aid, e in entries if e.scoring_version == version]
        genomes, names = [std], {std.agent_id: ("standard", STANDARD)}
        for aid, e in group:
            g = e.checked_genome(cfg.genome)
            genomes.append(g)
            names[g.agent_id] = (aid, e.name)
        starts = [_utc(e.frozen_utc) for _, e in group] + ([_utc(outside["start"])]
                                                           if outside and version == SCORING_VERSION else [])
        if hrdps_root is not None and version == SCORING_VERSION:
            starts.append(_utc(HRDPS_START))
        earliest = min(starts)
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
    if outside:
        scored += append_store(store_path, score_outside(refs, outside, cfg.scoring_weights, SCORING_VERSION, std))
    if hrdps_root is not None:
        scored += score_hrdps(pit_ids, store_path, lab_root, hrdps_root, repo, engine, workers, cfg)
    return {"cases": len(refs), "scored": scored}


def score_hrdps(pit_ids: list[str], store_path: Path, lab_root: Path, hrdps_root: Path, repo: Path, engine=None,
                workers: int = 2, cfg=None) -> int:
    """Standard SNOWPACK on HRDPS forecast weather (ADR-116; owner 2026-11-18: the frozen agents keep GFS this
    winter, HRDPS is scored beside them). The processed tables of ``lab_root`` are copied to ``hrdps_root``, the
    72 h cases of ``pit_ids`` are built there with ``forecast_source: hrdps`` (same run, as-of time and pit as the
    GFS cases, so the same case ids), and standard SNOWPACK is scored, under the current scoring version, on the
    cases whose forecast has an HRDPS run. Appended to the same store; returns the rows added."""
    import shutil

    from snowagent.lab.benchmark.builder import CaseBuildError, build_cases
    from snowagent.lab.competition.runner import EngineSpec, select_cases
    from snowagent.lab.competition.scoring import SCORING_VERSION
    from snowagent.lab.genome import default_genome
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training.evaluate import EvalContext, case_refs, evaluate_population

    src, paths = LabPaths(lab_root), LabPaths(hrdps_root)
    if not src.processed.is_dir():
        return 0
    if paths.processed.exists():
        shutil.rmtree(paths.processed)
    shutil.copytree(src.processed, paths.processed)
    fc = cfg.benchmark.forecast_h72.model_copy(update={"forecast_source": "hrdps"})
    hcfg = cfg.model_copy(update={"benchmark": cfg.benchmark.model_copy(update={"forecast_h72": fc})})
    try:
        build_cases(paths, hcfg, repo, profile_ids=pit_ids, case_types=["forecast_h72"])
    except CaseBuildError:
        return 0
    refs = [r for r in case_refs(select_cases(paths, hcfg.splits.case_set()))
            if r.manifest.target_profile_id in set(pit_ids)
            and any(run.source_id.startswith("hrdps:") for run in r.manifest.forecast_runs)]
    have = load_store(store_path)
    seen = set(map(tuple, have[list(KEY)].astype(str).values)) if not have.empty else set()
    todo = [r for r in refs if (r.manifest.case_id, HRDPS_ID, SCORING_VERSION) not in seen]
    if not todo:
        return 0
    std = default_genome("snowpack", hcfg.genome)
    ctx = EvalContext(paths=paths, case_set=hcfg.splits.case_set(), seed=0, weights=hcfg.scoring_weights,
                      config_hash=hcfg.config_hash(), engine=engine or EngineSpec(kind="auto", source_root=str(repo)),
                      scoring_version=SCORING_VERSION)
    res = evaluate_population(todo, [std], ctx, workers)
    by_case = {r.manifest.case_id: r.manifest for r in todo}
    rows = []
    for x in res.scores.to_dict("records"):
        m = by_case[x["case_id"]]
        rows.append({"case_id": x["case_id"], "agent_id": HRDPS_ID, "name": HRDPS_NAME,
                     "scoring_version": SCORING_VERSION, "site_code": m.site_code.value,
                     "case_type": m.case_type.value, "pit_utc": _utc(m.valid_time).isoformat(),
                     "as_of_utc": _utc(m.as_of_time).isoformat(),
                     "scored_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                     **{k: (None if isinstance(x.get(k), float) and math.isnan(x[k]) else x.get(k)) for k in KEEP}})
    return append_store(store_path, rows)


def outside_issue_time(m) -> pd.Timestamp:
    """When an outside forecast must have been saved to count for a case (ADR-112): the case's as-of time, except
    for a ``next_pit`` case, where our agents get measured weather up to the pit, so the pit time."""
    return _utc(m.valid_time) if m.case_type.value == "next_pit" else _utc(m.as_of_time)


def score_outside(refs, outside: dict, weights, version: str, std) -> list[dict]:
    """Score rows of SnowpackBC (``ops.snowpackbc``) on the cases whose pit came after its first snapshot: its
    profile at the pit from the newest snapshot saved by the case's issue time, through standard SNOWPACK's output
    step. A case with no such snapshot (none saved yet, or the fetch failed for days) is left unscored, not
    counted as a miss: the gap is ours, not the forecast's."""
    from snowagent.lab.agents.common import stamp_prediction
    from snowagent.lab.agents.snowpack import FixedEngineResult, SnowpackAgent
    from snowagent.lab.benchmark.loader import load_visible_case
    from snowagent.lab.competition import scoring
    from snowagent.lab.competition.truth import scorable, scoring_truth
    from snowagent.ops import snowpackbc as sb

    archive, season, start = Path(outside["archive"]), outside["season"], _utc(outside["start"])
    snaps = {p: sb.snapshots(archive, season, p) for p in sb.POINTS if p.site_code}
    rows = []
    for r in refs:
        m = r.manifest
        if _utc(m.valid_time) <= start or not scorable(m):
            continue
        site = m.site_code.value
        issue = outside_issue_time(m)
        case = truth = None
        for p in [p for p in snaps if p.site_code == site]:
            got = sb.issued_snapshot(snaps[p], issue)
            if got is None:
                continue
            issued, path = got
            result = sb.profile_at(sb.load_snapshot(path), _utc(m.valid_time), issued)
            if result is None:
                continue
            case = case or load_visible_case(r.case_dir)
            truth = truth or scoring_truth(r.case_dir, m).truth_profile
            pred = SnowpackAgent(std, backend=FixedEngineResult(result)).predict(case, 0)
            pred = stamp_prediction(pred, m)
            with scoring.scoring_version(version):
                s = scoring.score_case(pred, truth, m.target_scope)
            s.pop("status", None)
            aid, name = sb.AGENTS[p.run]
            x = s | {"composite": scoring.case_composite(s, weights), "status": pred.status}
            rows.append({"case_id": m.case_id, "agent_id": aid, "name": name, "scoring_version": version,
                         "site_code": site, "case_type": m.case_type.value,
                         "pit_utc": _utc(m.valid_time).isoformat(), "as_of_utc": _utc(m.as_of_time).isoformat(),
                         "issued_utc": issued.isoformat(), "point": f"{p.area}/{p.location}",
                         "profile_lag_h": result.profile_lag_h,
                         "scored_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                         **{k: (None if isinstance(x.get(k), float) and math.isnan(x[k]) else x.get(k))
                            for k in KEEP}})
    return rows


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
                   do_import: bool = True, outside_archive: Path | None = None, hrdps: bool = True) -> dict:
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
    from snowagent.ops.snowpackbc import ARCHIVE as OUTSIDE_ARCHIVE
    from snowagent.ops.snowpackbc import first_snapshot

    outside_archive = Path(outside_archive or OUTSIDE_ARCHIVE)
    start = first_snapshot(outside_archive, season)  # SnowpackBC's saved forecasts (ADR-112)
    outside = {"archive": str(outside_archive), "season": season, "start": start.isoformat()} if start else None
    if entries:
        earliest = min(_utc(e.frozen_utc) for _, e in entries)
        pits = [p for p in season_pits(season, observed) if _utc(p["time"]) > earliest]
        res["pits"] = len(pits)
        state = {"pits": pits, "entries": sorted(aid for aid, _ in entries)}
        if outside:  # its first scoring, then each new pit (not each new snapshot)
            state["outside_start"] = outside["start"]
        hrdps_root = Path(str(lab_root) + "_hrdps") if hrdps else None
        if hrdps_root is not None:
            state["hrdps_start"] = HRDPS_START
        old = json.loads(state_path.read_text()) if state_path.is_file() else None
        if pits and state != old:
            ids = [p["id"] for p in pits if p["id"]]
            if ids:
                res |= score_new(entries, ids, store_path, lab_root, repo, engine, cfg=cfg, do_import=do_import,
                                 outside=outside, hrdps_root=hrdps_root)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state, indent=1) + "\n")
    summary = summarise(load_store(store_path), entries, season, now, outside["start"] if outside else None,
                        HRDPS_START if hrdps else None)
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
