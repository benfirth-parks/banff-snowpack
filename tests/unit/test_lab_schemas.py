"""Lab data contracts: validation rules of the build guide's schemas and prediction contract (ADR-056)."""

from __future__ import annotations

import typing
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ValidationError

from snowagent.lab.schemas import (
    AgentGenome,
    CaseManifest,
    ForecastRun,
    HiddenTruth,
    PredictedLayer,
    Quantiles,
    ReferenceScenario,
    RunManifest,
    ScoringWeights,
    SnowLayer,
    SnowpackPrediction,
    SnowProfile,
    VisibleBenchmarkCase,
    VisibleForecastRun,
    VisibleObservation,
    VisiblePit,
    VisibleWeatherHour,
    WeatherRecord,
    gene_bounds,
    normalize_ensemble_weights,
)
from snowagent.lab.schemas.profile import structure_warnings

T0 = datetime(2024, 1, 10, 19, tzinfo=UTC)
SCEN = ReferenceScenario(name="Study plot", slope_deg=0.0, terrain_class="study_plot")
H = "a" * 64


def _layer(i: int, top: float, bottom: float, pid: str = "p1", **kw) -> SnowLayer:
    return SnowLayer(layer_id=f"{pid}_L{i:02d}", profile_id=pid, top_depth_m=top, bottom_depth_m=bottom, **kw)


def _profile(layers, pid: str = "p1", t: datetime = T0, **kw) -> SnowProfile:
    return SnowProfile(profile_id=pid, site_code="BOW", plot_id="bow_summit", observed_at=t, source_id="synthetic",
                       layers=layers, **kw)


def _q(a, b, c) -> Quantiles:
    return Quantiles(p10=a, p50=b, p90=c)


# ------------------------------------------------------------------------------------------- profiles


def test_negative_thickness_is_caught_with_a_clear_message():
    with pytest.raises(ValidationError, match="zero or negative thickness"):
        _layer(0, 0.40, 0.30)
    with pytest.raises(ValidationError, match="zero or negative thickness"):
        _layer(0, 0.30, 0.30)
    with pytest.raises(ValidationError):
        _layer(0, -0.05, 0.10)  # depth above the surface


def test_layer_order_surface_to_ground_is_enforced():
    with pytest.raises(ValidationError, match="out of order"):
        _profile([_layer(0, 0.5, 0.8), _layer(1, 0.0, 0.5)])
    ok = _profile([_layer(0, 0.0, 0.5), _layer(1, 0.5, 0.8)])
    assert [ly.top_depth_m for ly in ok.layers] == [0.0, 0.5]


def test_incomplete_history_gives_warnings_not_rejection():
    layers = [_layer(0, 0.05, 0.20), _layer(1, 0.30, 0.50), _layer(2, 0.48, 0.90)]
    w = structure_warnings(layers, snow_depth_m=0.80)
    assert any(x.startswith("gap_at_surface") for x in w)
    assert any(x.startswith("gap_10.0cm") for x in w) and any(x.startswith("overlap_2.0cm") for x in w)
    assert any(x.startswith("layers_reach_") for x in w) and "grain_form_unknown_in_3_layers" in w
    p = _profile(layers, validation_warnings=w)  # no snow depth, unknown grains, gaps: still a valid profile
    assert p.snow_depth_m is None and p.validation_warnings == w


def test_layer_vocabularies_and_concern_basis():
    with pytest.raises(ValidationError, match="IACS"):
        _layer(0, 0.0, 0.1, grain_primary="XX")
    with pytest.raises(ValidationError, match="hand-hardness"):
        _layer(0, 0.0, 0.1, hardness="hard")
    with pytest.raises(ValidationError, match="must say why"):
        _layer(0, 0.0, 0.1, is_layer_of_concern=True)
    assert _layer(0, 0.0, 0.1, hardness="P-K", grain_primary="UNKNOWN").hardness == "P-K"


def test_naive_times_rejected():
    with pytest.raises(ValidationError, match="naive"):
        _profile([], t=datetime(2024, 1, 10, 19))


