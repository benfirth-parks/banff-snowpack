# Implementation decisions (ADRs)

Short records of choices not fixed by the README/spec. Newest last.

## ADR-001 Stack
Python 3.11, pydantic v2 contracts, numpy/pandas, pyproj, matplotlib, typer CLI, pytest.
File-backed storage (JSON/CSV/GeoJSON + engine files). No LLM, no network services, no DuckDB yet
(Parquet/DuckDB store deferred until real data volume justifies it). `learn/` and `agent/` are empty
placeholders, kept separate from deterministic execution.

## ADR-002 Engine: native pinned SNOWPACK build
- Source: `github.com/snowpack-model/snowpack` @ `b324cbdd1b7f` (mirror merging WSL GitLab MeteoIO `337bfbc3`
  and SNOWPACK `364eb947`, 2026-06-01). The WSL GitLab host is blocked by this environment's egress policy;
  the GitHub mirror was reachable via git.
- Built natively (`scripts/build_snowpack.sh`) into `/opt/snowpack`. Reported version `20260930.b324cbd`.
- Upstream `tests/albedo/albedoTest.cc` does not compile at this commit (`slope.snow_erosion` is now a
  string); upstream tests are therefore built OFF. This does not affect the engine binary.
- `docker/snowpack.Dockerfile` wraps the same script but was **not built** here (native build was
  sufficient and faster); treat it as untested.
- Upstream example proof: MST96 (Weissfluhjoch 1995-96) runs; HS correlation with the bundled 2017
  (v3.41) reference = 0.999, peak SWE 444.9 vs 447.2 kg m-2. Token-level diff is not meaningful because
  output columns changed since 3.41. `snowagent engine-example` / `tests/integration::test_upstream_mst96...`.

## ADR-003 Verified engine keys (vs starter `config/snowpack/base.ini`)
Keys were grepped in the installed source before use. Differences from the starter file:
- `BACKUP_DAYS_BETWEEN` does not exist -> `SNOW_DAYS_BETWEEN`.
- `STATION1` for SMET input is deprecated -> `METEOFILE1 = <id>.smet`.
- `NUMBER_SLOPES = 5` + `SNOW_REDISTRIBUTION = TRUE` (virtual slopes) replaced by `NUMBER_SLOPES = 1`,
  `SNOW_REDISTRIBUTION = FALSE`, `SNOW_EROSION = FALSE`: generic virtual slopes are not the production
  geometry and their redistribution is incompatible with terrain units (spec).
- Added `PERP_TO_SLOPE = TRUE` (ADR-004), `PROF_AGE_OR_DATE = DATE` (lineage), `OUT_STAB/OUT_MASS/OUT_HAZ`.
- `PSUM::resample1 = accumulate` with period = calculation step (as in upstream example); otherwise hourly
  sums would be interpolated.
The starter `base.ini` is preserved unchanged for reference; the engine uses `terrain_column.ini`.

## ADR-004 Single owner for every terrain correction
Verified in `DataClasses.cc::dataForCurrentTimeStep`: with `PERP_TO_SLOPE=false` SNOWPACK splits ISWR,
projects the direct beam onto the slope **without horizon shading** and multiplies PSUM by cos(slope).
We set `PERP_TO_SLOPE=TRUE`, so the engine does neither, and the terrain adapter owns: lapse-rate TA,
dewpoint-conserving RH, precip elevation factor, phase, horizontal->slope-area PSUM (x cos),
Erbs direct/diffuse split, incidence + horizon shading, sky-view diffuse, terrain-reflected SW,
simplified sky-view longwave. The engine owns albedo/reflected SW from snow and slope-normal layer
geometry (`cos_sl` in settling and output).
Geometry (verified in `AsciiIO::writeProfilePro`): `.pro` heights are vertical (internal slope-normal / cos).
`.met` SWE, runoff, sublimation, evaporation, erosion are per horizontal area (divided by cos); rain and
solid precip are per slope area. Contracts expose both `*_vertical`/`*_slope_normal` and
`per_slope_area`/`per_horizontal_area` explicitly.

## ADR-005 Initialization policy
Forecasts load `latest_valid`: newest checkpoint with analysis time AND assimilation cutoff <= issue time,
matching terrain version, hash-verified. No checkpoint -> `initialization_required`. `snowagent init`
replays history only from an **explicit** `snow_free` start whose month is in
`initialization.snow_free_months` (default Jul-Oct; adjust per domain, e.g. glaciers). Midwinter bare-ground
starts are refused. Observed-profile or ensemble initial states are designed for (checkpoint lineage
fields) but not implemented.

