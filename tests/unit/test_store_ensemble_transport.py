"""State store immutability/selection, ensemble reproducibility, transport contract, initialization policy."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from snowagent.contracts import EnsembleConfig, StateCheckpoint, UnitState
from snowagent.engine.snowpack import sha256_file
from snowagent.errors import CheckpointIntegrityError, ImmutableRecord, InitializationRequired
from snowagent.forecast import ensemble
from snowagent.state.replay import initialize_from_history
from snowagent.state.store import StateStore
from snowagent.transport import interface as tr

T0 = datetime(2026, 1, 15, tzinfo=UTC)


def _stage(tmp_path, name="stage"):
    d = tmp_path / name
    (d / "units").mkdir(parents=True)
    (d / "units" / "u1.sno").write_text("SMET 1.1 ASCII\n[HEADER]\n[DATA]\n")
    return d


def _cp(stage, state_id="s1", t=T0, cutoff=None, version=1) -> StateCheckpoint:
    return StateCheckpoint(
        state_id=state_id, domain_id="d", analysis_time=t, analysis_version=version, parent_state_id=None,
        terrain_version="tv", engine_version="E", engine_config_hash="c", parameter_version="p",
        forcing_lineage=["x"], forcing_hash="f", assimilation_cutoff=cutoff or t + timedelta(hours=1),
        initialization="test", units=[UnitState(unit_id="u1", sno_file="units/u1.sno",
                                                 sha256=sha256_file(stage / "units" / "u1.sno"),
                                                 hs_vertical_m=1.0, swe_kg_m2_per_slope_area=100.0)],
        synthetic=True, created_utc=datetime.now(UTC))


def test_checkpoint_write_once_readonly_and_verified(tmp_path):
    store = StateStore(tmp_path / "store")
    st = _stage(tmp_path)
    cp = store.write(_cp(st), st)
    f = store.checkpoint_dir("d", "s1") / "units" / "u1.sno"
    assert not os.access(f, os.W_OK) or os.geteuid() == 0  # root ignores mode bits
    assert not (f.stat().st_mode & 0o222)
    store.verify(cp)
    st2 = _stage(tmp_path, "stage2")
    with pytest.raises(ImmutableRecord):
        store.write(_cp(st2), st2)


def test_tampered_checkpoint_detected(tmp_path):
    store = StateStore(tmp_path / "store")
    st = _stage(tmp_path)
    store.write(_cp(st), st)
    f = store.checkpoint_dir("d", "s1") / "units" / "u1.sno"
    os.chmod(f, 0o644)
    f.write_text("tampered")
    with pytest.raises(CheckpointIntegrityError):
        store.latest_valid("d", T0 + timedelta(days=1), "tv")


def test_latest_valid_respects_issue_time_and_assimilation_cutoff(tmp_path):
    store = StateStore(tmp_path / "store")
    for sid, t, cutoff in [("a", T0, T0 + timedelta(hours=1)),
                           ("b", T0 + timedelta(days=1), T0 + timedelta(days=1, hours=1)),
                           # new analysis version for T0 using an observation that arrived 3 days later
                           ("c", T0, T0 + timedelta(days=3))]:
        st = _stage(tmp_path, "st_" + sid)
        store.write(_cp(st, sid, t, cutoff, version=store.next_analysis_version("d", t)), st)
    assert store.latest_valid("d", T0 + timedelta(hours=2), "tv").state_id == "a"
    assert store.latest_valid("d", T0 + timedelta(days=2), "tv").state_id == "b"
    assert store.latest_valid("d", T0 - timedelta(hours=1), "tv") is None
    assert store.latest_valid("d", T0 + timedelta(hours=2), "other_terrain") is None
    # the re-analysed version never rewrote 'a'
    assert store.load("d", "a").analysis_version == 1 and store.load("d", "c").analysis_version == 2


def test_ensemble_is_reproducible_and_control_unperturbed():
    cfg = EnsembleConfig(members=4, seed=7, ta_sigma_k=1.5, psum_log_sigma=0.3, iswr_rel_sigma=0.1,
                         ilwr_sigma_wm2=10, ar1_hourly=0.9)
    a = ensemble.member_perturbations(cfg, 48)
    b = ensemble.member_perturbations(cfg, 48)
    for x, y in zip(a, b, strict=True):
        for k in x:
            assert np.array_equal(np.asarray(x[k]), np.asarray(y[k]))
    assert a[0]["psum_factor"] == 1.0 and not np.any(a[0]["ta_offset_k"])
    c = ensemble.member_perturbations(cfg.model_copy(update={"seed": 8}), 48)
    assert c[1]["psum_factor"] != a[1]["psum_factor"]


def test_transport_contract_checks():
    ok = tr.StepBudget(10, 0, 1, 0.5, 2, 0, 6.5)
    assert tr.check_conservation(ok) == pytest.approx(0)
    with pytest.raises(ValueError, match="does not close"):
        tr.check_conservation(tr.StepBudget(10, 0, 0, 0, 0, 0, 15))  # transported snow counted as snowfall
    with pytest.raises(ValueError, match="available"):
        tr.check_transfers([tr.UnitTransfer("a", "b", 5.0, deposited_density_kg_m3=250)], {"a": 1.0})
    with pytest.raises(ValueError, match="density"):
        tr.check_transfers([tr.UnitTransfer("a", "b", 0.5)], {"a": 1.0})


class _Hist:
    """Minimal stand-in for WeatherSeries used only to hit the policy checks (no engine call)."""

    def __init__(self, start):
        self.data = pd.DataFrame({"ta": [270.0]}, index=pd.DatetimeIndex([pd.Timestamp(start)]))


@pytest.mark.parametrize("cond,start,match", [
    (None, "2025-10-01T00:00Z", "no defensible initial state"),
    ("snow_free", "2026-01-01T00:00Z", "outside the configured snow-free months"),
])
def test_initialization_required(cond, start, match):
    with pytest.raises(InitializationRequired, match=match):
        initialize_from_history(None, _Hist(start), None, T0, None, None, None, cond)
