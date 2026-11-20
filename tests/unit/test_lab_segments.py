"""Shared SNOWPACK restart states (milestone 5, ADR-071): reuse gives exactly the profile of running every segment,
and never crosses a leakage boundary. A restart may only contain information visible to every case that uses it,
and pit restarts happen only from pits visible to that case.

No binary is needed: ``ChainEngine`` replaces the three engine steps with a hash chain, so a segment's "state" is a
digest of everything it was computed from (the SMET text it was given, the io.ini, the state it started from, the
pit it was updated with). Equal results then mean equal inputs, and the provenance of every restart a case loads can
be checked against what that case can see."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.engine import snowpack as sp  # noqa: E402
from snowagent.lab.agents.common import season_pits  # noqa: E402
from snowagent.lab.agents.physics import engine_physics  # noqa: E402
from snowagent.lab.agents.segments import SegmentStore, pit_content, pit_hash, sha  # noqa: E402
from snowagent.lab.agents.snowpack import (  # noqa: E402
    PRECIP_LEARN_RANGE,
    EngineLayer,
    VisiblePackageEngine,
    _pit_dict,
    reference_time,
    scaled_pit,
)
from snowagent.lab.benchmark import builder  # noqa: E402
from snowagent.lab.benchmark.loader import case_dirs, load_visible_case  # noqa: E402
from snowagent.lab.genome import default_genome  # noqa: E402
from snowagent.lab.schemas.genome import AgentFamily  # noqa: E402
from snowagent.lab.settings import load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from tests.unit.lab_fixtures import write_multiseason_lab  # noqa: E402
from tests.unit.test_lab_benchmark import CONFIG  # noqa: E402


class ChainEngine(VisiblePackageEngine):
    """The engine steps as a hash chain (see the module doc); records the SMET text of every segment it runs."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.smet_shas: list[str] = []

    def engine(self):
        return SimpleNamespace(version_string="chain-0")

    def _run(self, eng, settings, run_dir, unit, f, t, sno, seg_start):
        from snowagent.engine.column import station_id

        sid = station_id(unit)
        lon, lat = unit.centroid_lonlat
        (run_dir / "input").mkdir(parents=True, exist_ok=True)
        (run_dir / "output").mkdir(exist_ok=True)
        sp.write_smet_forcing(run_dir / "input" / f"{sid}.smet", sid, lat, lon, unit.elevation_m, f)
        smet = (run_dir / "input" / f"{sid}.smet").read_text()
        self.smet_shas.append(sp.sha256_text(smet))
        prev = Path(sno).read_text() if sno else f"snowfree {seg_start.isoformat()}"
        out = run_dir / "output" / "state.sno"
        out.write_text(sha({"prev": prev, "smet": smet, "ini": settings.render(sid), "end": t.isoformat()}))
        return SimpleNamespace(sno=out, met=out, pro=out)

    @staticmethod
    def _model_hs(out) -> float:
        return 100.0

    @staticmethod
    def _update(state, o, model_hs, init, rec, how="layers") -> None:
        key = {"state": Path(state).read_text(), "pit": pit_content(o)}
        if how != "layers":
            key["how"] = how
        Path(init).write_text(sha(key))
        rec["method"] = "depth" if how == "depth" else "layers"

    def _profile(self, out, valid):
        h = Path(out.pro).read_text()
        return 1.0, [EngineLayer(top_depth_m=0.0, bottom_depth_m=1.0, grain=h[:12])], 0.0


@pytest.fixture(scope="module")
def cases(tmp_path_factory):
    root = tmp_path_factory.mktemp("segments")
    cfg = load_lab_config(CONFIG)
    paths = LabPaths(root / "lab")
    write_multiseason_lab(paths.root, root / "checkout", cfg)
    builder.build_cases(paths, cfg, root / "checkout", exclude_flagged=True)
    return [with_longwave(load_visible_case(d)) for d in case_dirs(paths, "all")]


