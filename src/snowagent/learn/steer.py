"""Observation steering, experiment 1: pit depth nudges the simulated state (ADR-038, in progress).

At each plot pit the state of the measured-weather run is corrected toward the pit's snow depth with a weight w:
every snow layer's thickness (and therefore its mass; densities and microstructure unchanged) is scaled by
f = 1 + w * (HS_pit / HS_model - 1). w = 0 is the free run, w = 1 matches the pit exactly. The corrected state is
taken at the first 00 UTC after the pit (nothing later than the pit enters it) and run to the NEXT pit, which it
has not seen; that pit scores it. Comparing w on held-out seasons says how much a pit's depth should be trusted
against the model - the weight of this data type.

Structure (grain forms, hardness, weak layers) is not altered here; that is experiment 2.
"""

from __future__ import annotations

import json
import shutil
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)
MIN_GAP_DAYS = 3  # next pit at least this long after the corrected state
MAX_GAP_DAYS = 45
MIN_HS_CM = 20.0  # below this the ratio is unstable; no correction


def scale_sno(src: Path, dest: Path, f: float) -> float:
    """Copy a .sno restart file with every snow layer thickness multiplied by ``f``. Returns the new HS (m)."""
    lines = Path(src).read_text().splitlines()
    out, data, hs = [], False, 0.0
    for ln in lines:
        if data and ln.strip():
            parts = ln.split()
            th = float(parts[1]) * f
            hs += th
            parts[1] = f"{th:.6f}"
            out.append("  ".join(parts))
            continue
        if ln.strip() == "[DATA]":
            data = True
        out.append(ln)
    text = "\n".join(out) + "\n"
    text = "\n".join(f"HS_Last          = {hs:.6f}" if ln.startswith("HS_Last") else ln for ln in text.splitlines()) + "\n"
    Path(dest).write_text(text)
    return hs


def _pit_hs(o: dict) -> float | None:
    hs = o.get("hs_cm")
    if hs is None and o.get("layers"):
        hs = max((ly.get("top_cm") or 0) for ly in o["layers"])
    return float(hs) if hs else None


def pairs_for(pits: list[dict]) -> list[tuple[dict, dict]]:
    pits = sorted((o for o in pits if _pit_hs(o)), key=lambda o: o["obs_time_utc"])
    out = []
    for i, a in enumerate(pits):
        ta = pd.Timestamp(a["obs_time_utc"])
        for b in pits[i + 1:]:
            gap = (pd.Timestamp(b["obs_time_utc"]) - ta).total_seconds() / 86400
            if MIN_GAP_DAYS <= gap <= MAX_GAP_DAYS:
                out.append((a, b))
                break
    return out


def _run_pair(args) -> list[dict]:
    """All weights for one pit pair, from the free run's restart state after pit a."""
    from snowagent.baseline.run import plot_unit
    from snowagent.engine import snowpack as sp
    from snowagent.engine.column import prepare_and_run
    from snowagent.web.build import _cfg, _profiles, _score

    plot, a, b, sno, forcing_csv, model_hs_a, work = args
    p = _cfg()["plots"][plot]
    unit = plot_unit(plot, p["lat"], p["lon"], p["elevation_m"])
    t0 = pd.Timestamp(Path(sno).name.split(".sno")[1][:12], tz="UTC")
    tb = pd.Timestamp(b["obs_time_utc"])
    forcing = pd.read_csv(forcing_csv, index_col=0, parse_dates=True)
    forcing = forcing[(forcing.index >= t0 - pd.Timedelta(hours=6)) & (forcing.index <= tb.ceil("6h"))]
    hs_a = _pit_hs(a)
    out = []
    for w in WEIGHTS:
        ratio = hs_a / model_hs_a if model_hs_a >= MIN_HS_CM else 1.0
        f = 1.0 + w * (ratio - 1.0)
        rd = Path(work) / f"{a['profile_id'][:40]}_w{int(w * 100):03d}"
        rd.mkdir(parents=True, exist_ok=True)
        init = rd / "init.sno"
        scale_sno(Path(sno), init, f)
        try:
            res = prepare_and_run(sp.find_engine(), sp.EngineSettings(prof_days_between=0.25), rd / "run", unit,
                                  forcing, tb.ceil("6h").to_pydatetime(), initial_sno=init)
            prof = _profiles(res.pro, 0.0, None, [])
            near = min(prof, key=lambda pr: abs((pd.Timestamp(pr["t"] + ":00", tz="UTC") - tb).total_seconds()))
            sc = _score(b, near["L"])
            out.append({"plot": plot, "pit_a": a["profile_id"], "pit_b": b["profile_id"], "t_b": str(tb),
                        "gap_d": round((tb - t0).total_seconds() / 86400, 1), "w": w, "factor": round(f, 3),
                        "hs_pit_a": hs_a, "hs_model_a": model_hs_a, "hs_pit_b": _pit_hs(b),
                        "hs_model_b": near["hs"], **sc})
        except Exception as exc:  # noqa: BLE001 - one failed pair is recorded
            out.append({"plot": plot, "pit_a": a["profile_id"], "w": w, "error": f"{type(exc).__name__}: {exc}"[:200]})
        shutil.rmtree(rd, ignore_errors=True)
    return out


