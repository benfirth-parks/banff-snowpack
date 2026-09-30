"""predict(domain, forecast_run, initial_state="latest_valid") -> snowpack field + profiles.

1. Validate forecast availability against issue time (no future data).
2. Load the latest valid checkpoint (or advance one with actuals available by issue time).
3. Build terrain-unit forcing per ensemble member from the forecast.
4. Branch: copy checkpoint state into private run directories; run SNOWPACK per unit x member.
5. Save member profiles at requested leads, diagnostics, map-ready summaries and provenance.

The checkpoint is never written. The run directory is write-once.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from snowagent.contracts import (
    CapabilityFlags,
    EnsembleConfig,
    ForecastRequest,
    ForecastResultMeta,
    Layer,
    NullReason,
    ProfileDiagnostics,
    ProfileRecord,
    TerrainDomain,
    TerrainUnit,
    WeatherKind,
    WhatIf,
)
from snowagent.engine import snowpack as sp
from snowagent.engine.column import mass_budget, prepare_and_run
from snowagent.engine.profiles import LINEAGE_BASIS, convert_profile
from snowagent.errors import (
    DataLeakage,
    EngineRunFailed,
    ImmutableRecord,
    InitializationRequired,
    InvalidInput,
)
from snowagent.forecast import ensemble, outputs
from snowagent.spatial_forcing.builder import ForcingConfig, build_unit_forcing
from snowagent.state.replay import advance, parallel_map
from snowagent.state.store import StateStore
from snowagent.weather.io import WeatherSeries

MODEL_VERSION = "snowagent-0.1.0/terrain-columns"


@dataclass
class PredictResult:
    run_id: str
    run_dir: Path
    meta: ForecastResultMeta


def _run_id(domain_id: str, init: pd.Timestamp, issue: pd.Timestamp, key: str, what_if: WhatIf) -> str:
    tag = "" if what_if.is_identity else "_whatif"
    return f"fc_{domain_id}_{init:%Y%m%dT%H%M}Z_iss{issue:%Y%m%dT%H%M}Z{tag}_{key[:8]}"


def predict(domain: TerrainDomain, forecast: WeatherSeries, store: StateStore, runs_root: Path,
            engine: sp.EngineInfo, settings: sp.EngineSettings, fcfg: ForcingConfig, ens: EnsembleConfig,
            output_lead_hours: list[int], issue_time: datetime | None = None, what_if: WhatIf | None = None,
            actuals: WeatherSeries | None = None, input_provenance_extra: list | None = None) -> PredictResult:
    what_if = what_if or WhatIf()
    if forecast.meta.kind != WeatherKind.forecast:
        raise InvalidInput(f"{forecast.meta.series_id} is not a forecast series")
    issue = pd.Timestamp(issue_time) if issue_time is not None else pd.Timestamp(forecast.meta.available_time)
    forecast = forecast.available_by(issue)  # raises DataLeakage if the run arrived after issue time
    init = pd.Timestamp(forecast.meta.issue_time)
    horizon_h = int((forecast.data.index[-1] - init) / pd.Timedelta(hours=1))
    if max(output_lead_hours) > horizon_h:
        raise InvalidInput(f"requested lead {max(output_lead_hours)} h exceeds forecast horizon {horizon_h} h")

    cp = store.latest_valid(domain.domain_id, issue, domain.terrain_version)
    if cp is None:
        raise InitializationRequired(
            f"no valid state checkpoint for domain {domain.domain_id} at or before {issue.isoformat()}; "
            "run `snowagent init` with historical weather (explicit snow-free start) first.")
    if pd.Timestamp(cp.analysis_time) < init:
        if actuals is None:
            raise InitializationRequired(
                f"latest checkpoint {cp.state_id} is at {cp.analysis_time}, before forecast init {init}; "
                "supply actuals available by issue time to advance it")
        cp = advance(domain, cp, actuals, store, init.to_pydatetime(), engine, settings, fcfg, issue.to_pydatetime())
    t0 = pd.Timestamp(cp.analysis_time)
    if t0 > init:
        raise DataLeakage(f"checkpoint {cp.state_id} analysis time {t0} is after forecast init {init}; "
                          "use a forecast initialised at or after the analysis time")
    fc_window = forecast.data[forecast.data.index > t0]
    if fc_window.empty:
        raise InvalidInput("forecast has no valid times after the checkpoint time")

    request = ForecastRequest(domain_id=domain.domain_id, issue_time=issue.to_pydatetime(),
                              forecast_series_id=forecast.meta.series_id, horizon_hours=horizon_h,
                              output_lead_hours=output_lead_hours, ensemble=ens, what_if=what_if,
                              model_version=MODEL_VERSION)
    forcing_hash = sp.sha256_text(forecast.sha256 + cp.forcing_hash)[:16]
    key = sp.sha256_text(request.model_dump_json(exclude={"issue_time"}) + cp.state_id + forcing_hash +
                         engine.version_string + settings.config_hash())
    run_id = _run_id(domain.domain_id, init, issue, key, what_if)
    run_dir = Path(runs_root) / run_id
    if run_dir.exists():
        raise ImmutableRecord(f"forecast run {run_id} already exists; issued forecasts are never rewritten",
                              run_dir=str(run_dir))
    work = Path(runs_root) / f".work_{run_id}"
    shutil.rmtree(work, ignore_errors=True)
    cp_dir = store.checkpoint_dir(cp.domain_id, cp.state_id)

    perts = ensemble.member_perturbations(ens, len(fc_window))
    units = [u for u in domain.units if u.supported]
    lead_times = {h: init + pd.Timedelta(hours=h) for h in output_lead_hours}
    fc_settings = dataclasses.replace(settings, prof_days_between=1.0 / 24.0)

    def one(task: tuple[TerrainUnit, int]) -> dict:
        unit, m = task
        src = ensemble.apply(fc_window, perts[m], what_if)
        uf = build_unit_forcing(src, forecast.meta.lat, forecast.meta.lon, forecast.meta.source_elevation_m, unit,
                                fcfg, forecast.meta.elevation_adjusted_by_provider)
        tail = store.unit_tail(cp, unit.unit_id)
        smet = pd.concat([tail, uf.smet])
        rd = work / unit.unit_id / f"m{m:02d}"
        try:
            out = prepare_and_run(engine, fc_settings, rd, unit, smet, fc_window.index[-1].to_pydatetime(),
                                  initial_sno=cp_dir / "units" / f"{unit.unit_id}.sno")
        except EngineRunFailed as exc:
            return {"unit_id": unit.unit_id, "member": m, "error": exc.to_dict()}
        _, profs = sp.parse_pro(out.pro)
        by_time = {p.time: p for p in profs}
        analysis = store.analysis_profile(cp, unit.unit_id)
        records = []
        for h, vt in lead_times.items():
            if vt == t0:
                layers = [Layer.model_validate(x) for x in analysis["layers"]]
                diag = ProfileDiagnostics.model_validate(analysis["diagnostics"])
                nulls = [NullReason.model_validate(x) for x in analysis["nulls"]]
                ident = analysis["identity_uncertainty"]
            elif vt in by_time:
                layers, diag, nulls, ident = convert_profile(by_time[vt], unit.slope_deg, unit.unit_id)
            else:
                return {"unit_id": unit.unit_id, "member": m,
                        "error": {"code": "missing_lead", "message": f"engine produced no profile at {vt}"}}
            records.append(ProfileRecord(run_id=run_id, unit_id=unit.unit_id, member_id=m,
                                         valid_time=vt.to_pydatetime(), lead_hours=float(h), slope_deg=unit.slope_deg,
                                         aspect_deg=unit.aspect_deg, layers=layers, diagnostics=diag, nulls=nulls,
                                         lineage_basis=LINEAGE_BASIS, identity_uncertainty=ident))
        last = convert_profile(profs[-1], unit.slope_deg, unit.unit_id)[1]
        budget = mass_budget(sp.parse_met(out.met), unit.slope_deg,
                             analysis["diagnostics"]["swe_kg_m2_per_slope_area"], last.swe_kg_m2_per_slope_area)
        comp = uf.components
        forcing_diag = {
            "iswr_slope_mean": float(comp["iswr_slope_total"].mean()),
            "iswr_direct_slope_mean": float(comp["iswr_direct_slope"].mean()),
            "iswr_source_mean": float(comp["iswr_source_horizontal"].mean()),
            "sunlit_hours": float((comp["sunlit_fraction"] * comp["sun_up_fraction"]).sum()),
            "sun_up_hours": float(comp["sun_up_fraction"].sum()),
            "ta_unit_mean_c": float(comp["ta_unit_c"].mean()),
            "psum_slope_total": float(comp["psum_slope_area"].sum()),
            "liquid_precip_slope_total": float((comp["psum_slope_area"] * comp["liquid_fraction"]).sum()),
            "engine_step_min": float(out.extra.get("calculation_step_min", fc_settings.calculation_step_min)),
            "numerical_retry": out.extra.get("numerical_retry", ""),
        }
        return {"unit_id": unit.unit_id, "member": m, "records": records, "budget": budget,
                "forcing": forcing_diag, "assumptions": uf.assumptions}

    results = parallel_map(one, [(u, m) for u in units for m in range(ens.members)])
    failures = {f"{r['unit_id']}/m{r['member']:02d}": r["error"]["message"] for r in results if "error" in r}
    ok = [r for r in results if "error" not in r]
    if not ok:
        shutil.rmtree(work, ignore_errors=True)
        raise EngineRunFailed("all forecast columns failed; no profiles produced", failures=failures)
    status = "ok" if not failures else "partial"
    # units with any failed member are excluded from products (no partial ensembles presented as complete)
    failed_units = {k.split("/")[0] for k in failures}
    ok = [r for r in ok if r["unit_id"] not in failed_units]

    capability = CapabilityFlags()
    residuals = [abs(r["budget"]["residual"]) for r in ok]
    assumptions = sorted({a for r in ok for a in r["assumptions"]})
    meta = ForecastResultMeta(
        run_id=run_id, status=status, synthetic_inputs=forecast.meta.provenance.synthetic or cp.synthetic,
        request=request, forecast_init_time=init.to_pydatetime(), initial_state_id=cp.state_id,
        initial_state_manifest_sha256=cp.manifest_sha256 or "", terrain_version=domain.terrain_version,
        engine_version=engine.version_string, engine_config_hash=fc_settings.config_hash(), forcing_hash=forcing_hash,
        input_provenance=[forecast.meta.provenance, domain.provenance] + list(input_provenance_extra or []),
        capability=capability,
        units_simulated=sorted({r["unit_id"] for r in ok}),
        units_unsupported={u.unit_id: u.unsupported_reasons for u in domain.units if not u.supported},
        unit_failures=failures,
        member_perturbations={str(m): ensemble.summarize(p) for m, p in enumerate(perts)},
        forcing_assumptions=assumptions, mass_budget_tolerance_kg_m2=max(r["budget"]["tolerance"] for r in ok),
        mass_budget_max_residual_kg_m2=max(residuals) if residuals else None, created_utc=datetime.now(UTC))

    staging = Path(runs_root) / f".staging_{run_id}"
    shutil.rmtree(staging, ignore_errors=True)
    outputs.write_run(staging, meta, domain, ok, cp)
    staging.rename(run_dir)
    shutil.rmtree(work, ignore_errors=True)
    return PredictResult(run_id, run_dir, meta)


def load_meta(run_dir: Path) -> ForecastResultMeta:
    return ForecastResultMeta.model_validate_json((Path(run_dir) / "manifest.json").read_text())


def dump_json(obj, path: Path) -> None:
    path.write_text(json.dumps(obj, indent=1, default=str))
