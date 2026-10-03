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
- Daily update hardening (ADR-043): an FTS360 reply with fewer data rows than the month's existing raw or archived file (e.g. an empty or header-only 200 reply) no longer replaces it; the event is a warning in the `update fetch` output (new top-level `warnings` list).
- `update fetch` ERA5: months not yet on the mirror are told apart from errors (listed with the exception text), overdue months and extracted months with missing flux hours are warnings (ADR-043). The 5-day ERA5 latency of the Phase 2 cases is kept and the mirror's ~3 months recorded beside it; no model behaviour change.
- GFS in the daily update (ADR-043): runs of the 21-day retry window that are incomplete in the archive (fewer points or leads) are re-extracted like missing ones; older gaps are reported as permanently missing/incomplete. A live season cut at a GFS gap is now a warning (gap, cut time, unused hours) in the build output, the forcing notes and `web/data/status.json` (new `warnings` list).
- Stale inputs flagged in code (ADR-043): `update build` writes station and GFS staleness (a plot station > 24 h behind, a GFS run > 48 h old) to `status.json` `warnings` with each station's role and what replaces it, shown as a banner on the site; Simpson Upper added to the data status. Lookout is marked seasonal (`seasonal_stations`, off June-October): its summer outage is an info note, not a fault. Runbook steps 4 and 6 use the warnings.
- Daily update failures contained (ADR-044): each source of `update fetch` and each part of `update build` runs in its own error boundary; a failure (an FTS360 401/403, a MIN listing error, a failed GFS archive sync, a plot whose season fails) is listed in `failed_steps` and as an `error` warning, and the other steps still run. The build always writes `sites.json` and `status.json`, with the failures shown on the site.
- `update fetch` and `update build` exit with code 2 when any step failed (after printing the whole result), 0 otherwise (ADR-044).
