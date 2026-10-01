"""Phase 2 acceptance on a finished real-data forecast run (README §9; terrain-forecast spec, validation).

"A domain and archived forecast produce distinct future profiles at unobserved units without a new pit; no
future-weather leakage." Everything here reads the run's stored products and the state store; nothing is
re-simulated except the two leakage probes, which must be refused before any engine run starts.

- distinctness: are the predicted profiles at different units actually different, and do the differences
  follow the terrain-conditioned forcing (elevation -> temperature, slope/aspect/horizon -> shortwave)?
- leakage audit: availability of every input relative to the issue time, the checkpoint chain's cutoffs,
  observations used, and refusal probes (a run that became available after the issue time; a rerun of an issued
  forecast).
- withheld pit: a pit observed after issue time, compared with the forecast at the site unit. It is read only
  here, after the run, and never by the run.
"""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from snowagent.contracts import ForecastResultMeta, ProfileRecord, StateCheckpoint, TerrainDomain


def load_run(run_dir: Path) -> tuple[ForecastResultMeta, TerrainDomain, dict[str, list[ProfileRecord]]]:
    run_dir = Path(run_dir)
    meta = ForecastResultMeta.model_validate_json((run_dir / "manifest.json").read_text())
    domain = TerrainDomain.model_validate_json((run_dir / "domain.json").read_text())
    recs: dict[str, list[ProfileRecord]] = {}
    for f in sorted((run_dir / "profiles").glob("*.jsonl")):
        recs[f.stem] = [ProfileRecord.model_validate_json(x) for x in f.read_text().splitlines() if x.strip()]
    return meta, domain, recs


def _ctrl(recs: list[ProfileRecord], lead: float) -> ProfileRecord:
    return next(r for r in recs if r.member_id == 0 and r.lead_hours == lead)


def signature(rec: ProfileRecord, dz_m: float = 0.01) -> tuple:
    """Profile identity at 1 cm / grain-form / 10 kg m-3 resolution (two profiles equal => same signature)."""
    return tuple((round(ly.top_vertical_m / dz_m), ly.grain_form_primary, round(ly.density_kg_m3 / 10))
                 for ly in rec.layers)


def _features(rec: ProfileRecord, init: pd.Timestamp) -> dict:
    ls = sorted(rec.layers, key=lambda x: -x.top_vertical_m)
    top10 = [ly for ly in ls if ly.depth_top_vertical_m < 0.10]
    w = np.array([ly.thickness_vertical_m for ly in top10]) if top10 else np.array([1.0])
    new = [ly for ly in ls if ly.deposition_time is not None and pd.Timestamp(ly.deposition_time) > init]
    crusts = [ly for ly in ls if ly.is_crust]
    return {"hs_m": rec.diagnostics.hs_vertical_m, "swe_kg_m2_h": rec.diagnostics.swe_kg_m2_per_horizontal_area,
            "n_layers": rec.diagnostics.n_layers, "surface_form": ls[0].grain_form_primary if ls else None,
            "surface_temp_c": ls[0].temperature_c if ls else None,
            "top10cm_density": float(np.average([ly.density_kg_m3 for ly in top10], weights=w)) if top10 else None,
            "top10cm_lwc_max": max((ly.lwc_vol_frac for ly in top10), default=0.0),
            "new_snow_m": float(sum(ly.thickness_vertical_m for ly in new)),
            "crust_shallowest_depth_m": min((ly.depth_top_vertical_m for ly in crusts), default=None),
            "melt_marked_top20cm": any(ly.melt_freeze_marker for ly in ls if ly.depth_top_vertical_m < 0.20)}


