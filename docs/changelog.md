# Changelog

## 0.1.0 — 2026-09-30
- Pinned native SNOWPACK build (b324cbd) + upstream MST96 example check.
- Typed contracts (terrain unit, weather meta, checkpoint, layer/profile, forecast request/result).
- DEM/land-cover/boundary ingest with CRS/units/no-data validation; slope, aspect, horizons, sky view,
  exposure proxy; configurable terrain units.
- Weather adapter with explicit units/UTC, availability times, gap QC and leakage guards.
- Terrain-conditioned forcing with single-owner corrections (engine `PERP_TO_SLOPE=TRUE`).
- Write-once, hash-verified checkpoints; history replay from explicit snow-free start; advance with actuals.
- Forecast branching, seeded scenario ensemble, what-if branches, per-unit/member/lead profiles,
  map-ready GeoJSON/CSV, mass budget, point/profile query, plots.
- Synthetic demo fixture; 67 tests (unit + real-engine integration).
- Verification numbers: none against real data yet (synthetic only).

## 0.2.0 — 2026-09-30
- `snowagent obs inventory`: Propagation Labs header parser (icon glyphs, unit mix, missing spaces),
  per-profile QC flags, duplicate and device-GPS detection, study-plot consensus locations.
- Uploads 2023-24, 2024-25, 2025-26: 136 profile files (64 study-plot, 64 test, 8 unclassified);
  38 with parseable header text, 34 with HS, 0 with machine-readable layers.
- Filename-date parser for all naming conventions seen (ambiguous/invalid dates flagged, never guessed);
  folder-layout classifier for the differing season structures; whitespace-tolerant header labels.
- Derived Goat's Eye and Bow Summit study-plot locations recorded (flagged as derived).

## 0.3.0 — in progress
- Observed-profile builder (`observed_profiles.jsonl`): height above ground, hardness index, pit-bottom trim,
  de-duplication (content hash, identical layers, same-pit heuristic), location QC against site medians,
  printed-UTM conversion.
- Exact structured parsers: SnowPro 2.1 / 3.x / Plus XML (1997-2014) and CAAML v5 (niViz, 2018-19).
- Image transcriptions (`observations/transcriptions/`, schema `transcription-1`, IACS 2009 / OGRS symbols,
  unreviewed) with validator and guide.
