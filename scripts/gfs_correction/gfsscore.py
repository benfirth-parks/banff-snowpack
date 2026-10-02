import json
from pathlib import Path
import pandas as pd
S = Path("artifacts/gfs")
rows = []
for plot in ("goats_eye", "simpson", "bow_summit"):
    for y in range(2021, 2026):
        season = f"{y}-{y+1}"
        d = json.loads(Path(f"web/data/{plot}/{season}.json").read_text())
        now = {p["t"]: (p["hs"], p.get("swe")) for p in d["nowcast"]}
        upd = [pd.Timestamp(u["time_utc"]) for u in d.get("steer", {}).get("updates", []) if not u.get("note")]
        srcs = {"raw": Path(f"web/data/{plot}/{season}_forecasts.json")}
        for v in ("t", "tp"):
            srcs[v] = S / "gfscorr" / v / plot / f"{season}_forecasts.json"
        if not all(p.exists() for p in srcs.values()):
            continue
        for v, f in srcs.items():
            for iss in json.loads(f.read_text())["issues"]:
                if "P" not in iss: continue
                t0 = pd.Timestamp(iss["issue"] + ":00", tz="UTC")
                if any(t0 < u <= t0 + pd.Timedelta(hours=72) for u in upd): continue
                base = now.get(iss["issue"])
                for p in iss["P"]:
                    lead = int((pd.Timestamp(p["t"] + ":00", tz="UTC") - t0).total_seconds() // 3600)
                    if lead not in (24, 48, 72) or p["t"] not in now or base is None: continue
                    hs_o, swe_o = now[p["t"]]
                    rows.append({"plot": plot, "season": y, "issue": iss["issue"], "v": v, "lead": lead,
                                 "hs_err": p["hs"] - hs_o, "dhs_obs": hs_o - base[0],
                                 "swe_err": (p.get("swe") or 0) - (swe_o or 0)})
r = pd.DataFrame(rows)
r.to_csv("artifacts/gfs/gfs_corr_scores.csv", index=False)
common = r.groupby(["plot", "season", "issue", "lead"]).v.nunique()
keep = common[common == 3].index
r = r.set_index(["plot", "season", "issue", "lead"]).loc[keep].reset_index()
r["abs_hs"] = r.hs_err.abs(); r["abs_swe"] = r.swe_err.abs()
print(r.groupby(["lead", "v"])[["abs_hs", "hs_err", "abs_swe", "swe_err"]].mean().round(2).unstack("v"))
st = r[r.dhs_obs > 10]
print("storms (>10 cm gain):", st.issue.nunique()); print(st.groupby(["lead", "v"])[["abs_hs", "hs_err"]].mean().round(1).unstack("v"))
ps = r[r.lead == 72].groupby(["plot", "season", "v"]).abs_hs.mean().unstack("v")
print(ps.round(2)); print("seasons better t:", int((ps.t < ps.raw).sum()), "tp:", int((ps.tp < ps.raw).sum()), "of", len(ps))
