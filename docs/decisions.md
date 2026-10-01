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


## ADR-025 ERA5-only forcing for seasons without station records (1996-2021)
Before 2021-05 there are no hourly station records at the plots (FTS360 starts then), so seasons 1996-97 to
2020-21 are forced by ERA5 alone. Two constants per plot are taken from the 2021-26 seasons, where station and
ERA5 overlap, and applied unchanged to the earlier seasons (`snowagent era5-transfer` -> config/era5_transfer.yaml):
temperature offset (station minus ERA5 at the plot elevation, ~+2.0-2.3 K, season range 1.7-2.7 K) and
precipitation catch ratio (gauge total / ERA5 total over gauge-supplied hours: Goat's Eye 1.43, Bow Summit 0.88,
Simpson 1.48 (Sunshine gauge); season range about +/-10%). Humidity keeps the ERA5 dewpoint; wind and radiation
are ERA5 as in every season. The adopted psum_factor (ADR-024) applies on top with --corrected.
Simplest option consistent with the principles: constants, not monthly or weather-dependent corrections, so
1996-2021 is a clean out-of-sample test (calibration 2021-26 only). Verification: observed pits at the plots
and GHCN-Daily snow depth (Sunshine CS 1997-2007 for Goat's Eye; Bow Summit PC/AE 1998-2007). Simpson has
no independent snow-depth record before 2021; its pits (2015-21) are the only check.
User station data for 2015-2021 (Power BI export, pending) would replace ERA5 for those seasons when supplied.
Result (2026-10-01, first run, constant transfer, --corrected): ERA5-only HS is too low at every plot, in the
calibration seasons as well as 1996-2021 (pit HS bias: Bow -15 cm (2021-26) / -13 cm (1996-2021), Simpson -27 / -24,
Goat's Eye -4 / -8; HS sensor bias negative in 14/15 2021-26 plot-seasons). Diagnosis on 2021-26 data only:
(1) the station-ERA5 temperature difference varies by month (0.6 K in Mar/Apr/Jun to 4-5 K in Nov-Jan) and is
smaller during precipitation (~1.5 K) than on average (~2.2 K), so a constant offset warms snowfall hours;
(2) the gauge/ERA5 ratio is higher for sub-zero hours (Goat's Eye 1.57, Bow 0.95) than overall (1.43, 0.88),
because warm-month rain lowers the season total. The constant method is therefore not adopted. A revised
transfer (monthly offsets for wet/dry hours; cold/warm precipitation ratios), still estimated from 2021-26 only,
is to be compared with it by leave-one-season-out within 2021-26 before any 1996-2021 result is used.
Note: the 1996-2021 scores of the constant method have been seen; they are reported, not used for choosing.

## ADR-026 User-confirmed study-plot locations (2026-10-01)
User supplied CalTopo markers ("<plot> Wx Station and Study Plot"), adopted as authoritative over the earlier
profile-header medians:
- Bow Summit 51.70946, -116.47950 (19 m from the derived point; DEM 2037 m, config 2040 m kept).
- Simpson 50.98516, -115.98430 (station and plot co-located; 12 m move; DEM 2105 m, config 2115 m kept, within
  DEM uncertainty).
- Goat's Eye 51.08588, -115.75672: 467 m from the derived point. DEM elevation there is 2190 m, while pit headers
  record 2271-2311 m. Elevation set to the DEM value (README §11: validate against the DEM) and the conflict is
  flagged to the user; the 92 m difference is ~0.6 K through the lapse rate. The marker names a weather station
  at the plot that is not among the FTS360 BYK stations; the Sunshine Village station used so far is 2.0 km W
  (31 m lower by DEM). Asked the user whether that station is the plot station.
User imagery shows Simpson and Goat's Eye as clearings within forest. The plot columns still assume open, flat,
unshaded ground (sky view 1, ERA5 wind); a 30 m DSM cannot resolve a small clearing, so no shelter correction is
invented. This is a recorded representativeness limit for radiation-, wind- and humidity-driven layers (surface
hoar, near-surface facets), to be examined only with evidence (e.g. a stated sensitivity test, not tuning).
No observation protocol exists (user): comparison settings (layer grouping, +-5 cm weak-layer window) are fixed a
priori and reported with a sensitivity range, never chosen by score. Stability tests come from the pit records.
Study-plot daily observations are not available (user). GFS point extracts for Goat's Eye remain at the old point
(467 m on a ~25 km grid; bilinear weights change negligibly).
Goat's Eye station check (user: "the Goat's Eye location may come up with Sunshine AB weather station"): pit HS
vs the Sunshine AB snow-depth sensor (+-3 h): 2023-24 agree within 3 cm (6 pits), 2021-22 pits 5-19 cm lower
(7 pits), 2026-01-12 and 2026-03-25 ~40 cm lower; overall median -5 cm, MAD 7 cm (Bow Summit station vs its pits:
+1 cm, MAD 3; Simpson Lower: +6 cm, MAD 2). Neither confirms nor rules out co-location; the station's FTS360
coordinates (2.0 km W) are kept as recorded and the question stays open. Forcing impact is small either way
(station 2200 m, plot 2190 m by DEM).
Leave-one-season-out comparison (2026-10-01, `snowagent era5-transfer-loso`, artifacts/era5_only/loso): each 2021-26
season run ERA5-only with parameters fitted on the other four seasons, --corrected, user-confirmed plot locations
(ADR-026). Mean held-out HS-sensor MAE: constant 0.217 m, phase 0.167 m; phase better in 12/15 plot-seasons
(Goat's Eye 5/5, Bow Summit 3/5, Simpson 4/5). Pit scores (phase vs constant): |HS| 13.7 vs 14.3 (Goat's Eye),
16.2 vs 17.1 (Bow), 24.4 vs 27.8 cm (Simpson); boundary F1 0.27/0.26/0.27 vs 0.21/0.25/0.15; grain, hardness and
weak-layer scores within +-0.06. Decision: the phase method replaces the constant method for ERA5-only seasons.
Both remain too shallow (pit HS bias -7, -14, -24 cm), so ERA5 still under-delivers plot snowfall; CaSR (ADR-028)
gets the same test.

## ADR-027 Observation-side variability is measured before model-vs-pit differences are interpreted
User ground rule (2026-10-01): do not assume the observer or the model is wrong. Differences between the model
and a pit can come from the model, observer judgement, pit position within the plot, transcription, or the
comparison method. Transcription is measured (ADR-017: negligible). The rest is bounded with observations only:
consecutive pits at the same plot scored with the same metric as model-vs-pit (`baseline/obs_noise.py`). Real
change over the gap is included, so these are upper bounds on pure observation noise. Stability-test failure
heights from the pit records locate weak layers independently of grain-type judgement and are used to check
model weak layers that observers did not classify as persistent. Comparison settings (+-5 cm weak-layer window,
+-2 cm boundaries, same-class/hardness-within-0.5 grouping of model elements) are fixed a priori; results are
reported, not tuned. Signed hardness difference (`hardness_bias_index`) is reported to separate offset from scatter.
First result (1996-2026, 515 pits at three plots): pit vs next pit at the same plot 7-14 days later (164 pairs):
grain agreement 0.63, hardness MAE 0.69 (bias +0.14), boundary F1 0.41, weak-layer recall 0.82, precision 0.81;
0-3 days (6 pairs) and 3-7 days (13 pairs) are similar but too few to separate observer from change.

## ADR-028 CaSR v3.2 reanalysis for seasons without station data (README §6 source)
Network access to hpfx.collab.science.gc.ca enabled by the user (2026-10-01). CaSR (ECCC, ~10 km, hourly,
1968-2024; precipitation from the CaPA analysis, which assimilates gauges) is the README's planned back-cast
source. The tile covering all three plots (rlon211-245_rlat421-455) is downloaded whole per variable/period (files
are chunked one full tile per hour, so range reads save nothing) to data/raw/casr with a manifest; nearest-cell
hourly series are extracted per plot in SI (`snowagent ingest casr`). It must beat ERA5 (phase transfer) by the
same leave-one-season-out test on 2021-24 (CaSR ends 2024) before it is used.
Chance-level check (station-driven 2021-26 runs, old Goat's Eye location; 73 pits): after grouping, model profiles
have 24-31 layers (15-21 persistent-class) vs 8-10 observed layers (4.5-4.7 persistent). With a +-5 cm window,
model persistent layers cover 69-81% of the column and observed ones 58-74%. Test failures fall at a model
persistent layer in 92% / 92% / 53% of cases (Goat's Eye / Bow / Simpson) and at an observed persistent layer in
78% / 90% / 47%, i.e. only modestly above (or below) the coverage expected by chance. Consequence: the weak-layer
recall/precision reported so far (+-5 cm, any number of layers) discriminate poorly and must be reported with
their chance level; they say little about model or observer skill. Required next: a chance-corrected weak-layer
score (hit rate minus coverage, or permutation of layer positions) and alignment-based comparison (DTW,
sarp.snowprofile.alignment, being installed). The difference in layer count (model ~3x observed) is itself a
measured structural difference; which resolution is "right" is not established and is not assumed.
Result (2026-10-01, `snowagent era5-transfer-loso --reanalysis casr|era5 --seasons 2021,2022,2023 --methods phase`):
CaSR nearest cells are 3-7 km from the plots (cell heights 2038/2357/2004 m), no time shift (temperature r 0.96-0.97
at lag 0), but daily precipitation correlates less with the gauges than ERA5 at Goat's Eye and Bow (0.72-0.73 vs
0.80; Simpson equal) and the Goat's Eye cell has < half the Sunshine gauge total. Held-out HS-sensor MAE: CaSR
worse than ERA5 in 7/7 completed plot-seasons (mean 0.287 vs 0.184 m); pit |HS| 20.9 vs 16.6 cm. Two CaSR runs
(2023-24 Goat's Eye and Bow) aborted in the engine (not investigated further). Decision: CaSR not adopted; ERA5 with
the phase transfer remains the source for seasons without station data. Raw CaSR files stay in data/raw/casr.

## ADR-029 DTW alignment similarity as the primary profile score (paper §7.5)
sarp.snowprofile.alignment was removed from CRAN (2026-02-02); the last archived release 2.0.2 (with
sarp.snowprofile 1.4.1) is installed from the CRAN archive and called through r/dtw_similarity.R (JSON in/out).
Package defaults are used unchanged (simType HerlaEtAl2021, 0.5 cm resampling, open end); native-depth similarity
is primary and HS-rescaled similarity is reported alongside (spec: rescaling must not hide HS error). Grain forms
are reduced to IACS main classes plus MFcr/IF. Raw engine layers are compared (no grouping); grouping changes the
result by < 0.005, so the earlier grouping choice does not drive conclusions. The observation ceiling at each plot
is measured the same way (pit vs next pit <= 14 days).
Update (user, 2026-10-01, later): Goat's Eye plot at 51.089530 N, 115.754620 W, elevation 2280 m (pit headers).
The DEM gives 2281 m at this point (5x5 range 2275-2287 m), so location and elevation now agree; the earlier
marker (51.08588, -115.75672, DEM 2190 m) is superseded. The Sunshine AB station is nearby, not at the plot (user);
it remains the Goat's Eye temperature/precipitation source. Goat's Eye ERA5 transfer and runs are regenerated.

## ADR-030 User logger-database exports (2014-2020) as station actuals
The user supplied exports of the same FTS360/BYK logger network (README §6 "User's FTS360 archive", so not a new
source): an all-stations CSV (Dec 2014 - Nov 2018), the full Simpson Lower table (Jan 2015 - Mar 2020, with
humidity) and the Bow Summit gauge as MS Access XML (Mar 2016 - Jun 2019). They are archived unchanged in
archive/byk_export with sha256 and converted (`snowagent ingest byk`) to per-station CSVs with FTS360 column
names, then QC'd by the same code as the API records (`fts360.parse_frame`; `load_station` joins both archives,
FTS360 wins on overlap - there is none, the API starts 2021-05). Choices and checks:
- Time zone: MST (UTC-7) fixed. Evidence: warmest hour 13-16 local, no DST gaps/duplicates; the user's Power BI
  dashboard (same loggers) matches the API exactly after +7 h.
- Station identity: logger names map to the config keys ("Avi - BYK Sunshine Village" -> sunshine_village_ab_env,
  etc.). Not testable by overlap; supported by matching sensor sets and by Oct-May gauge totals (Sunshine 828/816
  mm in 2016-17/2017-18 vs 587-992 mm 2021-26; Bow 604/537 vs 377-677 mm).
- Sentinels 6999 (all fields) and -999 (wind direction) -> missing. Gauge: cumulative PC where present (Bow),
  otherwise the logger's hourly increment H2O_Eq_1hr_mm (Sunshine), with the same bad-increment flags.
- Files overlapping for a station (Simpson Lower, Bow gauge) agree exactly (0 differing values).
Gaps that remain: no humidity except Simpson Lower; Sunshine gauge starts 2015-12-17, Bow gauge 2016-03-22; nothing
for Sunshine/Lookout/Bow station after 2018-11-06 or the Bow gauge after 2019-06-13 until the API (2021-05).

## ADR-031 Hand-hardness relation BELLAIRE (was the engine default MONTI)
Key verified in the installed engine (b324cbd): `[SnowpackAdvanced] HARDNESS_PARAMETERIZATION`, values MONTI (default),
BELLAIRE, ASARC (Stability.cc mapHandHardness). Hardness is computed from the element state for output and stability
indices only; a rerun with ASARC gave an identical snowpack state. Evidence (docs/verification/baseline_2021_2026.md):
model density at pit layers is unbiased (+11 kg/m3) but MONTI hardness is ~1 step soft at matched density and class;
BELLAIRE (continuous A + B*rho per grain class) is chosen in 28/28 leave-one-season-out folds and beats MONTI on
27/28 held-out seasons (hardness MAE 1.27 -> 0.99 on 435 ERA5-era pits, 1.00 -> 0.93 on 73 station-era pits), with
DTW similarity unchanged (+0.008 / -0.003). Earlier run artifacts keep MONTI; saved runs can be rescored with
`hardness_diag.rehardness` (port of the three relations, validated against engine output to 0.05 steps).
