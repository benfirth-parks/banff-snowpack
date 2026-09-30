"""Run one terrain-unit column through SNOWPACK and compute its mass budget."""

from __future__ import annotations

import dataclasses
import math
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from snowagent.contracts import TerrainUnit
from snowagent.engine import snowpack as sp
from snowagent.errors import EngineRunFailed

# Mass-budget closure tolerance for one column over one run, slope-area kg m-2.
# Empirically the engine closes to ~0.01-0.3 kg m-2 per season (docs/decisions.md ADR-008);
# rounding in hourly .met output dominates. We allow an absolute + relative term.
BUDGET_ABS_TOL = 1.0
BUDGET_REL_TOL = 0.005
# Numerical fallback (ADR-009): if the engine aborts with a numerical error at the configured
# step, the column is re-run ONCE from the same inputs at this finer step and the retry is recorded.
RETRY_STEP_MIN = 5.0
NUMERICAL_ERROR_MARKERS = ("out of bound", "computation not completed", "did not converge")


@dataclass
class ColumnResult:
    unit_id: str
    outputs: sp.RunOutputs
    budget: dict[str, float]


def station_id(unit: TerrainUnit) -> str:
    return unit.unit_id.replace("-", "_")


def prepare_and_run(engine: sp.EngineInfo, settings: sp.EngineSettings, run_dir: Path, unit: TerrainUnit,
                    forcing: pd.DataFrame, end: datetime, *, initial_sno: Path | None = None,
                    snowfree_start: datetime | None = None, allow_retry: bool = True) -> sp.RunOutputs:
    """Run one column; on a numerical abort retry once at RETRY_STEP_MIN (recorded in ``extra``)."""
    try:
        out = _prepare_and_run_once(engine, settings, run_dir, unit, forcing, end, initial_sno, snowfree_start)
        out.extra["calculation_step_min"] = f"{settings.calculation_step_min:g}"
        return out
    except EngineRunFailed as exc:
        text = " ".join(str(e) for e in exc.details.get("errors", [])) + str(exc.details.get("log_tail", ""))
        numerical = any(m in text.lower() for m in NUMERICAL_ERROR_MARKERS)
        if not (allow_retry and numerical and settings.calculation_step_min > RETRY_STEP_MIN):
            raise
        fine = dataclasses.replace(settings, calculation_step_min=RETRY_STEP_MIN)
        try:
            out = _prepare_and_run_once(engine, fine, run_dir, unit, forcing, end, initial_sno, snowfree_start)
        except EngineRunFailed as exc2:
            raise EngineRunFailed(f"{exc.message}; retry at {RETRY_STEP_MIN:g} min also failed: {exc2.message}",
                                  first_errors=exc.details.get("errors"), retry_errors=exc2.details.get("errors")
                                  ) from exc2
        out.extra["calculation_step_min"] = f"{RETRY_STEP_MIN:g}"
        out.extra["numerical_retry"] = f"first attempt at {settings.calculation_step_min:g} min failed: {text[:200]}"
        return out


def _prepare_and_run_once(engine, settings, run_dir, unit, forcing, end, initial_sno, snowfree_start):
    """Exactly one of ``initial_sno`` (restart, copied) or ``snowfree_start`` must be given."""
    if (initial_sno is None) == (snowfree_start is None):
        raise ValueError("give either initial_sno or snowfree_start")
    sid = station_id(unit)
    if run_dir.exists():
        shutil.rmtree(run_dir)
    (run_dir / "input").mkdir(parents=True)
    lon, lat = unit.centroid_lonlat
    sp.write_smet_forcing(run_dir / "input" / f"{sid}.smet", sid, lat, lon, unit.elevation_m, forcing)
    aspect = unit.aspect_deg if unit.aspect_deg is not None else 0.0
    if initial_sno is not None:
        # copy (never link) so the engine cannot touch the checkpoint's file
        shutil.copyfile(initial_sno, run_dir / "input" / f"{sid}.sno")
        restart = True
    else:
        sp.write_snowfree_sno(run_dir / "input" / f"{sid}.sno", sid, lat, lon, unit.elevation_m,
                              unit.slope_deg, aspect, snowfree_start)
        restart = False
    return sp.run_engine(engine, run_dir, sid, end, settings, restart=restart)


def mass_budget(met: pd.DataFrame, slope_deg: float, swe_start_slope: float, swe_end_slope: float
                ) -> dict[str, float]:
    """Column mass budget in kg m-2 per SLOPE-PARALLEL area.

    Verified in AsciiIO::writeMeteo: SWE, eroded mass, runoff, surface flux,
    sublimation and evaporation are written divided by cos(slope); solid
    precipitation (MS_HNW) and rain (MS_RAIN) are written per slope area. We
    convert all terms to slope area. Enabled sources/sinks: snowfall, rain,
    snowpack runoff (bottom boundary), sublimation/deposition, evaporation/
    condensation, wind erosion (disabled: must be zero), lateral transport
    (unresolved: zero by construction).
    """
    c = math.cos(math.radians(slope_deg))
    hours = 1.0  # TS output interval is hourly; rates are kg m-2 h-1
    solid = float(met["Precipitation rate at surface (solid only)"].fillna(0).sum() * hours)
    rain_series = met["Rain rate"].fillna(0) * hours
    rain = float(rain_series.sum())
    # rain falling while no snow is present passes to the ground (lower boundary), not the snowpack
    swe_h = met["SWE (of snowpack)"].fillna(0)
    bare = (swe_h <= 0) & (swe_h.shift(1).fillna(swe_start_slope) <= 0)
    rain_bare = float(rain_series[bare].sum())
    runoff = float(met["Snowpack runoff (virtual lysimeter -- snow only)"].fillna(0).sum() * c)
    subl = float(met["Sublimation"].fillna(0).sum() * c)
    evap = float(met["Evaporation"].fillna(0).sum() * c)
    eroded = float(met["Eroded mass"].fillna(0).sum() * c)
    d_swe = swe_end_slope - swe_start_slope
    sources = solid + rain - rain_bare + subl + evap - runoff - abs(eroded)
    residual = d_swe - sources
    tol = BUDGET_ABS_TOL + BUDGET_REL_TOL * (solid + rain + abs(runoff))
    return {
        "swe_start": swe_start_slope, "swe_end": swe_end_slope, "delta_swe": d_swe,
        "snowfall": solid, "rain": rain, "rain_on_bare_ground": rain_bare, "runoff": runoff, "sublimation": subl, "evaporation": evap,
        "wind_erosion": eroded, "lateral_transport": 0.0, "residual": residual, "tolerance": tol,
        "closed": float(abs(residual) <= tol),
    }
