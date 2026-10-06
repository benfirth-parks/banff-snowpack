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
    EngineLayer,
    VisiblePackageEngine,
    _pit_dict,
    reference_time,
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
    def _update(state, o, model_hs, init, rec) -> None:
        Path(init).write_text(sha({"state": Path(state).read_text(), "pit": pit_content(o)}))
        rec["method"] = "layers"

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


def run(case, store=None, physics=None, tmp=None):
    eng = ChainEngine(work_dir=tmp, segments=store)
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
