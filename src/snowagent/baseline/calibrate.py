"""Leave-one-season-out (LOSO) test of a single precipitation multiplier per plot.

For every candidate factor and season the plot is rerun with gauge/ERA5 precipitation x factor (all other
forcing unchanged). For each held-out season the factor is chosen on the OTHER seasons (lowest mean HS MAE vs
the plot sensor) and scored on the held-out season against the factor-1.0 baseline. The factor is adopted
only if it improves the held-out score in most seasons (CLAUDE.md principle 3).
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd
import yaml

from snowagent.baseline.assemble import assemble
from snowagent.baseline.evaluate import hs_scores
from snowagent.baseline.run import plot_unit, run_season
from snowagent.ingest.fts360 import load_station

HS_COL = "Modelled snow depth (vertical)"


def _pit_hs_errors(plot: str, met: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    """Model minus pit snow depth (cm) at every plot pit of the season (model HS at the pit hour)."""
    from snowagent.baseline.evaluate import observed_at_plot

    hs = met[HS_COL]
    errs = []
    for o in observed_at_plot(Path("data/interim/obs/observed_profiles.jsonl"), plot, start, end):
        t = pd.Timestamp(o["obs_time_utc"])
        if o.get("hs_cm") is None or t < hs.index[0] or t > hs.index[-1]:
            continue
        errs.append(float(hs[hs.index <= t].iloc[-1]) - float(o["hs_cm"]))
    return {"pit_n": len(errs), "pit_abs_cm": float(pd.Series(errs).abs().mean()) if errs else None,
            "pit_bias_cm": float(pd.Series(errs).mean()) if errs else None}


def _one(args) -> dict:
    plot, year, factor, work = args
    cfg = yaml.safe_load(Path("config/plot_forcing.yaml").read_text())
    p = cfg["plots"][plot]
    st = p["hs_check"][0]
    d = load_station(st).set_index("time_utc")
    obs = d["hs_m"].where(d["hs_m_qc"] == "ok")
    done = list((Path(work) / f"f{factor:.2f}" / f"{plot}_{year}" / "output").glob("*.met"))
    if done:  # resume after an interrupted test (container restarts)
        from snowagent.engine import snowpack as sp

        met = sp.parse_met(done[0])
        if met.index[-1] >= pd.Timestamp(f"{year + 1}-05-30", tz="UTC"):
            return {"plot": plot, "season": year, "factor": factor, **hs_scores(met[HS_COL] / 100.0, obs),
                    **_pit_hs_errors(plot, met, met.index[0], met.index[-1])}
    start = pd.Timestamp(f"{year}-{cfg['season_start']}", tz="UTC")
    end = pd.Timestamp(f"{year + 1}-{cfg['season_end']}", tz="UTC")
    pf = assemble(plot, str(start - pd.Timedelta(hours=6)), str(end))
    complete = pf.data.notna().all(axis=1)
    if not complete.all():
        end = (complete[~complete].index[0] - pd.Timedelta(hours=1)).floor("D")
        pf.data, pf.sources = pf.data[:end], pf.sources[:end]
    pf.data["psum"] = pf.data["psum"] * factor
    r = run_season(pf, plot_unit(plot, p["lat"], p["lon"], p["elevation_m"]), start, end,
                   Path(work) / f"f{factor:.2f}")
    sc = hs_scores(r["met"][HS_COL] / 100.0, obs)
    return {"plot": plot, "season": year, "factor": factor, **sc, **_pit_hs_errors(plot, r["met"], start, end)}


def loso(plot: str, seasons: list[int], factors: list[float], work: Path, workers: int = 8,
         pit_weight: float = 0.0) -> dict:
    """``pit_weight`` 0 chooses the factor by the plot sensor alone (ADR-024); 1 by the pits alone; between,
    by (1 - w) * sensor MAE (cm) + w * pit |HS error| (cm). Scores for both targets are always reported."""
    jobs = [(plot, y, f, str(work)) for y in seasons for f in factors]
    with ProcessPoolExecutor(workers) as ex:
        rows = list(ex.map(_one, jobs))
    df = pd.DataFrame(rows)
    df["target"] = (1 - pit_weight) * df["mae_m"] * 100 + pit_weight * df["pit_abs_cm"].fillna(df["mae_m"] * 100)
    out = []
    for y in seasons:
        train = df[df.season != y].groupby("factor")["target"].mean()
        best = float(train.idxmin())
        test = df[df.season == y].set_index("factor")
        out.append({"held_out": y, "chosen_factor": best, "mae_baseline": float(test.loc[1.0, "mae_m"]),
                    "mae_corrected": float(test.loc[best, "mae_m"]), "bias_baseline": float(test.loc[1.0, "bias_m"]),
                    "bias_corrected": float(test.loc[best, "bias_m"]),
                    "pit_n": int(test.loc[1.0, "pit_n"] or 0),
                    "pit_abs_baseline": _f(test.loc[1.0, "pit_abs_cm"]), "pit_abs_corrected": _f(test.loc[best, "pit_abs_cm"]),
                    "pit_bias_baseline": _f(test.loc[1.0, "pit_bias_cm"]),
                    "pit_bias_corrected": _f(test.loc[best, "pit_bias_cm"])})
    res = pd.DataFrame(out)
    improved = int((res.mae_corrected < res.mae_baseline).sum())
    return {"grid": rows, "loso": out, "seasons_improved": improved, "n_seasons": len(seasons),
            "mean_mae_baseline": float(res.mae_baseline.mean()), "mean_mae_corrected": float(res.mae_corrected.mean()),
            "adopt": bool(improved > len(seasons) / 2 and res.mae_corrected.mean() < res.mae_baseline.mean())}


def _f(x) -> float | None:
    return None if x is None or pd.isna(x) else float(x)