# ------------------------------------------------------------------------------------------- weather


def _wx(**kw) -> WeatherRecord:
    base = dict(site_code="BOW", observed_at=T0, source_id="plot_stations", provenance_id="import-x")
    return WeatherRecord(**(base | kw))


def test_weather_record_flags_must_match_values():
    r = _wx(air_temperature_k=265.0, qc={"air_temperature_k": "ok"}, sources={"air_temperature_k": "bow_summit"},
            quality_flag="ok")
    assert r.available_at == T0 and r.availability_assumption == "observed_at"
    with pytest.raises(ValidationError, match="no QC flag"):
        _wx(air_temperature_k=265.0, quality_flag="missing")
    with pytest.raises(ValidationError, match="null but flagged ok"):
        _wx(qc={"snow_depth_m": "ok"}, quality_flag="ok")
    with pytest.raises(ValidationError, match="worst variable flag"):
        _wx(air_temperature_k=265.0, snow_depth_m=1.2, qc={"air_temperature_k": "ok", "snow_depth_m": "suspect"},
            quality_flag="ok")
    bad = _wx(qc={"air_temperature_k": "bad"}, quality_flag="bad")  # failed QC: null, flagged, never filled
    assert bad.air_temperature_k is None


# ------------------------------------------------------------------------------------------- predictions


def test_quantiles_must_be_ordered_and_non_negative():
    assert _q(0.8, 1.0, 1.2).p50 == 1.0
    with pytest.raises(ValidationError, match="ordered"):
        _q(1.0, 0.8, 1.2)
    with pytest.raises(ValidationError, match="ordered"):
        _q(0.8, 1.3, 1.2)
    with pytest.raises(ValidationError):
        _q(-0.1, 0.2, 0.3)


def _pl(name="SH Jan 5", top=(0.2, 0.25, 0.3), bottom=(0.21, 0.26, 0.31), **kw) -> PredictedLayer:
    return PredictedLayer(name=name, top_depth_m=_q(*top), bottom_depth_m=_q(*bottom),
                          **({"probability_present": 0.7, "confidence": 0.5} | kw))


def test_predicted_layer_rules():
    assert _pl(grain_form=["SH"], critical_class="surface_hoar").probability_present == 0.7
    with pytest.raises(ValidationError, match="not below top depth p50"):
        _pl(top=(0.2, 0.3, 0.4), bottom=(0.25, 0.3, 0.45))
    with pytest.raises(ValidationError):
        _pl(probability_present=1.2)
    with pytest.raises(ValidationError):
        _pl(confidence=-0.1)
    with pytest.raises(ValidationError, match="IACS"):
        _pl(grain_form=["weak"])


def _pred(**kw) -> SnowpackPrediction:
    base = dict(case_id="BOW_2024_01_10_H72", agent_id="persistence", issued_at=T0, valid_at=T0 + timedelta(hours=72),
                site_code="BOW", scenario=SCEN, confidence={"overall": 0.4, "main_limits": ["profile age"]})
    return SnowpackPrediction(**(base | kw))


def test_prediction_contract_ok_and_insufficient_data():
    p = _pred(bulk_state={"snow_depth_m": _q(1.0, 1.1, 1.3)}, layers=[_pl(), _pl("FC", (0.6, 0.7, 0.8), (0.9, 1.0, 1.1))])
    assert not p.insufficient_data and "not an avalanche forecast" in p.label
    none = _pred(status="insufficient_data", insufficient_data_reason="no permitted profile before as-of")
    assert none.insufficient_data
    with pytest.raises(ValidationError, match="needs bulk_state"):
        _pred()
    with pytest.raises(ValidationError, match="give its reason"):
        _pred(status="insufficient_data")
    with pytest.raises(ValidationError, match="carries no layers"):
        _pred(status="insufficient_data", insufficient_data_reason="x", layers=[_pl()])
    with pytest.raises(ValidationError, match="ordered surface to ground"):
        _pred(bulk_state={"snow_depth_m": _q(1, 1, 1)}, layers=[_pl("FC", (0.6, 0.7, 0.8), (0.9, 1.0, 1.1)), _pl()])
    with pytest.raises(ValidationError, match="precedes"):
        _pred(valid_at=T0 - timedelta(hours=1), bulk_state={"snow_depth_m": _q(1, 1, 1)})


