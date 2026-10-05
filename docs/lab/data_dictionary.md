# Snowpack Agent Lab: data dictionary

The lab's tables under `data/lab/` (gitignored, derived; rebuilt by `snowagent lab import`). Contracts:
`src/snowagent/lab/schemas/` (ADR-056). Times are UTC; units SI with the unit in the name (lengths m, air
temperature K, precipitation and SWE mm = kg m-2), except snow temperature (deg C), grain size (mm) and density
(kg m-3). The UI converts to cm, deg C and local time.

```
data/lab/
  registry.sqlite                         run registry (table run_manifest)
  manifests/<run_id>.json                 the same manifests as JSON
  processed/profiles/profiles.parquet     one row per profile
  processed/profiles/layers.parquet       one row per layer
  processed/observations/observations.parquet   pit stability tests
  processed/weather/weather_hourly.parquet      one row per site, hour and source set
  benchmark/<case set>/build_report.json          counts, exclusions, leakage results (milestone 2)
  benchmark/<case set>/<split>/<case_id>/         one case package: manifest.json, checks.json,
                                                  visible/ (agents), hidden/ (evaluator); docs/lab/benchmark_protocol.md
  outputs/{predictions,reports,exports}/            (milestones 3-4)
```

## Sites (`config/lab.yaml`)

| code | plot_id | name | coordinates, elevation |
|---|---|---|---|
| BOW | bow_summit | Bow Summit | read from `config/plot_forcing.yaml` |
| GOAT | goats_eye | Sunshine / Goat's Eye | read from `config/plot_forcing.yaml` |
| SIMP | simpson | Simpson | read from `config/plot_forcing.yaml` |

Season key: `YYYY-YYYY`, the season starting 15 Sep 00 UTC (`season_start` in `config/plot_forcing.yaml`).

## profiles.parquet (`SnowProfile`)

Source: `data/interim/obs/observed_profiles.jsonl` (`snowagent obs profiles`), records whose `site_key` is one of the
three plots. Records at other plots, test profiles and unassigned files are counted in the import report, not
converted; a record without an observation time is counted as `no_observation_time`.

| column | meaning |
|---|---|
| profile_id | the observed set's id (date, plot, file hash) |
| site_code, plot_id, season | lab site, the observed set's `site_key`, season key |
| observed_at | observation time (UTC); date-only records keep the flag `time_unknown_date_only` |
| source_recorded_at, availability_assumption | when the pit became available: unknown (null), so `observed_at` |
| latitude, longitude, elevation_m | as recorded in the file (null when absent) |
| aspect_deg | parsed from the recorded aspect (`NE` 45, `135° SE` 135); null for "inapplicable"/"N/A" |
| slope_deg, terrain_class | as recorded; terrain class is the observed set's category (`study_plot`) |
| observer_id | null (observer names are not carried by the observed set) |
| source_id | how it was digitised: `structured:<format>` (exact files) or `transcription:<method>` |
| profile_quality | `exact` (structured file) or the transcription's confidence (high/medium/low) |
| notes | the record's free-text comments |
| snow_depth_m | HS as recorded (null when not recorded) |
| profile_depth_m | pit depth when recorded |
| usable | false when the observed set marks it unusable or no layer could be placed |
| duplicate_of | the primary record when this is another export of the same pit (kept, flagged) |
| unique_usable | usable and not a duplicate: the pits that count |
| n_layers, n_layers_of_concern | placed layers, of which layers of concern |
| review_reasons_json | the owner's review list reasons (location_qc, printed date/site flags; ADR-050): such pits are excluded from scoring |
| flags_json | the observed record's QC flags, unchanged |
| validation_warnings_json | lab warnings: unknown HS, surface taken at the top layer, gaps, overlaps, layers below HS, unknown grain forms, layers left out |
| temperatures_json | `[{depth_m, temperature_c}]`, depth from the surface |
| raw_json | the observed record unchanged, except its layers (kept per layer) |

## layers.parquet (`SnowLayer`)