def distinctness(run_dir: Path, observed_units: set[str], dtw: bool = True) -> dict:
    """Compare control-member profiles across unobserved units at every lead; relate them to unit forcing."""
    meta, domain, recs = load_run(run_dir)
    init = pd.Timestamp(meta.forecast_init_time)
    units = {u.unit_id: u for u in domain.units}
    unobs = sorted(u for u in recs if u not in observed_units)
    forcing = pd.read_csv(Path(run_dir) / "diagnostics" / "forcing.csv")
    forcing = forcing[forcing.member == 0].set_index("unit_id")
    terrain = {(round(units[u].elevation_m), round(units[u].slope_deg, 1),
                None if units[u].aspect_deg is None else round(units[u].aspect_deg),
                round(units[u].sky_view_factor, 3), tuple(round(h, 1) for h in units[u].horizon_elevation_deg))
               for u in unobs}
    out: dict = {"units_with_profiles": len(recs), "unobserved_units": len(unobs),
                 "distinct_terrain": len(terrain), "leads": {}}
    for lead in meta.request.output_lead_hours:
        rows = []
        for uid in unobs:
            u = units[uid]
            rows.append({"unit_id": uid, "elevation_m": u.elevation_m, "slope_deg": u.slope_deg,
                         "aspect_deg": u.aspect_deg, "sky_view": u.sky_view_factor,
                         "ta_unit_mean_c": forcing.loc[uid, "ta_unit_mean_c"],
                         "iswr_slope_mean": forcing.loc[uid, "iswr_slope_mean"],
                         **_features(_ctrl(recs[uid], lead), init)})
        df = pd.DataFrame(rows)
        sigs = [signature(_ctrl(recs[u], lead)) for u in unobs]
        hs = df["hs_m"].to_numpy()
        pair_dhs = [abs(a - b) for a, b in combinations(hs, 2)]
        res = {"n_units": len(unobs), "unique_profiles": len(set(sigs)),
               "hs_m": {"min": round(float(hs.min()), 3), "max": round(float(hs.max()), 3),
                        "std": round(float(hs.std()), 3)},
               "pairwise_abs_dhs_m_mean": round(float(np.mean(pair_dhs)), 3),
               "n_layers_range": [int(df.n_layers.min()), int(df.n_layers.max())],
               "surface_forms": df.surface_form.value_counts().to_dict(),
               "units_with_crust": int(df.crust_shallowest_depth_m.notna().sum()),
               "units_melt_marked_top20cm": int(df.melt_marked_top20cm.sum())}
        if lead > 0:  # terrain response over the forecast window (rank correlations)
            res["spearman"] = {
                "elevation_vs_ta_unit": round(float(df.elevation_m.corr(df.ta_unit_mean_c, method="spearman")), 2),
                "iswr_slope_vs_top10cm_density": round(float(df.iswr_slope_mean.corr(df.top10cm_density,
                                                                                     method="spearman")), 2),
                "iswr_slope_vs_surface_temp": round(float(df.iswr_slope_mean.corr(df.surface_temp_c,
                                                                                  method="spearman")), 2),
                "elevation_vs_new_snow": round(float(df.elevation_m.corr(df.new_snow_m, method="spearman")), 2),
            }
        out["leads"][str(lead)] = res
        out.setdefault("tables", {})[str(lead)] = df.round(4).to_dict("records")
    if dtw:
        from snowagent.baseline.run import model_profile_as_observed
        from snowagent.obs.dtw import similarity

        last = meta.request.output_lead_hours[-1]
        prof = {u: model_profile_as_observed(_ctrl(recs[u], last).layers) for u in unobs}
        pairs = [(f"{a}|{b}", prof[a], prof[b]) for a, b in combinations(unobs, 2)]
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(4) as ex:  # one R process per chunk
            res = [r for part in ex.map(similarity, [pairs[i::4] for i in range(4)]) for r in part.values()]
        sims = [v.get("sim") for v in res if v.get("sim") is not None]
        if sims:
            out["dtw_pairwise_lead_last"] = {"lead": last, "pairs": len(sims), "mean": round(float(np.mean(sims)), 3),
                                             "min": round(float(np.min(sims)), 3),
                                             "max": round(float(np.max(sims)), 3),
                                             "share_identical": round(float(np.mean(np.array(sims) > 0.999)), 3)}
    return out