def season_jobs(plot: str, y: int, work: Path) -> list[tuple]:
    """Free run with daily restart states, then one job per pit pair."""
    from snowagent.baseline.evaluate import observed_at_plot
    from snowagent.baseline.run import plot_unit, run_season
    from snowagent.engine import snowpack as sp
    from snowagent.spatial_forcing.builder import ForcingConfig, build_unit_forcing
    from snowagent.web.build import OBSERVED, _cfg, season_forcing

    p = _cfg()["plots"][plot]
    pf, start, end, mode = season_forcing(plot, y)
    pits = observed_at_plot(OBSERVED, plot, start, end)
    prs = pairs_for(pits)
    if not prs:
        return []
    unit = plot_unit(plot, p["lat"], p["lon"], p["elevation_m"])
    swork = Path(work) / f"{plot}_{y}"
    shutil.rmtree(swork, ignore_errors=True)
    r = run_season(pf, unit, start, end, swork, settings=sp.EngineSettings(prof_days_between=0.25,
                                                                        snow_days_between=1.0, first_backup=0.0))
    out = r["outputs"]
    backups = {pd.Timestamp(f.name.split(".sno")[1][:12], tz="UTC"): f
               for f in (Path(out.run_dir) / "output").glob("*.sno2*")}
    uf = build_unit_forcing(pf.data, p["lat"], p["lon"], p["elevation_m"], unit, ForcingConfig())
    fcsv = swork / "forcing.csv"
    uf.smet.to_csv(fcsv)
    met = sp.parse_met(out.met)["Modelled snow depth (vertical)"]
    jobs = []
    for a, b in prs:
        t0 = pd.Timestamp(a["obs_time_utc"]).ceil("D")  # first 00 UTC after the pit
        if t0 not in backups:
            continue
        model_hs = float(met[met.index <= t0].iloc[-1])
        jobs.append((plot, a, b, str(backups[t0]), str(fcsv), model_hs, str(swork / "pairs")))
    return jobs


