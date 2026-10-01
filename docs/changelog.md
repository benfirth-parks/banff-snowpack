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
- Per-station logger tables (Bow Summit from Dec 2014, Simpson Lower/Upper from Jan 2015, Sunshine from Aug 2015)
  merged into `snowagent ingest byk` (ADR-036): Bow Summit humidity measured 2015-21; Goat's Eye and Simpson
  2015-16 become measured-weather seasons. Logger aliases (Temp/TA, HS/SD) coalesced to one name per variable.
