"""The site's main simulated snowpack: an evolved lab column model (ADR-136; owner 2026-11-22: "lets put Carver as
the main simulated snowpack on our app").

The model is named in ``config/site_main_model.json`` (a display name, its genome and where it came from). Swapping
the main model is replacing that file: nothing else names it. Only the lab's column-model families run here
(``process``: the column with switchable processes, ADR-120; ``column``: the plain column, ADR-118); a SNOWPACK
genome stays on the experimental-agent route (ADR-084).

What runs is the lab agent's own code, not a copy: ``lab.agents.column.run_hours`` steps the column hour by hour,
exactly as ``simulate`` does for a lab case, and the site reads the column at its output hours. The season is run
as the lab's cases see it: from snow-free at the season start until the first pit with layers, then from each pit
(its layers, grain forms, hardness and temperatures as observed) to the next one. So a profile at any time is the
model's answer to "the latest pit plus the weather since", which is the question it was trained and tested on.

Weather: the site's plot forcing (stations at the plot, ERA5 or the GFS day-1 fill where a station is missing), at
the plot unit, with the precipitation factors the lab uses (ADR-024/038/106: the plot's gauge factor on gauge hours,
its ERA5 factor on reanalysis hours, raw on GFS hours) and the genome's plot snowfall gene on measured hours. Snowfall
comes from the plot's snow depth sensor up to the switch (00 UTC of the last reading's day, ADR-101), then from
precipitation, as in the lab (ADR-100).

Output, per plot and season: ``web/data/<plot>/<season>_main.json`` (6-hourly profiles in the site's layer layout,
daily snow depth, scores at each pit, run id, genome hash, forcing hash, code version, the pits the run restarted
from) and the index ``web/data/main_model.json``. A failure is a warning on the status page: the standard SNOWPACK
output is never touched, and the page falls back to it."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

from snowagent.ops.site_agents import NAME, PLOT_CODES

CONFIG = Path("config/site_main_model.json")
FAMILIES = ("process", "column")
OUTPUT_EVERY_H = 6
LABEL = ("Main simulated snowpack: an evolved lab column model (no SNOWPACK), chosen by the owner on 2026-11-22 "
         "after it scored level with the best standard SNOWPACK on winters it never saw (better on facets and depth "
         "hoar, worse on crusts, spring and the next pit). Research and decision support only, not an avalanche "
         "forecast. Standard SNOWPACK is one choice away in the Weather input menu.")


class MainModelSource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str | None = Field(default=None, max_length=120)
    label: str | None = Field(default=None, max_length=64)  # the genome's label in its run, e.g. r14-x04-process
    genome_name: str | None = Field(default=None, max_length=48)  # the genome's own agent name, e.g. Carver Daniels
    evidence: str | None = Field(default=None, max_length=400)


class MainModel(BaseModel):
    """``config/site_main_model.json``. ``name`` labels the main model on the site (owner 2026-11-22: "keep it
    at Carver Daniels"); ``source.genome_name`` names the genome behind it, which can be swapped for a better one."""

    model_config = ConfigDict(extra="forbid")

    name: str
    genome: dict
    source: MainModelSource = Field(default_factory=MainModelSource)
    chosen_utc: str
    decision: str = Field(default="ADR-136", max_length=40)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME.match(v):
            raise ValueError("name: 1-48 letters, digits, spaces or _.:+()'-")
        return v

    def checked_genome(self, spec=None):
        """The genome under the lab's contract (ValueError otherwise); a column-model family only."""
        from snowagent.lab.schemas.genome import AgentGenome

        g = AgentGenome.model_validate(self.genome, context={"spec": spec} if spec else None)
        if g.family.value not in FAMILIES:
            raise ValueError(f"family {g.family.value}: the site's main model must be one of {', '.join(FAMILIES)}")
        return g


def load_main(path: Path = CONFIG) -> MainModel | None:
    """The configured main model, or None when the file is absent (the site then shows standard SNOWPACK first)."""
    if not Path(path).is_file():
        return None
    return MainModel.model_validate(json.loads(Path(path).read_text()))


def model_parts(genome):
    """(genes, a new extension per run, agent version) of a column-model genome, as its lab agent uses them."""
    from snowagent.lab.agents import column as cm
    from snowagent.lab.agents import process as pr

    if genome.family.value == "process":
        g = pr.genes_of(genome)
        return g, (lambda: pr.ProcessExt(g)), pr.AGENT_VERSION
    return dict(genome.genes), (lambda: None), cm.AGENT_VERSION


def code_version() -> str:
    """sha256 (16 hex) of the column model's source and this module: what makes a profile, besides genes and weather."""
    import snowagent.lab.agents.column as cm
    import snowagent.lab.agents.common as common
    import snowagent.lab.agents.process as pr

    h = hashlib.sha256()
    for mod in (cm, common, pr):
        h.update(Path(mod.__file__).read_bytes())
    h.update(Path(__file__).read_bytes())
    return h.hexdigest()[:16]


# --------------------------------------------------------------------------------------------- inputs


def lab_psum_factors(sources: pd.Series, plot_cfg: dict) -> np.ndarray:
    """Per hour, the factor the lab applies to the plot's precipitation (ADR-106): raw on GFS hours, the ERA5 factor on
    reanalysis hours, the gauge factor otherwise."""
    gauge = float(plot_cfg.get("psum_factor", 1.0))
    era5 = float(plot_cfg.get("era5_psum_factor", gauge))
    s = sources.astype(str)
    return np.where(s.str.startswith("gfs"), 1.0, np.where(s.str.startswith("era5"), era5, gauge))


def column_inputs(pf, smet: pd.DataFrame, plot_cfg: dict, hs: pd.Series | None):
    """The column's ``Forcing`` from the site's plot forcing (``pf``, with the site's uniform gauge factor already in
    its precipitation) and its unit forcing ``smet``; the clock is hours after ``smet``'s first hour."""
    from snowagent.lab.agents.column import Forcing

    idx = smet.index
    ref = idx[0]
    src = pf.sources["psum"].reindex(idx).fillna("missing")
    site_factor = float(plot_cfg.get("psum_factor", 1.0))
    raw = smet["PSUM"].to_numpy(dtype=float) / site_factor
    psum = np.nan_to_num(raw, nan=0.0).clip(min=0.0) * lab_psum_factors(src, plot_cfg)
    gfs = src.astype(str).str.startswith("gfs").to_numpy()
    lw = smet["ILWR"].ffill().bfill().to_numpy(dtype=float)
    if not np.isfinite(lw).all():
        raise ValueError("no incoming longwave in the forcing")
    t = ((idx - ref) / pd.Timedelta(hours=1)).to_numpy(dtype=float)
    hs_arr = None if hs is None else hs.reindex(idx).to_numpy(dtype=float)
    return Forcing(t=t, ta=smet["TA"].to_numpy(dtype=float) - 273.15, rh=smet["RH"].to_numpy(dtype=float),
                   vw=smet["VW"].to_numpy(dtype=float), sw=smet["ISWR"].to_numpy(dtype=float), lw=lw, psum=psum,
                   measured=~gfs, hs=hs_arr,
                   dw=smet["DW"].to_numpy(dtype=float) if "DW" in smet else None), ref


def start_pits(records: list[dict], site_code: str, ref: pd.Timestamp) -> list[tuple[dict, object]]:
    """The plot's pits with layers as the column starts from them (the lab's ``VisiblePit``: layers, temperatures and
    snow depth, times on the forcing's clock), oldest first. A record the lab's import refuses is left out."""
    from snowagent.lab.ingest.profiles import profile_from_observed
    from snowagent.lab.schemas.benchmark import VisibleLayer, VisiblePit
    from snowagent.lab.schemas.common import SiteCode

    fields = set(VisibleLayer.model_fields)
    out = []
    for rec in records:
        try:
            p = profile_from_observed(rec, SiteCode(site_code))
        except (ValueError, TypeError):
            continue
        if not p.layers:
            continue
        t = pd.Timestamp(p.observed_at)
        t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
        layers = [VisibleLayer(**{k: v for k, v in ly.model_dump().items() if k in fields}) for ly in p.layers]
        vp = VisiblePit(pit_key=f"pit_{len(out) + 1:02d}", t_rel_h=(t - ref) / pd.Timedelta(hours=1),
                        day_of_year=min(t.dayofyear, 366), season_offset=0, available_rel_h=(t - ref) / pd.Timedelta(hours=1),
                        availability_assumption="observed_at", snow_depth_m=p.snow_depth_m,
                        profile_depth_m=p.profile_depth_m, temperatures=p.temperatures, layers=layers)
        out.append((rec, vp))
    out.sort(key=lambda x: x[1].t_rel_h)
    return out


# --------------------------------------------------------------------------------------------- output


def site_rows(col, g: dict, pit) -> list[list]:
    """The column as the site's display layers (top first): [top_cm, bottom_cm, grain, hardness, density, temp_c,
    lwc_pct, flags] (heights above ground; flags bit 1 crust, bit 2 persistent grain form), the layers the lab scores
    (``layers_from_column``) with temperature and liquid water averaged over their elements."""
    from snowagent.lab.agents.column import layers_from_column

    if not len(col):
        return []
    hs = col.hs
    tops = np.cumsum(col.dz)
    bots = tops - col.dz
    water_pct = np.where(col.dz > 0, col.water / 1000.0 / np.maximum(col.dz, 1e-9) * 100.0, 0.0)
    rows = []
    for ly in layers_from_column(col, g, pit):
        top_h, bot_h = hs - ly.top, hs - ly.bottom
        ov = np.clip(np.minimum(tops, top_h) - np.maximum(bots, bot_h), 0.0, None)
        w = ov.sum()
        temp = float((col.T * ov).sum() / w) if w > 0 else float("nan")
        lwc = float((water_pct * ov).sum() / w) if w > 0 else 0.0
        grain = ly.grain or ""
        flags = (1 if grain.startswith(("MFcr", "IF")) else 0) | (2 if grain[:2] in ("FC", "DH", "SH") else 0)
        rows.append([max(round(top_h * 100, 1), 0.0), max(round(bot_h * 100, 1), 0.0), grain,
                     None if ly.hardness_index is None else round(float(ly.hardness_index), 2),
                     None if ly.density is None else round(float(ly.density)),
                     None if not math.isfinite(temp) else round(min(temp, 0.0), 1), round(lwc, 1), flags])
    return [r for r in rows if r[0] > r[1]]


def step_season(wx, times: pd.DatetimeIndex, starts: list[tuple[dict, object]], g: dict, new_ext, site_code: str,
                want) -> tuple[list[dict], pd.Series, list[dict]]:
    """The season as consecutive column runs: from snow-free to the first start pit, then from each pit to the next
    (``starts``: ``start_pits``). Returns the profiles at the hours ``want(time)`` selects (time order), the hourly
    modelled depth (m) and the pits the runs started from."""
    from snowagent.lab.agents.column import run_hours

    horizon = float(wx.t[-1])
    profiles: dict[str, dict] = {}
    hs_hourly = pd.Series(np.nan, index=times)
    used: list[dict] = []
    segments = [(None, None), *starts]
    for k, (rec, pit) in enumerate(segments):
        nxt = segments[k + 1][1] if k + 1 < len(segments) else None
        stop = nxt.t_rel_h if nxt is not None else math.inf
        if pit is not None and pit.t_rel_h > horizon:
            break
        if pit is not None:
            used.append({"id": rec["profile_id"], "t": pd.Timestamp(rec["obs_time_utc"]).strftime("%Y-%m-%dT%H:%M")})
        for col, _notes, i in run_hours(wx, pit, g, new_ext(), site_code, min(horizon, stop + 1.0)):
            if i < 0 or wx.t[i] >= stop - 1e-6:
                continue  # the next pit starts its own run from this hour
            ts = times[i]
            hs_hourly.iloc[i] = col.hs
            if want(ts):
                swe = float(col.ice.sum() + col.water.sum()) if len(col) else 0.0
                key = ts.strftime("%Y-%m-%dT%H")
                profiles[key] = {"t": key, "hs": round(col.hs * 100, 1), "swe": round(swe),
                                 "L": site_rows(col, g, pit)}
    return [profiles[k] for k in sorted(profiles)], hs_hourly, used


def run_main_season(plot: str, y: int, model: MainModel, now: pd.Timestamp | None = None, spec=None,
                    observed: Path | None = None) -> dict:
    """The main model's run of season ``y`` at ``plot``: profiles every 6 h (daily at 18 UTC in a reanalysis season),
    daily snow depth and a score at each pit, with the run's traceability."""
    from snowagent.baseline.evaluate import plot_pits
    from snowagent.baseline.run import plot_unit
    from snowagent.baseline.snow_depth import observed_snow_depth, snow_depth_plan
    from snowagent.spatial_forcing.builder import ForcingConfig, build_unit_forcing
    from snowagent.web.build import OBSERVED, _cfg, _pit_record, _score, season_forcing

    genome = model.checked_genome(spec)
    g, new_ext, agent_version = model_parts(genome)
    p = _cfg()["plots"][plot]
    pf, start, end, mode = season_forcing(plot, y, now)
    measured = mode in ("station", "live")
    unit = plot_unit(plot, p["lat"], p["lon"], p["elevation_m"])
    smet = build_unit_forcing(pf.data, p["lat"], p["lon"], p["elevation_m"], unit, ForcingConfig()).smet
    smet = smet[(smet.index >= start) & (smet.index <= end)]
    hs = switch = None
    if measured:
        hs, switch = snow_depth_plan(observed_snow_depth(p.get("hs_check", [])), smet.index, start, end)
    wx, ref = column_inputs(pf, smet, p, hs)
    site_code = PLOT_CODES[plot]
    pits, _excluded = plot_pits(Path(observed or OBSERVED), plot, start, end)
    starts = start_pits(pits, site_code, ref)
    every_h = OUTPUT_EVERY_H if measured else 24
    want = (lambda ts: ts.hour % OUTPUT_EVERY_H == 0) if measured else (lambda ts: ts.hour == 18)
    nowcast, hs_hourly, used = step_season(wx, smet.index, starts, g, new_ext, site_code, want)
    days = pd.date_range(start.floor("D"), end.floor("D"), freq="D")
    hs_daily = hs_hourly.resample("D").mean()
    pit_scores = []
    for o in pits:  # each pit against the last profile before it: from the run of the pit before, never this one
        t = pd.Timestamp(o["obs_time_utc"])
        t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
        rec = _pit_record(o)
        before = [pr for pr in nowcast if pd.Timestamp(pr["t"] + ":00", tz="UTC") <= t]
        if not before:
            continue
        near = before[-1]
        dt_h = (t - pd.Timestamp(near["t"] + ":00", tz="UTC")) / pd.Timedelta(hours=1)
        pit_scores.append({"id": rec["id"], "pit_t": rec["t"], "t": near["t"],
                           **(_score(o, near["L"]) if dt_h <= 13 and near["L"] else {})})
    fh = hashlib.sha256()
    for a in (wx.t, wx.ta, wx.rh, wx.vw, wx.sw, wx.lw, wx.psum, wx.measured.astype(float),
              wx.hs if wx.hs is not None else np.zeros(0)):
        fh.update(np.ascontiguousarray(np.round(a, 6)).tobytes())
    fhash = fh.hexdigest()[:16]
    ghash = genome.genome_hash
    cver = code_version()
    season = f"{y}-{y + 1}"
    snowfall = ({"source": "snow depth sensor", "until_utc": switch.strftime("%Y-%m-%dT%H"),
                 "stations": p.get("hs_check", []), "then": "precipitation"} if switch is not None
                else {"source": "precipitation"} | ({"reason": "too few snow depth sensor readings"} if measured else {}))
    return {"id": genome.agent_id, "name": model.name, "label": LABEL, "family": genome.family.value,
            "genome_hash": ghash, "genes": genome.genes, "source": model.source.model_dump(),
            "chosen_utc": model.chosen_utc, "decision": model.decision, "snowfall": snowfall,
            "run_id": f"main-{plot}-{season}-{genome.agent_id}-{cver[:8]}-{fhash[:8]}", "forcing_hash": fhash,
            "engine": {"version": f"lab {genome.family.value} model {agent_version} (no SNOWPACK)",
                       "code_version": cver, "config_hash": ghash[:16]},
            "mode": mode, "nowcast_every_h": every_h, "nowcast": nowcast,
            "restarts": {"method": "layers", "pits": used, "profile_ids": [u["id"] for u in used]},
            "daily": {"d0": days[0].strftime("%Y-%m-%d"),
                      "hs_model": [None if pd.isna(v) else round(v * 100, 1) for v in hs_daily.reindex(days)]},
            "pits": pit_scores}


def build_main(y: int, out_dir: Path, now: pd.Timestamp | None = None, config: Path = CONFIG,
               seasons: list[int] | None = None, spec=None, observed: Path | None = None) -> dict:
    """Run the main model at every plot for season ``y`` (or each of ``seasons``, a backfill) and write the files the
    site reads; ``write_index`` lists each season's file in sites.json (key ``main``). Never raises for one plot: a
    failure is a warning and the plot keeps its last file (the page says when that run was made), so standard
    SNOWPACK's output and the deploy check (ADR-045) are untouched. Each file names the model that made it, so past
    seasons run by an earlier main model stay labelled with that model until they are run again."""
    from snowagent.web.build import SITES

    now = now or pd.Timestamp.now(tz="UTC")
    out_dir = Path(out_dir)
    warnings: list[dict] = []
    try:
        model = load_main(config)
        genome = model.checked_genome(spec) if model is not None else None
    except (ValueError, OSError) as exc:
        model = genome = None
        warnings.append({"level": "warning", "source": "main_model",
                         "message": f"main model not run ({config}): {str(exc).splitlines()[0][:200]}; the site "
                                    "shows standard SNOWPACK first"})
    index_path = out_dir / "main_model.json"
    if model is None:
        if index_path.exists():
            index_path.unlink()  # no main model: the page shows standard SNOWPACK first again
        return {"model": None, "plots": {}, "warnings": warnings}
    built: dict[str, list[str]] = {}
    for yy in seasons or [y]:
        season = f"{yy}-{yy + 1}"
        for plot in SITES:
            try:
                run = run_main_season(plot, yy, model, now, spec, observed)
            except Exception as exc:  # noqa: BLE001 - the main model never stops the update
                kept = (out_dir / plot / f"{season}_main.json").exists()
                warnings.append({"level": "warning", "source": f"main_model:{plot}",
                                 "message": f"main model {model.name} at {SITES[plot]} {season}: "
                                            f"{type(exc).__name__}: {str(exc)[:200]}; "
                                            + ("the page keeps its last run" if kept else "standard SNOWPACK shown")})
                continue
            f = out_dir / plot / f"{season}_main.json"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({"label": LABEL, "site": plot, "season": season,
                                     "generated_utc": now.isoformat(timespec="seconds"), "main": run},
                                    separators=(",", ":")))
            built.setdefault(plot, []).append(season)
    index = {"label": LABEL, "generated_utc": now.isoformat(timespec="seconds"),
             "model": {"id": genome.agent_id, "name": model.name, "family": genome.family.value,
                       "genome_hash": genome.genome_hash, "source": model.source.model_dump(),
                       "chosen_utc": model.chosen_utc, "decision": model.decision}}
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, indent=1))
    return {"model": genome.agent_id, "plots": built, "warnings": warnings}
