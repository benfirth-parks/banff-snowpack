import json, shutil, sys
from pathlib import Path
import pandas as pd
from snowagent.web.build import build_season
S = Path("artifacts/gfs")
d = pd.read_csv("artifacts/gfs/gfs_vs_obs_by_lead.csv"); d = d[d.gauge_ok == True]
r3 = d.groupby(["plot", "season", "run"]).agg(pg=("p_gfs", "sum"), po=("p_obs", "sum"), tg=("t_gfs", "mean"),
                                              to=("t_obs", "mean"), n=("day", "size")).reset_index()
r3 = r3[r3.n == 3]
for plot in ("goats_eye", "simpson", "bow_summit"):
    for y in range(2021, 2026):
        tr = r3[(r3["plot"] == plot) & (r3.season != y)]
        f, dt = float(tr.po.sum() / tr.pg.sum()), float((tr.to - tr.tg).mean())
        for name, corr in (("t", {"ta_offset_k": round(dt, 2)}),
                           ("tp", {"ta_offset_k": round(dt, 2), "psum_factor": round(f, 3)})):
            out = S / "gfscorr" / name
            if (out / plot / f"{y}-{y+1}_forecasts.json").exists():
                continue
            r = build_season(plot, y, out, S / "gfscorr_work", workers=4, issued_dir=S / "gfscorr_issued" / name,
                             gfs_correction=corr)
            print(name, json.dumps(r), corr, flush=True)
            # keep only forecasts (profiles of the season itself are not needed)
            (out / plot / f"{y}-{y+1}.json").unlink(missing_ok=True)
