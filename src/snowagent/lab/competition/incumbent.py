"""Can the SNOWPACK incumbent reuse the site's season run for a case? (ADR-063)

The site already runs SNOWPACK per plot and season (``web/data/<plot>/<season>.json``: 6-hourly steered nowcast
profiles; ``<season>_forecasts.json``: 72 h raw-GFS forecasts from the steered 00 UTC states). A case may reuse such
a profile only when everything that run used was available at the case's as-of time:

1. the run is in ``station`` mode (not the ERA5-only or live seasons);
2. no forcing variable is taken from ERA5 (``forcing_sources``): ERA5 hours count as available only
   ``benchmark.availability.era5_latency_h`` after their hour, and the run uses them right up to the profile time;
3. every pit update (``steer.updates``) at or before the profile time is a pit the case may see (the manifest's
   visible pits);
4. measured stand-in cases: a nowcast profile at most 6 h before the valid time; archived-GFS cases: the site's
   forecast from the case's own run, at most 6 h before the valid time and not before as-of.

Every refusal reason is returned and recorded by the harness; the agent then runs the engine from the visible
package. The site runs of 2015-2026 all take wind and radiation from ERA5 (no plot measurement), so in practice
none qualifies today (ADR-063); the check is kept so a run without reanalysis forcing is reused automatically.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from snowagent.lab.agents.snowpack import EngineLayer, EngineResult
from snowagent.lab.schemas.benchmark import CaseManifest, ForecastSource

MAX_LAG_H = 6.0


@lru_cache(maxsize=16)
def _load(path: str, mtime_ns: int) -> dict:
    return json.loads(Path(path).read_text())


def _read(path: Path) -> dict | None:
    return _load(str(path), path.stat().st_mtime_ns) if path.is_file() else None


def _t(s: str) -> datetime:
    t = datetime.fromisoformat(s if len(s) > 13 else s + ":00")
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _profile(entries: list[dict], start: datetime, valid: datetime) -> dict | None:
    best = None
    for e in entries:
        t = _t(e["t"])
        if start <= t <= valid and valid - t <= timedelta(hours=MAX_LAG_H) and (best is None or t > _t(best["t"])):
            best = e
    return best


def to_engine_result(entry: dict, valid: datetime, version: str, config_hash: str | None,
                     forcing_hash: str | None, updates: int) -> EngineResult:
    """A site profile (rows [top_cm, bottom_cm, grain, hardness, density, T, lwc, flags], heights above ground) as
    an anonymous engine result (depths from the surface)."""
    hs_cm = float(entry["hs"])
    layers = [EngineLayer(top_depth_m=round((hs_cm - r[0]) / 100, 4), bottom_depth_m=round((hs_cm - r[1]) / 100, 4),
                          grain=r[2], hardness_index=r[3], density_kg_m3=r[4],
                          temperature_c=r[5] if len(r) > 5 else None)
              for r in entry.get("L", []) if r[0] > r[1]]
    return EngineResult(source="site_run_reuse", snow_depth_m=hs_cm / 100, layers=layers,
                        profile_lag_h=round((valid - _t(entry["t"])).total_seconds() / 3600, 3),
                        snowpack_version=version, config_hash=config_hash, forcing_hash=forcing_hash,
                        steer_updates=updates)


def site_run_check(source_root: Path, manifest: CaseManifest, plot_id: str) -> tuple[EngineResult | None, dict]:
    """(the reusable profile or None, provenance: file, run id, reasons it was refused)."""
    season_file = Path(source_root) / "web" / "data" / plot_id / f"{manifest.season}.json"
    prov: dict = {"file": str(season_file.relative_to(source_root)), "reasons": []}
    reasons = prov["reasons"]
    run = _read(season_file)
    if run is None:
        reasons.append("no site season run")
        return None, prov
    prov["run_id"] = run.get("run_id")
    if run.get("mode") != "station":
        reasons.append(f"site run mode {run.get('mode')!r} (only station runs qualify)")
    era5 = sorted(v for v, src in (run.get("forcing_sources") or {}).items() if (src or {}).get("era5", 0) > 0)
    if era5:
        reasons.append(f"ERA5 forcing ({', '.join(era5)}) up to the profile time; ERA5 is available only after its "
                       "configured latency")
    valid = manifest.valid_time
    visible = set(manifest.pit_keys.values())
    if manifest.forecast_source == ForecastSource.archived_gfs and manifest.forecast_runs:
        issued = manifest.forecast_runs[0].issued_at
        fc = _read(season_file.with_name(f"{manifest.season}_forecasts.json")) or {}
        issue = next((i for i in fc.get("issues", []) if _t(i["issue"]) == issued), None)
        entry = _profile(issue["P"], manifest.as_of_time, valid) if issue else None
        state_time = issued
        if issue is None:
            reasons.append(f"no site forecast from the case's run ({issued:%Y-%m-%dT%H}Z)")
        forcing_hash = issue.get("forcing_hash") if issue else None
        cfg_hash = (fc.get("engine") or {}).get("config_hash")
    else:
        entry = _profile(run.get("nowcast", []), manifest.as_of_time - timedelta(days=365), valid)
        state_time = _t(entry["t"]) if entry else valid
        forcing_hash, cfg_hash = run.get("forcing_hash"), (run.get("engine") or {}).get("config_hash")
    if entry is None and not any(r.startswith("no site forecast") for r in reasons):
        reasons.append(f"no site profile within {MAX_LAG_H:g} h before the valid time")
    used = [u for u in (run.get("steer") or {}).get("updates", []) if _t(u["time_utc"]) <= state_time]
    hidden = [u["pit"] for u in used if u["pit"] not in visible]
    if hidden:
        reasons.append(f"{len(hidden)} pit update(s) from pits the case may not see")
    prov["steer_updates"] = len(used)
    if reasons or entry is None:
        return None, prov
    version = (run.get("engine") or {}).get("version", "unknown")
    return to_engine_result(entry, valid, version, cfg_hash, forcing_hash, len(used)), prov