def with_longwave(case):
    """The case with measured incoming longwave on every observed hour. The synthetic weather has none, and the
    engine forcing fills missing longwave from the case's mean clearness over all its visible hours (incumbent
    behaviour), which differs between cases and so, rightly, gives every case its own segment keys."""
    ref = pd.Timestamp(reference_time(case.as_of_day_of_year))
    hours = [h.model_copy(update={"longwave_radiation_wm2": 250.0 + 30.0 * ((ref + pd.Timedelta(hours=h.t_rel_h)).hour
                                                                             / 23.0)})
             for h in case.weather_observed]
    return case.model_copy(update={"weather_observed": hours})


def visible_pit_hashes(case) -> set[str]:
    ref = pd.Timestamp(reference_time(case.as_of_day_of_year))
    return {pit_hash(d) for p in season_pits(case) if (d := _pit_dict(p, ref)) is not None}


def run(case, store=None, physics=None, tmp=None, pit_reset="layers", **options):
    eng = ChainEngine(work_dir=tmp, segments=store, pit_reset=pit_reset, **options)
    if store is not None:
        store.loaded = []
    r = eng.simulate(case, physics)
    return r, eng, list(store.loaded) if store is not None else []


def test_reuse_gives_exactly_the_profile_of_running_every_segment(cases, tmp_path):
    store = SegmentStore(tmp_path / "seg", "test")
    multi = [c for c in cases if len(visible_pit_hashes(c)) >= 2]
    assert multi, "the fixture needs cases with two visible pits of their season"
    for c in sorted(cases, key=lambda c: (c.site_code, c.as_of_day_of_year)):
        plain, _, _ = run(c, tmp=tmp_path)
        shared, eng, _ = run(c, store, tmp=tmp_path)
        assert shared == plain, c.case_key
    assert store.hits > 0 and store.misses > 0  # some cases did share their early segments
    # the store is keyed by the physics too: other physics never meets these states
    phys = engine_physics(dict(default_genome(AgentFamily.snowpack).genes) | {"sp_roughness_length_m": 0.004}, "BOW")
    hits = store.hits
    r, _, _ = run(multi[0], store, phys, tmp_path)
    assert store.hits == hits and r != run(multi[0], tmp=tmp_path)[0]


def test_a_restart_only_carries_what_every_case_using_it_can_see(cases, tmp_path):
    store = SegmentStore(tmp_path / "seg", "test")
    used_by: dict[str, set[str]] = {}
    for c in sorted(cases, key=lambda c: (c.site_code, -c.as_of_day_of_year)):  # latest first: most states stored
        _, own, _ = run(c, tmp=tmp_path)  # the case's own segments, store-less
        _, _, loaded = run(c, store, tmp=tmp_path)
        visible = visible_pit_hashes(c)
        for prov in loaded:
            # pit restarts only from pits this case can see; forcing slices identical to its own, segment by segment
            assert set(prov["pits"]) <= visible, (c.case_key, prov["key"])
            assert prov["smet"] == own.smet_shas[: len(prov["smet"])], (c.case_key, prov["key"])
            used_by.setdefault(prov["key"], set()).add(c.case_key)
    assert any(len(v) > 1 for v in used_by.values())  # states were shared, and each check above held for each user


