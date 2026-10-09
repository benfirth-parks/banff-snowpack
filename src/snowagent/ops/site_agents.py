"""Experimental evolved agents on the public site (ADR-084, owner 2026-10-06: "Now, as experimental").

Up to ``MAX_AGENTS`` SNOWPACK-family genomes, sent from the lab app to the ``site-agents`` branch (one JSON file per
agent under ``site_agents/``), are run by the daily update for the live season at every plot, beside standard
SNOWPACK, and written to ``web/data/<plot>/<season>_agents.json`` with an index ``web/data/agents.json``. The site
shows them only when chosen, labelled experimental and not validated; standard SNOWPACK stays the default and the
pit-steered nowcast stays SNOWPACK's.

An agent runs weather-only (no pit steering), like the site's "free" weather input, with its physics genes applied
as in the lab (ADR-070): precipitation and wind multipliers on measured and reanalysis hours (not the GFS day-1
fill), the rain/snow ramp, and the allow-listed io.ini keys. Its output genes (layer reading, uncertainty) are lab
scoring choices and do not apply to the site's raw SNOWPACK layers.

The files come from a branch anyone with push access can write: they are data, validated strictly (the genome
contract: allow-listed genes, ranges, no data; family snowpack only; a short display name). Anything else is skipped
with a warning, never run."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

BRANCH = "site-agents"
FOLDER = "site_agents"
MAX_AGENTS = 3
MAX_FILE_BYTES = 16_384
PLOT_CODES = {"bow_summit": "BOW", "goats_eye": "GOAT", "simpson": "SIMP"}
NAME = re.compile(r"^[A-Za-z0-9 _.:+()'-]{1,48}$")
LABEL = ("Experimental evolved agent: not validated (no promotion check passed). Research and decision support "
         "only, not an avalanche forecast. Standard SNOWPACK is the site's model.")


class SiteAgentSource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str | None = Field(default=None, max_length=120)
    round: int | None = None
    rank: int | None = None
    composite: float | None = None  # in-sample composite of the training run
    locked_composite: float | None = None  # on the run's locked test winters (ADR-083), if any
    locked_seasons: list[str] = Field(default_factory=list, max_length=10)
    incumbent_locked_composite: float | None = None


class SiteAgent(BaseModel):
    """One file of the ``site-agents`` branch."""

    model_config = ConfigDict(extra="forbid")

    name: str
    genome: dict
    source: SiteAgentSource = Field(default_factory=SiteAgentSource)
    added_utc: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME.match(v):
            raise ValueError("name: 1-48 letters, digits, spaces or _.:+()'-")
        return v

    def checked_genome(self, spec=None):
        """The genome under the lab's contract (ValueError otherwise); SNOWPACK family only."""
        from snowagent.lab.schemas.genome import AgentFamily, AgentGenome

        g = AgentGenome.model_validate(self.genome, context={"spec": spec} if spec else None)
        if g.family != AgentFamily.snowpack:
            raise ValueError(f"family {g.family.value}: only SNOWPACK-family agents run on the site")
        return g


def agent_file(name: str, genome: dict, source: dict | None = None, now: datetime | None = None) -> dict:
    """The JSON the lab app sends for one agent (validated before it is written)."""
    rec = {"name": name, "genome": genome, "source": source or {},
           "added_utc": (now or datetime.now(UTC)).isoformat(timespec="seconds")}
    SiteAgent.model_validate(rec).checked_genome()
    return rec


# --------------------------------------------------------------------------------------------- reading the branch


def _git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=120)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[:300]}")
    return r.stdout


def branch_files(repo: Path, ref: str = f"origin/{BRANCH}", fetch: bool = True, folder: str = FOLDER
                 ) -> dict[str, bytes]:
    """name -> content of ``<folder>/*.json`` on the branch (default ``site_agents/``; the blind test's entries are in
    ``blind_test/``, ADR-086); {} when the branch does not exist."""
    if fetch:
        _git(repo, "fetch", "--quiet", "origin", f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}", check=False)
    if subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=repo, capture_output=True).returncode:
        return {}
    names = [n for n in _git(repo, "ls-tree", "--name-only", ref, f"{folder}/").split("\n") if n.endswith(".json")]
    out = {}
    for n in names:
        blob = subprocess.run(["git", "show", f"{ref}:{n}"], cwd=repo, capture_output=True, timeout=60).stdout
        out[Path(n).name] = blob
    return out