Depth from the surface: `top_depth_m` 0 at the surface, increasing downward; ordered surface to ground.
Conversion from the observed set's height above ground (cm): depth = (surface - height) / 100, surface = HS, or the
top of the highest layer when HS is unknown or below that top (warning). Depth charts without HS are already depths
(value / 100). A layer without both boundaries, with zero thickness, or above the surface is left out of this table
(warning on the profile) and stays in the profile's raw record.

| column | meaning |
|---|---|
| layer_id, profile_id | `<profile_id>_L<nn>`, nn counting from the surface |
| top_depth_m, bottom_depth_m | depth from the surface, bottom > top |
| grain_primary, grain_secondary | IACS 2009 codes; `UNKNOWN` when none or not an IACS code (raw kept) |
| grain_size_mm, grain_size_max_mm | single size, or midpoint and upper end of a range |
| hardness, hardness_index | OGRS code as recorded (layer top); F=1 .. I=6 with +-1/3 steps |
| wetness | D M W V S |
| density_kg_m3 | when measured |
| temperature_c | null: pits record temperatures by depth (profile `temperatures_json`) |
| critical_class | surface_hoar, facets, depth_hoar, crust, other, unknown (`lab.ingest.mapping`, ADR-057) |
| is_layer_of_concern, concern_basis_json | true by critical class or an observer's layer tag; the basis says which |
| confidence | the profile's quality (exact/high/medium/low) |
| uncertain_fields_json | fields the transcriber marked uncertain |
| date_tag, comment | observer's layer name/date tag and comment, as written |
| raw_json | the observed layer unchanged (`top_cm`, `bottom_cm` height above ground, ...) plus `raw_index` |

Critical class table (ADR-057): SH* surface hoar; FC, FCso, FCsf, FCxr facets; DH* depth hoar; MFcr, IFrc, IFsc, IF,
IFil crust; other codes "other"; no form "unknown". The primary form decides, the secondary only when no primary was
recorded.

## observations.parquet (`Observation`)

Pit stability tests from the observed records: `observation_id` `<profile_id>_T<nn>`, `observation_type`
`stability_test`, `profile_id`, the profile's time and location, `payload_json` = the test as recorded (raw text,
type, result, score, fracture character, height_cm) plus `depth_m` from the surface, `source_id`, `provenance_id`
(the import run).

## weather_hourly.parquet (`WeatherRecord`)

One row per site, hour (end of interval, UTC) and `source_id`. Milestone 1 has one source set, `plot_stations`:
the QC'd station records (`ingest.fts360.load_station`: FTS360 files, logger exports, dashboard history). Rows run
hourly from a site's first to last station hour, so gaps are explicit rows.

| column | meaning |
|---|---|
| site_code, observed_at, source_id, kind | `kind` observed (reanalysis and forecast later) |
| issued_at | forecasts only (null) |
| source_recorded_at, availability_assumption | unknown (null), so `observed_at` |
| air_temperature_k | stations `ta` of the plot recipe, in order |
| relative_humidity_frac | stations `rh` |
| precipitation_mm | stations `psum`: hourly gauge increment as measured (uncorrected; small negative increments kept) |
| wind_speed_ms, wind_direction_deg | stations `wind` in `config/lab.yaml` (not at the plot except Bow Summit) |
| snow_depth_m | stations `hs_check` |
| swe_mm | stations `swe_check` (Sunshine snow pillow) |
| shortwave_radiation_wm2, longwave_radiation_wm2, station_pressure_pa | not measured at the plots: ERA5 only (filled), else null |
| `<variable>_source` | the station that supplied the value (or whose value failed QC) |
| `<variable>_qc` | ok, suspect (kept, e.g. a snow-depth spike), bad (null; the raw file keeps the value), missing |
| quality_flag | the worst flag over the variables (ok < filled < suspect < bad; missing when no variable has a value) |
| provenance_id | the import run |