def test_a_planted_difference_breaks_reuse_from_that_segment_on(cases, tmp_path):
    case = max((c for c in cases if len(visible_pit_hashes(c)) >= 2), key=lambda c: c.as_of_day_of_year)
    store = SegmentStore(tmp_path / "seg", "test")
    _, _, base = run(case, store, tmp=tmp_path)
    keys = [p["key"] for p in base]
    assert len(keys) >= 2
    first_end = pd.Timestamp(base[0]["end"])
    second_end = pd.Timestamp(base[1]["end"])
    ref = pd.Timestamp(reference_time(case.as_of_day_of_year))

    def withheld(lo: pd.Timestamp, hi: pd.Timestamp):
        """The case with one measured hour between lo and hi withheld (as an ERA5 value younger than its latency
        is for an earlier case)."""
        hours = list(case.weather_observed)
        i = next(i for i, h in enumerate(hours) if lo + pd.Timedelta(hours=12) < ref + pd.Timedelta(hours=h.t_rel_h)
                 < hi - pd.Timedelta(hours=12) and h.air_temperature_k is not None)
        hours[i] = hours[i].model_copy(update={"air_temperature_k": hours[i].air_temperature_k + 2.0})
        return case.model_copy(update={"weather_observed": hours})

    # a difference before the first update: nothing is shared
    h0 = store.hits
    _, _, a = run(withheld(ref - pd.Timedelta(days=400), first_end), store, tmp=tmp_path)
    assert store.hits == h0 and not {p["key"] for p in a} & set(keys)
    # a difference between the first and the second update: the first segment is shared, nothing after it
    h0 = store.hits
    _, _, b = run(withheld(first_end, second_end), store, tmp=tmp_path)
    assert store.hits == h0 + 1 and b[0]["key"] == keys[0] and not {p["key"] for p in b[1:]} & set(keys)
    # a case that cannot see the pit of the first update never loads a state restarted from it
    hidden = base[1]["pits"][0]
    pit = next(p for p in season_pits(case) if (d := _pit_dict(p, ref)) is not None and pit_hash(d) == hidden)
    blind = case.model_copy(update={"permitted_pits": [p for p in case.permitted_pits if p.pit_key != pit.pit_key]})
    assert hidden not in visible_pit_hashes(blind)
    _, _, c = run(blind, store, tmp=tmp_path)
    assert c and all(hidden not in p["pits"] for p in c)
    assert not {p["key"] for p in c} & {p["key"] for p in base if hidden in p["pits"]}


def test_the_store_rejects_a_torn_entry_and_clears(tmp_path):
    store = SegmentStore(tmp_path / "seg", "test")
    store.put("ab" * 32, b"state", {"model_hs": 1.0})
    assert store.get("ab" * 32)[0] == b"state"
    (tmp_path / "seg" / "ab" / f"{'ab' * 32}.sno").write_bytes(b"other")
    assert store.get("ab" * 32) is None  # content does not match its recorded hash: recomputed, never trusted
    assert store.clear() > 0 and store.get("ab" * 32) is None and not (tmp_path / "seg").exists()


def test_pit_reset_variants_never_share_restarts_with_the_adopted_reset(cases, tmp_path):
    """ADR-124: a run with another pit reset never loads a restart state made with the adopted one (or back)."""
    store = SegmentStore(tmp_path / "seg", "test")
    multi = [c for c in cases if len(visible_pit_hashes(c)) >= 2]
    for c in multi:
        run(c, store, tmp=tmp_path)
    for c in multi:
        plain, _, _ = run(c, tmp=tmp_path, pit_reset="depth")
        shared, _, _ = run(c, store, tmp=tmp_path, pit_reset="depth")
        assert shared == plain, c.case_key
        assert shared != run(c, tmp=tmp_path)[0]


def test_pit_reset_off_runs_the_whole_season_without_pits(cases, tmp_path):
    multi = [c for c in cases if len(visible_pit_hashes(c)) >= 2]
    r, eng, _ = run(multi[0], tmp=tmp_path, pit_reset="off")
    assert r.notes["updates"] == [] and r.steer_updates == 0
    assert eng.segment_stats["run"] == 1
    assert r != run(multi[0], tmp=tmp_path)[0]


def test_an_unknown_pit_reset_is_refused():
    with pytest.raises(ValueError, match="pit_reset"):
        VisiblePackageEngine(pit_reset="blend")


# ADR-124, the two tests of a smarter reset: the depth the reset takes where the snow depth sensor drives the snowfall
# (``sensor_trust``), and this winter's snowfall error learned from its pits (``learn_precip_cm``).


def sensor_driven():
    genes = dict(default_genome(AgentFamily.snowpack).genes) | {"sp_snowfall_source": "snow_depth"}
    return engine_physics(genes, "BOW")


def last_case_with_two_pits(cases):
    return max((c for c in cases if len(visible_pit_hashes(c)) >= 2), key=lambda c: c.as_of_day_of_year)