def leakage_audit(run_dir: Path, store_root: Path, inputs: dict[str, Path]) -> dict:
    """Availability of every input vs the issue time; checkpoint chain cutoffs and observations used."""
    from snowagent.weather.io import load_weather

    meta, domain, _ = load_run(run_dir)
    issue = pd.Timestamp(meta.request.issue_time)
    init = pd.Timestamp(meta.forecast_init_time)
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    fc = load_weather(inputs["forecast"])
    check("forecast run available by issue time", pd.Timestamp(fc.meta.available_time) <= issue,
          f"{fc.meta.series_id}: available {fc.meta.available_time}, issue {issue}")
    chain, sid = [], meta.initial_state_id
    cp_dir = Path(store_root) / domain.domain_id / "checkpoints"
    while sid:
        cp = StateCheckpoint.model_validate_json((cp_dir / sid / "manifest.json").read_text())
        chain.append(cp)
        sid = cp.parent_state_id
    for cp in chain:
        check(f"checkpoint {cp.state_id}: assimilation cutoff <= issue", pd.Timestamp(cp.assimilation_cutoff) <= issue,
              f"cutoff {cp.assimilation_cutoff}, analysis {cp.analysis_time}, lineage {cp.forcing_lineage}")
        check(f"checkpoint {cp.state_id}: no observations used", not cp.observations_used,
              f"observations_used = {cp.observations_used}")
    check("forecast starts from a state at its initial time", pd.Timestamp(chain[0].analysis_time) == init,
          f"initial state {chain[0].state_id} at {chain[0].analysis_time}, forecast init {init}")
    for name in ("history", "recent"):
        s = load_weather(inputs[name])
        used = s.available_by(issue).data.index
        check(f"{name} series extends past issue (filter exercised, not assumed)", s.data.index[-1] > issue,
              f"{s.meta.series_id}: records to {s.data.index[-1]}, latency {s.meta.availability_latency_s} s; "
              f"usable at issue to {used[-1] if len(used) else None}")
    for cp in chain:
        cdir = cp_dir / cp.state_id
        last = max(pd.read_csv(t, index_col=0, parse_dates=True).index.max() for t in (cdir / "units").glob("*.tail.csv"))
        check(f"checkpoint {cp.state_id}: forcing ends at its analysis time", last == pd.Timestamp(cp.analysis_time),
              f"last forcing record in unit tails {last}")
    return {"issue_time": str(issue), "forecast_init": str(init), "all_pass": all(c["pass"] for c in checks),
            "checks": checks}


def withheld_pit(run_dir: Path, pit: dict, site_unit: str) -> dict:
    """Forecast at ``site_unit`` at the lead nearest the pit time vs the pit (read only after the run)."""
    from snowagent.baseline.run import model_profile_as_observed
    from snowagent.obs.agreement import compare_profiles
    from snowagent.obs.dtw import similarity

    meta, _domain, recs = load_run(run_dir)
    init = pd.Timestamp(meta.forecast_init_time)
    t = pd.Timestamp(pit["obs_time_utc"])
    lead = min(meta.request.output_lead_hours, key=lambda h: abs((init + pd.Timedelta(hours=h) - t).total_seconds()))
    rows = []
    for m in sorted({r.member_id for r in recs[site_unit]}):
        rec = next(r for r in recs[site_unit] if r.member_id == m and r.lead_hours == lead)
        model = model_profile_as_observed(rec.layers)
        rows.append({"member": m, "model_hs_cm": model["hs_cm"], **compare_profiles(pit, model)})
    ctrl = model_profile_as_observed(_ctrl(recs[site_unit], lead).layers)
    sim = similarity([("ctrl", pit, ctrl)]).get("ctrl", {})
    return {"profile_id": pit["profile_id"], "pit_time": str(t), "lead_hours": lead,
            "pit_minus_valid_time_h": round((t - (init + pd.Timedelta(hours=lead))).total_seconds() / 3600, 2),
            "pit_hs_cm": pit.get("hs_cm"), "control": rows[0], "members": rows,
            "dtw_control": {"sim": sim.get("sim"), "sim_rescaled": sim.get("sim_rescaled")}}