## ADR-006 Restart equivalence and tolerances
Restarts use SNOWPACK's `-r` (skip first step). Each checkpoint stores the last 6 h of unit forcing so the
PSUM accumulator has data before the restart time without touching post-issue data. Measured continuous vs
restarted (30 days): HS within 4 mm, SWE within 0.01 kg m-2, gridded density mean |d| ~1 kg m-3,
temperature ~0.05 K. Element boundaries of snow deposited *after* the restart can differ slightly, so
element-level identity is not guaranteed across restarts. Test tolerances: HS 1 cm, SWE 1 kg m-2,
density 5 kg m-3, temperature 0.2 K (mean on a 1 cm grid).

## ADR-007 Layer lineage
Engine element IDs are runtime counters not stored in `.sno`, so lineage = deposition time (`.pro 0505`,
persisted in `.sno`) + ordinal. Representative profile = member 0 (actual control member); layer indices
are never averaged across members. Profiles without deposition dates carry `identity_uncertainty`.

## ADR-008 Mass budget
Per column, slope-area kg m-2: dSWE = snowfall + rain - rain_on_bare_ground + sublimation + evaporation
- snowpack runoff - wind erosion (disabled, asserted 0) + lateral transport (unresolved, 0).
Rain while no snow is present is a lower-boundary transfer. Tolerance 1 kg m-2 + 0.5 % of throughput;
observed residuals <= 0.035 kg m-2.

## ADR-009 Numerical fallback
SNOWPACK can abort ("Temperature out of bound", exit 1) at a 15-min step in knife-edge cases (reproduced:
flat 2300 m column, heavy mixed-phase precip under clear-sky ILWR). The adapter re-runs that column once at
5 min and records `calculation_step_min`/`numerical_retry` in diagnostics; a second failure is an explicit
error. SNOWPACK also logs `[E]` lines and exits 0 on missing data, so any `[E]` line fails the run.

## ADR-010 Terrain units
Square blocks (default 1 km) of the DEM inside the boundary (>= 50 % coverage). Slope/aspect from the mean
unit normal; horizons = per-azimuth median over 3x3 interior points (36 bins, 15 km); sky view via Dozier &
Frew. Forest/unknown land cover and slopes > 55 deg are unsupported (explicit reasons). ESRI ASCII + JSON
sidecar (CRS, units required); projected metric CRS only. Unit resolution is reported with each query.

## ADR-011 Uncertainty
Scenario ensemble: member 0 control; members 1..N domain-coherent log-normal PSUM factor and AR(1) TA,
ISWR (relative), ILWR perturbations, seeded by (seed, member). Labelled scenario spread, not probability.
No initial-state perturbation yet (single analysis).

## ADR-012 Field-profile intake (2025-26 upload)
The uploaded profiles are Propagation Labs "Manual Snow Profile" exports (PDF/PNG/JPG). The PDF text layer
holds only the header (time, observer, location, weather, HS, pen., notes); layers, grain forms,
hardness, temperatures and tests are a rendered image. 5 PDFs (FPDF producer) and all PNG/JPG files have no
text. Therefore:
- `snowagent obs inventory` parses headers only, never edits `profiles/`, and writes to gitignored
  `data/interim/obs/` (observer names redacted by default). Every profile has `layers_status=image_only`.
- Layers are NOT machine-extracted. Options in order of preference: (1) structured export from the app
  (CAAML/JSON) if available; (2) human transcription into `templates/profile.example.json`; (3) assisted
  transcription from images with mandatory human review, labelled as transcribed. No option is used
  without the user's decision (CLAUDE.md: data contracts).
- Header times have no zone; converted assuming America/Edmonton (MST/MDT) with
  `time_zone_confirmed=false` until the user confirms.
- QC flags (kept, not fixed): ft->m conversion, missing HS, filename date outside season, duplicate formats,
  location > 1 km from the site median, and identical coordinates shared by different sites (device/home GPS
  captured at data entry, observed for 3 profiles at 51.1908,-115.560).
- Study-plot locations are derived as the median of non-suspect header fixes (>= 3 required for
  "consensus"); written to `config/stations.yaml` marked `location_source: profile_headers_median`.

