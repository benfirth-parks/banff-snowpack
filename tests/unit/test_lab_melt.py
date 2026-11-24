"""Process switches + melt and training on one part of the winter (ADR-136): the melt, refreeze and water settings
evolve; at their defaults the new family predicts exactly as process switches, so Carver Daniels can seed a run of
it; a run with a period keeps only the spring or warm-spell cases, for training, locked winters and resumes alike."""

from __future__ import annotations

import json

import pandas as pd
import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.agents import ProcessAgent, make_agent  # noqa: E402
from snowagent.lab.benchmark import builder  # noqa: E402
from snowagent.lab.benchmark import package as pkg  # noqa: E402
from snowagent.lab.benchmark.loader import case_dirs, load_visible_case, read_manifest  # noqa: E402
from snowagent.lab.genome import WIDER_FAMILY, as_family, default_genome, make_genome, mutate, widen_seeds  # noqa: E402
from snowagent.lab.schemas.genome import STANDARD_FAMILIES, AgentFamily  # noqa: E402
from snowagent.lab.services import labels  # noqa: E402
from snowagent.lab.services.training import PRESETS, training_command  # noqa: E402
from snowagent.lab.settings import load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from snowagent.lab.training import periods  # noqa: E402
from snowagent.lab.training.loop import TrainOptions, _extensions, _opts_from_plan  # noqa: E402
from tests.unit.lab_fixtures import write_synthetic_lab  # noqa: E402
from tests.unit.test_lab_benchmark import CONFIG  # noqa: E402

MELT_GENES = ("col_albedo_fresh", "col_albedo_old", "col_albedo_decay_days", "col_turb_base", "col_turb_wind",
              "col_lw_offset_wm2", "col_ground_temp_c", "col_conductivity_mult", "col_water_hold_frac",
              "col_crust_water_frac")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("melt")
    cfg = load_lab_config(CONFIG)
    paths = LabPaths(tmp / "lab")
    write_synthetic_lab(paths.root, tmp / "checkout", cfg)
    builder.build_cases(paths, cfg, tmp / "checkout", exclude_flagged=True)
    return [(d, read_manifest(d)) for d in case_dirs(paths)]


def _carver_like():
    """A process genome away from its defaults (switches and strengths), as an evolved winner is."""
    g = default_genome(AgentFamily.process)
    return make_genome(AgentFamily.process, g.genes | {"proc_sw_surface": "off", "proc_crust_boost_below": 3.0,
                                                       "snow_mult_goat": 1.11, "presence_confidence": 0.55},
                       label="r14-x04-process")


def _layers(p):
    return [(ly.grain_form, round(ly.top_depth_m.p50, 6), round(ly.bottom_depth_m.p50, 6)) for ly in p.layers]


def test_process_melt_is_process_plus_the_melt_genes():
    melt, proc = default_genome(AgentFamily.process_melt), default_genome(AgentFamily.process)
    assert set(melt.genes) - set(proc.genes) == set(MELT_GENES)  # see below for ADR-152
    weather = {"proc_sw_phase", "proc_sw_catch", "proc_phase_shift_k"}  # ADR-152: process family only (gene cap)
    assert set(proc.genes) - weather < set(melt.genes) and len(melt.genes) <= 64
    assert AgentFamily.process_melt not in STANDARD_FAMILIES
    assert isinstance(make_agent(melt), ProcessAgent)
    assert WIDER_FAMILY[AgentFamily.process] == AgentFamily.process_melt
    assert labels.evolution_of(["process_melt"]) == "process_melt"
    assert any(p.get("initial") == ["process_melt"] for p in PRESETS.values())


def test_a_widened_genome_predicts_exactly_as_before(built):
    carver = _carver_like()
    wide = as_family(carver, AgentFamily.process_melt)
    assert wide.family == AgentFamily.process_melt and wide.parents == [carver.genome_hash]
    assert wide.label == carver.label and all(wide.genes[k] == v for k, v in carver.genes.items() if k in wide.genes)
    assert set(carver.genes) - set(wide.genes) == {"proc_sw_phase", "proc_sw_catch", "proc_phase_shift_k"}  # ADR-152, off
    a, b = make_agent(carver), make_agent(wide)
    ok = 0
    for d, _m in built:
        case = load_visible_case(d)
        pa, pb = a.predict(case, seed=1), b.predict(case, seed=1)
        assert pa.status == pb.status
        if pa.status == "ok":
            ok += 1
            assert _layers(pa) == _layers(pb)
            assert pa.bulk_state.snow_depth_m.p50 == pytest.approx(pb.bulk_state.snow_depth_m.p50)
    assert ok


def test_the_melt_genes_change_the_snowpack(built):
    base = default_genome(AgentFamily.process_melt)
    warm = make_genome(AgentFamily.process_melt, base.genes | {"col_albedo_old": 0.45, "col_albedo_fresh": 0.75,
                                                               "col_lw_offset_wm2": 40.0, "col_turb_base": 8.0})
    changed = 0
    for d, _m in built:
        case = load_visible_case(d)
        pa, pb = make_agent(base).predict(case, seed=1), make_agent(warm).predict(case, seed=1)
        if pa.status == pb.status == "ok" and (
                _layers(pa) != _layers(pb)
                or abs(pa.bulk_state.snow_depth_m.p50 - pb.bulk_state.snow_depth_m.p50) > 1e-6):
            changed += 1
    assert changed


def test_mutation_can_move_the_melt_genes():
    base = default_genome(AgentFamily.process_melt)
    moved = set()
    for s in range(40):
        child = mutate(base, 0.5, s)
        moved |= {k for k in MELT_GENES if child.genes[k] != base.genes[k]}
    assert moved == set(MELT_GENES)


