"""Storm-conditional GFS precipitation correction (ADR-042): quantile mapping of 24 h totals, fitted per plot and
lead day on the other seasons (leave-one-season-out), scored on the held-out season."""
import numpy as np
import pandas as pd

QS = np.linspace(0.0, 1.0, 101)


def fit(tr: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    return np.quantile(tr.p_gfs, QS), np.quantile(tr.p_obs, QS)


def apply(x: np.ndarray, qg: np.ndarray, qo: np.ndarray, threshold: float = 0.0) -> np.ndarray:
    """Map GFS 24 h totals to measured quantiles; amounts at or below ``threshold`` mm stay as forecast."""
    qg_u, idx = np.unique(qg, return_index=True)
    y = np.interp(x, qg_u, qo[idx], right=np.nan)
    top = qo[-1] / max(qg[-1], 1e-6)          # beyond the training range: the top ratio
    y = np.where(np.isnan(y), x * top, y)
    return np.where(x > threshold, np.maximum(y, 0.0), x)


if __name__ == "__main__":
    d = pd.read_csv("artifacts/gfs/gfs_vs_obs_by_lead.csv")
    d = d[d.gauge_ok == True]  # noqa: E712
    rows = []
    for (plot, day), g in d.groupby(["plot", "day"]):
        for y in sorted(g.season.unique()):
            tr, te = g[g.season != y], g[g.season == y].copy()
            qg, qo = fit(tr)
            for thr in (0.0, 5.0, 10.0):
                te[f"qm{int(thr)}"] = apply(te.p_gfs.to_numpy(), qg, qo, thr)
            rows.append(te.assign(held_out=y))
    r = pd.concat(rows)
    r.to_csv("artifacts/gfs/storm_qm_days.csv", index=False)
    v = ["p_gfs", "qm0", "qm5", "qm10"]
    print("mean abs error per forecast day (mm):")
    print(r.groupby("day")[v].apply(lambda x: (x.sub(r.loc[x.index, "p_obs"], axis=0)).abs().mean()).round(2))
    st = r[r.p_obs > 15]
    print("storm days (>15 mm measured):", len(st), " total ratio forecast/measured:")
    print((st[v].sum() / st.p_obs.sum()).round(2))
    fa = r[(r.p_obs < 2)]
    print("dry days (<2 mm measured): mean forecast mm", fa[v].mean().round(2).to_dict())
    s = r.groupby(["plot", "season"]).apply(lambda x: pd.Series({k: (x[k] - x.p_obs).abs().mean() for k in v}))
    print("held-out plot-seasons better than raw:", {k: int((s[k] < s.p_gfs).sum()) for k in v[1:]}, "of", len(s))
    s72 = r.groupby(["plot", "season", "run"])[v + ["p_obs"]].sum()
    s72 = s72.groupby(["plot", "season"]).apply(lambda x: pd.Series({k: (x[k] - x.p_obs).abs().mean() for k in v}))
    print("72 h totals, plot-seasons better:", {k: int((s72[k] < s72.p_gfs).sum()) for k in v[1:]}, "mean abs:",
          s72.mean().round(2).to_dict())