# ------------------------------------------------------------------------------------------- genome


def test_default_genome_is_valid_and_within_bounds():
    g = AgentGenome()
    bounds = gene_bounds()
    assert "weather.precipitation_multiplier.SIMP" in bounds and bounds["modules.use_physics_adapter"] is bool
    flat = g.model_dump()
    for path, b in bounds.items():
        v = flat
        for part in path.split("."):
            v = v[part]
        assert isinstance(v, bool) if b is bool else b[0] <= v <= b[1], path


def test_genome_bounds_are_enforced():
    with pytest.raises(ValidationError):
        AgentGenome(observations={"profile_age_half_life_days": 0.5})
    with pytest.raises(ValidationError, match="outside"):
        AgentGenome(weather={"precipitation_multiplier": {"BOW": 3.0, "GOAT": 1.0, "SIMP": 1.0}})
    with pytest.raises(ValidationError, match="each site"):
        AgentGenome(weather={"temperature_bias_k": {"BOW": 0.0}})
    with pytest.raises(ValidationError):
        AgentGenome(uncertainty={"interval_multiplier": 10.0})


def test_ensemble_weights_must_be_normalized_and_match_modules():
    with pytest.raises(ValidationError, match="sum to"):
        AgentGenome(ensemble={"persistence_weight": 0.5, "weather_rule_weight": 0.5, "analogue_weight": 0.5})
    with pytest.raises(ValidationError, match="disabled"):
        AgentGenome(ensemble={"persistence_weight": 0.4, "weather_rule_weight": 0.2, "analogue_weight": 0.2,
                              "physics_weight": 0.2})
    with pytest.raises(ValidationError, match="is 0"):
        AgentGenome(ensemble={"persistence_weight": 0.5, "weather_rule_weight": 0.5, "analogue_weight": 0.0})
    w = normalize_ensemble_weights({"persistence_weight": 2, "weather_rule_weight": 1, "analogue_weight": 1,
                                    "physics_weight": 5},
                                   {"use_persistence": True, "use_weather_rules": True, "use_analogue_search": True,
                                    "use_physics_adapter": False})
    assert w["physics_weight"] == 0 and sum(w.values()) == pytest.approx(1) and w["persistence_weight"] == 0.5
    assert AgentGenome(ensemble=w).ensemble.persistence_weight == 0.5


# ------------------------------------------------------------------------------------------- benchmark

KEY = "0123456789abcdef"


def _hour(t: float, kind: str = "observed", **kw) -> VisibleWeatherHour:
    base = dict(t_rel_h=t, day_of_year=10.5, kind=kind, source_id="plot_stations", available_rel_h=min(t + 1, 0),
                availability_assumption="assumed_delay", air_temperature_k=265.0, qc={"air_temperature_k": "ok"},
                sources={"air_temperature_k": "bow_summit"}, quality_flag="ok")
    if kind in ("forecast", "perfect_forecast"):
        base |= {"issued_rel_h": 0.0 if kind == "perfect_forecast" else -6.0, "available_rel_h": 0.0 if
                 kind == "perfect_forecast" else -1.0}
    return VisibleWeatherHour(**(base | kw))


def _pit(key: str = "pit_01", t: float = -100.0, **kw) -> VisiblePit:
    return VisiblePit(**(dict(pit_key=key, t_rel_h=t, day_of_year=6.0, season_offset=0, available_rel_h=t + 24,
                              availability_assumption="assumed_delay") | kw))