def parse_agents(files: dict[str, bytes], spec=None) -> tuple[list[tuple[str, SiteAgent]], list[dict]]:
    """Valid agents (oldest first, at most ``MAX_AGENTS``) and a warning for every file skipped."""
    ok, warnings = [], []
    for name, blob in sorted(files.items()):
        try:
            if len(blob) > MAX_FILE_BYTES:
                raise ValueError(f"{len(blob)} bytes, more than {MAX_FILE_BYTES}")
            agent = SiteAgent.model_validate(json.loads(blob))
            g = agent.checked_genome(spec)
            ok.append((g.agent_id, agent))
        except (ValueError, ValidationError, json.JSONDecodeError) as exc:
            warnings.append({"level": "warning", "source": "site_agents",
                             "message": f"site agent {name} skipped: {str(exc).splitlines()[0][:200]}"})
    ok.sort(key=lambda x: x[1].added_utc)
    seen, uniq = set(), []
    for aid, a in ok:
        if aid not in seen:
            seen.add(aid)
            uniq.append((aid, a))
    if len(uniq) > MAX_AGENTS:
        warnings.append({"level": "warning", "source": "site_agents",
                         "message": f"{len(uniq)} site agents; only the {MAX_AGENTS} oldest run"})
    return uniq[:MAX_AGENTS], warnings


# --------------------------------------------------------------------------------------------- running one


def agent_forcing(pf, genes: dict, plot: str):
    """The site's plot forcing with the agent's forcing genes applied as in the lab (ADR-070): precipitation and
    wind multipliers except on GFS day-1 hours; returns the data and the physics (ini keys, rain/snow ramp)."""
    from snowagent.lab.agents.physics import engine_physics

    phys = engine_physics(genes, PLOT_CODES[plot])
    data = pf.data.copy()
    gfs_p = pf.sources["psum"].astype(str).str.startswith("gfs")
    gfs_w = pf.sources["vw"].astype(str).str.startswith("gfs")
    data.loc[~gfs_p, "psum"] = data.loc[~gfs_p, "psum"] * phys.precip_mult
    data.loc[~gfs_w, "vw"] = data.loc[~gfs_w, "vw"] * phys.wind_mult
    measured = {c: ~pf.sources[c].astype(str).str.startswith("gfs") if c in pf.sources else
                pd.Series(True, index=data.index) for c in ("ta", "ilwr")}
    return phys.adjust_forcing(data, measured), phys