## ADR-013 Layer transcription from images (user decision 2026-09-30)
User instruction: "read the layers off of the images, use the CAA OGRS to translate the symbols; time
zone is MST; Tak Falls is a study plot without a weather station."
- Readers (vision model agents, or people) follow `docs/transcription/GUIDE.md` and write one
  `transcription-1` JSON per profile file to `observations/transcriptions/<season>/`. Symbols map to the
  IACS 2009 classification used by OGRS (the OGRS PDF itself is not reachable from this environment; the
  symbol key uses snowpyt's MIT-licensed IACS icons).
- Every record carries `transcriber.method`, `reviewed=false`; illegible values are null with
  `uncertain_fields`. `validate_transcription` enforces vocabulary/ordering/physical bounds and flags gaps,
  HS mismatch and partial pits without editing content.
- A random subsample is independently re-read to estimate transcription error before any use.
- Transcribed profiles are evaluation/training data only; they are never mixed with engine output and
  stay distinguishable from structured exports.
- Time zone: fixed UTC-7 (Etc/GMT+7) per the user's "MST"; profiles after DST starts are converted
  with UTC-7 as instructed.

## ADR-014 SnowPro structured profiles (exact data, 1997-2014)
The archive contains ~660 Gasman SnowPro files in three formats (2.1 block format, 3.x INI, SnowPro
Plus XML .prx). They are parsed directly (`snowagent.obs.snowpro`) and labelled `structured:*`,
confidence `exact`; they take precedence over image transcriptions of the same pit.
- Layer convention verified against the matching printout of BS 05 12 27: layers bottom-up, each with
  its top height; first real layer starts at the pit bottom; SnowPro 3 entry 1 (height -1) and SnowPro
  Plus zero-thickness entry at HS are the surface grains, not layers.
- 1990 numeric grain codes are mapped to IACS 2009 (class + standard subclass correspondences). A bare
  class "9" (crust/surface deposit, no subclass) is kept unmapped with `crust_class_unspecified`.
- SnowPro Plus files state `timeZone="Mountain Standard Time"`, consistent with the user's MST ruling.
- Files without an internal date take the file-name date (flagged); disagreements are flagged.
- Numeric Windows short dates are locale-dependent (both D/M and M/D orders occur, some with 2-digit years).
  All valid readings are listed; a single reading is used, several are resolved only by the file-name
  date (exact match, else the unique reading within 7 days, flagged); otherwise `date_ambiguous`.
  (Previously D/M/Y was assumed silently, which swapped day and month in some files, e.g. GE 04 11 09.)
- `*.~PR`/`*.~rx` autosave backups are ignored; byte-identical copies are counted once.

## ADR-015 Vermilion study plot (user, 2026-09-30)
Vermilion is an old study plot, distinct from Simpson; no station. Its location is unknown to the user and
no SnowPro file stores coordinates; recorded elevations vary (2000-2273 m, some "Vermillion Lower"), so the
plot may have moved. No coordinates are guessed. Its 89 profiles (1999-2013) are usable only where a
forcing source can be justified for an explicitly stated location. Older files without a site folder are
assigned by exact in-file site name only (e.g. "Bow Summit" yes, "Bow Summit Ski Hill" no).

## ADR-016 CAAML v5 profiles (niViz exports, 2018-19)
Three `.caaml` files (CAAML v5.0 SnowProfileIACS, `dir="top down"`) are parsed directly
(`snowagent.obs.caaml`, `structured:caaml_v5`, confidence `exact`) instead of being transcribed.
- Heights above ground = HS - depth (layers, temperatures, test failure layers); HS from `hS/snowHeight`.
- The offset in `timePosition` is used; one that differs from MST (UTC-7) is flagged, not corrected.
- CAAML intermediate classes (`P-K`, `D-M`) are kept literally; the hardness index is the midpoint of the two
  classes; an intermediate moisture leaves `moisture` null with the value in `comment`.
- A layer comment starting with a month and day ("Jan 17") also fills `date_tag`.
- `gml:pos` in CRS84 is read as "lon lat".

## ADR-017 Transcription QA (before transcribed layers are used)
Transcribed layers carry reader error, so it is measured, not assumed (`snowagent obs agreement`):
- image vs exact file of the same pit, paired by site + local date only (pairing by layer similarity would
  select the pairs that already agree);
- blind re-reads of a stratified random sample by different readers who do not see the first reading.
Metrics on 1 cm slices over the common height range: HS difference, grain-class agreement, hand-hardness
index MAE, interior-boundary F1 (+-2 cm), persistent weak-layer (SH/DH/FC) recall (+-5 cm), temperature MAE.
Results are reported per source format; a format whose agreement is poor is used only with that error
attached (or not at all) for calibration/evaluation.

## ADR-018 One vertical conversion per record; Avanet pit-bottom axes
Every vertical position in a transcription (layer boundaries, temperatures, test heights) goes through the
same conversion to height above ground (`observed.vertical_conversion`). Before this, depth charts converted
layers but left temperature and test positions as depths.
- Depth charts: height = HS - depth; without HS the record stays in depths (`depth_from_surface`).
- Avanet charts draw the height axis from the pit bottom (top labelled "<snowpit depth> SURFACE", verified on
  2017-02-16 Bow Summit: pit 130, snowpack 165). If the pit is shallower than HS and the top layer is at the
  pit depth, heights are shifted by HS - pit depth (flagged). If HS is "--", the ground is unknown and the
  record becomes depths below the surface (flagged). A pit dug to the ground is unchanged.
Results (2026-09-30, 480 transcriptions, `data/interim/obs/transcription_agreement.json`):
- Image vs exact file, 25 pairs (SnowPro screenshots vs their .PRO/.prx): boundary F1 0.996, grain-class
  agreement 0.996, hardness-index MAE 0.003, HS difference 0, temperature MAE 0.04 C, weak-layer recall 101/102.
- Blind re-reads, 36 pairs (stratified: Avanet 10, SnowPilot 8, Propagation Labs 6, niViz 6, other 6):
  boundary F1 0.99, grain-class agreement 0.996, hardness MAE 0.007, temperature MAE 0.02 C, weak-layer recall
  126/126. Disagreement concentrates in low-resolution phone screenshots (one pair: boundaries up to 12 cm
  apart, F1 0.67) and one pair not comparable (one reader took HS from a ground marker, the other kept depths).
- Caveat: reader-reader agreement cannot reveal a bias both readers share (e.g. the Avanet temperature-axis
  geometry both took from the guide); only the SnowPro screenshots have an exact reference.
Use: printed-value digital charts are used as observations with their flags; phone screenshots and
low-confidence records carry +-1 cm (or worse) boundary uncertainty and are down-weighted in calibration.

## ADR-019 External weather, terrain and forecast sources (user approval 2026-09-30)
User: "you can get hourly weather data history from NOAA ... see if you can get terrain and archived
forecasts on the internet". This session's network reaches AWS Open Data buckets but not NCEI, ECCC/MSC,
Open-Meteo, Avalanche Canada or ACIS (proxy 403), so the README §6 sources stay unimplemented here.
- NOAA ISD Global Hourly (s3://noaa-global-hourly-pds, mirror ends 2025-08-24): the study-plot stations
  are NOT in ISD. Nearest: Banff CS 1397 m, Banff MARS, Yoho Park 1602 m, Nakiska Ridgetop 2543 m, Bow Valley
  1298 m, Golden 785 m (1996-2025). Hourly TA/TD/wind/pressure, 1-6 h precipitation as reported; no radiation,
  no snow depth. Supporting actuals only; ISD QC codes kept, no filling.
- Copernicus GLO-30 DSM (s3://copernicus-dem-30m): 6 tiles, mosaicked and reprojected to EPSG:32611 at 30 m
  (bilinear). A surface model (canopy included), EGM2008 heights. Check: DEM vs station elevations
  2105/2115, 2036/2040, 2168/2200 m; Goat's Eye plot 2282/2282 m.
- Archived forecasts: GFS 0.25 deg (s3://noaa-gfs-bdp-pds, 2021-01 on) point-extracted by byte range from
  the .idx; values bilinear with the model surface height; provenance = URL + byte range + sha256 per
  message (the public archive is the raw record; global fields are not stored). Also reachable, not yet
  ingested: ECMWF IFS open data (2023-01 on) and GEFSv12 reforecast (2000-2019, 5 members, daily 00Z).
- Global models at 25-30 km do not resolve these valleys; they are forcing candidates only after
  downscaling (lapse rates to the DEM) and verification against station actuals.

## ADR-020 FTS360 station records (README §6 primary actuals)
Station hex ids and the request (`/data/v1/agencies/450/records/csv`, Bearer token) come from the user's
Rockies Weather Explorer source. The token lives only in the environment credential for fts360api.com
(the proxy adds the header) or FTS360_TOKEN; never in code/config. Raw monthly CSVs are stored unchanged
with a manifest; the current month is re-fetched until complete. Header names differ by station, so SI
parsing is written after inspecting real headers. Blocked in the session that wrote it (proxy 403);
`snowagent ingest fts360` runs once the credential is active.

## ADR-021 ERA5 reanalysis for 1996-2026 forcing (user approval 2026-10-01)
User: "I give the OK" to ERA5 as the complete hourly source where station records are missing (before
2021-05; radiation always). Read from the NSF NCAR AWS mirror by HTTP range: hourly analysis fields (2t, 2d,
10u, 10v, sp, tcc) are cheap (spatial chunks); forecast mean fluxes (mtpr, msdwswrf, msdwlwrf) are stored
as one global chunk per forecast, ~0.7 GB transferred per variable-month, so the backfill covers Sep-Jun
only. Box 50.5-52 N, 117-115.25 W (7 x 8 cells, 0.25 deg) with cell surface heights; no downscaling or bias
correction at ingest. ERA5 is a 25-30 km model: it is bias-corrected against the FTS360 stations
(2021-2026 overlap) before driving SNOWPACK, and every forcing file records which source each variable
came from.

## ADR-022 Atmospheric stability scheme MO_SCHLOEGL_MULTI_OFFSET (was MO_MICHLMAYR)
In the 2024-01-12 Alberta cold snap (air -40 C, ERA5 wind 1-2 m/s, clear-sky ILWR ~100 W m-2) every plot run
with MO_MICHLMAYR cooled the snow surface to the engine's hard limit (210 K) and aborted, also at 5-min steps
and with MO_HOLTSLAG. MO_SCHLOEGL_MULTI_OFFSET (Schloegl et al. 2017, evaluated for snow surfaces in
stable mountain conditions) completes; NEUTRAL also completes. One scheme is used for all seasons and plots
so the baseline stays consistent: MO_SCHLOEGL_MULTI_OFFSET. The 23-25 K surface inversion it still produces
in that event is a known weakness (wind not downscaled, ERA5 calm), recorded for verification.
ERA5 ILWR is rescaled by (Ta_plot / Ta_cell)^4 so incoming longwave matches the plot air temperature used.

## ADR-023 Forcing/evaluation QC for weighing gauges and snow-depth sensors
- Gauge precipitation: a day is implausible if gauge > 4 x (usual gauge/ERA5 daily ratio) x ERA5 and
  > ratio x ERA5 + 15 mm. Such days are replaced by ERA5 x ratio and labelled `era5_x_<gauge>_ratio`.
  Verified on 10 gauge-seasons: flags only the 2024-03-28..04-10 Sunshine fault (47-97 mm/day while ERA5,
  the Bow gauge and the Sunshine snow-depth sensor show 3-8 mm/day), which had produced a 3.0 m model peak.
- Snow depth: values > 0.30 m from the centred 24 h median are `suspect` and excluded from scoring.
- Usual catch ratios to ERA5 (Oct-May, 2021-26): Sunshine ~1.6, Bow Summit ~0.9.

## ADR-024 Bow Summit precipitation factor 1.15 (first adopted correction)
Leave-one-season-out over 2021-22..2025-26 (factors 1.0-1.8, chosen on the other four seasons by mean daily HS
MAE vs the Bow Summit sensor): factor 1.15 chosen in 4/5 folds (1.30 once); held-out MAE improved in 4/5
seasons, mean 0.163 -> 0.116 m; bias -0.09..-0.24 m -> -0.03..-0.11 m (artifacts/baseline/precip_loso_bow_summit.json).
Interpretation: modest undercatch of the exposed Bow gauge (catch ~0.9x ERA5 vs Sunshine ~1.6x). Applied
only via `snowagent baseline --corrected`; the uncorrected run remains the reference. Profiles were not used
to choose the factor, so profile scores of the corrected run are an independent check.
Same test for the other plots (artifacts/baseline/precip_loso_*.json):
- Simpson (Sunshine gauge transferred ~15 km): factor 1.15 chosen in 5/5 folds; held-out MAE 0.180 -> 0.119 m,
  4/5 seasons better -> adopted (psum_factor 1.15).
- Goat's Eye (its own Sunshine gauge): factor 1.0 chosen in 5/5 folds -> no correction. Factors < 1 not tested.

