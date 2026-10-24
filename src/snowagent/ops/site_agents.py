"""Experimental evolved agents on the public site (ADR-084, owner 2026-10-06: "Now, as experimental").

Up to ``MAX_AGENTS`` SNOWPACK-family genomes, sent from the lab app to the ``site-agents`` branch (one JSON file per
agent under ``site_agents/``), are run by the daily update for the live season at every plot, beside standard
SNOWPACK, and written to ``web/data/<plot>/<season>_agents.json`` with an index ``web/data/agents.json``. The site
shows them only when chosen, labelled experimental and not validated; standard SNOWPACK stays the default and the
pit-steered nowcast stays SNOWPACK's.

An agent runs weather-only (no pit steering), like the site's "free" weather input, with its physics genes applied
as in the lab (ADR-070): precipitation and wind multipliers on measured and reanalysis hours (not the GFS day-1
fill), the rain/snow ramp, and the allow-listed io.ini keys. Its output genes (layer reading, uncertainty) are lab
scoring choices and do not apply to the site's raw SNOWPACK layers. An agent whose snowfall comes from the snow depth
sensor (ADR-100) runs as in the lab (ADR-101): driven by the plot's measured snow depth (the ``hs_check`` stations,
the first QC-ok reading each hour) up to 00 UTC of the last reading's day, then by precipitation; with too few
readings, or in a season before the stations, it runs on precipitation, and the output says which.

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


def observed_snow_depth(stations: list[str], load=None) -> pd.Series:
    """Hourly measured snow depth (m) of a plot: per hour the first of ``stations`` with a QC-ok reading, as the lab's
    weather ingest picks it (ADR-056); empty without readings."""
    if load is None:
        from snowagent.ingest.fts360 import load_station as load
    out = pd.Series(dtype=float)
    for st in stations:
        d = load(st)
        if d.empty or "hs_m" not in d:
            continue
        ok = d[d["hs_m_qc"] == "ok"] if "hs_m_qc" in d else d
        hs = pd.Series(ok["hs_m"].to_numpy(dtype=float), index=pd.DatetimeIndex(ok["time_utc"]).round("h"))
        if hs.index.tz is None:
            hs.index = hs.index.tz_localize("UTC")
        hs = hs[~hs.index.duplicated()].dropna()
        out = hs if out.empty else out.combine_first(hs)
    return out.sort_index()


def snow_depth_plan(obs: pd.Series, index: pd.DatetimeIndex, start: pd.Timestamp, end: pd.Timestamp
                    ) -> tuple[pd.Series | None, pd.Timestamp | None]:
    """HS forcing (m) on ``index`` and the switch to precipitation (ADR-101): 00 UTC of the last reading's day before
    ``end``, a day earlier when that leaves under 6 h (the last segment must write a profile). ``(None, None)`` when
    no switch fits inside the run or the readings cover too little of it (lab rule, ADR-100)."""
    from snowagent.lab.agents.snowpack import snow_depth_forcing

    obs = obs[obs.index <= end] if not obs.empty else obs
    if obs.empty:
        return None, None
    switch = obs.index.max().floor("D")
    if end - switch < pd.Timedelta(hours=6):
        switch -= pd.Timedelta(days=1)
    if not start < switch < end:
        return None, None
    hs = snow_depth_forcing(obs, index, start, switch)
    return (None, None) if hs is None else (hs, switch)


def run_column(eng, settings, run_dir: Path, unit, smet: pd.DataFrame, start: pd.Timestamp,
               end: pd.Timestamp, hs: pd.Series | None = None, switch: pd.Timestamp | None = None) -> list:
    """The engine run(s) of one agent season: one run from snow-free, or with a snow depth forcing a run driven by it
    to ``switch`` and a precipitation run from that state to ``end`` (ADR-101). Returns the runs' outputs in order."""
    import dataclasses

    from snowagent.engine.column import prepare_and_run

    if switch is None:
        return [prepare_and_run(eng, settings, run_dir, unit, smet, end.to_pydatetime(),
                                snowfree_start=start.to_pydatetime())]
    driven = dataclasses.replace(settings, snow_depth_driven=True)
    first = prepare_and_run(eng, driven, run_dir / "seg0", unit, smet[smet.index <= switch].assign(HS=hs),
                            switch.to_pydatetime(), snowfree_start=start.to_pydatetime())
    rest = prepare_and_run(eng, settings, run_dir / "seg1", unit, smet[smet.index >= switch - pd.Timedelta(hours=6)],
                           end.to_pydatetime(), initial_sno=Path(first.sno))
    return [first, rest]


def run_agent_season(plot: str, y: int, agent_id: str, agent: SiteAgent, work: Path,
                     now: pd.Timestamp | None = None, spec=None) -> dict:
    """One agent's weather-only run of season ``y`` at ``plot``: its 6-hourly profiles, daily snow depth and its
    score at each pit, with the run's traceability (run id, forcing and config hashes, engine version)."""
    import dataclasses
    import hashlib
    import shutil

    from snowagent.baseline.evaluate import plot_pits
    from snowagent.baseline.run import plot_unit
    from snowagent.engine import snowpack as sp
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
    hs_m = switch = None
    if phys.snowfall_source == "snow_depth" and measured:
        hs_m, switch = snow_depth_plan(observed_snow_depth(p.get("hs_check", [])), uf.smet.index, start, end)
    outs = run_column(eng, settings, run_dir, unit, uf.smet, start, end, hs_m, switch)
    out = outs[-1]
    skipped: list[dict] = []
    every = None if measured else {t for t in pd.date_range(start, end, freq="D") + pd.Timedelta(hours=18)}
    profiles, seen = [], set()
    for o in outs:
        for pr in _profiles(o.pro, 0.0, every, skipped):
            if pr["t"] not in seen:
                seen.add(pr["t"])
                profiles.append(pr)
    met = pd.concat([sp.parse_met(o.met) for o in outs])
    met = met[~met.index.duplicated(keep="first")]
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
    fhashes = [_forcing_hash(o.run_dir) for o in outs]
    fhash = fhashes[0] if len(fhashes) == 1 else hashlib.sha256("".join(fhashes).encode()).hexdigest()[:16]
    chash = used.config_hash()
    snowfall = None
    if phys.snowfall_source == "snow_depth":
        snowfall = ({"source": "snow depth sensor", "until_utc": switch.strftime("%Y-%m-%dT%H")} if switch is not None
                    else {"source": "precipitation", "reason": "too few snow depth sensor readings"})
    return {"id": agent_id, "name": agent.name, "label": LABEL, "family": g.family.value,
            "genome_hash": g.genome_hash, "genes": g.genes, "source": agent.source.model_dump(),
            "added_utc": agent.added_utc, "physics": phys.as_dict(), **({"snowfall": snowfall} if snowfall else {}),
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
