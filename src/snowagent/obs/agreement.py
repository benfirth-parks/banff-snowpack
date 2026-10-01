"""Agreement between two observed profiles of the same pit (transcription QA).

Used to estimate transcription error before transcribed layers are used for calibration or evaluation:
- blind re-reads (a second reader transcribes the same image without seeing the first transcription);
- image vs exact structured file (SnowPro/CAAML) of the same pit, paired by site and local date (NOT by
  layer similarity, which would select only the pairs that already agree).

Profiles are observed-profile dicts (height above ground, cm). Metrics are computed on 1 cm slices over the
height range both profiles cover.
"""

from __future__ import annotations

import math
from statistics import mean, median

PERSISTENT = {"SH", "DH", "FC"}


def _slices(o: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for ly in o.get("layers", []):
        top, bot = ly.get("top_cm"), ly.get("bottom_cm")
        if top is None or bot is None:
            continue
        for h in range(math.ceil(bot), math.ceil(top)):
            out[h] = ly
    return out


def _boundaries(o: dict) -> list[float]:
    tops = sorted({ly["top_cm"] for ly in o.get("layers", []) if ly.get("top_cm") is not None})
    return tops[:-1]  # interior boundaries (the surface is HS, compared separately)


def _match_count(a: list[float], b: list[float], tol: float) -> int:
    used: set[int] = set()
    n = 0
    for x in a:
        best = min(((abs(x - y), j) for j, y in enumerate(b) if j not in used and abs(x - y) <= tol), default=None)
        if best is not None:
            used.add(best[1])
            n += 1
    return n


def _weak_layers(o: dict, lo: float, hi: float) -> list[dict]:
    return [ly for ly in o.get("layers", []) if ly.get("grain_class") in PERSISTENT and ly.get("top_cm") is not None
            and ly.get("bottom_cm") is not None and lo <= ly["top_cm"] <= hi]


def _as_heights(o: dict) -> dict:
    """Depth-only records (no HS) are compared on a negated-depth axis so slices and boundaries still work."""
    if o.get("height_reference") != "depth_from_surface":
        return o

    def neg(v):
        return None if v is None else -v

    return {**o, "layers": [{**ly, "top_cm": neg(ly.get("top_cm")), "bottom_cm": neg(ly.get("bottom_cm"))}
                            for ly in o.get("layers", [])],
            "temperatures": [{**t, "height_cm": -t["height_cm"]} for t in o.get("temperatures", [])
                             if t.get("height_cm") is not None]}


def compare_profiles(ref: dict, other: dict, boundary_tol_cm: float = 2.0, weak_tol_cm: float = 5.0) -> dict:
    """Compare ``other`` against ``ref`` (the exact file, or the first reading)."""
    if (ref.get("height_reference") == "depth_from_surface") != (other.get("height_reference") == "depth_from_surface"):
        return {"hs_diff_cm": None, "n_layers": [len(ref.get("layers", [])), len(other.get("layers", []))],
                "overlap_cm": 0, "grain_class_agreement": None, "hardness_mae_index": None,
                "boundary_precision": None, "boundary_recall": None, "boundary_f1": None, "weak_layers_ref": 0,
                "weak_layers_found": 0, "temperature_mae_c": None, "temperature_pairs": 0,
                "not_comparable": "height_reference_differs"}
    ref, other = _as_heights(ref), _as_heights(other)
    sa, sb = _slices(ref), _slices(other)
    common = sorted(set(sa) & set(sb))
    g = [(sa[h].get("grain_class"), sb[h].get("grain_class")) for h in common]
    g = [(x, y) for x, y in g if x and y]
    hsig = [sb[h]["hardness_index"] - sa[h]["hardness_index"] for h in common
            if sa[h].get("hardness_index") is not None and sb[h].get("hardness_index") is not None]
    hd = [abs(x) for x in hsig]
    lo, hi = (min(common), max(common) + 1) if common else (0, 0)
    ba = [x for x in _boundaries(ref) if lo <= x <= hi]
    bb = [x for x in _boundaries(other) if lo <= x <= hi]
    m = _match_count(ba, bb, boundary_tol_cm)
    precision = m / len(bb) if bb else None
    recall = m / len(ba) if ba else None
    f1 = (2 * precision * recall / (precision + recall) if precision and recall else 0.0) \
        if precision is not None and recall is not None else None
    wa, wb = _weak_layers(ref, lo, hi), _weak_layers(other, lo, hi)
    def near(w, w2):
        return (w2["grain_class"] == w["grain_class"] and w2["bottom_cm"] - weak_tol_cm <= w["top_cm"]
                and w["bottom_cm"] <= w2["top_cm"] + weak_tol_cm)

    found = sum(any(near(w, w2) for w2 in wb) for w in wa)
    confirmed = sum(any(near(w2, w) for w in wa) for w2 in wb)  # other's weak layers that ref also has
    ta = {round(t["height_cm"]): t["t_c"] for t in ref.get("temperatures", [])}
    tb = {round(t["height_cm"]): t["t_c"] for t in other.get("temperatures", [])}
    td = [abs(ta[h] - tb[h]) for h in ta.keys() & tb.keys()]
    hs_a, hs_b = ref.get("hs_cm"), other.get("hs_cm")
    return {
        "hs_diff_cm": None if hs_a is None or hs_b is None else hs_b - hs_a,
        "n_layers": [len(ref.get("layers", [])), len(other.get("layers", []))],
        "overlap_cm": len(common),
        "grain_class_agreement": sum(x == y for x, y in g) / len(g) if g else None,
        "hardness_mae_index": mean(hd) if hd else None,
        "hardness_bias_index": mean(hsig) if hsig else None,  # other minus ref (+ = other harder)
        "boundary_precision": precision, "boundary_recall": recall, "boundary_f1": f1,
        "weak_layers_ref": len(wa), "weak_layers_found": found,
        "weak_layers_other": len(wb), "weak_layers_other_confirmed": confirmed,
        "temperature_mae_c": mean(td) if td else None, "temperature_pairs": len(td),
    }


def summarise(rows: list[dict]) -> dict:
    """Median/mean of per-pair metrics plus pooled weak-layer recall."""
    out: dict = {"pairs": len(rows)}
    for k in ("hs_diff_cm", "grain_class_agreement", "hardness_mae_index", "boundary_f1", "temperature_mae_c"):
        v = [r[k] for r in rows if r.get(k) is not None]
        if k == "hs_diff_cm":
            v = [abs(x) for x in v]
            k = "abs_hs_diff_cm"
        out[k] = {"n": len(v), "median": median(v) if v else None, "mean": mean(v) if v else None}
    wa = sum(r["weak_layers_ref"] for r in rows)
    out["weak_layer_recall"] = {"ref_layers": wa, "found": sum(r["weak_layers_found"] for r in rows),
                                "recall": sum(r["weak_layers_found"] for r in rows) / wa if wa else None}
    wo = sum(r.get("weak_layers_other", 0) for r in rows)
    conf = sum(r.get("weak_layers_other_confirmed", 0) for r in rows)
    out["weak_layer_precision"] = {"other_layers": wo, "confirmed": conf, "precision": conf / wo if wo else None}
    v = [r["n_layers"][1] for r in rows if r.get("n_layers")]
    out["layers_per_profile"] = {"ref_mean": mean([r["n_layers"][0] for r in rows]) if rows else None,
                                 "other_mean": mean(v) if v else None}
    return out


def structured_vs_transcribed_pairs(obs: list[dict], tz: str = "Etc/GMT+7") -> list[tuple[dict, dict]]:
    """(exact, transcribed) pairs of the same site and local date; pairing ignores layer content."""
    import pandas as pd

    def key(o: dict):
        if not o.get("site_key") or not o.get("obs_time_utc") or not o.get("layers"):
            return None
        if o.get("height_reference") != "height_above_ground":
            return None
        return o["site_key"], pd.Timestamp(o["obs_time_utc"]).tz_convert(tz).date().isoformat()

    exact: dict[tuple, list[dict]] = {}
    for o in obs:
        if o["provenance"].get("confidence") == "exact" and key(o):
            exact.setdefault(key(o), []).append(o)
    pairs = []
    for o in obs:
        k = key(o)
        if k and o["provenance"].get("method", "").startswith("transcription") and len(exact.get(k, [])) == 1:
            pairs.append((exact[k][0], o))
    return pairs


def reread_pairs(primary: list[dict], rereads: list[dict]) -> list[tuple[dict, dict]]:
    """(first reading, blind re-read) pairs matched by source file hash."""
    by_sha = {o["source_sha256"]: o for o in primary}
    return [(by_sha[r["source_sha256"]], r) for r in rereads if r["source_sha256"] in by_sha]
