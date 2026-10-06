"""Benchmark services shared by the CLI and the Streamlit pages (build guide: "The UI and CLI must call shared service
functions"): build cases, list them, re-run leakage checks, and the evaluator's guarded truth access."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from snowagent.lab.benchmark.builder import build_cases
from snowagent.lab.benchmark.leakage import LeakageReport, check_case
from snowagent.lab.benchmark.loader import (
    case_dirs,
    case_sets,
    find_case,
    load_hidden_truth,
    read_checks,
    read_manifest,
)
from snowagent.lab.schemas.benchmark import HiddenTruth, Split
from snowagent.lab.settings import LabConfig
from snowagent.lab.storage.paths import LabPaths

__all__ = ["build_cases", "build_report", "case_index", "case_sets", "check_cases", "hidden_truth"]

INDEX_COLUMNS = ["case_id", "case_key", "case_set", "split", "site_code", "case_type", "season", "as_of_time",
                 "valid_time", "horizon_hours", "forecast_source", "weather_source", "target_scope", "target_profile_id",
                 "visible_pits", "visible_weather_hours", "forecast_hours", "excluded_records", "leakage_check",
                 "warnings", "path"]


def case_index(paths: LabPaths, case_set: str | None = None) -> pd.DataFrame:
    """One row per built case, from its manifest and check result. The target pit of a sealed-test case is not
    listed (``sealed``)."""
    rows = []
    for d in case_dirs(paths, case_set):
        m = read_manifest(d)
        rows.append({
            "case_id": m.case_id, "case_key": m.case_key, "case_set": m.case_set or d.parent.parent.name,
            "split": m.split.value, "site_code": m.site_code.value, "case_type": m.case_type.value,
            "season": m.season, "as_of_time": pd.Timestamp(m.as_of_time), "valid_time": pd.Timestamp(m.valid_time),
            "horizon_hours": m.horizon_hours, "forecast_source": m.forecast_source.value if m.forecast_source else None,
            "weather_source": m.weather_source.value if m.weather_source else None,
            "target_scope": m.target_scope.value,
            "target_profile_id": "sealed" if m.split == Split.sealed_test else m.target_profile_id,
            "visible_pits": m.visible_counts.get("permitted_pits", 0),
            "visible_weather_hours": m.visible_counts.get("weather_observed", 0),
            "forecast_hours": m.visible_counts.get("weather_forecasts", 0),
            "excluded_records": sum(m.excluded_counts.values()), "leakage_check": read_checks(d)["status"],
            "warnings": len(m.warnings), "path": str(d)})
    return pd.DataFrame(rows, columns=INDEX_COLUMNS)


def build_report(paths: LabPaths, case_set: str) -> dict | None:
    f = paths.benchmark / case_set / "build_report.json"
    return json.loads(f.read_text()) if f.is_file() else None


def check_cases(paths: LabPaths, config: LabConfig, case_id: str | None = None, case_set: str | None = None
                ) -> list[LeakageReport]:
    """Re-run the leakage checks on one case (every set it was built in) or on every built case; read only."""
    dirs = find_case(paths, case_id, case_set) if case_id else case_dirs(paths, case_set)
    a = config.benchmark.availability
    return [check_case(d, a.era5_latency_h, list(config.benchmark.standin.withheld)) for d in dirs]


def hidden_truth(paths: LabPaths, case_id: str, case_set: str | None = None, unseal: str | None = None
                 ) -> HiddenTruth:
    """The evaluator's access to one case's withheld pit (sealed-test cases need the typed unseal phrase)."""
    dirs = find_case(paths, case_id, case_set)
    if not dirs:
        raise FileNotFoundError(f"no built case {case_id}" + (f" in {case_set}" if case_set else ""))
    if len(dirs) > 1:
        raise ValueError(f"{case_id} is built in several case sets {[d.parent.parent.name for d in dirs]}; "
                         "name one with --case-set")
    return load_hidden_truth(dirs[0], unseal)


def case_dir_of(paths: LabPaths, case_id: str, case_set: str | None = None) -> Path | None:
    dirs = find_case(paths, case_id, case_set)
    return dirs[0] if len(dirs) == 1 else None