def test_a_scaled_pit_has_every_height_multiplied_and_nothing_else_changed():
    pit = {"profile_id": "p", "hs_cm": 100.0, "layers": [
        {"top_cm": 100.0, "bottom_cm": 40.0, "grain_form": "RG", "hardness_index": 3.0},
        {"top_cm": 40.0, "bottom_cm": 0.0, "grain_form": "DH", "hardness_index": 2.0}]}
    out = scaled_pit(pit, 0.9)
    assert out["hs_cm"] == 90.0 and [(ly["top_cm"], ly["bottom_cm"]) for ly in out["layers"]] == [(90.0, 36.0),
                                                                                                 (36.0, 0.0)]
    assert [(ly["grain_form"], ly["hardness_index"]) for ly in out["layers"]] == [("RG", 3.0), ("DH", 2.0)]
    assert out["profile_id"] == "p" and pit["hs_cm"] == 100.0 and pit["layers"][0]["top_cm"] == 100.0  # a copy


@pytest.mark.parametrize("trust", [1.0, 0.5, 0.0])
def test_sensor_trust_resets_between_the_sensor_and_the_pit_and_moves_the_sensor_from_there(cases, tmp_path, trust):
    case = last_case_with_two_pits(cases)
    base, eng0, _ = run(case, physics=sensor_driven(), tmp=tmp_path)
    assert base.notes["snowfall"] == "snow depth sensor"
    assert all("hs_sensor_cm" not in u for u in base.notes["updates"])  # the adopted run: the pit's depth, as before
    r, eng, _ = run(case, physics=sensor_driven(), tmp=tmp_path, sensor_trust=trust)
    ups = [u for u in r.notes["updates"] if "hs_sensor_cm" in u]
    assert ups and len(r.notes["updates"]) == len(base.notes["updates"])
    for u in ups:
        assert abs(u["hs_pit_cm"] - u["hs_sensor_cm"]) > 1.0  # the synthetic sensor and pits do differ
        assert u["hs_reset_cm"] == pytest.approx(u["hs_sensor_cm"] + trust * (u["hs_pit_cm"] - u["hs_sensor_cm"]),
                                                 abs=0.11)
    # nothing differs before the first pit; from there the reset state does, and the sensor too unless the reset
    # took the sensor's own depth (trust 0)
    assert eng.smet_shas[0] == eng0.smet_shas[0] and r != base
    assert (eng.smet_shas[1:] == eng0.smet_shas[1:]) == (trust == 0.0)


def test_sensor_trust_changes_nothing_where_precipitation_drives_the_snowfall(cases, tmp_path):
    case = last_case_with_two_pits(cases)
    assert run(case, tmp=tmp_path, sensor_trust=1.0)[0] == run(case, tmp=tmp_path)[0]


def test_the_two_tests_never_share_restarts_with_the_adopted_run(cases, tmp_path):
    case = last_case_with_two_pits(cases)
    store = SegmentStore(tmp_path / "seg", "test")
    run(case, store, sensor_driven(), tmp_path)
    for options in ({"sensor_trust": 1.0}, {"learn_precip_cm": 30.0}):
        hits = store.hits
        plain, _, _ = run(case, physics=sensor_driven(), tmp=tmp_path, **options)
        shared, _, loaded = run(case, store, sensor_driven(), tmp_path, **options)
        assert shared == plain and store.hits == hits and loaded  # its own states only, none of the adopted run's
        again, _, _ = run(case, store, sensor_driven(), tmp_path, **options)
        assert again == plain and store.hits > hits  # and it does reuse its own


def test_bad_values_of_the_two_tests_are_refused():
    with pytest.raises(ValueError, match="sensor_trust"):
        VisiblePackageEngine(sensor_trust=1.5)
    with pytest.raises(ValueError, match="learn_precip_cm"):
        VisiblePackageEngine(learn_precip_cm=0.0)


