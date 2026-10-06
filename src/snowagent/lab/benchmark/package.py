"""Case package layout (build guide "Case package layout"; ADR-059). Visible files are anonymous: no profile, case,
observer or pit identifier, no free text, times relative to as-of plus day of year, never a calendar date."""

from __future__ import annotations

from pathlib import Path

from snowagent.lab.storage.provenance import sha256_file

MANIFEST = "manifest.json"
CHECKS = "checks.json"
VISIBLE = "visible"
HIDDEN = "hidden"

# visible/: everything an agent may see. case.json is the visible header (opaque key, type, horizon, labels), so the
# agent-facing loader never opens the manifest, which names the target pit.
CASE = "case.json"
SITE = "site.json"
TERRAIN = "terrain_scenario.json"
WEATHER_OBSERVED = "weather_observed.parquet"
WEATHER_FORECASTS = "weather_forecasts.parquet"  # archived GFS run, or the labelled measured stand-in
FORECAST_RUNS = "forecast_runs.json"
PITS = "permitted_pits.parquet"
LAYERS = "permitted_layers.parquet"
OBSERVATIONS = "permitted_observations.parquet"
VISIBLE_FILES = (CASE, SITE, TERRAIN, WEATHER_OBSERVED, WEATHER_FORECASTS, FORECAST_RUNS, PITS, LAYERS, OBSERVATIONS)

# Never in a visible file: identifiers, absolute times, free text, pit coordinates (owner, 2026-10-05: agents must not
# memorize snowpacks). Checked on every case (leakage.check_case).
FORBIDDEN_FIELDS = frozenset({
    "case_id", "profile_id", "layer_id", "observation_id", "observer_id", "target_profile_id", "anchor_profile_id",
    "duplicate_of", "observed_at", "issued_at", "source_recorded_at", "as_of_time", "valid_time", "created_at",
    "date_tag", "layer_date_tag", "comment", "notes", "raw", "raw_json", "source_file", "provenance_id",
    "season", "file", "sha256"})
PIT_COORDINATES = frozenset({"latitude", "longitude", "elevation_m"})  # allowed in site.json only

# hidden/: the withheld pit, read by the evaluator only
TRUTH_PROFILE = "truth_profile.json"
TRUTH_LAYERS = "truth_layers.parquet"
TRUTH_OBSERVATIONS = "truth_observations.parquet"
VERIFICATION = "verification.json"
HIDDEN_FILES = (TRUTH_PROFILE, TRUTH_LAYERS, TRUTH_OBSERVATIONS, VERIFICATION)

UNSEAL_PHRASE = "UNSEAL {case_id}"  # typed confirmation needed to read a sealed-test case's hidden truth


def hash_dir(directory: Path, names: tuple[str, ...]) -> dict[str, str]:
    """sha256 of each named file in ``directory`` (all must exist)."""
    return {n: sha256_file(Path(directory) / n) for n in names}


def unseal_phrase(case_id: str) -> str:
    return UNSEAL_PHRASE.format(case_id=case_id)