- Wave 2 complete: 480 transcriptions (466 profiles, 14 non-profiles), all valid; blind re-read QA (36 pairs)
  and image-vs-exact QA (25 pairs) in ADR-017. Observed set: 1,059 unique observations, 732 usable at the five
  study plots (Bow Summit 257, Goat's Eye 218, Tak Falls 128, Vermilion 89, Simpson 40), 1996-97 to 2025-26.
- Fixes: transparent PNGs rendered on white; one vertical conversion for layers/temperatures/tests (ADR-018);
  locale-dependent SnowPro dates; agreement metric now covers depth-only charts.
- No model behaviour change; no verification numbers (no real weather forcing yet).
- First real-data baseline (2021-22 to 2025-26, three plots): FTS360 stations + ERA5, SNOWPACK flat-plot
  columns; numbers in docs/verification/baseline_2021_2026.md. Stability scheme MO_SCHLOEGL_MULTI_OFFSET
  (ADR-022); gauge and snow-depth QC (ADR-023).

## 0.3.1 — 2026-10-01
- GFS ingest re-extracts a run whose CSV lacks requested points or leads (an early 3-point test extract of
  2023-01-15 caused the single hindcast IndexError); the archive sync replaces such a copy with the full one.
- Hindcast skips (with a reason) a GFS run that lacks the plot's point instead of erroring.
- Hindcast caches each completed pit-lead (artifacts/hindcast/runs/results) so restarts resume.
- Retry-path engine test pins MO_MICHLMAYR, the scheme under which its abort fixture is reproducible (ADR-022).

## 0.4.0 — 2026-10-01
- `snowagent era5-transfer`: per-plot ERA5 temperature offset and precipitation catch ratio from the 2021-26
  station seasons (config/era5_transfer.yaml, ADR-025).
- `snowagent baseline --era5-only` (ERA5 with those constants), season ranges (`--seasons 1996-2020`) and
  `--workers` (process pool); snow depth also scored against GHCN-Daily records (`hs_check_ghcnd`).

## 0.5.0 — 2026-10-01
- User-confirmed plot locations (ADR-026); Goat's Eye moved 467 m, elevation 2190 m (DEM; headers disagree).
- ERA5-only transfer: phase method (monthly wet/dry temperature offsets, cold/warm precipitation ratios) adopted
  over the constant method by leave-one-season-out (`snowagent era5-transfer-loso`); 1996-2026 rerun.
- CaSR v3.2 ingest (`snowagent ingest casr`, ADR-028); tested against ERA5 by LOSO and not adopted (worse 7/7).
- Observation-noise checks (`baseline/obs_noise.py`, ADR-027): pit-vs-pit scores, test-failure support,
  chance-level coverage; signed hardness difference in compare_profiles.
- DTW similarity via r/dtw_similarity.R (sarp.snowprofile.alignment 2.0.2 from the CRAN archive, ADR-029).
- Verification numbers updated (docs/verification/baseline_2021_2026.md).


## 0.6.0 — in progress
- Direction (ADR-058): evolving a forecast agent that predicts observed snowpack structure is the primary purpose; CLAUDE.md product goal updated; SNOWPACK is the incumbent agent.
- `snowagent ingest byk`: the user's 2014-2020 logger-database exports archived in archive/byk_export and converted
  per station (ADR-030); `fts360.load_station` joins them with the FTS360 API records under the same QC, and the
  baseline, calibration and snow-depth checks use it.
- Snow pillow SWE (FTS360 `SW`, AB Env stations) parsed as `swe_mm`; readings implausible for the measured snow
  depth (bulk density outside 50-650 kg/m3 where HS > 0.3 m) flagged suspect (Sunshine pillow dead 2021-24).
  `snowagent baseline` scores modelled SWE against `swe_check` stations (Goat's Eye: Sunshine pillow).
- Station-driven baseline for 2015-19 from the logger exports (fully measured: 2016-17, 2017-18); verification
  numbers added.
- Hardness diagnosis (`baseline/hardness_diag.py`): density vs hardness pairing, offline port of the engine's three
  hand-hardness relations, LOSO choice; engine now runs HARDNESS_PARAMETERIZATION = BELLAIRE (ADR-031).
- Phase 2 on real terrain (ADR-032/033): `snowagent prepare-domain` (Copernicus DEM window + ESA WorldCover land
  cover + boundary, study plots as site units), `snowagent case-inputs` (history/recent/forecast series with real
  availability; GFS day-1 composite fill), `snowagent phase2-report` (distinctness, leakage audit + refusal probes,
  withheld pit). Goat's Eye 6 km domain, GFS 2026-03-23 00 UTC: acceptance met (docs/verification/phase2_*).
- `baseline/run.plot_unit` now shares `terrain.units.site_unit`; GFS point per plot in config/plot_forcing.yaml.
- Visitor Safety dashboard history (Power BI) ingested as a third station archive (`snowagent ingest fts-dashboard`,
  ADR-034): measured plot weather now covers 2016-17 .. 2025-26 at all three plots (gap Nov 2018 - May 2021 closed).
- Engine template: optional daily restart states (`EngineSettings.snow_days_between/first_backup`; defaults unchanged).
- Site tool (`web/`, `snowagent web-build`, ADR-035): pick a plot, date and time, and weather input (measured, or
  GFS forecast at lead up to 24/48/72 h); simulated profile beside the observed pit with scores, the hourly weather
  that drove the simulation, and the season's snow depth; every profile shows its run id, engine version and
  configuration/forcing hashes. Deployed to banff-snowpack.netlify.app (behind the Netlify team login).
- Per-station logger tables (Bow Summit from Dec 2014, Simpson Lower/Upper from Jan 2015, Sunshine from Aug 2015)
  merged into `snowagent ingest byk` (ADR-036): Bow Summit humidity measured 2015-21; Goat's Eye and Simpson
  2015-16 become measured-weather seasons. Logger aliases (Temp/TA, HS/SD) coalesced to one name per variable.
- Phase 2 acceptance at the other two plots, same code: Simpson 6 km domain with GFS 2026-03-08 and Bow Summit 6 km
  with GFS 2026-03-02. Distinct profiles at all 25 / 43 simulated units, leakage audit and refusal probes pass,
  withheld pits: HS 8 / 1 cm from the forecast (docs/verification/phase2_simpson_*, phase2_bow_summit_*). The
  overview figure now labels the site unit by plot.
- Verification: station-driven vs ERA5-only baselines rerun for 2015-16 .. 2025-26 with the complete station archive
  (docs/verification/baseline_2021_2026.md, last section): measured weather removes the ERA5 depth deficit at Bow
  Summit and Simpson in both periods; Goat's Eye station runs stay too deep (+16 cm at the pits).
- Daily update (ADR-037, runbook docs/operations.md): `snowagent update bootstrap|fetch|build`. Live 2026-27 season
  on measured weather to the latest hour (GFS day-1 fill until ERA5 is published; previous-run fallback for a
  missing run), daily GFS forecasts stored once as issued (`archive/live_forecasts`).
- Avalanche Canada MIN reports near the plots: `snowagent ingest min` (raw JSON per version in `archive/min`,
  backfilled from Oct 2016), shown on the site within 15 km and 3 days of the selected time.
- Profile drop-in: upload form on the site (Netlify Forms), `snowagent obs inbox` files uploads unchanged into
  `profiles/`; PDFs/photos join the transcription queue, CAAML v5 is read exactly. Data-status panel on the site.
- Forcing notes name the fill source actually used (ERA5, CaSR or GFS day-1) instead of always "ERA5".

- Observation steering (ADR-038, docs/verification/observation_steering.md): study-plot pits update the simulated
  snow depth from the next 00 UTC on (weight 1, chosen leave-one-season-out; next-pit depth error 15.3 -> 7.4 cm in
  11/11 seasons). Re-initialising the layering from a pit tested (grain agreement 0.564 vs 0.494) but not adopted.
  Site: measured-weather profiles are pit-steered, the free run stays selectable and on the season chart.
  `calibrate.loso(pit_weight=)` blends pit depth into the precipitation-factor target.
- Precipitation factor at Goat's Eye 0.9 (ADR-038): 11-season leave-one-season-out with pit-weighted target; held-out
  sensor depth MAE 16.3 -> 13.0 cm, pit depth error 18.0 -> 10.1 cm. Bow Summit and Simpson keep 1.15.
- Pit updates restart the layering from each pit (ADR-039): next-pit grain agreement 0.49 -> 0.58, hardness and
  layer boundaries better in 9-11 of 11 seasons; depth error 6.8 -> 8.8 cm. Hardness -> density from the pits' own
  1847 measured layers. `steer.run_experiment2(variant=)`; raw rows artifacts/steer/exp3_variants.csv.
- Site: simulated and observed profile charts render at the same size with aligned axes (shared row heights, common width).
- GFS correction test (ADR-040): constant per-plot temperature/precipitation corrections improve held-out forcing but not forecast snow depth in most seasons (7/15); not adopted. GFS stays raw.
- Sunshine Village webcams (ADR-041): daily capture of the snow stake and Trappers & Standish cameras (Windy Webcams), stale feeds skipped; stake readings recorded as checks.
- Storm-only GFS precipitation correction (quantile mapping, ADR-042) tested leave-one-season-out: storm totals 51% -> 72% of measured but overall error worse in most seasons; not adopted.
- Packaging: declare the ingest dependencies the code already imports (requests, fsspec, h5py, eccodes, rasterio, pbixray), so a fresh `pip install -e .[dev]` can run `snowagent update bootstrap`.
- Daily update hardening (ADR-043): an FTS360 reply with fewer data rows than the month's existing raw or archived file (e.g. an empty or header-only 200 reply) no longer replaces it; the event is a warning in the `update fetch` output (new top-level `warnings` list).
- `update fetch` ERA5: months not yet on the mirror are told apart from errors (listed with the exception text), overdue months and extracted months with missing flux hours are warnings (ADR-043). The 5-day ERA5 latency of the Phase 2 cases is kept and the mirror's ~3 months recorded beside it; no model behaviour change.
- GFS in the daily update (ADR-043): runs of the 21-day retry window that are incomplete in the archive (fewer points or leads) are re-extracted like missing ones; older gaps are reported as permanently missing/incomplete. A live season cut at a GFS gap is now a warning (gap, cut time, unused hours) in the build output, the forcing notes and `web/data/status.json` (new `warnings` list).
- Stale inputs flagged in code (ADR-043): `update build` writes station and GFS staleness (a plot station > 24 h behind, a GFS run > 48 h old) to `status.json` `warnings` with each station's role and what replaces it, shown as a banner on the site; Simpson Upper added to the data status. Lookout is marked seasonal (`seasonal_stations`, off June-October): its summer outage is an info note, not a fault. Runbook steps 4 and 6 use the warnings.
- Daily update failures contained (ADR-044): each source of `update fetch` and each part of `update build` runs in its own error boundary; a failure (an FTS360 401/403, a MIN listing error, a failed GFS archive sync, a plot whose season fails) is listed in `failed_steps` and as an `error` warning, and the other steps still run. The build always writes `sites.json` and `status.json`, with the failures shown on the site.
- `update fetch` and `update build` exit with code 2 when any step failed (after printing the whole result), 0 otherwise (ADR-044).
- Run log (ADR-044): every `update fetch` and `update build` appends one line (time, command, ok, exit code, failed steps, key counts, warnings per level) to `archive/ops/runs.jsonl`, committed with the raw files.
- Update lock (ADR-044): `update fetch` and `update build` hold `data/update.lock` (pid, host, command, start time); a second run while it is held does nothing and exits 3; a lock older than 3 h or whose process has died is taken over with a warning.
- Site: a banner says when the daily update was missed (`status.json` older than 36 h, new `stale_after_h.update`), above the data warnings (ADR-044).
- Step errors in the update output, run log and `status.json` never carry the FTS360 credential (`FTS360_TOKEN` masked; ADR-044).
- Runbook step 5 (ADR-045): the raw files and the run log are committed and pushed before the site is deployed; no deploy when the push failed, so the site never shows issued forecasts that git does not have.
- `snowagent update check-deploy` (ADR-045): before a deploy, checks that every data file in `web/data/sites.json` exists and is valid JSON, no site or season of the deployed site (`--reference`, a downloaded `sites.json`) is missing, `status.json` is at most 6 h old and no update run holds the lock; exits 2 with the problems listed. Runbook step 5 deploys only on exit 0.
- `snowagent update restore-web` (ADR-045): in a fresh container, restores `web/data` from the deployed site (its `sites.json`, every data file listed there and `status.json`) when `web/data/sites.json` is missing; each file must parse as JSON, local files are kept unless `--force`, `sites.json` is written last and only when nothing failed (a rerun resumes). Runbook section 0 uses it; it and ADR-035 now say that `update build` regenerates only the live season.
- ADR-046 (open): no durable off-site copy of `web/data`, the ERA5 cache or engine states exists; the options (GitHub release asset, a data branch, external storage) are recorded for the owner to choose, with `update restore-web` from the deployed site as the interim fallback.
- `update fetch` FTS360 (ADR-047): requests from the start of the previous calendar month on every day and refreshes that month in `archive/fts360` at every run; before, from the 2nd of a month only the current month was requested and refreshed, so a missed or failed run on the 1st lost the previous month's last hours.
- `update fetch` FTS360 (ADR-047): a failed request that `fetch_station` records itself (a 5xx after its retries, a connection dropped on every attempt, any other non-2xx reply) is now a warning, and a station none of whose requests was answered is a failed step (exit 2); before, both were only in the station's `errors` and the run exited 0. Lookout's failed requests in its off months are an `info` entry, never a failure.
- `update fetch` FTS360: only a refused credential (401/403, new `CredentialRefused`) skips the remaining stations; a file-system `PermissionError` is now that station's failure alone (ADR-047).
- `update check-deploy` (ADR-047): refuses without `--reference` (the deployed `sites.json`), since the local checks alone passed a site folder holding only the live season; `--no-reference` is for a first deploy only. Runbook step 5.2: no deploy on a day the deployed index cannot be downloaded.
- `update restore-web --force` (ADR-047) removes the local `sites.json` first, so after a partial forced restore the index stays missing and a rerun without `--force` resumes (before, it found the build's index and restored nothing).
- Update lock (ADR-047): taking over a stale lock is serialised by an `flock` on `data/update.lock.guard`, so two runs that find the same stale lock can no longer both hold it.
- `archive/ops/runs.jsonl` merges by union (`.gitattributes`), so a rebase of the routine's push over another branch's run-log lines no longer stops on a conflict (ADR-047).
- Project brief: the daily update's publish order (commit and push, `update check-deploy`, then deploy; `update restore-web` in a fresh container) and the ADR range (to ADR-047) brought up to date.
- Profile drop-in (ADR-048): CAAML v5 is recognised by content, so a profile saved as `.xml` (receipt `filed_exact`) is now read by `obs profiles` like a `.caaml`; before, it was filed and never read. Other XML (CAAML v6, unknown) is kept unchanged and listed in the `obs profiles` output (`structured_not_read`, `not_read` with file, format and reason) instead of being skipped silently. The inbox and the reader share one detector (`obs.caaml.xml_kind`). Observed set unchanged (no `.xml` files in `profiles/` yet).
- Transcribed pits (ADR-049): a printed date that differs from the filename date is flagged (`printed_date_<d>_differs_from_filename_<d>`, as the structured path already did), and a printed site/location name that clearly names another place than the folder's study plot is flagged with the name as printed (`printed_site_name_not_folder_plot:` / `printed_site_name_is_other_plot:`; plot names from `config/observations.yaml`, new `printed_site_names` list). Transcribed records also carry `site_name_as_written`. On current data 33 date flags (22 at study plots) and 4 site flags (Avanet's place label "Brewster Rock, Alberta" at the Goat's Eye plot is in `printed_site_names`, like "Bow Pass, Alberta"); nothing else in the observed set changes.
- `exclude_flagged_pits_from_steering_and_scoring` in `config/observations.yaml` (ADR-050), default `false` (today's behaviour). When `true`, pits with `location_qc` entries or printed date/site flags neither steer the site runs (`learn.steer.update_pits`, now the selection `steered_run` uses) nor count in scoring (`baseline.evaluate.observed_at_plot`); the season files and `snowagent baseline` list them as `pits_excluded`. No output changes with the default.
- Owner's ruling (2026-10-03): `exclude_flagged_pits_from_steering_and_scoring` set to `true` (ADR-050). The 39 flagged study-plot pits no longer steer the site runs or count in scoring; 22 of them came out of 19 season files (21 that steered a run plus one scored-only location-flagged pit, Bow Summit 2015-16); the 11 seasons 2015-16 to 2025-26 were rebuilt and deployed on 2026-10-04 (0 build errors). Re-verification on the remaining pits (136 next-pit pairs instead of 156, docs/verification/observation_steering.md): pit re-initialisation still beats the depth update on grain agreement 0.483 -> 0.568 (10/11 seasons), hardness MAE 0.913 -> 0.771 (9/11) and boundary F1 0.222 -> 0.277 (10/11), depth error 7.0 -> 9.0 cm; precipitation-factor LOSO unchanged (Goat's Eye 0.9, Simpson 1.15, Bow Summit 1.15). No model setting changes.
- `snowagent obs flagged-pits` (ADR-051): review table (CSV and Markdown, default `artifacts/pit_review/`) of every study-plot pit with `location_qc` entries or printed date/site flags: printed vs filename date, printed site name, location_qc, whether it steers a site run (same selection as the site build; actual updates read from the built season files when present), duplicates, transcription confidence, source file. On 2026-10-03: 39 pits (date 22, site 4, location 16), 21 of them steer a site run.
- Observed set (ADR-052): a file the inbox named after its upload date (no form date, no date in the original name; receipt flag `observation_date_unknown_upload_date_used_for_filing`) is flagged `filename_date_is_upload_date`, and that date is no longer compared with the printed or file date, so an upload made after the dig day no longer gets a false `printed_date_` (review list) or `file_date_` conflict. `build_observed` reads `observations/inbox/received.jsonl` for this. Observed set unchanged (no receipts yet).
- The site build and `snowagent baseline` take their pits and the `pits_excluded` record from one helper (`baseline.evaluate.plot_pits`, ADR-050), now tested with the switch on and off. No output changes.
- Calibration (`baseline.calibrate`) lists the pits `exclude_flagged_pits_from_steering_and_scoring` leaves out as `pits_excluded` in each grid row, like the site build and `snowagent baseline`; the config comment now names the outputs that list them (ADR-050). No output changes with the default.
- Daily `update build`: each profile file under `profiles/` that the observed set keeps but does not read (CAAML other than v5, unknown XML) is listed in status.json as an `info` entry (source `observed:not_read`, file and reason), also when committed straight into `profiles/` rather than through the inbox (ADR-048). None today.
- Snowpack Agent Lab, milestone 1 (ADR-055 to ADR-057): a local research module (`src/snowagent/lab/`, `snowagent lab
  init|import|coverage`, Streamlit app `lab_app/`) for benchmarking snowpack-prediction agents at Bow Summit, Goat's
  Eye and Simpson, SNOWPACK the incumbent. `config/lab.yaml` (sites by plot, coordinates read from
  `config/plot_forcing.yaml`; scoring weights; empty season splits with overlap blocked; 15 Sep season key). New
  data contracts (canonical weather, snow profile in depth from surface, observation, case manifest with a visible
  case type that cannot hold hidden truth or future records, prediction with ordered p10/p50/p90, bounded agent
  genome, run manifest). The import converts the observed profiles of the three plots (heights above ground to
  metres below the surface, raw fields kept, grain form -> critical class, layers of concern by class or observer
  tag) and the plots' QC'd station records (first QC-ok station per hour and variable, source and flag kept, never
  filled) to Parquet under `data/lab/`, with a write-once run manifest in a SQLite registry. On the data of
  2026-10-04: 552 profiles (536 unique usable: BOW 262, GOAT 224, SIMP 50), 4,703 layers (3,053 of concern),
  893 stability tests, 304,123 weather hours (BOW 103,717, GOAT 97,623, SIMP 102,783). Optional `lab` extra
  (streamlit, plotly, pyarrow, scikit-learn); the daily run imports none of them. Docs: `docs/lab/local_setup.md`,
  `docs/lab/data_dictionary.md`. No model behaviour change; no verification numbers change.
- Snowpack Agent Lab, milestone 2 (ADR-059, `docs/lab/benchmark_protocol.md`): benchmark cases. `snowagent lab
  build-cases|build-case|cases|check-leakage|case-truth` build one case per usable pit at the three plots:
  `forecast_h72` (as_of = pit - 72 h, the latest archived GFS run available then, else measured weather as a labelled
  `measured_standin`) and `next_pit` (as_of = availability of the previous pit, measured weather as a perfect
  forecast). Split modes in `config/lab.yaml`: `all` (default, owner 2026-10-05: every season 2015-16 to 2025-26 is
  training), `split` (development / validation / sealed test, provisional) and `loso` (one held-out season).
  Availability assumed and recorded per manifest: pits + 24 h (provisional), station hours + 1 h, ERA5 + 120 h, GFS
  + 5 h. Visible packages are anonymous (owner: agents must not memorise the pits): no ids, dated case ids, observer
  data or calendar dates, times relative to as_of plus day of year. Every case passes ten leakage checks before it
  is written (a planted future record or target pit fails the build); sealed truth only with `--unseal` and a typed
  phrase. Exclusions (duplicates, the ADR-050 review list, seasons outside the mode, no anchor) are reported with
  reasons. Lab import fills station gaps from the plot's ERA5 cell (flagged `filled`). Streamlit page **Benchmark
  Cases**. On the data of 2026-10-05: 340 cases (forecast_h72 186: 72 archived GFS, 114 stand-in; next_pit 154), all
  passing the leakage checks. No model behaviour change; no verification numbers change.
- Snowpack Agent Lab, milestone 3 (ADR-060 to ADR-065, `docs/lab/agents_and_scoring.md`): agents, scoring and
  competitions. Forecast cases now start when the earliest archived GFS run whose leads reach the pit is available
  (ADR-060; horizons 50-65 h, no forecast ending before its pit; case counts unchanged). Agent genome: a family and
  its allow-listed genes in `config/lab.yaml`, stable hash, seeded mutation and block crossover (ADR-061). Five agent
  families from the visible case only: persistence, weather rules, analogue (cases of other seasons only), the
  SNOWPACK incumbent (run from the visible package with the adopted settings and pit restarts; site runs reused only
  when they used nothing unavailable at as-of, which none does today) and a hybrid (ADR-062/063). Scoring: snow
  depth, layer structure, critical layers, uncertainty, robustness, frozen weights; truth read only for the scoring
  splits, never sealed (ADR-064). `snowagent lab compete` (parallel, resumable, run manifest with genome and case-set
  hashes) and `lab leaderboard`, the held-out gap hook, Streamlit page **Leaderboard** (ADR-065). First competition
  on the 340 cases: SNOWPACK 0.509, hybrid 0.500, analogue 0.489, persistence 0.407, weather rule 0.324 (composite;
  SNOWPACK depth MAE 0.10 m). Research benchmark only: no change to the site model or its verification numbers.
- Snowpack Agent Lab, milestone 4 (ADR-066 to ADR-069, `docs/lab/training.md`): local training. `snowagent lab
  train` (rounds, population, survivors, mutation strength, crossover share, seed, plots, case types, workers,
  run id, resume, initial genomes): round 1 scores the initial population on every training case of the split mode
  (default `all`, the 340 cases), each later round keeps the top two unchanged and fills the population with
  mutations and crossovers of them (unique genome hashes, duplicates re-drawn), ranked by the frozen composite with
  deterministic tie-breaks; deterministic per seed, resumable after a kill, every round committed atomically to the
  run registry. Prediction, score and engine-profile cache by genome, case and code/config context, so survivors and
  the SNOWPACK incumbent are never re-run and SNOWPACK runs once per case; a time estimate (and a warning when
  SNOWPACK dominates the cost) before the start. Per-round train-vs-held-out gap of the top two on a monitor season
  (2025-26), flagged after three widening rounds, documented as a warning signal only. `snowagent lab check-loso`
  (promotion check: the whole training re-run per held-out season, fold winners vs SNOWPACK on the held-out cases,
  pass rule of ADR-068), `snowagent lab lineage`, Streamlit page **Training** (runs training as a detached process).
  `config/lab.yaml` gains a `training` section (outside the config hash). First run (`--rounds 10 --population 10
  --seed 0`, 33 min): best composite 0.5089 -> 0.5198, winner a SNOWPACK-family agent with wider depth intervals and
  more confident layer presence (calibration of the SNOWPACK output; the simulated snowpack is unchanged); gap
  flagged in rounds 4-7. Promotion check (`check-loso`, full configuration): PASS, evolved 0.5187 vs SNOWPACK 0.5089 pooled over 340
  held-out cases, 10 of 11 seasons won (lost 2021-22). Research benchmark only: no change to the site model or its verification
  numbers.
- Snowpack Agent Lab, milestone 5 (ADR-070 to ADR-073, `docs/lab/training.md`, `docs/lab/agents_and_scoring.md`):
  SNOWPACK settings as genes. The SNOWPACK family and the hybrid's engine member carry a `snowpack_physics` block (16
  genes, genome schema `lab-genome-3`; milestone-4 genomes load and upgrade at defaults): new-snow density
  (HN_DENSITY, its parameterisation or fixed value), settlement (VISCOSITY_MODEL DEFAULT/KOJIMA), ROUGHNESS_LENGTH,
  surface hoar (HOAR_THRESH_TA/RH/VW, HOAR_DENSITY_BURIED, HOAR_MIN_SIZE_BURIED), and as forcing genes the
  precipitation factor per plot, the PSUM_PH rain-snow ramp and a measured-wind multiplier. Every key verified in the
  installed SNOWPACK source and on a real case; differences recorded (THRESH_RAIN unused with PSUM_PH,
  WIND_SCALING_FACTOR drift only, no snow conductivity key, CALIBRATION viscosity and NIED metamorphism excluded).
  Allow-listed keys and ranges only; default genes reproduce the milestone-3/4 incumbent exactly (tested; checked on
  real cases). Engine profiles cached by case and normalised physics; restart states shared within a plot and season
  under keys that hash every input, so no restart carries data a case cannot see (tested); `lab train
  --screen-cases K` and `--family-slots` (both off by default) and the same options on the Training page. Training
  run `--rounds 6 --population 8 --screen-cases 30` (2.5 h): best composite 0.5198 (M4 winner) -> 0.5399, winner
  with PAHAUT new-snow density, a warmer rain-snow ramp, lower roughness and lighter buried hoar; depth MAE 0.102 ->
  0.092 m, layer structure 0.513 -> 0.533, critical layers 0.301 -> 0.339 (incumbent 0.5089). Reduced promotion
  check (`check-loso --rounds 2`, weaker than the run): PASS, 0.5260 vs 0.5089 pooled over 340 held-out cases, 11 of
  11 seasons won. Research benchmark only: no change to the site model or its verification numbers.
- Snowpack Agent Lab, scoring version 2 (ADR-074): `snow_depth` = exp(-|p50 - observed| / 0.15 m), the coverage bonus
  dropped on the owner's decision (it had no width cost and evolution widened the ranges); `depth_covered` stays a
  diagnostic; weights and every other component unchanged. Scores leave the training cache's prediction and engine
  keys (scoring identity per entry, re-scored from stored predictions); `snowagent lab rescore` re-scores a stored
  competition. Re-scored without agent runs: incumbent 0.5089 -> 0.5022, M4 winner 0.5198 -> 0.5020 (its gain was
  the bonus), M5 winner 0.5399 -> 0.5216 (its physics gain remains); a fresh round 1 reproduces these exactly.
  Research benchmark only: no change to the site model or its verification numbers.
- Fresh clone to full training on a Mac (ADR-075, `docs/lab/run_locally.md`): `snowagent lab prepare` (bootstrap,
  observed profiles, the lab's ERA5 months from the existing mirror; resumable), `scripts/setup_env.sh`,
  `scripts/build_snowpack.sh` portable to macOS (bin/lib layout instead of upstream's app bundle, `~/.local/snowpack`
  default found without SNOWPACK_BIN). Walked on a scratch clone: cases identical to the published ones; smoke
  training and a two-season check-loso run under the `spawn` process start.
- Lab app: the Leaderboard and Training pages open on the most informative run (current scoring version, finished,
  most cases, most rounds) instead of the newest, which was often a smoke test; the Home page states the assumed
  availability delays from `config/lab.yaml` instead of an outdated note; the changed-genes table no longer logs an
  Arrow conversion warning for categorical genes.
- Lab docs: `run_locally.md` section 0 now installs Homebrew and puts it on PATH before any `brew` step (a new Mac has
  no `brew`); the setup scripts' error messages and the troubleshooting list point there. `setup_env.sh` prefers
  `python3.13`/`python3.12`/`python3.11` over a bare `python3` (Apple's 3.9) and rebuilds a `.venv` left by an older
  Python.
- ERA5 download: each remote read is retried four times (10 to 80 s apart) after a transient network failure (a
  range response cut off part-way, a timeout); a month not yet on the mirror is still not retried. `lab prepare` in
  the Mac guide uses 4 workers instead of 6.
- `lab prepare` first copies the extracted ERA5 months from the bundle branch `claude/lab-era5-box` (ADR-079; one
  download of about 0.2 GB, `--no-bundle` to skip), then reads only the months it lacks from the mirror.