def run_experiment(plots: list[str], seasons: list[int], work: Path, out: Path, workers: int = 4) -> pd.DataFrame:
    jobs = []
    for plot in plots:
        for y in seasons:
            try:
                jobs += season_jobs(plot, y, work)
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"plot": plot, "season": y, "error": str(exc)[:200]}), flush=True)
    rows = []
    with ProcessPoolExecutor(workers) as ex:
        for res in ex.map(_run_pair, jobs):
            rows += res
    d = pd.DataFrame(rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(out, index=False)
    return d


# ------------------------------------------------------------------------------------------------ experiment 2
# Engine microstructure per IACS class: medians of the model's own elements of that class (rg, rb in mm;
# dd dendricity, sp sphericity, mk marker), from the 2015-17 Goat's Eye runs (16,331 elements).
CLASS_MICRO = {
    "PP": (0.151, 0.055, 0.835, 0.485, 0), "DF": (0.219, 0.086, 0.402, 0.443, 0),
    "RG": (0.247, 0.140, 0.0, 1.0, 2), "FC": (0.514, 0.211, 0.0, 0.303, 0), "DH": (0.805, 0.397, 0.0, 0.060, 1),
    "SH": (0.447, 0.264, 0.0, 0.370, 3), "MF": (0.669, 0.347, 0.0, 1.0, 12), "MFcr": (0.570, 0.259, 0.0, 1.0, 22),
    "IF": (0.570, 0.259, 0.0, 1.0, 22), "FCxr": (0.523, 0.254, 0.0, 0.940, 2),
}
# hand-hardness index (F=1 .. I=6) -> density (kg m-3) where the pit has none; approximate dry-snow values in the
# range of Geldsetzer & Jamieson (2000)
HARD_RHO = {1: 95.0, 2: 165.0, 3: 245.0, 4: 315.0, 5: 385.0, 6: 550.0}
# medians of the transcribed pit layers with both a measured density and a hand hardness (1847 layers; K from 34)
HARD_RHO_PITS = {1: 130.0, 2: 220.0, 3: 260.0, 4: 308.0, 5: 340.0, 6: 550.0}
ELEM_CM = 2.0


def _read_sno(path: Path) -> tuple[list[str], list[list[str]]]:
    head, rows, data = [], [], False
    for ln in Path(path).read_text().splitlines():
        if data:
            if ln.strip():
                rows.append(ln.split())
            continue
        head.append(ln)
        if ln.strip() == "[DATA]":
            data = True
    return head, rows


def _class_key(g: str | None) -> str | None:
    if not g:
        return None
    for k in ("MFcr", "FCxr"):
        if g.startswith(k):
            return k
    return g[:2] if g[:2] in CLASS_MICRO else None


def _rho_from_hardness(h: float | None, table: dict[int, float] = HARD_RHO) -> float | None:
    if h is None:
        return None
    lo, hi = int(max(1, min(6, h // 1))), int(max(1, min(6, -(-h // 1))))
    return table[lo] + (table[hi] - table[lo]) * (h - lo) if hi != lo else table[lo]


def sno_swe(rows: list[list[str]]) -> float:
    """Column water equivalent (kg m-2 = mm) of .sno data rows: ice and liquid water."""
    return sum(float(r[1]) * (float(r[3]) * 917.0 + float(r[4]) * 1000.0) for r in rows)


def pit_to_sno(model_sno: Path, pit: dict, dest: Path, hard_rho: dict[int, float] = HARD_RHO,
               swe_target: float | None = None) -> dict:
    """Restart state whose layering is the pit's (thickness, grain class, hardness-derived or measured density),
    keeping the model's temperature and deposition dates at the same relative height. Layers without a grain
    form keep the model's microstructure there. ``swe_target`` (mm): densities scaled so the column holds that
    mass (thicknesses, hence depth, unchanged). Returns counts of what came from where."""
    head, rows = _read_sno(model_sno)
    if not rows:
        raise ValueError("model state has no snow")
    th = [float(r[1]) for r in rows]
    tops = pd.Series(th).cumsum().tolist()
    hs_m = tops[-1]
    layers = sorted((ly for ly in pit.get("layers", []) if ly.get("top_cm") is not None
                     and ly.get("bottom_cm") is not None and ly["top_cm"] > ly["bottom_cm"]), key=lambda x: x["bottom_cm"])
    hs_pit = float(pit.get("hs_cm") or max(ly["top_cm"] for ly in layers)) / 100.0
    if not layers or hs_pit <= 0.05:
        raise ValueError("pit has no usable layers")

    def model_row_at(rel: float) -> list[str]:
        z = rel * hs_m
        i = next((k for k, t in enumerate(tops) if t >= z), len(rows) - 1)
        return rows[i]

    out, src = [], {"grain_from_pit": 0, "grain_from_model": 0, "rho_measured": 0, "rho_from_hardness": 0,
                    "rho_from_model": 0}
    for ly in layers:
        bot, top = ly["bottom_cm"] / 100.0, min(ly["top_cm"] / 100.0, hs_pit)
        n = max(1, round((top - bot) * 100 / ELEM_CM))
        for k in range(n):
            z0 = bot + (top - bot) * k / n
            z1 = bot + (top - bot) * (k + 1) / n
            m = model_row_at(((z0 + z1) / 2) / hs_pit)
            rho = ly.get("density_kg_m3")
            if rho:
                src["rho_measured"] += 1
            else:
                rho = _rho_from_hardness(ly.get("hardness_index"), hard_rho)
                if rho:
                    src["rho_from_hardness"] += 1
                else:
                    rho = float(m[3]) * 917.0
                    src["rho_from_model"] += 1
            ice = min(0.95, rho / 917.0)
            key = _class_key(ly.get("grain_form"))
            if key:
                rg, rb, dd, spv, mk = CLASS_MICRO[key]
                src["grain_from_pit"] += 1
            else:
                rg, rb, dd, spv, mk = float(m[10]), float(m[11]), float(m[12]), float(m[13]), int(float(m[14]))
                src["grain_from_model"] += 1
            T = min(float(m[2]), 273.15)
            row = [m[0], f"{z1 - z0:.6f}", f"{T:.6f}", f"{ice:.6f}", "0.000000", f"{1 - ice:.6f}", "0.000000",
                   "0.0", "0.000", "0.0", f"{rg:.6f}", f"{rb:.6f}", f"{dd:.6f}", f"{spv:.6f}", str(mk), "0.000000",
                   *m[16:]]
            out.append(row)
    mass_factor = 1.0
    if swe_target:
        mass_factor = swe_target / max(sno_swe(out), 1e-6)
        for r in out:
            ice = min(0.95, float(r[3]) * mass_factor)
            r[3], r[5] = f"{ice:.6f}", f"{1 - ice:.6f}"
    hs_new = sum(float(r[1]) for r in out)
    text = []
    for ln in head:
        if ln.startswith("nSnowLayerData"):
            ln = f"nSnowLayerData   = {len(out)}"
        elif ln.startswith("HS_Last"):
            ln = f"HS_Last          = {hs_new:.6f}"
        elif ln.startswith("ErosionLevel"):
            ln = f"ErosionLevel     = {max(0, len(out) - 1)}"
        text.append(ln)
    Path(dest).write_text("\n".join(text) + "\n" + "\n".join("  ".join(r) for r in out) + "\n")
    return {"elements": len(out), "hs_m": round(hs_new, 3), "swe_mm": round(sno_swe(out), 1),
            "mass_factor": round(mass_factor, 3), **src}


def _run_reinit(args) -> dict:
    """Re-initialise from pit a (its layering, the model's temperatures), run to pit b; also score persistence
    (pit a itself as the prediction of pit b, no model)."""
    from snowagent.baseline.run import plot_unit
    from snowagent.engine import snowpack as sp
    from snowagent.engine.column import prepare_and_run
    from snowagent.obs.agreement import compare_profiles
    from snowagent.web.build import _cfg, _profiles, _score

    plot, a, b, sno, forcing_csv, model_hs_a, work, *rest = args
    variant = rest[0] if rest else "reinit"
    p = _cfg()["plots"][plot]
    unit = plot_unit(plot, p["lat"], p["lon"], p["elevation_m"])
    t0 = pd.Timestamp(Path(sno).name.split(".sno")[1][:12], tz="UTC")
    tb = pd.Timestamp(b["obs_time_utc"])
    base = {"plot": plot, "pit_a": a["profile_id"], "pit_b": b["profile_id"], "t_b": str(tb),
            "gap_d": round((tb - t0).total_seconds() / 86400, 1), "hs_pit_a": _pit_hs(a), "hs_pit_b": _pit_hs(b)}
    c = compare_profiles(b, a)
    keep = ("hs_diff_cm", "grain_class_agreement", "hardness_mae_index", "hardness_bias_index", "boundary_f1")
    out = {**base, **{f"persist_{k}": (None if c.get(k) is None else round(float(c[k]), 3)) for k in keep}}
    rd = Path(work) / f"{a['profile_id'][:40]}_{variant}"
    rd.mkdir(parents=True, exist_ok=True)
    try:
        kw: dict = {}
        if variant in ("reinit_pitrho", "reinit_mass"):
            kw["hard_rho"] = HARD_RHO_PITS
        if variant == "reinit_mass":  # the mass the depth update (w = 1) would carry: model SWE x HS_pit / HS_model
            hs_a = _pit_hs(a)
            f = hs_a / model_hs_a if hs_a and model_hs_a >= MIN_HS_CM else 1.0
            kw["swe_target"] = sno_swe(_read_sno(Path(sno))[1]) * f
        info = pit_to_sno(Path(sno), a, rd / "init.sno", **kw)
        forcing = pd.read_csv(forcing_csv, index_col=0, parse_dates=True)
        forcing = forcing[(forcing.index >= t0 - pd.Timedelta(hours=6)) & (forcing.index <= tb.ceil("6h"))]
        res = prepare_and_run(sp.find_engine(), sp.EngineSettings(prof_days_between=0.25), rd / "run", unit,
                              forcing, tb.ceil("6h").to_pydatetime(), initial_sno=rd / "init.sno")
        prof = _profiles(res.pro, 0.0, None, [])
        near = min(prof, key=lambda pr: abs((pd.Timestamp(pr["t"] + ":00", tz="UTC") - tb).total_seconds()))
        out |= {"reinit_hs_model_b": near["hs"], **{f"reinit_{k}": v for k, v in _score(b, near["L"]).items()},
                **{f"reinit_{k}": v for k, v in info.items()}}
    except Exception as exc:  # noqa: BLE001
        out["reinit_error"] = f"{type(exc).__name__}: {exc}"[:200]
    shutil.rmtree(rd, ignore_errors=True)
    return out


def run_experiment2(plots: list[str], seasons: list[int], work: Path, out: Path, workers: int = 4,
                    variant: str = "reinit") -> pd.DataFrame:
    """``variant``: "reinit" (ADR-038 table hardness -> density), "reinit_pitrho" (density from the pits' own
    hardness-density medians), "reinit_mass" (pitrho, then densities scaled to the depth update's column mass)."""
    jobs = []
    for plot in plots:
        for y in seasons:
            try:
                jobs += season_jobs(plot, y, work)
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"plot": plot, "season": y, "error": str(exc)[:200]}), flush=True)
    jobs = [(*j, variant) for j in jobs]
    with ProcessPoolExecutor(workers) as ex:
        rows = list(ex.map(_run_reinit, jobs))
    d = pd.DataFrame(rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(out, index=False)
    return d


# ------------------------------------------------------------------------------------------------ adopted update
STEER_WEIGHT = 1.0  # ADR-038: chosen leave-one-season-out in 11/11 seasons (next-pit |HS| 15.3 -> 7.4 cm)


def _backups(run_dir: Path) -> dict[pd.Timestamp, Path]:
    return {pd.Timestamp(f.name.split(".sno")[1][:12], tz="UTC"): f for f in (Path(run_dir) / "output").glob("*.sno2*")}


def steered_run(free, smet: pd.DataFrame, unit, start: pd.Timestamp, end: pd.Timestamp, pits: list[dict],
                work: Path, settings, w: float = STEER_WEIGHT) -> dict:
    """Pit-steered season: the free run until the first 00 UTC after a pit, then its state scaled toward the pit's
    snow depth (``scale_sno``), continued to the next update, and so on. Each update uses only pits observed before
    it, so the profile at a pit's own time is still the prediction made without that pit.

    Returns ``segments`` [(t_from, t_to, RunOutputs)], ``updates`` (pit, time, depths, factor), ``backups`` (state
    at each 00 UTC, the updated one at an update time), ``hs`` (modelled snow depth, cm, hourly)."""
    from snowagent.engine import snowpack as sp
    from snowagent.engine.column import prepare_and_run

    times = {}
    for o in pits:
        hs = _pit_hs(o)
        if hs is None:
            continue
        t = pd.Timestamp(o["obs_time_utc"]).ceil("D")
        if start < t < end:
            times[t] = o  # the last pit before each update time wins
    segs = [(start, end, free)]
    backups = dict(_backups(free.run_dir))
    updates = []
    hs_series = sp.parse_met(free.met)["Modelled snow depth (vertical)"]
    cur, cur_hs = free, hs_series
    for k, t in enumerate(sorted(times)):
        o = times[t]
        bk = _backups(cur.run_dir).get(t)
        if bk is None:
            continue
        model_hs = float(cur_hs[cur_hs.index <= t].iloc[-1])
        if model_hs < MIN_HS_CM:
            updates.append({"pit": o["profile_id"], "time_utc": t.isoformat(), "hs_pit_cm": _pit_hs(o),
                            "hs_model_cm": round(model_hs, 1), "factor": 1.0, "note": "model too shallow; no update"})
            continue
        f = 1.0 + w * (_pit_hs(o) / model_hs - 1.0)
        rd = Path(work) / f"steer_{k:02d}_{t:%Y%m%d}"
        rd.mkdir(parents=True, exist_ok=True)
        init = rd / "init.sno"
        scale_sno(bk, init, f)
        forcing = smet[(smet.index >= t - pd.Timedelta(hours=6)) & (smet.index <= end)]
        out = prepare_and_run(sp.find_engine(), settings, rd / "run", unit, forcing, end.to_pydatetime(),
                              initial_sno=init)
        segs[-1] = (segs[-1][0], t, segs[-1][2])
        segs.append((t, end, out))
        backups = {**{bt: p for bt, p in backups.items() if bt < t}, t: init,
                   **{bt: p for bt, p in _backups(out.run_dir).items() if bt > t}}
        cur, cur_hs = out, sp.parse_met(out.met)["Modelled snow depth (vertical)"]
        hs_series = pd.concat([hs_series[hs_series.index < t], cur_hs[cur_hs.index >= t]])
        updates.append({"pit": o["profile_id"], "time_utc": t.isoformat(), "hs_pit_cm": _pit_hs(o),
                        "hs_model_cm": round(model_hs, 1), "factor": round(f, 3)})
    return {"segments": segs, "updates": updates, "backups": backups, "hs": hs_series, "weight": w}
