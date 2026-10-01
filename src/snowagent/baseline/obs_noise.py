"""How much do observations of the same plot differ among themselves? (ADR-027)

Model-vs-pit differences mix model error with observation-side variability (observer judgement, pit position
within the plot, day-to-day change). Before any model-vs-pit difference is called a model error, the same
comparison is made between observations:

- ``pit_pairs``: consecutive pits at one plot a few days apart, scored with the same metric as model-vs-pit
  (``compare_profiles``, earlier pit as reference). Real snowpack change over the gap is included, so this is
  an upper bound on pure observation noise for that gap.
- ``failure_layers``: heights where a stability test recorded a failure. A test failure locates a weak layer
  without relying on grain-type judgement, so it gives a check of model weak layers that is independent of the
  observer's grain classification.

No observation is altered; nothing here changes the model.
"""

from __future__ import annotations

import pandas as pd

from snowagent.obs.agreement import PERSISTENT, compare_profiles

NO_FAILURE = {"CTN", "ECTX", "DTN", "STN", "RB7", "PSTX"}


def pit_pairs(obs: list[dict], max_days: float) -> list[dict]:
    """Scores for each pit against the next pit at the same plot within ``max_days`` (earlier = reference)."""
    obs = sorted((o for o in obs if o.get("layers")), key=lambda o: o["obs_time_utc"])
    rows = []
    for a, b in zip(obs, obs[1:], strict=False):
        gap = (pd.Timestamp(b["obs_time_utc"]) - pd.Timestamp(a["obs_time_utc"])).total_seconds() / 86400
        if 0 < gap <= max_days:
            rows.append({"a": a["profile_id"], "b": b["profile_id"], "gap_days": round(gap, 2)}
                        | compare_profiles(a, b))
    return rows


def failure_layers(o: dict) -> list[float]:
    """Heights (cm above ground) of recorded test failures; tests reporting no failure are excluded."""
    out = []
    for t in o.get("tests", []):
        res = (t.get("result") or "").upper()
        if t.get("height_cm") is None or res in NO_FAILURE:
            continue
        out.append(float(t["height_cm"]))
    return out


def weak_layer_test_support(observed: dict, model: dict, tol_cm: float = 5.0) -> dict:
    """For one pit: are model / observed persistent weak layers located at a recorded test failure?

    Counts model persistent layers that are (a) confirmed by an observed persistent layer, (b) not confirmed
    by grain type but at a test failure, (c) neither; and test failures that the model / the observer mark
    as a persistent layer.
    """
    fails = failure_layers(observed)
    ow = [ly for ly in observed.get("layers", []) if ly.get("grain_class") in PERSISTENT
          and ly.get("top_cm") is not None and ly.get("bottom_cm") is not None]
    mw = [ly for ly in model.get("layers", []) if ly.get("grain_class") in PERSISTENT]

    def near(ly, h):
        return ly["bottom_cm"] - tol_cm <= h <= ly["top_cm"] + tol_cm

    def overlaps(a, b):
        return a["bottom_cm"] - tol_cm <= b["top_cm"] and b["bottom_cm"] <= a["top_cm"] + tol_cm

    hs = observed.get("hs_cm") or model.get("hs_cm") or 0
    out = {"tests_failed": len(fails), "model_wl": len(mw), "model_wl_confirmed_grain": 0,
           # chance level: share of the column (1 cm steps) within tol of a persistent layer, weighted by the
           # number of test failures so pooled hit rates can be compared with it
           "chance_hits_model": _coverage(mw, hs, tol_cm) * len(fails),
           "chance_hits_observed": _coverage(ow, hs, tol_cm) * len(fails),
           "model_wl_unconfirmed_at_failure": 0, "model_wl_unsupported": 0,
           "failures_at_model_wl": sum(any(near(m, h) for m in mw) for h in fails),
           "failures_at_observed_wl": sum(any(near(w, h) for w in ow) for h in fails)}
    for m in mw:
        if any(overlaps(m, w) and m["grain_class"] == w["grain_class"] for w in ow):
            out["model_wl_confirmed_grain"] += 1
        elif any(near(m, h) for h in fails):
            out["model_wl_unconfirmed_at_failure"] += 1
        else:
            out["model_wl_unsupported"] += 1
    return out


def _coverage(layers: list[dict], hs: float, tol_cm: float) -> float:
    n = int(hs)
    if n <= 0:
        return 0.0
    hit = sum(any(ly["bottom_cm"] - tol_cm <= h <= ly["top_cm"] + tol_cm for ly in layers) for h in range(n + 1))
    return hit / (n + 1)


def chance_corrected(hits: float, chance_hits: float, n: float) -> float | None:
    """(observed - expected) / (n - expected): 0 = no better than random placement, 1 = every failure hit."""
    return None if n <= chance_hits else (hits - chance_hits) / (n - chance_hits)


def test_support_from_runs(results_json, observed_jsonl, tol_cm: float = 5.0) -> dict:
    """Pool ``weak_layer_test_support`` over every pit of a `snowagent baseline` result set (per plot)."""
    import json
    from pathlib import Path

    from snowagent.baseline.evaluate import observed_at_plot
    from snowagent.baseline.run import model_profile_as_observed, profile_at
    from snowagent.engine import snowpack as sp

    res = json.loads(Path(results_json).read_text())
    pooled: dict = {}
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
        acc = pooled.setdefault(plot, {"pits": 0})
        for o in pits:
            t, layers, _d = profile_at(profiles, pd.Timestamp(o["obs_time_utc"]))
            if abs((t - pd.Timestamp(o["obs_time_utc"])).total_seconds()) > 12 * 3600:
                continue
            s = weak_layer_test_support(o, model_profile_as_observed(layers), tol_cm)
            acc["pits"] += 1
            for k, v in s.items():
                acc[k] = acc.get(k, 0) + v
    return pooled