def _case(**kw) -> VisibleBenchmarkCase:
    base = dict(case_key=KEY, case_type="forecast_h72", site_code="BOW", as_of_day_of_year=10.8, horizon_hours=72,
                scenario=SCEN, forecast_source="archived_gfs")
    return VisibleBenchmarkCase(**(base | kw))


def test_visible_case_refuses_future_data():
    assert _case(weather_observed=[_hour(-2)], permitted_pits=[_pit()],
                 weather_forecasts=[_hour(5, "forecast"), _hour(72, "forecast")])
    with pytest.raises(ValidationError, match="future data leakage"):
        _case(weather_observed=[_hour(-2), _hour(1)])  # an hour after as-of
    with pytest.raises(ValidationError, match="future data leakage"):
        _case(weather_observed=[_hour(-0.5, available_rel_h=0.5)])  # observed before, available after as-of
    with pytest.raises(ValidationError, match="future data leakage"):
        _case(permitted_pits=[_pit(t=-10.0)])  # observed before as-of, available 14 h after it
    with pytest.raises(ValidationError, match="future data leakage"):
        _case(weather_forecasts=[_hour(24, "forecast", issued_rel_h=2.0)])  # issued after as-of
    with pytest.raises(ValidationError, match="future data leakage"):
        _case(weather_forecasts=[_hour(73, "forecast")])  # beyond the valid time
    with pytest.raises(ValidationError, match="future data leakage"):
        _case(forecast_runs=[VisibleForecastRun(source_id="gfs025:p", issued_rel_h=1, available_rel_h=6,
                                                max_lead_h=72)])
    with pytest.raises(ValidationError, match="future data leakage"):
        _case(permitted_pits=[_pit()], permitted_observations=[
            VisibleObservation(pit_key="pit_01", observation_type="stability_test", t_rel_h=2, available_rel_h=26)])


def test_measured_standin_must_be_declared():
    """Measured weather after as-of appears only as a labelled stand-in issued at as-of (owner, 2026-10-05)."""
    standin = [_hour(t, "perfect_forecast") for t in (1, 2, 72)]
    assert _case(forecast_source="measured_standin", weather_forecasts=standin)
    with pytest.raises(ValidationError, match="future data leakage"):
        _case(weather_forecasts=standin)  # archived_gfs case holding measured hours
    with pytest.raises(ValidationError, match="future data leakage"):
        _case(forecast_source="measured_standin", weather_forecasts=[_hour(5, "forecast")])
    with pytest.raises(ValidationError, match="future data leakage"):
        _case(weather_observed=[_hour(-1, "perfect_forecast")])  # stand-in hours as observed weather
    with pytest.raises(ValidationError, match="need issued_rel_h"):
        _hour(3, "perfect_forecast", issued_rel_h=None)


def test_visible_case_is_anonymous_and_cannot_carry_hidden_truth():
    assert HiddenTruth not in _types(VisibleBenchmarkCase)
    assert SnowProfile not in _types(VisibleBenchmarkCase) and WeatherRecord not in _types(VisibleBenchmarkCase)
    names = {n for t in [VisibleBenchmarkCase, *_types(VisibleBenchmarkCase)] for n in t.model_fields}
    assert not {"target_profile_id", "truth_profile", "hidden", "verification", "case_id", "profile_id", "layer_id",
                "observer_id", "observed_at", "issued_at", "source_recorded_at", "date_tag", "comment", "notes",
                "raw", "provenance_id", "observation_id", "source_file"} & names
    assert not {"latitude", "longitude", "elevation_m"} & set(VisiblePit.model_fields)  # pit GPS is a fingerprint
    with pytest.raises(ValidationError, match="Extra inputs"):
        _case(truth_profile=_profile([]))
    with pytest.raises(ValidationError, match="opaque"):
        _case(case_key="BOW_20240110T1900Z_H72")
    with pytest.raises(ValidationError, match="anonymous"):
        _pit(key="2024-01-10_bow_summit_syn001")