STATE = """SMET 1.1 ASCII
[HEADER]
station_id       = t
ProfileDate      = 2026-01-10T00:00:00
HS_Last          = 0.300000
nSoilLayerData   = 0
nSnowLayerData   = 3
fields           = timestamp Layer_Thick  T  Vol_Frac_I  Vol_Frac_W  Vol_Frac_V  Vol_Frac_S Rho_S Conduc_S HeatCapac_S  rg  rb  dd  sp  mk mass_hoar ne CDot metamo
[DATA]
2025-11-01T00:00:00 0.100000 268.0 0.30 0.0 0.70 0.0 0.0 0.000 0.0 0.8 0.4 0.0 0.06 1 0.0 1 0.0 0.0
2025-12-01T00:00:00 0.100000 265.0 0.25 0.0 0.75 0.0 0.0 0.000 0.0 0.5 0.2 0.0 0.30 0 0.0 1 0.0 0.0
2026-01-05T00:00:00 0.100000 262.0 0.10 0.0 0.90 0.0 0.0 0.000 0.0 0.15 0.05 0.8 0.5 0 0.0 1 0.0 0.0
"""


def test_the_snowfall_error_is_learned_from_the_snow_since_the_last_reset_and_the_pits_depth(tmp_path):
    """The state holds 10 cm that fell since the last reset; the pit is 10 cm deeper than the model, so 20 cm should
    have fallen. With 30 cm of snow at no error as the starting belief: (20 + 30) / (10 + 30) = 1.25."""
    state = tmp_path / "state.sno"
    state.write_text(STATE)
    since = pd.Timestamp("2025-12-15", tz="UTC")
    learn = {"obs_cm": 0.0, "raw_cm": 0.0, "mult": 1.0}

    def step(hs_pit, hs_model, driven=False, method="layers", start=since):
        rec = {"method": method, "hs_pit_cm": hs_pit, "hs_model_cm": hs_model}
        VisiblePackageEngine._learn_precip(learn, state, start, driven, rec, 30.0)
        return rec["precip_mult"]

    assert step(40.0, 30.0) == 1.25
    # the next stretch ran at 1.25: its 10 cm is 8 cm of unscaled snowfall. A pit that agrees keeps the multiplier
    assert step(30.0, 30.0) == 1.25 and learn["raw_cm"] == pytest.approx(18.0) and learn["obs_cm"] == 30.0
    before = dict(learn)
    # nothing is learned from a stretch the sensor drove, a reset that did not happen, or too little new snow
    assert step(60.0, 30.0, driven=True) == 1.25 and step(60.0, 30.0, method="none (model too shallow)") == 1.25
    assert step(60.0, 30.0, start=pd.Timestamp("2026-01-06", tz="UTC")) == 1.25 and learn == before
    # one wild pit cannot push it out of its range
    assert step(400.0, 30.0) == PRECIP_LEARN_RANGE[1]
    learn.update(obs_cm=0.0, raw_cm=0.0, mult=1.0)
    assert step(5.0, 30.0) == pytest.approx(30.0 / 40.0)  # the pit 25 cm shallower: none of the 10 cm should have fallen


def test_learning_the_snowfall_error_scales_only_the_precipitation_after_each_pit(cases, tmp_path, monkeypatch):
    case = last_case_with_two_pits(cases)
    base, eng0, _ = run(case, tmp=tmp_path)

    def learned(learn, state, seg_start, driven, rec, prior_cm):
        learn["mult"] = 1.5
        rec["precip_mult"] = 1.5

    monkeypatch.setattr(VisiblePackageEngine, "_learn_precip", staticmethod(learned))
    r, eng, _ = run(case, tmp=tmp_path, learn_precip_cm=30.0)
    assert [u["precip_mult"] for u in r.notes["updates"]] == [1.5] * len(base.notes["updates"])
    assert eng.smet_shas[0] == eng0.smet_shas[0]  # the stretch before the first pit runs on the precipitation as given
    assert all(a != b for a, b in zip(eng.smet_shas[1:], eng0.smet_shas[1:], strict=True)) and r != base
