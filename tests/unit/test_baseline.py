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


def _fake_era5(d, n_hours=48):
    import numpy as np
    import pandas as pd

    lat, lon = np.array([51.0, 51.25]), np.array([-116.0, -115.75])
    np.savez(d / "era5_box_z.npz", lat=lat, lon=lon, z=np.full((2, 2), 2000.0 * 9.80665))
    t = pd.date_range("2000-01-01", periods=n_hours, freq="h", tz="UTC")
    shp = (n_hours, 2, 2)
    np.savez(d / "era5_box_200001.npz", time_utc=t.tz_convert(None).to_numpy(), **{
        "2t": np.full(shp, 263.15), "2d": np.full(shp, 258.15), "10u": np.full(shp, 1.0), "10v": np.zeros(shp),
        "msdwswrf": np.full(shp, 100.0), "msdwlwrf": np.full(shp, 220.0), "mtpr": np.full(shp, 1.0 / 3600)})


def test_era5_only_applies_transfer_constants(tmp_path):
    import yaml

    from snowagent.baseline.assemble import assemble

    _fake_era5(tmp_path)
    cfg = {"plots": {"x": {"lat": 51.0, "lon": -116.0, "elevation_m": 2000, "ta": ["st"], "rh": ["st"],
                           "psum": ["st"]}}, "station_elevation_m": {"st": 2000}}
    (tmp_path / "pf.yaml").write_text(yaml.safe_dump(cfg))
    pf = assemble("x", "2000-01-01", "2000-01-02", cfg_path=tmp_path / "pf.yaml", fts_raw=tmp_path / "none",
                  era5_dir=tmp_path, era5_only={"ta_offset_k": 2.0, "psum_ratio": 1.5})
    assert pf.data.notna().all().all()
    assert abs(pf.data["ta"].iloc[0] - 265.15) < 1e-6        # same elevation: ERA5 + offset only
    assert abs(pf.data["psum"].iloc[0] - 1.5) < 1e-6         # 1 mm/h x catch ratio
    assert (pf.sources["psum"] == "era5_x_gauge_ratio").all() and (pf.sources["ta"] == "era5").all()
    assert pf.data["ilwr"].iloc[0] > 220.0                   # longwave rescaled to the warmer plot air


def test_ghcnd_snow_depth_reader(tmp_path):
    import gzip

    from snowagent.baseline.evaluate import ghcnd_snwd

    rows = ["ID,DATE,ELEMENT,DATA_VALUE,M_FLAG,Q_FLAG,S_FLAG,OBS_TIME", "X,20000105,SNWD,1200,,,C,",
            "X,20000106,SNWD,9990,,I,C,", "X,20000106,TMAX,-50,,,C,"]
    f = tmp_path / "X.csv.gz"
    f.write_bytes(gzip.compress("\n".join(rows).encode()))
    s = ghcnd_snwd(f)
    assert list(s.round(3)) == [1.2] and str(s.index[0]) == "2000-01-05 15:00:00+00:00"


def test_phase_transfer_fit_and_apply():
    import numpy as np
    import pandas as pd

    from snowagent.baseline.era5_transfer import fit, offsets_for

    idx = pd.date_range("2022-01-01", periods=24 * 60, freq="h", tz="UTC")  # Jan + Feb
    wet = (np.arange(len(idx)) % 4 == 0)
    e5_psum = np.where(wet, 1.0, 0.0)
    e5_ta = np.where(idx.month == 1, 260.0, 280.0)                  # Jan cold, Feb warm
    st_ta = e5_ta + np.where(wet, 1.0, 3.0)                          # station warmer by 1 K wet, 3 K dry
    gauge = e5_psum * np.where(idx.month == 1, 1.5, 1.0)            # catch ratio 1.5 cold, 1.0 warm
    f = pd.DataFrame({"month": idx.month, "e5_ta": e5_ta, "e5_psum": e5_psum, "st_ta": st_ta, "gauge": gauge},
                     index=idx)
    p = fit(f, "phase")
    assert p["ta_offset_k_by_month"][1] == {"wet": 1.0, "dry": 3.0}
    assert p["psum_ratio_cold"] == 1.5 and p["psum_ratio_warm"] == 1.0
    o = offsets_for(idx[:2], pd.Series([1.0, 0.0], index=idx[:2]), p)
    assert list(o) == [1.0, 3.0]
    c = fit(f, "constant")
    assert abs(c["ta_offset_k"] - 2.5) < 1e-9


def test_failure_layers_and_pit_pairs():
    from snowagent.baseline.obs_noise import failure_layers, pit_pairs

    o = {"tests": [{"result": "CT21", "height_cm": 80}, {"result": "CTN", "height_cm": None},
                   {"result": "ECTX", "height_cm": 50}, {"result": "ECTN", "height_cm": 40}]}
    assert failure_layers(o) == [80.0, 40.0]
    ly = [{"top_cm": 100, "bottom_cm": 0, "grain_class": "RG", "hardness_index": 3}]
    obs = [{"profile_id": k, "obs_time_utc": t, "layers": ly, "hs_cm": 100, "temperatures": []}
           for k, t in (("a", "2024-01-01T19:00Z"), ("b", "2024-01-05T19:00Z"), ("c", "2024-02-20T19:00Z"))]
    rows = pit_pairs(obs, 14)
    assert [(r["a"], r["b"], r["gap_days"]) for r in rows] == [("a", "b", 4.0)]


def test_chance_corrected_weak_layer_score():
    from snowagent.baseline.obs_noise import chance_corrected, weak_layer_test_support

    obs = {"hs_cm": 100, "tests": [{"result": "CT12", "height_cm": 50}],
           "layers": [{"top_cm": 52, "bottom_cm": 48, "grain_class": "SH"}]}
    model = {"hs_cm": 100, "layers": [{"top_cm": 100, "bottom_cm": 0, "grain_class": "FC"}]}
    s = weak_layer_test_support(obs, model)
    assert s["failures_at_model_wl"] == 1 and abs(s["chance_hits_model"] - 1.0) < 1e-9   # covers everything
    assert chance_corrected(1, s["chance_hits_model"], 1) is None                       # no skill measurable
    assert chance_corrected(s["failures_at_observed_wl"], s["chance_hits_observed"], 1) > 0.8
