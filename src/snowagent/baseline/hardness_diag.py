"""Where does the model's soft-hardness offset come from? (diagnostic, no model change)

For every observed pit layer with a measured density and hand hardness, the model element at the same relative
height (height / HS, so an HS error does not shift the pairing) is taken from the run's .pro output. Two
questions are separated:

1. Density: is the model's density at that height lower than measured? (densification / settlement)
2. Hardness at a given density: for the same grain class and density bin, is the model's hand hardness lower
   than the observers'? (the engine's density/grain -> hardness relation, output 0534)

If (2) holds while (1) does not, the offset is in the hardness relation, not in the snow's mass structure.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

DENSITY_BINS = [0, 100, 150, 200, 250, 300, 350, 400, 450, 600]


def pairs_from_runs(results_json: Path, observed_jsonl: Path, max_hours: float = 12.0) -> pd.DataFrame:
    from snowagent.baseline.evaluate import observed_at_plot
    from snowagent.baseline.run import profile_at
    from snowagent.engine import snowpack as sp

    res = json.loads(Path(results_json).read_text())
    rows = []
    for key, r in res.items():
        if "run_dir" not in r:
            continue
        plot, season = key.rsplit("_", 1)
        y0 = int(season[:4])
        pro = sorted(Path(r["run_dir"]).rglob("*.pro"))
        if not pro:
            continue
        _h, profiles = sp.parse_pro(pro[0])
        pits = observed_at_plot(Path(observed_jsonl), plot, pd.Timestamp(f"{y0}-08-01", tz="UTC"),
                                pd.Timestamp(f"{y0 + 1}-07-31", tz="UTC"))
        for o in pits:
            when = pd.Timestamp(o["obs_time_utc"])
            t, layers, _d = profile_at(profiles, when)
            if abs((t - when).total_seconds()) > max_hours * 3600 or not layers:
                continue
            hs_obs = o.get("hs_cm") or max((ly["top_cm"] for ly in o["layers"] if ly.get("top_cm") is not None),
                                           default=None)
            hs_mod = max(ly.top_vertical_m for ly in layers) * 100
            if not hs_obs or hs_mod <= 0:
                continue
            for ly in o["layers"]:
                if ly.get("density_kg_m3") is None or ly.get("top_cm") is None or ly.get("bottom_cm") is None:
                    continue
                z = (ly["top_cm"] + ly["bottom_cm"]) / 2 / hs_obs * hs_mod
                m = next((e for e in layers if e.bottom_vertical_m * 100 <= z <= e.top_vertical_m * 100), None)
                if m is None:
                    continue
                g = m.grain_form_primary
                rows.append({"plot": plot, "season": season, "profile_id": o["profile_id"],
                             "rel_height": round(z / hs_mod, 3), "obs_class": ly.get("grain_class"),
                             "mod_class": g[:2] if g else None, "obs_density": ly["density_kg_m3"],
                             "mod_density": m.density_kg_m3, "obs_hardness": ly.get("hardness_index"),
                             "mod_hardness": m.hand_hardness_index})
    return pd.DataFrame(rows)


def summarise(df: pd.DataFrame) -> dict:
    """Density and hardness differences, and hardness at matched density and class."""
    d = df.dropna(subset=["obs_density", "mod_density"])
    h = df.dropna(subset=["obs_hardness", "mod_hardness"])
    out = {"pairs": len(df), "pits": int(df["profile_id"].nunique()),
           "density_bias_kg_m3": round(float((d.mod_density - d.obs_density).mean()), 1),
           "density_mae_kg_m3": round(float((d.mod_density - d.obs_density).abs().mean()), 1),
           "hardness_bias_index": round(float((h.mod_hardness - h.obs_hardness).mean()), 2)}
    by_class = {}
    for c, g in df.groupby("obs_class"):
        if len(g) >= 15:
            gd, gh = g.dropna(subset=["mod_density"]), g.dropna(subset=["obs_hardness", "mod_hardness"])
            by_class[c] = {"n": len(g), "obs_density": round(float(gd.obs_density.mean())),
                           "mod_density": round(float(gd.mod_density.mean())),
                           "obs_hardness": round(float(gh.obs_hardness.mean()), 2),
                           "mod_hardness": round(float(gh.mod_hardness.mean()), 2)}
    out["by_observed_class"] = by_class
    # hardness at matched density: observed layers vs model elements binned by their OWN density and class
    obs = df.dropna(subset=["obs_hardness"]).assign(b=lambda x: pd.cut(x.obs_density, DENSITY_BINS))
    mod = df.dropna(subset=["mod_hardness", "mod_density"]).assign(b=lambda x: pd.cut(x.mod_density, DENSITY_BINS))
    rel = []
    for (c, b), g in obs.groupby(["obs_class", "b"], observed=True):
        gm = mod[(mod.mod_class == c) & (mod.b == b)]
        if len(g) >= 8 and len(gm) >= 8:
            rel.append({"class": c, "density_bin": str(b), "n_obs": len(g), "n_mod": len(gm),
                        "obs_hardness": round(float(g.obs_hardness.mean()), 2),
                        "mod_hardness": round(float(gm.mod_hardness.mean()), 2)})
    out["hardness_at_matched_density"] = rel
    if rel:
        w = np.array([min(r["n_obs"], r["n_mod"]) for r in rel], dtype=float)
        diff = np.array([r["mod_hardness"] - r["obs_hardness"] for r in rel])
        out["hardness_bias_at_matched_density"] = round(float((w * diff).sum() / w.sum()), 2)
    return out


# --- the engine's three hand-hardness relations (SNOWPACK StabilityAlgorithms.cc, b324cbd), for re-scoring saved
# runs offline. Hardness is a diagnostic output (it does not feed back into mass/energy), so recomputing it from
# the .pro element state is equivalent to rerunning with HARDNESS_PARAMETERIZATION changed. Crust/ice branches
# (marker %100 >= 20), identical in all three, are not in the .pro: elements where the MONTI port does not
# reproduce the engine's own 0534 value are taken to be those and keep the engine value.
HOAR_DENSITY_BURIED = 125.0  # engine default (SnowpackConfig.cc), not overridden in our ini
THRESH_MOIST = 0.003  # SnowStation::thresh_moist_snow (volume fraction)

_MONTI_STEPS = {  # F -> upper density bounds of index 1, 2, 3, ... (dry)
    1: [143.9397], 2: [214.2380, 268.2981, 387.4305], 3: [189.2103, 277.8087, 368.4093, 442.4917],
    4: [247.2748, 319.3549, 400.4450, 517.5751], 5: [287.8198, 344.3826], 9: [259.7887, 326.8632, 396.9411, 484.5384],
}
_MONTI_MF = {"dry": [213.7375, 317.3527, 406.9522, 739.8220], "moist": [338.3760, 417.4638, 541.6018, 614.6830]}


def _sh(rho: float) -> float:
    b = HOAR_DENSITY_BURIED
    return 1.0 - b / (250.0 - b) + rho / (250.0 - b)


def _steps(bounds: list[float], rho: float) -> float:
    return float(1 + sum(rho > x for x in bounds))


def _monti_f(f: int, rho: float, theta_w: float) -> float:
    if f == 0:
        return max(1.0, min(6.0, 0.0078 + 0.0105 * rho))
    if f == 6:
        return max(1.0, min(6.0, _sh(rho)))
    if f == 7:
        return _steps(_MONTI_MF["dry" if theta_w < THRESH_MOIST else "moist"], rho)
    if f == 8:
        return 6.0
    if f == 1:
        return 1.0 if rho < 143.9397 else 2.0
    return _steps(_MONTI_STEPS[f], rho)


def hardness_monti(code: int, rho: float, gsz: float, theta_w: float) -> float:
    f1, f2 = code // 100, (code // 10) % 10
    h = 0.5 * (_monti_f(f1, rho, theta_w) + _monti_f(f2, rho, theta_w))
    if f1 == 6:
        h = 1.0 if gsz >= 5.0 else min(h, 2.0)
    return max(1.0, min(6.0, h))


_BELLAIRE = {0: (0.0078, 0.0105), 1: (0.7927, 0.0036), 2: (0.4967, 0.0074), 3: (0.2027, 0.0072),
             4: (0.3867, 0.0083), 5: (-0.00249, 0.0072), 7: (0.5852, 0.0056), 8: (6.0, 0.0), 9: (-0.5226, 0.0104)}


def hardness_bellaire(code: int, rho: float, gsz: float, theta_w: float) -> float:
    f1 = code // 100
    h = _sh(rho) if f1 == 6 else _BELLAIRE[f1][0] + _BELLAIRE[f1][1] * rho
    if f1 == 6:
        h = 1.0 if gsz >= 5.0 else min(h, 2.0)
    return max(1.0, min(6.0, h))


def hardness_asarc(code: int, rho: float, gsz: float, theta_w: float) -> float:
    f1, f2 = code // 100, (code // 10) % 10
    if f1 == 0:
        a, b, c = 1.5, 0.0, 0.0
    elif f1 == 1:
        a, b, c = 0.45, 0.0068, 0.0
    elif f1 == 2:
        a, b, c = 0.0, 0.0140, 0.0
    elif f1 == 3:
        a, b, c = 1.94, 0.0073, -0.192
    elif f1 == 4:
        a, b, c = (0.0, 0.0138, -0.284) if f2 != 9 else (1.29, 0.0094, -0.350)
    elif f1 == 5:
        a, b, c = (-0.80, 0.0150, -0.140) if gsz > 1.5 else (0.0, 0.0138, -0.284)
    elif f1 == 6:
        a, b, c = 1.0 - HOAR_DENSITY_BURIED / (250.0 - HOAR_DENSITY_BURIED), 1.0 / (250.0 - HOAR_DENSITY_BURIED), 0.0
    elif f1 == 7:
        a, b, c = (2.14, 0.0048, 0.0) if theta_w < 0.001 else (3.0, 0.0, 0.0)
    elif f1 == 8:
        a, b, c = 6.0, 0.0, 0.0
    else:  # 9
        a, b, c = 1.29, 0.0094, -0.350
    h = a + b * rho + c * gsz
    if f1 == 6:
        h = min(h, 2.0)
    return max(1.0, min(6.0, h))


METHODS = {"MONTI": hardness_monti, "BELLAIRE": hardness_bellaire, "ASARC": hardness_asarc}


def rehardness(profiles, method: str) -> dict:
    """Replace the 0534 hardness of every .pro profile in place by ``method``; returns port-check counts."""
    fn, n, kept = METHODS[method], 0, 0
    for p in profiles:
        d = p.data
        if "hardness" not in d or "grain_type" not in d:
            continue
        new = []
        for i, h_eng in enumerate(d["hardness"]):
            try:
                code, rho = int(float(d["grain_type"][i])), float(d["density"][i])
                gsz, tw = float(d["grain_size_mm"][i]), float(d["lwc_pct"][i]) / 100.0
                he = abs(float(h_eng))
            except (ValueError, IndexError, KeyError):
                new.append(h_eng)
                continue
            n += 1
            if code // 100 not in range(10) or abs(hardness_monti(code, rho, gsz, tw) - he) > 1e-6:
                kept += 1  # crust / ice branch (or a code the port does not cover): engine value kept
                new.append(h_eng)
            else:
                new.append(f"{fn(code, rho, gsz, tw):.3f}")
        d["hardness"] = new
    return {"elements": n, "engine_value_kept": kept}


def _score_run(args) -> list[dict]:
    key, run_dir, observed_jsonl, methods = args
    from snowagent.baseline.evaluate import observed_at_plot, profile_scores
    from snowagent.engine import snowpack as sp

    plot, season = key.rsplit("_", 1)
    y0 = int(season[:4])
    pro = sorted(Path(run_dir).rglob("*.pro"))
    if not pro:
        return []
    obs = observed_at_plot(Path(observed_jsonl), plot, pd.Timestamp(f"{y0}-08-01", tz="UTC"),
                           pd.Timestamp(f"{y0 + 1}-07-31", tz="UTC"))
    if not obs:
        return []
    out = []
    for method in methods:
        _h, raw = sp.parse_pro(pro[0])
        if method != "ENGINE":
            rehardness(raw, method)
        rows, _s = profile_scores(raw, obs)
        out += [{"key": key, "plot": plot, "season": season, "method": method, **r} for r in rows]
    return out


def score_methods(run_sets: dict[str, list[str]], observed_jsonl: Path, methods=("MONTI", "BELLAIRE", "ASARC"),
                  workers: int = 4) -> pd.DataFrame:
    """Pit scores of each hardness relation for every run in ``run_sets`` ({set name: [results.json, ...]})."""
    from concurrent.futures import ProcessPoolExecutor

    jobs, set_of = [], {}
    for name, files in run_sets.items():
        for f in files:
            for key, r in json.loads(Path(f).read_text()).items():
                if "run_dir" in r:
                    jobs.append((key, r["run_dir"], str(observed_jsonl), tuple(methods)))
                    set_of[(key, r["run_dir"])] = name
    with ProcessPoolExecutor(workers) as ex:
        rows = [row | {"set": set_of[(j[0], j[1])]} for j, res in zip(jobs, ex.map(_score_run, jobs), strict=True)
                for row in res]
    return pd.DataFrame(rows)


def loso_choice(df: pd.DataFrame, metric: str = "hardness_mae_index") -> pd.DataFrame:
    """Leave-one-season-out: per held-out season pick the method with the lowest mean ``metric`` on the other
    seasons, score it on the held-out season against MONTI (the incumbent)."""
    per = df.groupby(["season", "method"])[metric].mean().unstack()
    rows = []
    for s in per.index:
        pick = per.drop(index=s).mean().idxmin()
        rows.append({"season": s, "chosen": pick, "held_out_chosen": per.loc[s, pick],
                     "held_out_incumbent": per.loc[s, "MONTI"]})
    return pd.DataFrame(rows)
