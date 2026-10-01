"""Transcription-agreement metrics."""

from __future__ import annotations

from snowagent.obs.agreement import compare_profiles, summarise


def _p(layers, hs, temps=()):
    return {"hs_cm": hs, "layers": [{"top_cm": t, "bottom_cm": b, "grain_class": g, "hardness_index": h}
                                    for t, b, g, h in layers],
            "temperatures": [{"height_cm": z, "t_c": v} for z, v in temps]}


REF = _p([(100, 80, "PP", 1.0), (80, 79, "SH", 1.0), (79, 40, "RG", 4.0), (40, 0, "FC", 3.0)], 100,
         [(100, -10.0), (50, -5.0)])


def test_identical_profiles_agree_fully():
    r = compare_profiles(REF, REF)
    assert r["grain_class_agreement"] == 1 and r["hardness_mae_index"] == 0 and r["boundary_f1"] == 1
    assert r["weak_layers_ref"] == 2 and r["weak_layers_found"] == 2 and r["temperature_mae_c"] == 0


def test_shifted_boundary_and_missed_weak_layer():
    other = _p([(101, 78, "PP", 1.0), (78, 41, "RG", 4.0), (41, 0, "FC", 3.0)], 101, [(100, -9.0)])
    r = compare_profiles(REF, other)
    assert r["hs_diff_cm"] == 1
    assert r["weak_layers_found"] == 1  # the thin SH was missed, FC found
    assert r["boundary_recall"] == 2 / 3 and r["boundary_precision"] == 1
    assert 0.9 < r["grain_class_agreement"] < 1
    assert r["temperature_mae_c"] == 1.0
    s = summarise([r, compare_profiles(REF, REF)])
    assert s["pairs"] == 2 and s["weak_layer_recall"]["recall"] == 3 / 4


def test_depth_only_records_are_compared():
    def d(layers):
        return {"hs_cm": None, "height_reference": "depth_from_surface", "temperatures": [],
                "layers": [{"top_cm": t, "bottom_cm": b, "grain_class": "FC", "hardness_index": 3.0} for t, b in layers]}

    r = compare_profiles(d([(0, 10), (10, 40)]), d([(0, 11), (11, 40)]))
    assert r["overlap_cm"] == 40 and r["boundary_f1"] == 1.0  # 1 cm shift is within tolerance
    r = compare_profiles(d([(0, 10), (10, 40)]), d([(0, 22), (22, 40)]))
    assert r["boundary_f1"] == 0.0


def test_dtw_similarity_identical_profiles_is_one():
    import shutil
    import subprocess

    import pytest

    if not shutil.which("Rscript") or subprocess.run(
            ["Rscript", "-e", '.libPaths("/root/R/library"); library(sarp.snowprofile.alignment)'],
            capture_output=True).returncode != 0:
        pytest.skip("R / sarp.snowprofile.alignment not installed")
    from snowagent.obs.dtw import similarity

    a = {"hs_cm": 100, "layers": [{"top_cm": 100, "bottom_cm": 70, "grain_form": "DF", "hardness_index": 1},
                                  {"top_cm": 70, "bottom_cm": 0, "grain_form": "RG", "hardness_index": 4}]}
    b = {"hs_cm": 100, "layers": [{"top_cm": 100, "bottom_cm": 0, "grain_form": "FC", "hardness_index": 2}]}
    r = similarity([("same", a, a), ("diff", a, b)])
    assert r["same"]["sim"] == 1 and r["diff"]["sim"] < 0.9