def run_agent_season(plot: str, y: int, agent_id: str, agent: SiteAgent, work: Path,
                     now: pd.Timestamp | None = None, spec=None) -> dict:
    """One agent's weather-only run of season ``y`` at ``plot``: its 6-hourly profiles, daily snow depth and its
    score at each pit, with the run's traceability (run id, forcing and config hashes, engine version)."""
    import dataclasses
    import shutil

    from snowagent.baseline.evaluate import plot_pits
    from snowagent.baseline.run import plot_unit
    from snowagent.engine import snowpack as sp
    from snowagent.engine.column import prepare_and_run
    from snowagent.lab.agents.physics import require_patches
    from snowagent.lab.agents.snowpack import LabEngineSettings
    from snowagent.spatial_forcing.builder import build_unit_forcing
    from snowagent.web.build import (
        OBSERVED,
        _cfg,
        _forcing_hash,
        _pit_record,
        _profiles,
        _score,
        season_forcing,
    )

    g = agent.checked_genome(spec)
    p = _cfg()["plots"][plot]
    pf, start, end, mode = season_forcing(plot, y, now)
    measured = mode in ("station", "live")
    data, phys = agent_forcing(pf, g.genes, plot)
    unit = plot_unit(plot, p["lat"], p["lon"], p["elevation_m"])
    uf = build_unit_forcing(data, p["lat"], p["lon"], p["elevation_m"], unit, phys.forcing_config())
    base = sp.EngineSettings(prof_days_between=0.25, snow_days_between=1.0 if measured else 3650.0,
                             first_backup=0.0 if measured else 400.0)
    settings = LabEngineSettings.from_base(base, 0.0, phys)
    run_dir = Path(work) / f"{plot}_{y}_{agent_id}"
    shutil.rmtree(run_dir, ignore_errors=True)
    eng = sp.find_engine()
    if phys.patches:
        require_patches(phys, eng.binary)  # ADR-092: refused (skipped with a warning) on an engine without the patch
    out = prepare_and_run(eng, settings, run_dir, unit, uf.smet, end.to_pydatetime(),
                          snowfree_start=start.to_pydatetime())
    skipped: list[dict] = []
    every = None if measured else {t for t in pd.date_range(start, end, freq="D") + pd.Timedelta(hours=18)}
    profiles = _profiles(out.pro, 0.0, every, skipped)
    met = sp.parse_met(out.met)
    hs = (met["Modelled snow depth (vertical)"] / 100).resample("D").mean()
    days = pd.date_range(start.floor("D"), end.floor("D"), freq="D")
    pits, _excluded = plot_pits(OBSERVED, plot, start, end)
    pit_scores = []
    for o in pits:
        t = pd.Timestamp(o["obs_time_utc"])
        if not profiles:
            break
        near = min(profiles, key=lambda pr: abs((pd.Timestamp(pr["t"] + ":00", tz="UTC") - t).total_seconds()))
        dt_h = abs((pd.Timestamp(near["t"] + ":00", tz="UTC") - t).total_seconds()) / 3600
        rec = _pit_record(o)
        pit_scores.append({"id": rec["id"], "pit_t": rec["t"], "t": near["t"],
                           **(_score(o, near["L"]) if dt_h <= 13 else {})})
    used = dataclasses.replace(settings, calculation_step_min=float(out.extra.get("calculation_step_min",
                                                                                 settings.calculation_step_min)))
    season = f"{y}-{y + 1}"
    fhash, chash = _forcing_hash(out.run_dir), used.config_hash()
    return {"id": agent_id, "name": agent.name, "label": LABEL, "family": g.family.value,
            "genome_hash": g.genome_hash, "genes": g.genes, "source": agent.source.model_dump(),
            "added_utc": agent.added_utc, "physics": phys.as_dict(),
            "run_id": f"agent-{plot}-{season}-{agent_id}-{chash[:8]}-{fhash[:8]}", "forcing_hash": fhash,
            "engine": {"version": sp.find_engine().version_string, "config_hash": chash},
            "mode": mode, "nowcast_every_h": 6 if measured else 24, "nowcast": profiles, "skipped_profiles": skipped,
            "daily": {"d0": days[0].strftime("%Y-%m-%d"),
                      "hs_model": [None if pd.isna(v) else round(v * 100, 1) for v in hs.reindex(days)]},
            "pits": pit_scores}


def build_agents(y: int, out_dir: Path, work: Path, now: pd.Timestamp | None = None, repo: Path = Path("."),
                 files: dict[str, bytes] | None = None, spec=None) -> dict:
    """Run every valid site agent at every plot for season ``y`` and write the files the site reads. Never raises
    for one agent or plot: each failure is a warning, and the standard SNOWPACK output is untouched. ``files``:
    the branch's files (default: read from git)."""
    from snowagent.web.build import SITES

    now = now or pd.Timestamp.now(tz="UTC")
    warnings: list[dict] = []
    if files is None:
        try:
            files = branch_files(repo)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            files = {}
            warnings.append({"level": "warning", "source": "site_agents",
                             "message": f"could not read the {BRANCH} branch: {str(exc)[:200]}"})
    agents, w = parse_agents(files, spec)
    warnings += w
    season = f"{y}-{y + 1}"
    index = {"label": LABEL, "generated_utc": now.isoformat(timespec="seconds"), "season": season, "agents": [],
             "plots": {}}
    for aid, a in agents:
        index["agents"].append({"id": aid, "name": a.name, "source": a.source.model_dump(), "added_utc": a.added_utc})
    for plot in SITES:
        runs = []
        for aid, a in agents:
            try:
                runs.append(run_agent_season(plot, y, aid, a, work, now, spec))
            except Exception as exc:  # noqa: BLE001 - an experimental agent never stops the update
                warnings.append({"level": "warning", "source": f"site_agents:{plot}",
                                 "message": f"site agent {a.name} at {SITES[plot]}: {type(exc).__name__}: "
                                            f"{str(exc)[:200]}"})
        f = Path(out_dir) / plot / f"{season}_agents.json"
        if runs:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({"label": LABEL, "site": plot, "season": season, "agents": runs},
                                    separators=(",", ":")))
            index["plots"][plot] = {season: f.name}
        elif f.exists():
            f.unlink()  # no agent any more: the site stops offering it
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "agents.json").write_text(json.dumps(index, indent=1))
    return {"agents": [a["id"] for a in index["agents"]], "plots": index["plots"], "warnings": warnings}