def write_json(obj: dict, path: Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=1, default=str))


def write_overview_figure(run_dir: Path, acc: dict, out_png: Path, sun_lead: str = "24") -> None:
    """Maps of HS (last lead), near-surface density after the first forecast day, shallowest crust depth, and
    slope shortwave vs near-surface density. Inspection aid only; numbers are in ``acc``."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MplPolygon

    meta, domain, _ = load_run(run_dir)
    last = str(meta.request.output_lead_hours[-1])
    tabs = {k: pd.DataFrame(v).set_index("unit_id") for k, v in acc["distinctness"]["tables"].items()}
    x0 = min(x for x, _ in domain.boundary_xy)
    y0 = min(y for _, y in domain.boundary_xy)

    def km(ring):
        return [((x - x0) / 1000.0, (y - y0) / 1000.0) for x, y in ring]

    fig, axes = plt.subplots(1, 4, figsize=(19, 4.6))
    panels = [(tabs[last], "hs_m", f"HS (m), lead {last} h", "Blues"),
              (tabs[sun_lead], "top10cm_density", f"top 10 cm density (kg m-3), lead {sun_lead} h", "YlOrRd"),
              (tabs[last], "crust_shallowest_depth_m", f"shallowest crust depth (m), lead {last} h", "viridis_r")]
    for ax, (tab, col, title, cmap) in zip(axes[:3], panels, strict=True):
        vals = tab[col].astype(float)
        if col == "crust_shallowest_depth_m":
            vals = vals.clip(upper=1.0)
        lo, hi = float(np.nanmin(vals)), float(np.nanmax(vals))
        norm = plt.Normalize(lo, hi if hi > lo else lo + 1)
        for u in domain.units:
            if u.unit_id.startswith("site_"):
                cx, cy = km([u.centroid_xy])[0]
                ax.plot(cx, cy, "k*", ms=11, zorder=5)
                ax.annotate("Goat's Eye plot (site unit)", (cx, cy), xytext=(4, 4), textcoords="offset points",
                            fontsize=7)
                continue
            v = vals.get(u.unit_id, np.nan)
            face = "#dddddd" if pd.isna(v) else plt.get_cmap(cmap)(norm(v))
            hatch = "//" if not u.supported else ("xx" if pd.isna(v) and col == "crust_shallowest_depth_m" else None)
            ax.add_patch(MplPolygon(km(u.polygon_xy), closed=True, facecolor=face, edgecolor="grey", lw=0.3,
                                    hatch=hatch))
        ax.set_xlim(0, 6)
        ax.set_ylim(0, 6)
        ax.set_aspect("equal")
        ax.set_title(title, fontsize=9)
        ax.set_xlabel(f"km east of {x0:.0f} m ({domain.crs})", fontsize=7)
        ax.tick_params(labelsize=7)
        fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap), ax=ax, shrink=0.75)
    t = tabs[sun_lead]
    sc = axes[3].scatter(t["iswr_slope_mean"], t["top10cm_density"], c=t["elevation_m"], cmap="terrain", s=22)
    axes[3].set_xlabel("mean slope shortwave over the forecast, control (W m-2)", fontsize=8)
    axes[3].set_ylabel(f"top 10 cm density at lead {sun_lead} h (kg m-3)", fontsize=8)
    axes[3].tick_params(labelsize=7)
    fig.colorbar(sc, ax=axes[3], shrink=0.75, label="elevation (m)")
    rho = acc["distinctness"]["leads"][sun_lead].get("spearman", {}).get("iswr_slope_vs_top10cm_density")
    axes[3].set_title(f"terrain response (Spearman {rho})", fontsize=9)
    fig.suptitle(f"{meta.run_id} - grey hatched: forest (unsupported); crosshatched: no crust in column\n"
                 "EXPERIMENTAL structure prediction - not avalanche guidance; transport unresolved", fontsize=8)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=90)
    plt.close(fig)
