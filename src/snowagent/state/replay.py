"""History replay: initialize or advance the analysis state checkpoint.

Initialization policy (ADR-005):
- With a valid checkpoint, forecasts start from it (``StateStore.latest_valid``).
- Without one, a checkpoint can be built by replaying historical weather from an
  EXPLICIT snow-free start. The start must fall in configured snow-free months
  for the domain; otherwise ``initialization_required`` is returned, because
  silently starting a midwinter domain from bare ground would fabricate a
  snowpack deficit.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from snowagent.contracts import StateCheckpoint, TerrainDomain, TerrainUnit, UnitState
from snowagent.engine import snowpack as sp
from snowagent.engine.column import mass_budget, prepare_and_run
from snowagent.engine.profiles import LINEAGE_BASIS, convert_profile
from snowagent.errors import EngineRunFailed, InitializationRequired, StateGap
from snowagent.spatial_forcing.builder import ForcingConfig, build_unit_forcing
from snowagent.state.store import StateStore
from snowagent.weather.io import WeatherSeries

TAIL_HOURS = 6
PARAMETER_VERSION = "phys-0.1-uncalibrated"


@dataclass(frozen=True)
class InitPolicy:
    snow_free_months: tuple[int, ...] = (7, 8, 9, 10)
    min_spinup_hours: int = 24 * 7
    buffer_hours: int = 6  # forcing needed before the state time (PSUM accumulation)


def _workers() -> int:
    return max(1, min(8, os.cpu_count() or 1))


def parallel_map(fn: Callable, items: list) -> list:
    with ThreadPoolExecutor(max_workers=_workers()) as ex:
        return list(ex.map(fn, items))


def _forcing_hash(series: list[WeatherSeries]) -> str:
    return sp.sha256_text("|".join(s.sha256 for s in series))[:16]


def _profile_payload(unit: TerrainUnit, pro_path: Path, at: pd.Timestamp) -> dict:
    _, profs = sp.parse_pro(pro_path)
    match = [p for p in profs if p.time == at]
    if not match:
        raise EngineRunFailed(f"engine output lacks a profile at state time {at} for {unit.unit_id}")
    layers, diag, nulls, ident = convert_profile(match[-1], unit.slope_deg, unit.unit_id)
    return {"valid_time": at.isoformat(), "layers": [ly.model_dump(mode="json") for ly in layers],
            "diagnostics": diag.model_dump(mode="json"), "nulls": [n.model_dump() for n in nulls],
            "lineage_basis": LINEAGE_BASIS, "identity_uncertainty": ident}


def _seal(store: StateStore, domain: TerrainDomain, staging: Path, results: list[dict], analysis_time: pd.Timestamp,
          parent: StateCheckpoint | None, lineage: list[str], forcing_hash: str, cutoff: pd.Timestamp,
          initialization: str, engine: sp.EngineInfo, settings: sp.EngineSettings, synthetic: bool
          ) -> StateCheckpoint:
    units = [UnitState(unit_id=r["unit_id"], sno_file=f"units/{r['unit_id']}.sno",
                       sha256=sp.sha256_file(staging / "units" / f"{r['unit_id']}.sno"),
                       hs_vertical_m=r["profile"]["diagnostics"]["hs_vertical_m"],
                       swe_kg_m2_per_slope_area=r["profile"]["diagnostics"]["swe_kg_m2_per_slope_area"])
             for r in results]
    version = store.next_analysis_version(domain.domain_id, analysis_time.to_pydatetime())
    state_id = f"st_{analysis_time:%Y%m%dT%H%M}Z_v{version}_{forcing_hash[:8]}"
    cp = StateCheckpoint(
        state_id=state_id, domain_id=domain.domain_id, analysis_time=analysis_time.to_pydatetime(),
        analysis_version=version, parent_state_id=parent.state_id if parent else None,
        terrain_version=domain.terrain_version, engine_version=engine.version_string,
        engine_config_hash=settings.config_hash(), parameter_version=PARAMETER_VERSION,
        forcing_lineage=lineage, forcing_hash=forcing_hash, assimilation_cutoff=cutoff.to_pydatetime(),
        initialization=initialization, units=units, synthetic=synthetic, created_utc=datetime.now(UTC))
    (staging / "budget.json").write_text(json.dumps({r["unit_id"]: r["budget"] for r in results}, indent=1))
    return store.write(cp, staging)


def _run_units(domain: TerrainDomain, units: list[TerrainUnit], forcing_src: pd.DataFrame, src: WeatherSeries,
               end: pd.Timestamp, work: Path, staging: Path, engine: sp.EngineInfo, settings: sp.EngineSettings,
               fcfg: ForcingConfig, start_sno: dict[str, Path] | None, snowfree_start: pd.Timestamp | None,
               tails: dict[str, pd.DataFrame] | None, swe0: dict[str, float] | None) -> list[dict]:
    (staging / "units").mkdir(parents=True, exist_ok=True)
    (staging / "profiles").mkdir(parents=True, exist_ok=True)

    def one(unit: TerrainUnit) -> dict:
        uf = build_unit_forcing(forcing_src, src.meta.lat, src.meta.lon, src.meta.source_elevation_m, unit, fcfg,
                                src.meta.elevation_adjusted_by_provider)
        smet = uf.smet
        if tails is not None:
            smet = pd.concat([tails[unit.unit_id], smet[smet.index > tails[unit.unit_id].index[-1]]])
        run_dir = work / unit.unit_id
        out = prepare_and_run(engine, settings, run_dir, unit, smet, end.to_pydatetime(),
                              initial_sno=None if start_sno is None else start_sno[unit.unit_id],
                              snowfree_start=None if snowfree_start is None else snowfree_start.to_pydatetime())
        hdr = sp.read_sno_header(out.sno)
        if pd.Timestamp(hdr["ProfileDate"]).tz_localize("UTC") != end:
            raise EngineRunFailed(f"restart state for {unit.unit_id} dated {hdr['ProfileDate']}, expected {end}")
        shutil.copyfile(out.sno, staging / "units" / f"{unit.unit_id}.sno")
        smet[smet.index <= end].tail(TAIL_HOURS).to_csv(staging / "units" / f"{unit.unit_id}.tail.csv")
        profile = _profile_payload(unit, out.pro, end)
        (staging / "profiles" / f"{unit.unit_id}.json").write_text(json.dumps(profile))
        met = sp.parse_met(out.met)
        budget = mass_budget(met, unit.slope_deg, 0.0 if swe0 is None else swe0[unit.unit_id],
                             profile["diagnostics"]["swe_kg_m2_per_slope_area"])
        return {"unit_id": unit.unit_id, "profile": profile, "budget": budget}

    return parallel_map(one, units)


def initialize_from_history(domain: TerrainDomain, history: WeatherSeries, store: StateStore, until: datetime,
                            engine: sp.EngineInfo, settings: sp.EngineSettings, fcfg: ForcingConfig,
                            initial_condition: str | None, policy: InitPolicy | None = None,
                            work_root: Path | None = None) -> StateCheckpoint:
    policy = policy or InitPolicy()
    until_ts = pd.Timestamp(until)
    if initial_condition != "snow_free":
        raise InitializationRequired(
            "No checkpoint exists and no defensible initial state was declared. Replay requires an explicit "
            "initial_condition='snow_free' with history starting in the snow-free season, or a supplied state.",
            supported_initial_conditions=["snow_free"])
    if history.data.empty:
        raise InitializationRequired("no historical weather supplied")
    first = history.data.index[0]
    start = first + pd.Timedelta(hours=policy.buffer_hours)
    if start.month not in policy.snow_free_months:
        raise InitializationRequired(
            f"history starts {start.date()}, outside the configured snow-free months {policy.snow_free_months}; "
            "a snow-free start there is not defensible. Supply earlier history or an observed/ensemble initial state.",
            history_start=str(first))
    if until_ts - start < pd.Timedelta(hours=policy.min_spinup_hours):
        raise InitializationRequired(f"history shorter than minimum spin-up {policy.min_spinup_hours} h")
    usable = history.available_by(until_ts + pd.Timedelta(seconds=history.meta.availability_latency_s or 0))
    if usable.data.index[-1] < until_ts:
        raise InitializationRequired(f"history ends {usable.data.index[-1]}, before requested state time {until_ts}")
    window = usable.window(first, until_ts)
    cutoff = pd.Timestamp(until_ts) + pd.Timedelta(seconds=history.meta.availability_latency_s or 0)
    return _replay(domain, history, window, store, until_ts, engine, settings, fcfg, work_root, parent=None,
                   snowfree_start=start, cutoff=cutoff,
                   initialization=f"snow_free start {start.isoformat()} (explicit; month in snow-free window) "
                                  f"+ replay of {history.meta.series_id}")


def advance(domain: TerrainDomain, cp: StateCheckpoint, actuals: WeatherSeries, store: StateStore, until: datetime,
            engine: sp.EngineInfo, settings: sp.EngineSettings, fcfg: ForcingConfig, issue_time: datetime,
            work_root: Path | None = None) -> StateCheckpoint:
    """Advance an analysis checkpoint with actuals available by ``issue_time`` (new checkpoint)."""
    until_ts = pd.Timestamp(until)
    usable = actuals.available_by(issue_time)
    t0 = pd.Timestamp(cp.analysis_time)
    if usable.data.empty or usable.data.index[-1] < until_ts:
        raise StateGap(f"actuals available by {issue_time} end at "
                       f"{usable.data.index[-1] if not usable.data.empty else 'n/a'}; cannot advance {cp.state_id} "
                       f"from {t0} to {until_ts}")
    window = usable.window(t0 + pd.Timedelta(hours=1), until_ts)
    if len(window) != int((until_ts - t0) / pd.Timedelta(hours=1)):
        raise StateGap("actuals have missing hours between checkpoint and target time")
    cutoff = max(pd.Timestamp(cp.assimilation_cutoff),
                 until_ts + pd.Timedelta(seconds=actuals.meta.availability_latency_s or 0))
    return _replay(domain, actuals, window, store, until_ts, engine, settings, fcfg, work_root, parent=cp,
                   snowfree_start=None, cutoff=cutoff, initialization=f"advanced from {cp.state_id}")


def _replay(domain, src, window, store, until_ts, engine, settings, fcfg, work_root, parent, snowfree_start,
            cutoff, initialization) -> StateCheckpoint:
    units = [u for u in domain.units if u.supported]
    base = Path(work_root) if work_root else store.root / domain.domain_id / "work"
    base.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="replay_", dir=base))
    staging = tmp / "staging"
    start_sno = tails = swe0 = None
    lineage = [src.meta.series_id]
    if parent is not None:
        cdir = store.checkpoint_dir(parent.domain_id, parent.state_id)
        start_sno = {u.unit_id: cdir / "units" / f"{u.unit_id}.sno" for u in units}
        tails = {u.unit_id: store.unit_tail(parent, u.unit_id) for u in units}
        swe0 = {s.unit_id: s.swe_kg_m2_per_slope_area for s in parent.units}
        lineage = parent.forcing_lineage + lineage
    results = _run_units(domain, units, window, src, until_ts, tmp / "runs", staging, engine, settings, fcfg,
                         start_sno, snowfree_start, tails, swe0)
    fh = _forcing_hash([src]) if parent is None else sp.sha256_text(parent.forcing_hash + src.sha256)[:16]
    cp = _seal(store, domain, staging, results, until_ts, parent, lineage, fh, cutoff, initialization, engine,
               settings, src.meta.provenance.synthetic)
    shutil.rmtree(tmp, ignore_errors=True)
    return cp
