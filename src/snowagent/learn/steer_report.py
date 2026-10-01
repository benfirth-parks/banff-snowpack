"""Analysis of the observation-steering experiments (ADR-038): weights chosen leave-one-season-out, scored on the
next pit, against the free run and against persistence (the previous pit carried forward)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def season_of(t: str) -> int:
    ts = pd.Timestamp(t)
    return ts.year if ts.month >= 8 else ts.year - 1


def exp1_loso(d: pd.DataFrame) -> dict:
    """Depth-nudge weight w: for each held-out season choose w on the other seasons (lowest mean |HS error| at the
    next pit, all plots pooled and per plot), report held-out errors vs the free run (w = 0)."""
    d = d[d.get("error").isna()] if "error" in d else d
    d = d.assign(season=d["t_b"].map(season_of), abs_hs=(d["hs_model_b"] - d["hs_pit_b"]).abs(),
                 bias=d["hs_model_b"] - d["hs_pit_b"])
    out: dict = {"pairs": int(d[d.w == 0].shape[0]), "by_w": {}, "loso": {}}
    for w, g in d.groupby("w"):
        out["by_w"][float(w)] = {k: round(float(g[k].mean()), 3) for k in
                                 ("abs_hs", "bias", "grain_class_agreement", "hardness_mae_index", "boundary_f1")}
    for scope, sub in [("all", d)] + [(p, g) for p, g in d.groupby("plot")]:
        rows = []
        for y in sorted(sub.season.unique()):
            train = sub[sub.season != y].groupby("w")["abs_hs"].mean()
            best = float(train.idxmin())
            test = sub[sub.season == y]
            free, chosen = test[test.w == 0], test[test.w == best]
            rows.append({"season": int(y), "w": best, "n": len(free), "free_abs_hs": free.abs_hs.mean(),
                         "steered_abs_hs": chosen.abs_hs.mean(),
                         "free_grain": free.grain_class_agreement.mean(),
                         "steered_grain": chosen.grain_class_agreement.mean()})
        r = pd.DataFrame(rows)
        wts = r.n / r.n.sum()
        out["loso"][scope] = {"seasons": len(r), "w_chosen": r.w.value_counts().to_dict(),
                              "free_abs_hs": round(float((r.free_abs_hs * wts).sum()), 2),
                              "steered_abs_hs": round(float((r.steered_abs_hs * wts).sum()), 2),
                              "seasons_better": int((r.steered_abs_hs < r.free_abs_hs - 1e-9).sum()),
                              "seasons_worse": int((r.steered_abs_hs > r.free_abs_hs + 1e-9).sum()),
                              "free_grain": round(float((r.free_grain * wts).sum()), 3),
                              "steered_grain": round(float((r.steered_grain * wts).sum()), 3)}
    return out


def variance_weight(d: pd.DataFrame, obs_sd_cm: float) -> dict:
    """Optimal-interpolation weight w = s_m^2 / (s_m^2 + s_o^2): s_m from the free run's error at pits (minus the
    observation's own share), s_o the pit-to-pit depth noise measured independently."""
    f = d[(d.w == 0) & d.hs_model_a.notna()]
    err = (f.hs_model_a - f.hs_pit_a).dropna()
    tot = float(np.var(err))
    s_m2 = max(tot - obs_sd_cm ** 2, 0.0)
    return {"model_minus_pit_sd_cm": round(float(np.sqrt(tot)), 1), "obs_sd_cm": obs_sd_cm,
            "model_sd_cm": round(float(np.sqrt(s_m2)), 1), "w_oi": round(s_m2 / (s_m2 + obs_sd_cm ** 2), 2)}


def pit_noise_cm(observed: list[dict], max_days: float = 3.0) -> dict:
    """Snow-depth difference between pits at the same plot within ``max_days`` (observation noise ceiling)."""
    rows = []
    by_site: dict[str, list] = {}
    for o in observed:
        if o.get("hs_cm") and o.get("site_key") and not o.get("duplicate_of"):
            by_site.setdefault(o["site_key"], []).append((pd.Timestamp(o["obs_time_utc"]), float(o["hs_cm"])))
    for site, v in by_site.items():
        v.sort()
        for (t1, h1), (t2, h2) in zip(v, v[1:], strict=False):
            if (t2 - t1).total_seconds() / 86400 <= max_days:
                rows.append({"site": site, "diff": h2 - h1})
    r = pd.DataFrame(rows)
    if r.empty:
        return {"pairs": 0}
    return {"pairs": len(r), "sd_cm": round(float(r["diff"].std() / np.sqrt(2)), 1),
            "mean_abs_diff_cm": round(float(r["diff"].abs().mean()), 1)}


def exp2_compare(e1: pd.DataFrame, e2: pd.DataFrame) -> pd.DataFrame:
    """Next-pit scores per method on the same pit pairs: free run, depth update (w = 1), re-initialisation from
    the pit, and persistence (the previous pit itself)."""
    e1 = e1[e1.get("error").isna()] if "error" in e1 else e1
    e2 = e2[e2.get("reinit_error").isna()] if "reinit_error" in e2 else e2
    free = e1[e1.w == 0].set_index("pit_a")
    nudge = e1[e1.w == 1.0].set_index("pit_a")
    e2 = e2.set_index("pit_a")
    ids = free.index.intersection(nudge.index).intersection(e2.index)
    rows = []
    for name, hs, g, h, f1 in (
            ("free run", free.hs_model_b - free.hs_pit_b, free.grain_class_agreement, free.hardness_mae_index,
             free.boundary_f1),
            ("depth update", nudge.hs_model_b - nudge.hs_pit_b, nudge.grain_class_agreement,
             nudge.hardness_mae_index, nudge.boundary_f1),
            ("re-initialised from pit", e2.reinit_hs_model_b - e2.hs_pit_b, e2.reinit_grain_class_agreement,
             e2.reinit_hardness_mae_index, e2.reinit_boundary_f1),
            ("previous pit (persistence)", e2.hs_pit_a - e2.hs_pit_b, e2.persist_grain_class_agreement,
             e2.persist_hardness_mae_index, e2.persist_boundary_f1)):
        rows.append({"method": name, "pairs": len(ids), "abs_hs_cm": round(float(hs.loc[ids].abs().mean()), 1),
                     "grain": round(float(g.loc[ids].mean()), 3), "hardness_mae": round(float(h.loc[ids].mean()), 3),
                     "boundary_f1": round(float(f1.loc[ids].mean()), 3)})
    return pd.DataFrame(rows)