Per hour and variable the first station (recipe order) with an ok value supplies it; with none ok, the first
suspect value; a bad value is never used. No value is moved to the plot elevation: values are as measured at the
named station (station elevations in `config/plot_forcing.yaml`). With `weather.era5_backfill` on (ADR-059), an hour
and variable with no usable station value takes the plot's ERA5 cell value, `<variable>_qc` filled and
`<variable>_source` `era5_cell_<elev>m` (the cell's surface elevation); without the ERA5 cache nothing is filled.

## run_manifest (registry) and manifests/*.json (`RunManifest`)

run_id, kind (data_import, case_build, competition, evolution, sealed_test), status, created_at, finished_at,
config_hash (sha256 of the lab config as loaded, plot coordinates included), data_hash (sha256 over the inputs'
path and sha256), software_version, git_commit, snowpack_version, seed, scoring_weights (frozen; required for scored
runs), splits, case_ids, agent_ids, genome_hashes and case_set_hash (competitions), profile_ids_used, inputs (path, sha256, bytes), outputs, counts, warnings,
runtime_s, label (the decision-support disclaimer). Rows are inserted once and never overwritten.

## Benchmark case packages (`CaseManifest`, `VisibleBenchmarkCase`, `HiddenTruth`)

`manifest.json` (evaluator side): case_id (`<SITE>_<pit time>Z_<H72|NP>`), case_key, case type, site, season, split,
case set, split mode, holdout season, as_of and valid time (UTC), horizon, target profile id and its duplicate
copies, target scope (layers or depth only), anchor pit (`next_pit`), forecast source (`archived_gfs` or
`measured_standin`) with the GFS run (file, sha256, issue and availability time) or the stand-in description,
availability rules, pit keys (`pit_01` -> profile id), visible and excluded counts, file hashes, build run id, config
and data hash, warnings. `checks.json`: the leakage checks as built.

`visible/` (agents; anonymous: hours relative to as_of plus day of year, no ids, dates or free text): `case.json`,
`site.json`, `terrain_scenario.json`, `weather_observed.parquet` and `weather_forecasts.parquet` (`t_rel_h`,
`day_of_year`, `kind`, `source_id`, `issued_rel_h`, `available_rel_h`, the weather variables with `_source` and
`_qc`, `quality_flag`), `forecast_runs.json`, `permitted_pits.parquet` (`pit_key`, `t_rel_h`, `day_of_year`,
`season_offset`, `available_rel_h`, aspect, slope, terrain class, quality, snow and profile depth, temperatures),
`permitted_layers.parquet` (`pit_key`, `layer_index`, depths, grain, hardness, wetness, density, temperature,
critical class, layer of concern and basis), `permitted_observations.parquet` (`pit_key`, type, times, test
payload). `hidden/`: `truth_profile.json`, `truth_layers.parquet`, `truth_observations.parquet`, `verification.json`.

## Genomes, predictions and competitions (milestone 3)

`AgentGenome` (`lab-genome-2`): family, genes (flat scalar map, allow-listed in `config/lab.yaml` `genome`), label,
origin, parents; hash and agent id derived (ADR-061). `SnowpackPrediction`: case id, agent id, as-of and valid time,
site, scenario, status (`ok` / `insufficient_data` with a reason), snow depth p10/p50/p90 (m), layers (name, top and
bottom depth p10/p50/p90 from the surface, grain forms, hardness code, presence probability, critical class, layer of
concern, confidence), overall confidence and main limits, `model_metadata` (family, genome hash and label, agent
diagnostics such as engine source, SNOWPACK version, engine config and forcing hashes, profile lag).

`outputs/competitions/<run_id>/` (ADR-065): `run.json` (plan and its hash), `genomes/<agent_id>.json`,
`library.json` (analogue entries: season, anonymous digest, withheld depth and layers; harness side),
`cases/<case_id>.json` (stamped predictions, per agent status, reason, runtime, scores; engine provenance),
`scores.parquet` (one row per case and agent: case id, agent id, family, label, genome hash, site, season, split,
case type, forecast source, target scope, horizon, status, runtime_s, the four case components, composite, and
diagnostics: depth error, coverage, interval score, match F1, grain and hardness agreement, Brier, observed and
predicted layers of concern), `leaderboard.json` (overall and by forecast source, plot and case type: composite,
components, robustness, failures, skipped, depth MAE and bias, runtime per case).
