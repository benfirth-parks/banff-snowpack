# Snowpack Agent Lab: benchmark protocol

How the lab turns observed pits into benchmark cases for snowpack-prediction agents, and what an agent may see.
Decision record: ADR-059 in `docs/decisions.md`. Code: `src/snowagent/lab/benchmark/`. This is research and
decision support, never an avalanche forecast.

## 1. Cases

One case per usable pit and case type at Bow Summit (BOW), Goat's Eye (GOAT) and Simpson (SIMP). The target is the
pit; the agent predicts its layering (or, for a pit with snow depth but no placed layers, its snow depth only).

| case type | as_of | forecast weather from as_of to the pit |
|---|---|---|
| `forecast_h72` | availability (issue + 5 h) of the archived GFS run whose leads reach the pit, the earliest such run (ADR-060); pit time - 72 h when no run reaches it | that run to the pit; else measured weather as a labelled stand-in |
| `next_pit` | availability of the previous usable pit with layers at the plot, same season (the anchor) | measured weather as a perfect forecast (stand-in) |

`forecast_h72` is the training case (the owner: "the historical weather forecasts and weather actuals before every
observed pit for all seasons"). `next_pit` isolates the snowpack step from forecast error.

The archive's runs are 00 UTC with leads to 72 h, so a `forecast_h72` case on an archived run has a lead to the pit of 48-72 h and a horizon
(as_of to pit) of 43-67 h, and its forecast reaches the pit (milestone 2 used as_of = pit - 72 h, which left the last 7-22 h
without forecast). `config/lab.yaml` `benchmark.forecast_h72`: `run_choice: latest` takes the last run before the pit instead;
`as_of_rule: fixed_horizon` restores milestone 2.

The case id `<SITE>_<pit time UTC>_<H72|NP>` (e.g. `BOW_20240125T1940Z_H72`) names the package directory and the
manifest; agents never see it.

## 2. What an agent sees (visible/)

| file | content |
|---|---|
| `case.json` | random `case_key`, case type, site code, as_of day of year, horizon (h), forecast source, warnings |
| `site.json`, `terrain_scenario.json` | the plot (coordinates, elevation) and its reference terrain (flat study plot) |
| `weather_observed.parquet` | measured hours from the season start (15 Sep) to as_of |
| `weather_forecasts.parquet` | the archived GFS run's hours to the pit, or the measured stand-in |
| `forecast_runs.json` | the run used: source, issue and availability time relative to as_of, surface elevation, max lead |
| `permitted_pits.parquet`, `permitted_layers.parquet`, `permitted_observations.parquet` | earlier pits of the plot available at as_of (all seasons), their layers and stability tests |

**Anonymous** (against memorising the pits): every time is hours relative to as_of (`t_rel_h`, `issued_rel_h`,
`available_rel_h`) plus the UTC day of year; there is no calendar date, year or season label. Pits are `pit_01`..
(oldest first) with `season_offset` (0 = the case's season, -1 the season before). No profile, layer, observation
or observer id, no file names, notes, comments, raw fields or observer layer tags ("Nov crust"); a layer marked of
concern from an observer tag keeps the basis `observer_tag`. Stability tests keep type, result, score, fracture
character, shear quality and depth. The real ids are in `manifest.json` (`pit_keys`, `visible_profile_ids`,
`target_profile_id`, `season`) and `hidden/`.

Agents load a case with `snowagent.lab.benchmark.loader.load_visible_case(case_dir)`, which reads `visible/` only
and returns a `VisibleBenchmarkCase`; that type has no field for the target or any hidden value and refuses a record
not available at as_of.

## 3. Availability rule

A record is visible only if its availability time ≤ as_of. No source records publication times, so the builder
stamps assumed delays (`availability_assumption: assumed_delay`), configured in `config/lab.yaml`
`benchmark.availability` and written into every manifest:

| record | available at |
|---|---|
| pit and its tests | observed + 24 h (**provisional**, owner to confirm) |
| station hour | observed + 1 h |
| ERA5-filled value | observed + 120 h (younger ERA5 values are withheld, null and `missing`) |
| archived GFS run | issued + 5 h |
| measured stand-in | issued at as_of by convention (`perfect_forecast`), snow depth and SWE withheld |

Measured weather is the plot stations, with ERA5 (the plot's grid cell) filling missing hours and variables,
flagged `filled` with source `era5_cell_<elev>m` (`weather.era5_backfill`). Radiation and pressure are ERA5 only.

## 4. Splits

`splits.mode` in `config/lab.yaml`:

- `all` (default, owner 2026-10-05): seasons 2015-16 to 2025-26 are all training; nothing is sealed. Case set `all`.
- `split`: development / validation / sealed-test seasons (provisional, owner to confirm; a season in two splits is a
  configuration error). Case set `split`.
- `loso`: one season held out (`--holdout 2019-2020` or `loso_holdout`); its pits are the holdout split and are
  never shown to the training cases. Case set `loso_<season>`. Seasons are never split randomly.

Pits outside the mode's seasons (before 2015-16) are not targets but are visible history to later cases.

## 5. Exclusions (each reported with its reason)

| reason | meaning |
|---|---|
| `duplicate` | the pit is a `duplicate_of` another record |
| `flagged_review_list` | on the owner's review list (ADR-050 switch `exclude_flagged_pits_from_steering_and_scoring`, on) |
| `no_layers_no_snow_depth` | nothing to score |
| `season_not_in_split_mode` | the season is not used by the mode |
| `no_previous_pit_in_season` | `next_pit` only: no anchor |
| `standin_weather_coverage_below_min` | no archived forecast and measured temperature and precipitation for < 50 % of the hours |

Duplicates and flagged pits are not shown as history either. Each manifest counts the records not shown, by reason
(`excluded_counts`: target or copy, after as_of, not yet available, held-out season, ERA5 within latency).

## 6. Leakage checks

Every case is written to a temporary directory, checked, and only then moved into place; a failed check fails the
build (exit 3). `snowagent lab check-leakage` re-runs the checks on built cases:

1. `manifest_valid`; 2. `hashes_complete_and_match` (visible and hidden files); 3. `visible_header_matches_manifest`;
4. `visible_records_available_at_as_of` (including the ERA5 latency); 5. `target_profile_not_visible` (target and
copies, by id and text scan); 6. `no_profile_observed_after_as_of`; 7. `forecasts_issued_before_as_of`;
8. `visible_package_anonymous` (forbidden fields, datetime columns, date-like strings, known ids);
9. `loso_holdout_not_visible`; 10. `visible_case_validates`.

The tests (`tests/unit/test_lab_benchmark.py`) plant a future weather row and the target pit in a visible package and
expect the build to fail, tamper with a file and expect the hash check to fail, and refuse sealed truth without the
typed phrase.

## 7. Sealed truth

`hidden/` holds the withheld pit (`truth_profile.json`, `truth_layers.parquet`, `truth_observations.parquet`,
`verification.json`). For a sealed-test case, `snowagent lab case-truth --case-id <id> --unseal` asks for the phrase
`UNSEAL <case_id>`; nothing else reads it, and the Benchmark Cases page shows truth for training and development cases
only. In mode `all` nothing is sealed.

## 8. Commands

```bash
snowagent lab build-cases                     # every case of the configured mode (about 4-5 min on the full set)
snowagent lab build-cases --site BOW --case-type forecast_h72
snowagent lab build-cases --holdout 2019-2020 # with splits.mode: loso
snowagent lab build-case --profile-id <id>    # the cases of one pit
snowagent lab cases                           # counts per set, split, site, type, forecast source
snowagent lab check-leakage                   # re-check every built case (exit 3 on a leak)
snowagent lab case-truth --case-id <id>       # evaluator view (sealed: --unseal and the typed phrase)
```

`--source <checkout>` reads another checkout's `archive/forecasts/gfs` (read only). Each build writes
`benchmark/<case set>/build_report.json` (counts per plot and season, archived vs stand-in, exclusions with reasons,
leakage results) and a `case_build` run manifest in the registry. A rebuild removes the set's cases it no longer
produces (`--no-prune` keeps them).

## 9. On the data of 2026-10-05 (mode `all`)

340 cases, all 340 passing the leakage checks at build and on re-check:

| | BOW | GOAT | SIMP | total |
|---|---|---|---|---|
| `forecast_h72` | 77 | 69 | 40 | 186 (72 archived GFS, 114 stand-in) |
| `next_pit` | 66 | 58 | 30 | 154 (all stand-in) |

Archived GFS runs cover the `forecast_h72` cases from 2021-22 on (every case of those seasons); 2015-16 to 2020-21
use the stand-in. Exclusions per case type: 15 duplicates, 31 flagged pits, 320 pits before 2015-16; `next_pit` also
32 pits without an earlier pit in their season. No pit lacked both layers and snow depth and no stand-in fell below
the weather coverage. Since ADR-060 (builder version 3) every archived case uses the earliest run whose leads reach
its pit: horizons 50-65.3 h (median 62 h), the pit at lead 55-70.3 h (median 67 h), no forecast ending before its
pit. Archived cases per plot and season, 2021-22 to 2025-26: BOW 7, 5, 5, 8, 6; GOAT 7, 6, 5, 5, 5; SIMP 6, 3, 2, -,
2 (no Simpson case in 2024-25; the case counts are unchanged from milestone 2). Agents, scoring and competitions on
these cases: `agents_and_scoring.md`.