def test_seeds_widen_only_for_a_process_melt_run():
    carver = _carver_like()
    melt_run = [default_genome(AgentFamily.process_melt)]
    out = widen_seeds([carver], melt_run)
    assert out[0].family == AgentFamily.process_melt
    assert widen_seeds([carver], [default_genome(AgentFamily.process)])[0] is carver  # a process run keeps it
    assert widen_seeds([carver], None)[0] is carver
    col = default_genome(AgentFamily.column)
    with pytest.raises(ValueError, match="cannot become"):
        as_family(col, AgentFamily.process_melt)


# ------------------------------------------------------------------------------------------------ periods


def _case_with_weather(tmp_path, valid: str, temps_c: list[float], horizon: float = 72.0):
    """A minimal case folder: a manifest stand-in and a visible observed-weather table ending at the pit."""
    d = tmp_path / valid.replace(":", "")
    (d / pkg.VISIBLE).mkdir(parents=True)
    n = len(temps_c)
    pd.DataFrame({"t_rel_h": [horizon - n + 1 + i for i in range(n)], "kind": ["observed"] * n,
                  "air_temperature_k": [t + 273.15 for t in temps_c]}).to_parquet(d / pkg.VISIBLE /
                                                                                   pkg.WEATHER_OBSERVED)

    class M:  # only the fields the period rules read
        valid_time = pd.Timestamp(valid).to_pydatetime()
        horizon_hours = horizon
    return d, M()


def test_spring_and_warm_spell_rules(tmp_path):
    cold = [-8.0] * 72
    thaw = [-5.0] * 60 + [1.0] * 8 + [-2.0] * 4
    old_thaw = [1.0] * 10 + [-6.0] * 72  # warm, but more than 3 days before the pit
    jan_cold = _case_with_weather(tmp_path, "2021-01-15T18:00Z", cold)
    jan_thaw = _case_with_weather(tmp_path, "2021-01-20T18:00Z", thaw)
    jan_old = _case_with_weather(tmp_path, "2021-01-25T18:00Z", old_thaw)
    apr_cold = _case_with_weather(tmp_path, "2021-04-02T18:00Z", cold)
    assert periods.warm_hours(*jan_thaw) == 8 and periods.warm_hours(*jan_old) == 0
    assert periods.is_spring(apr_cold[1]) and not periods.is_spring(jan_thaw[1])
    cases = [jan_cold, jan_thaw, jan_old, apr_cold]
    pick = {p: [c[1].valid_time.day for c in periods.filter_cases(cases, p)] for p in periods.PERIODS}
    assert pick == {"spring": [2], "warm": [20], "spring_or_warm": [20, 2]}
    assert periods.filter_cases(cases, None) == cases
    with pytest.raises(ValueError, match="period"):
        periods.in_period(*jan_cold, "summer")


def test_measured_hours_win_over_forecast_hours(tmp_path):
    d, m = _case_with_weather(tmp_path, "2021-02-10T18:00Z", [-6.0] * 72)
    pd.DataFrame({"t_rel_h": [72.0 - i for i in range(10)], "kind": ["forecast"] * 10,
                  "air_temperature_k": [276.0] * 10}).to_parquet(d / pkg.VISIBLE / pkg.WEATHER_FORECASTS)
    assert periods.warm_hours(d, m) == 0  # the measured cold hours stand; the warm forecast is not counted twice


def test_period_on_the_synthetic_cases_keeps_a_subset(built):
    spring = periods.filter_cases(built, "spring")
    assert all(m.valid_time.month in periods.SPRING_MONTHS for _d, m in spring)
    assert len(periods.filter_cases(built, "spring_or_warm")) >= len(spring)


def test_period_is_validated_planned_and_kept_on_resume():
    cfg = load_lab_config()
    assert TrainOptions.from_config(cfg).period is None
    with pytest.raises(ValueError, match="period"):
        TrainOptions.from_config(cfg, period="summer").validate()
    opts = TrainOptions.from_config(cfg, period="spring_or_warm")
    assert _extensions(opts, [])["period"] == "spring_or_warm"
    assert "period" not in _extensions(TrainOptions.from_config(cfg), [])
    plan = {"rounds": 1, "population": 4, "survivors": 2, "mutation_strength": 0.2, "crossover_share": 0.25,
            "seed": 0, "plots": None, "case_types": None, "case_set": "all", "splits": None, "initial": [],
            "monitor_season": None, "gap_flag_rounds": 3, "gap_tolerance": 0.0, "max_redraws": 100,
            "engine": {"kind": "fake"}}
    assert _opts_from_plan(plan).period is None
    assert _opts_from_plan(plan | {"period": "warm"}).period == "warm"


def test_the_app_passes_period_and_focus_to_the_command(tmp_path):
    cmd = training_command(LabPaths(tmp_path), CONFIG, "r", rounds=1, population=4, survivors=2,
                           mutation_strength=0.2, crossover_share=0.25, seed=0, workers=1,
                           initial=["process_melt"], focus=1.0, period="spring_or_warm")
    assert cmd[cmd.index("--period") + 1] == "spring_or_warm" and cmd[cmd.index("--focus") + 1] == "1"
    plain = training_command(LabPaths(tmp_path), CONFIG, "r", rounds=1, population=4, survivors=2,
                             mutation_strength=0.2, crossover_share=0.25, seed=0, workers=1)
    assert "--period" not in plain and "--focus" not in plain
    assert json.dumps(cmd)