def _types(model: type[BaseModel], seen: set | None = None) -> set:
    seen = seen if seen is not None else set()
    for f in model.model_fields.values():
        for t in [f.annotation, *typing.get_args(f.annotation)]:
            for a in [t, *typing.get_args(t)]:
                if isinstance(a, type) and issubclass(a, BaseModel) and a not in seen:
                    seen.add(a)
                    _types(a, seen)
    return seen


def _manifest(**kw) -> CaseManifest:
    base = dict(case_id="c1", case_type="forecast_h72", site_code="BOW", season="2023-2024", split="training",
                as_of_time=T0, valid_time=T0 + timedelta(hours=72), horizon_hours=72, scenario=SCEN,
                target_profile_id="p9", availability_assumption="assumed_delay", created_at=T0, builder_version="0",
                visible_hashes={"site.json": H}, hidden_hashes={"truth_profile.json": H})
    return CaseManifest(**(base | kw))


def test_case_manifest_needs_hashes_and_consistent_times():
    assert _manifest()
    with pytest.raises(ValidationError, match="hashes missing"):
        _manifest(visible_hashes={})
    with pytest.raises(ValidationError, match="not a sha256"):
        _manifest(visible_hashes={"s": "abc"})
    with pytest.raises(ValidationError, match="does not match"):
        _manifest(horizon_hours=48)
    with pytest.raises(ValidationError, match="sha256"):
        _manifest(config_hash="abc")


def test_case_manifest_refuses_visible_target_and_late_runs():
    run = ForecastRun(source_id="gfs025:p", point="p", issued_at=T0 - timedelta(hours=6),
                      available_at=T0 - timedelta(hours=1), max_lead_h=72, file="gfs.csv", sha256=H)
    assert _manifest(pit_keys={"pit_01": "p1"}, target_copies=["p9b"], forecast_source="archived_gfs",
                     forecast_runs=[run], case_key=KEY, config_hash=H, data_hash=H)
    with pytest.raises(ValidationError, match="target profile"):
        _manifest(pit_keys={"pit_01": "p9"})
    with pytest.raises(ValidationError, match="target profile"):
        _manifest(visible_profile_ids=["p9b"], target_copies=["p9b"])
    with pytest.raises(ValidationError, match="anchor"):
        _manifest(case_type="next_pit")
    with pytest.raises(ValidationError, match="after as_of"):
        _manifest(forecast_runs=[run.model_copy(update={"issued_at": T0 + timedelta(hours=1),
                                                        "available_at": T0 + timedelta(hours=6)})])
    with pytest.raises(ValidationError, match="stand-in"):
        _manifest(forecast_source="measured_standin", forecast_runs=[run])
    with pytest.raises(ValidationError, match="names its forecast run"):
        _manifest(forecast_source="archived_gfs")
    with pytest.raises(ValidationError, match="holdout"):
        _manifest(split_mode="loso", split="holdout")
    with pytest.raises(ValidationError, match="before it is issued"):
        ForecastRun(**(run.model_dump() | {"available_at": T0 - timedelta(hours=7)}))


# ------------------------------------------------------------------------------------------- runs


def test_run_manifest_provenance_rules():
    w = ScoringWeights(snow_depth=0.2, layer_structure=0.3, critical_layers=0.25, uncertainty=0.15, robustness=0.1)
    base = dict(run_id="r1", status="ok", created_at=T0, config_hash=H, data_hash=H, software_version="0.1.0")
    assert RunManifest(**base, kind="data_import").seed is None
    assert RunManifest(**base, kind="competition", seed=42, scoring_weights=w).scoring_weights == w
    with pytest.raises(ValidationError, match="seed and the frozen scoring weights"):
        RunManifest(**base, kind="evolution", seed=1)
    with pytest.raises(ValidationError, match="sha256"):
        RunManifest(**(base | {"config_hash": "x"}), kind="data_import")
    with pytest.raises(ValidationError, match="sum to"):
        ScoringWeights(snow_depth=0.3, layer_structure=0.3, critical_layers=0.25, uncertainty=0.15, robustness=0.1)
