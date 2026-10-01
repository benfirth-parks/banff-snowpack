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
            return {"plot": plot, "season": year, "factor": factor, **hs_scores(met[HS_COL] / 100.0, obs)}
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
    return {"plot": plot, "season": year, "factor": factor, **sc}


def loso(plot: str, seasons: list[int], factors: list[float], work: Path, workers: int = 8) -> dict:
    jobs = [(plot, y, f, str(work)) for y in seasons for f in factors]
    with ProcessPoolExecutor(workers) as ex:
        rows = list(ex.map(_one, jobs))
    df = pd.DataFrame(rows)
    out = []
    for y in seasons:
        train = df[df.season != y].groupby("factor")["mae_m"].mean()
        best = float(train.idxmin())
        test = df[df.season == y].set_index("factor")
        out.append({"held_out": y, "chosen_factor": best, "mae_baseline": float(test.loc[1.0, "mae_m"]),
                    "mae_corrected": float(test.loc[best, "mae_m"]), "bias_baseline": float(test.loc[1.0, "bias_m"]),
                    "bias_corrected": float(test.loc[best, "bias_m"])})
    res = pd.DataFrame(out)
    improved = int((res.mae_corrected < res.mae_baseline).sum())
    return {"grid": rows, "loso": out, "seasons_improved": improved, "n_seasons": len(seasons),
            "mean_mae_baseline": float(res.mae_baseline.mean()), "mean_mae_corrected": float(res.mae_corrected.mean()),
            "adopt": bool(improved > len(seasons) / 2 and res.mae_corrected.mean() < res.mae_baseline.mean())}
