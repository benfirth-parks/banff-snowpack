"""Baseline helpers: model-layer aggregation and weak-layer precision."""

from __future__ import annotations

from types import SimpleNamespace

from snowagent.baseline.run import model_profile_as_observed
from snowagent.obs.agreement import compare_profiles


def _ly(top, bot, g, h):
    return SimpleNamespace(top_vertical_m=top, bottom_vertical_m=bot, grain_form_primary=g, hand_hardness_index=h,
                           density_kg_m3=200.0)


def test_aggregation_merges_like_elements_only():
    layers = [_ly(0.30, 0.29, "DF", 1.0), _ly(0.29, 0.28, "DF", 1.2), _ly(0.28, 0.27, "FCxr", 2.0),
              _ly(0.27, 0.0, "RG", 4.0)]
    p = model_profile_as_observed(layers)
    assert [(ly["top_cm"], ly["bottom_cm"], ly["grain_class"]) for ly in p["layers"]] == [
        (30.0, 28.0, "DF"), (28.0, 27.0, "FC"), (27.0, 0.0, "RG")]
    assert len(model_profile_as_observed(layers, aggregate=False)["layers"]) == 4


def test_weak_layer_precision_counts_false_alarms():
    ref = {"layers": [{"top_cm": 50, "bottom_cm": 40, "grain_class": "FC", "hardness_index": 2},
                      {"top_cm": 40, "bottom_cm": 0, "grain_class": "RG", "hardness_index": 4}], "temperatures": []}
    other = {"layers": [{"top_cm": 50, "bottom_cm": 40, "grain_class": "FC", "hardness_index": 2},
                        {"top_cm": 40, "bottom_cm": 20, "grain_class": "RG", "hardness_index": 4},
                        {"top_cm": 20, "bottom_cm": 10, "grain_class": "SH", "hardness_index": 1},
                        {"top_cm": 10, "bottom_cm": 0, "grain_class": "RG", "hardness_index": 4}], "temperatures": []}
    r = compare_profiles(ref, other)
    assert r["weak_layers_found"] == 1 and r["weak_layers_other"] == 2 and r["weak_layers_other_confirmed"] == 1


def test_gauge_plausibility_flags_only_gross_overcatch():
    import numpy as np
    import pandas as pd

    from snowagent.baseline.assemble import gauge_plausibility

    idx = pd.date_range("2024-01-01", periods=24 * 60, freq="h", tz="UTC")
    rng = np.random.default_rng(0)
    era5 = pd.Series(rng.gamma(0.5, 0.4, len(idx)), index=idx)
    gauge = era5 * 1.6
    gauge.loc["2024-02-10"] = gauge.loc["2024-02-10"] + 4.0  # +96 mm in a day: a gauge fault
    ratio, bad = gauge_plausibility(gauge, era5)
    assert abs(ratio - 1.6) < 0.05
    assert [str(d.date()) for d in bad] == ["2024-02-10"]


def test_hs_spike_marked_suspect(tmp_path):
    import pandas as pd

    from snowagent.ingest.fts360 import parse_station

    times = pd.date_range("2024-01-01", periods=48, freq="h", tz="UTC")
    lines = ["Station name,Station ID,Date,HS"]
    for k, t in enumerate(times):
        v = 300.0 if k == 20 else 100.0 + k * 0.1
        lines.append(f"X,1,{t:%Y-%m-%dT%H:%M:%SZ},{v}")
    f = tmp_path / "s.csv"
    f.write_text("\n".join(lines) + "\n")
    d = parse_station([f])
    assert (d["hs_m_qc"] == "suspect").sum() == 1
    assert d.loc[d["hs_m_qc"] == "suspect", "hs_m"].iloc[0] == 3.0
