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

## ADR-032 Real terrain domains: Copernicus DEM window, ESA WorldCover land cover, 600 m units, site units
Land cover: ESA WorldCover 2021 v200 (10 m, CC BY 4.0, AWS Open Data), fetched under the user's terrain approval
(2026-09-30, "see if you can get terrain ... on the internet"; the spec defines terrain as DEM + land cover); raw
tile in data/raw/esa_worldcover with manifest. Classes map to contracts.LandCover in ingest/worldcover.py (tree
cover/mangrove -> forest; shrub, grass, crop, wetland, moss/lichen -> open; bare/sparse -> rock; snow/ice ->
glacier; water -> water; built-up -> unknown), resampled by mode to the DEM grid; unit class = majority.
Domain (`snowagent prepare-domain`): a square in UTM 11N centred on a point, snapped to the 30 m DEM grid, DEM
window extended 15 km for horizons; unit blocks align with the boundary. Unit size must be a multiple of 30 m
(1000 m is not); 600 m chosen after inspecting the Goat's Eye 6 km domain (51 supported units spanning all aspect
sectors, 2123-2635 m, slopes 2-39 deg; 1200 m leaves 12). The DSM includes canopy (forest units are unsupported
anyway). Site units (`--sites`): named study plots added as the flat, open, unshaded 30 m column of ADR-026,
listed first so point queries inside them return the site column; they change the terrain version. Needed
because the Goat's Eye plot is a clearing inside a forest-majority 600 m block.

## ADR-033 Forecast-case inputs carry their real availability (no reanalysis after its publication time)
For an archived forecast issued at I (= GFS initial time + 5 h, the assumed dissemination delay):
history = station-first plot forcing with ERA5 fill, declared 5-day latency (ERA5T), so the replay checkpoint is
at the last 00 UTC with ERA5 available (I - 5 d, floored); recent = the same station forcing with the fill taken
from each day's 00 UTC GFS leads 1-24 h (`assemble(..., reanalysis="gfs_day1")`), declared 5 h latency, used to
advance the state to the forecast initial time; forecast = the raw GFS run at the plot's GFS point. Both actuals
series are written past I so `available_by` is exercised; the pipeline refuses later data. GFS is not
bias-corrected: over the 40 days before the first case the station was 3.4 K warmer than GFS day-1 at plot
elevation; a lead-dependent correction is a learned component and needs its own LOSO test.

## ADR-034 Visitor Safety dashboard history (2015-2026) as a third station archive
The user's Power BI dashboard history ("FTS Data Visitor Safety Historical Data") holds hourly records Oct 2015 -
Jul 2026 for six combined areas (two loggers each), including Nov 2018 - May 2021, which neither the FTS360 API
nor the logger exports cover. Its record table is extracted without the station table (credentials) to
archive/fts_dashboard (sha256 of the .pbix recorded; the .pbix stays out of git). Each dashboard column was mapped
to a station only after checking it against the overlapping FTS360/logger records (+7 h, MST->UTC): identical for
every temperature, humidity, wind, Bow/Simpson/Bosworth/Stanley snow depth column; Sunshine temperature 94%, snow
depth 91%, pillow 94% identical; gauge totals r = 1.00 (79-94% of hours identical). Bow Summit humidity is not in
the dashboard (ERA5 fill remains). Precedence in `load_station`: FTS360 API, logger exports, dashboard.
Result: measured temperature and precipitation for all three plots in every season 2016-17 .. 2025-26
(Sunshine gauge Nov-Apr 427-835 mm, Bow gauge 255-559 mm); 2015-16 partial (Sunshine gauge from mid-December).

## ADR-035 Site tool data: season runs with daily restart states and a daily GFS forecast archive
For banff-snowpack.netlify.app (`snowagent.web.build`), each plot-season is run once from a snow-free 15 Sep with
6-hourly profiles and a restart state at 00 UTC every day (`[Output] SNOW_DAYS_BETWEEN = 1`, `FIRST_BACKUP = 0`,
verified in the engine source: backups `<id>.sno<YYYYMMDDHHMM>`; defaults unchanged for all other runs). For
Nov-Apr 2021-26 every day's 00 UTC GFS run is then simulated 72 h from that day's state (6 h of the measured
forcing as precipitation lead-in), giving forecasts at any 6-hourly time with lead <= 72 h. GFS is used raw: the
plot precipitation factor (ADR-024) corrects the station gauge and is not applied to GFS (it was not tested there).
The forecasts' initial states use the measured forcing with ERA5 wind/radiation up to the issue time, which was not
yet published then; the strictly availability-honest chain is the Phase 2 pipeline (ADR-033), whose results this
does not replace.
Measured-weather seasons: Goat's Eye and Simpson 2015-16 .. 2025-26, Bow Summit 2016-17 .. 2025-26 (ADR-030/034/036;
the Bow gauge starts 22 Mar 2016); earlier seasons ERA5 with the phase transfer (ADR-025), daily profiles at 18 UTC.
A simulated profile that fails the layer contract (seen once, in a Bow Summit 2017-18 run with ERA5 humidity: a
1 cm residue at 0.91 degC on 30 May 2018) is left out and listed in the season file with the reason, not altered.
Display layers merge adjacent elements with the same grain form, flags and hand hardness within 0.5
(thickness-weighted density/temperature/water); scores use the same agreement code as the baselines.
Every season file carries a run_id, the engine version, the hash of the configuration actually run (after any
numerical retry) and the sha256 of the SMET forcing the engine read; each forecast carries its own forcing hash and
the run_id of the season run that supplied its initial state; pits are listed by profile_id.
The web data (~100 MB) is not stored in git. (Corrected 2026-10-03, ADR-045: `web-build` regenerates it
only where the ERA5 cache, also not in git, is present, and the daily `update build` only the live season, so
in practice the deployed site holds the only full copy; `snowagent update restore-web` restores it from there.)

## ADR-036 Per-station logger tables (2014/2015 - 2026) from the Visitor Safety station reports
The user's four per-station Power BI reports (Bow Summit 2014-now, Simpson Lower/Upper 2015-now, Sunshine Village
2015-now) contain the full logger record tables of the same FTS network (README §6 "User's FTS360 archive"). Each
record table is extracted once (`snowagent ingest byk --station-report <pbix>`), without the station table
(credentials) and with measurement columns only (no administrative columns such as editor names), to
archive/byk_export/station_tables/<station>.csv.gz with the source sha256, row count and time span in a manifest;
the .pbix files stay out of git (data/raw/byk_station_tables). Re-extraction reproduces the archived tables
exactly. `snowagent ingest byk` merges them with the earlier logger exports (ADR-030): per station, the file with
the most variables wins and the others fill gaps. Checks:
- Times MST (UTC-7) like the other exports; after +7 h Bow/Simpson temperature, humidity, snow depth and wind equal
  the FTS360 API exactly (2021-26) and the earlier exports exactly where they overlap (0 differing values).
- The Sunshine table carries each variable twice under logger aliases (Temp/TA, HS/SD; identical where both are
  set): one canonical name per variable, the alias fills gaps. Its hourly increment (H2O_Eq_1hr_mm) is dropped
  in favour of the 15-min cumulative gauge PC; the "_Raw_" columns (pre-cleaning logger values) are not used.
Effect on plot forcing (15 Sep - 30 Jun): Bow Summit humidity measured instead of ERA5 for 2015-16 .. 2020-21
(5,900-6,900 h per season); Goat's Eye and Simpson 2015-16 get the Sunshine gauge from the start of the season
(+96 mm over ERA5 fill) and become measured-weather seasons; 51 h of Sunshine gauge in Dec 2025 - Jan 2026 that the
API lacks (+12 mm). Remaining gaps: Lookout humidity (Goat's Eye) 2015-18 and most of 2024-25; Bow gauge before
22 Mar 2016; no station record before Dec 2014 (Bow) / Jan 2015 (Simpson) / Aug 2015 (Sunshine).

## ADR-037 Daily update: live season, public reports, dropped-in profiles (user request 2026-10-01)
The user asked for the site to keep evolving: collect weather periodically, search public report and profile
sources for new data, and accept new profiles. This brings forward part of README §9 Phase 8 (operations); its
acceptance (14 unattended days) is not yet claimed. Choices, simplest first:
- Sources. Avalanche Canada MIN (open API) is added to README §6 at the user's request: reports within 15 km of
  a plot, raw JSON archived unchanged per version in `archive/min`, parsed to `obs.public.PublicObservation`
  (no usernames/account ids; distance to each plot). They are context beside the simulations, never engine input.
  Snow Scope (no API key available), Avalanche Lab (shared database only inside its app) and SnowPilot (its
  Cloudflare front returns 403 to automated requests from cloud addresses) are used through their exports: PDF,
  CAAML or photos dropped in by the user.
- Drop-in. A Netlify Forms upload on the site (team login protects it; no new secret), retrieved by the daily
  run; files filed unchanged into the existing `profiles/<season>/...` folders (`obs.inbox`, receipts in
  `observations/inbox/received.jsonl`, no observer names collected). CAAML v5 and SnowPro are read exactly; PDFs
  and photos go through the existing vision transcription (GUIDE.md), marked transcribed. CAAML of other versions
  is kept but not yet read (follow-up: a CAAML 6 parser).
- Live season. ERA5 reaches the mirror ~3 months late, so the current season fills unmeasured variables (wind,
  radiation, gaps) from the GFS day-1 composite (ADR-033) until ERA5 is published; a missing day's run falls back
  to the previous run's 25-48 h leads (counted in the forcing notes). Every day's 00 UTC GFS forecast is stored
  once in `archive/live_forecasts/` and never recomputed; one produced more than a day after its issue time is
  flagged `computed_after_issue`.
- Scheduler. A daily Claude Code routine in this environment running `docs/operations.md`: it already holds the
  FTS360 credential (proxy), the Netlify connector and repository access, and it does the PDF transcription that
  needs a vision model; deterministic steps are `snowagent update fetch|build`. A cron/CI runner can run the same
  commands except transcription and deploy credentials.


## ADR-038 Observation steering: pits update the simulated depth in season (user request 2026-10-01)
The user asked that observed pits both improve the model generally and inform the running season, with explicit
weights per data type. Tested on 156 pit pairs, 11 seasons, weight chosen leave-one-season-out and scored on the
next pit (docs/verification/observation_steering.md).
- In-season update (adopted). At the first 00 UTC after a study-plot pit, the restart state's layer thicknesses
  are scaled toward the pit's snow depth with weight 1 (chosen in 11/11 held-out seasons; next-pit depth error
  15.3 -> 7.4 cm, better in 11/11 seasons; grain agreement 0.473 -> 0.494). No update when the model has < 20 cm.
  The site's measured-weather profile is the steered run; the free run is kept and shown beside it. A profile at
  a pit's own time never uses that pit; forecasts start from the steered restart state (copied, principle 4).
- Structure re-initialisation from the pit: best model grain agreement (0.564) but depth error 12.8 cm; not
  adopted. Persistence (previous pit) still has the best hardness and boundaries, so the observed pit stays on
  screen beside the simulation.
- Weights per data type (current):

  | data | role | weight / how chosen |
  |---|---|---|
  | measured plot weather | drives the engine | 1 where QC ok; gaps filled ERA5/GFS day-1 (flagged) |
  | precipitation | engine input x plot factor | factor chosen LOSO on sensor depth (ADR-024); pit-weighted target tested (below) |
  | plot snow-depth sensor | calibration target | (1 - pit_weight) of the precipitation target |
  | study-plot pit depth | in-season state update | w = 1 (LOSO); OI estimate 0.81 with 7.2 cm pit noise |
  | study-plot pit layers | scoring; re-init tested | 0 in the state (not yet better than depth-only) |
  | MIN reports, test pits | context on screen | 0 (not engine input) |

- General learning. Pits enter the precipitation-factor choice through `calibrate.loso(pit_weight=...)`; the
  factor is adopted only if it improves held-out seasons (principle 3). Results recorded in the verification doc.
  11-season result: Bow Summit and Simpson keep 1.15; Goat's Eye 0.9 is adopted (chosen in 11/11 folds at pit
  weight 0 and 0.5; sensor MAE 16.3 -> 13.0 cm, pit depth error 18.0 -> 10.1 cm). Pit weight 1 alone chose 0.8,
  worse on the sensor; the recorded pit weight for the precipitation target is 0.5.

## ADR-039 Pit updates restart the layering from the pit (supersedes the depth-only update of ADR-038 on the site)
The product goal is layered structure, so the in-season update now takes the pit's layering: thickness, grain form
(-> SNOWPACK microstructure by class medians), hand hardness -> density from the pits' own measured pairs, model
temperatures, densities scaled to the mass the depth update would carry. Against the depth update on 156 next-pit
tests: grain agreement 0.494 -> 0.584 (10/11 seasons), hardness MAE 0.910 -> 0.788 (9/11), boundary F1 0.223 ->
0.282 (11/11); depth error 6.8 -> 8.8 cm (worse in 11/11, still below the free run's 12.1). Trade accepted:
structure over 2 cm of depth. A pit without usable layers falls back to the depth update. Persistence still has
the best layer boundaries (0.348), so the observed pit stays on screen. GFS forecasts already stored in
`archive/live_forecasts/2025-2026` (computed after issue) keep the depth-updated initial states they were made from
(their `initial_state_run_id` says which); they are not rewritten.
Weights table (ADR-038) row change: study-plot pit layers -> state, full weight (replace the column).
Not yet tested: a weight below 1 for the layering (blending the model's and the pit's layers).
Melt-out check (last day with HS > 5 cm vs the plot sensor, 21 seasons with sensor records at melt-out):
pit-restarted run median -1 day (mean -2.4), free run median 0 (mean -1.2); no systematic later melt-out (Bow
Summit 2024-25: +3 days restarted, -3 free).

## ADR-040 GFS forecast correction (constant per plot): tested, not adopted
GFS 00 UTC runs Nov-Apr 2021-26 against the measured plot forcing (gauge hours only, 7566 forecast days; raw rows
artifacts/gfs/gfs_vs_obs_by_lead.csv, scripts/gfs_correction/). At the plots GFS (lapse-rate adjusted) is colder
than measured by 2.1 K (Bow Summit), 3.4 K (Goat's Eye) and 3.9 K (Simpson), the same at every lead; measured
precipitation is 1.02x (Bow Summit), 1.3x (Goat's Eye) and 1.5x (Simpson) GFS, and on days with > 15 mm GFS gives
half. Leave-one-season-out constants (temperature offset; precipitation factor = measured/GFS total) cut the
held-out temperature error from 2.3-3.9 to 1.2-1.6 K in 15/15 plot-seasons, but the precipitation factor did not
reduce run-by-run precipitation error (it inflates false storms).
Engine test, 15 plot-seasons, forecasts from the same (pit-steered) states, scored against the measured-weather
run at the same time (forecasts with a pit update inside their window excluded):

| 72 h snow depth error | raw | temperature | temperature + precipitation |
|---|---|---|---|
| all forecasts (cm) | 5.12 | 5.25 | 4.86 |
| storms, > 10 cm gain (cm; bias) | 12.5 (-11.7) | 14.7 (-14.4) | 12.2 (-11.8) |
| held-out plot-seasons better than raw | - | 7/15 | 7/15 |

Not adopted (principle 3: most held-out seasons). Temperature alone makes depth worse (more settlement). The storm
deficit is a timing/amount problem a constant cannot fix. Next: storm-conditional correction (quantile mapping of
24 h totals) or an ensemble/second model; HRDPS where available. The hook stays (`gfs_correction` per plot,
none configured).

## ADR-041 Sunshine Village webcams as checks (user request 2026-10-02)
The user asked whether Sunshine webcams could help and approved collecting them. Banff Sunshine's own site is not
reachable from this environment (connection refused by the network policy); its cameras are also published through
Windy Webcams with public image URLs: the snow stake (new-snow board with a 0-50 cm stake, 2195 m) and Trappers &
Standish (sky/terrain). Each daily update captures each camera's current and last-daylight image (the update runs
before winter sunrise, so the daylight image is the useful one). Stored only when new and younger than 48 h, so the
off-season placeholder (feeds unchanged since May/June 2026) is listed, not kept; resized to <= 1280 px (~100 kB) to
keep the repository small, with the original's hash and time recorded. Readings (new snow on the board, sky state)
are transcribed by the daily routine into `observations/webcams/readings.jsonl` with nulls rather than guesses.
Use: checks only (gauge/new-snow totals at Sunshine, GFS storm totals, clear-night counts against the radiation
fill), never engine input, until a season of readings is shown to help on held-out data.

## ADR-042 Storm-only GFS precipitation correction (quantile mapping): tested, not adopted
Follow-up to ADR-040 at the user's request. Quantile mapping of GFS 24 h totals to the measured plot precipitation,
fitted per plot and forecast day on the other seasons, applied to all days or only above 5 / 10 mm
(scripts/gfs_correction/storm_qm.py; rows artifacts/gfs/storm_qm_days.csv). Held-out, 7566 forecast days:
storm days (> 15 mm measured) rise from 51 % to 68-72 % of the measured total, but GFS's false storms are inflated
as well: mean 72 h total error 4.44 mm raw vs 4.53-4.65 mm mapped; better in 3-7 of 15 plot-seasons (24 h: 2-5).
Not adopted; no engine test, since the forcing it would feed is worse in most held-out seasons. A single
deterministic GFS run cannot tell a real storm from a false one (day-1 correlation with the gauges 0.79, day 3
0.64), so post-processing it is not enough. Next options: the GEFS ensemble (NOAA open data, same archive family as
GFS; storm probability and spread), or a higher-resolution model (HRDPS; ECCC hosts are blocked here, so it needs
another route). Webcam stake readings (ADR-041) will add an independent new-snow check once the cameras resume.

## ADR-043 Daily update flags stale and failed inputs in code (review 2026-10-03)
A review of the unattended daily update (ADR-037) found inputs that could go stale, be lost or be cut without
anything in the outputs saying so; staleness was judged only by the routine reading status.json. Choices:
- FTS360 replies. A 200 reply with fewer data rows than the month's existing raw file (empty and header-only
  replies included) never replaces it, and the archive sync never replaces an archived month with a raw file with
  fewer rows. The reply is still logged in the manifest (sha256, `data_rows`); the event is a warning in the fetch
  output. Equal or more rows replace the file as before (the current month grows; revised values are kept).
- ERA5 fetch. Only a month the mirror does not have (HTTP 404, or no meanflux file in the bucket listing) is
  `not_yet_available`; any other failure (a failed listing, connection errors that fsspec wraps in
  FileNotFoundError, a broken file) is listed under `errors` with its exception text and is a warning. A month still
  unpublished 122 days after it ended (the mirror's ~3 months + 30 days) is a warning, and an extracted month with
  hours lacking flux values is reported (detected only; not re-extracted automatically).
- ERA5 latency constant. `weather.sources.ERA5_LATENCY_S` stays 5 days: it is ERA5T at ECMWF, the availability
  the Phase 2 cases are declared with (ADR-033: what a real-time chain could know), and their verification numbers
  depend on it. Setting it to the mirror's ~3 months would move each case's analysis time three months back, out of
  the Nov-Apr GFS archive the recent series needs. The mirror's latency is recorded beside it
  (`ERA5_MIRROR_LATENCY_S`, 92 days); a case built for a current issue time with this mirror has an incomplete
  history series, which `write_plot_series` refuses (not filled), and the live site uses the GFS day-1 fill
  (ADR-037).
- GFS runs. The daily fetch now (re-)extracts every run of its 21-day retry window that is missing from the archive
  or incomplete there (fewer points or leads, or unreadable: `gfs_archive.run_complete`, the check `ingest gfs`
  already used); the archive sync still replaces a copy only by a larger extract. Runs of the live season past the
  window are listed as permanently missing/incomplete in the fetch output and as warnings in status.json; they are
  not re-extracted automatically (`snowagent ingest gfs` can redo them).
- Forcing cut. Where the season forcing has an hour with neither a station value nor a fill (live: a GFS gap longer
  than the 2-day previous-run fallback), the season still stops at the hour before (no filling), but the cut is now
  a warning in the build output and status.json and a forcing note: the variables, the gap, the cut time and the
  complete hours after the gap that are not used.
- Staleness in code. `update build` computes each plot station's last record and the latest archived GFS run and
  writes a `warnings` list to status.json (level, source, message, last_record_utc, age_h, most severe first),
  shown as a plain-text banner on the site; the runbook's thresholds are constants: a station more than 24 h behind,
  a GFS run more than 48 h old. Stations checked are those the plots use (config/plot_forcing.yaml: forcing
  variables and snow-depth check), now including Simpson Upper (Simpson temperature/humidity backup). Each station
  message names its role and what is used instead now (e.g. "Lookout supplies Goat's Eye humidity: GFS day-1 fill
  used instead"). Levels: info (expected, no action), warning (degraded or lost input); no warning fails the run.
- Seasonal stations. The user (2026-10-03) says Lookout is seasonal, off for the summer, not retired.
  `seasonal_stations` in config/plot_forcing.yaml lists it with `off_months` June-October (its 2021-26 record:
  summer outages from June, back between mid-September and October); stale in those months it is an info note
  ("seasonal station, off for the summer (expected)"). Outside them it is a warning, because a winter outage (as in
  2024-25) leaves Goat's Eye humidity to the GFS fill. A station listed without `off_months` is always info.
- Not done: fetch-time events (FTS360 replies kept out, ERA5 errors) are in the fetch output only, not carried into
  status.json; the routine reports both lists (docs/operations.md step 6).

## ADR-044 Daily update contains failures, exits non-zero, logs its runs and takes a lock (review 2026-10-03)
The same review (ADR-043) found that one exception stopped the whole unattended run and that nothing outside the
routine's own transcript recorded what ran. Choices:
- Error boundaries. Each source of `update fetch` (FTS360, GFS, ERA5, MIN, inbox, webcams) and each part of
  `update build` (observed set, each plot's season, public reports, index, the status checks) runs in its own
  boundary (`ops.update.Steps`): an exception is recorded as `{"step", "error": "<type>: <message>"}` in
  `failed_steps` and as an `error` warning (source `update:<step>`, with what the failure leaves undone), and the
  run goes on. Inside FTS360 each station is its own step and the archive sync runs either way; a 401/403
  (`CredentialRefused`) concerns the credential, not the station, so it is one failure and the remaining stations are
  listed as skipped. A failed GFS archive sync keeps the fetch output (`archive_synced: false`, the script's stderr
  tail in the error); the runs are redone at the next fetch since the archive still lacks them. The build always
  writes sites.json and status.json; a failed plot keeps its previous build on the site and is an error warning
  there. Results are `ok: false` when any step failed. Nothing is retried or filled in place of a failed step.
- Exit code. `update fetch` and `update build` print the whole JSON result, then exit 2 when any step failed (the
  code the CLI already uses for errors, `_emit_error`) and 0 otherwise; an uncontained crash keeps Python's 1 with
  its traceback. The routine can tell a partial run from a clean one without parsing the output. The rule is a pure
  function (`ops.update.exit_code`); the CLI is tested with typer's CliRunner.
- Run log. Each `update fetch`/`update build` appends one JSON line to `archive/ops/runs.jsonl` (start time UTC,
  command, ok, exit code, duration, failed steps with errors cut to 200 characters, a few counts, warnings per
  level), so there is a history beside the overwritten status.json and it is committed with the raw files (a few
  hundred bytes a run, ~0.3 MB a year). It is written by the CLI wrapper (`ops.update.run_command`), not by
  `fetch()`/`build()`, so library calls and tests do not touch it. A crash outside the step boundaries is logged
  (exit code 1) before it is raised again; a log that cannot be written is a failed step (`run_log`), not a crash.
- Lock. `update fetch` and `update build` share one lock, `data/update.lock` (not committed), created only if absent
  (O_EXCL) and holding pid, host, command and start time; overlapping runs could otherwise interleave writes to the
  manifests, the MIN state and the archive syncs. A second run while it is held does nothing, prints the holder and
  exits 3 (logged). A lock is stale, and taken over with a warning in the result, when it is older than 3 h (assumed
  above any normal daily fetch or build; the run log's `duration_s` will show the real times) or its pid is no
  longer running on the same host (a pid from another host cannot be checked, so only age counts there). The holder
  deletes it on exit, only if it is still its own. Taking over a stale lock is serialised by an `flock` guard
  (ADR-047). Library calls (`fetch()`, `build()`) do not lock; the
  CLI does (`ops.update.run_command`). `update bootstrap` is not locked (it only restores missing files).
- Missed run on the site. status.json gains `stale_after_h.update` (36 h: a daily run missed, with half a day of
  margin) and the site shows a banner, above the data warnings, when its `generated_utc` is older than that (36 h
  if the field is absent), so a missed or crashed daily update is visible to visitors without a separate service.
- Step errors go to the committed run log and the public status.json, so `failure()` masks the value of the
  FTS360 credential (`FTS360_TOKEN`, if set) in the error text; the other sources need no credential.
- Not done: a watchdog independent of the routine (it would need a scheduled job outside it) is the owner's
  decision; nothing was scheduled here.

## ADR-045 Publish only what git and the local site data hold (review 2026-10-03)
The review of the daily routine (ADR-043/044) found that the runbook deployed `web/` before committing and pushing
the raw files, and that nothing stopped a deploy of incomplete site data. Choices:
- Order. Step 5 commits and pushes the raw files and the run log (`archive/`, `profiles/`, `observations/`)
  first and deploys only after the push succeeded; a failed push means no deploy that day. The live season's
  forecasts are stored once in `archive/live_forecasts/` and never recomputed (ADR-037), so with the old order a
  container reclaimed between deploy and push left the site showing forecasts that git does not have. The new order
  can at worst leave the site a day behind git, which the next run repairs.
- Deploy check. `snowagent update check-deploy --web <copy of web/> [--reference <deployed sites.json>]`
  (`ops.deploy.check_deploy`) runs before every deploy and exits 2 with a list of problems; the runbook deploys
  only on exit 0. `web/data` is not in git, `update build` regenerates only the live season and writes
  `sites.json` from the season files present (`web.build.write_index`), so a deploy from a container with
  incomplete `web/data` would silently drop seasons from the site. Checked: the static files; every file listed in
  `data/sites.json` (season, forecasts and public files) is a relative `data/*.json` path, exists and parses; every
  plot of the site is listed and has seasons; `data/status.json` has a `generated_utc` at most 6 h old
  (`DEPLOY_STATUS_MAX_AGE_H`: the deploy follows the day's build, so an older one means no build ran since;
  `--max-age-h` for a deliberate redeploy) and not in the future; no update run holds a valid `data/update.lock`
  (ADR-044). With the deployed site's `sites.json` as reference (downloaded by the routine beforehand; the check
  itself makes no network call): no site or season of the deployed site is missing (which also means no fewer
  seasons per site), no season loses its forecasts or public file, and the local index is not older than the
  deployed one (a container that missed builds would roll the live season back). Without a reference it refuses
  unless `--no-reference` (ADR-047). It reads only; nothing is repaired or deleted. Parsing all ~140 files of the
  current site takes ~3 s.
- Restore. `snowagent update restore-web [--base-url https://banff-snowpack.netlify.app] [--out web/data]
  [--force]` (`ops.deploy.restore_web`) replaces the manual fresh-container step (download every `data/` path of
  the deployed `sites.json`, and `status.json`). It runs only when `web/data/sites.json` is missing (or with
  `--force`), fetches the deployed `data/sites.json`, then every file it lists and `data/status.json` with
  `requests` (no new dependency or service: it is the site's own public data). Each body must parse as JSON before
  it is written, through a temporary file and a rename, so no partial file is left; listed paths must be relative
  `data/*.json` paths (anything else is a failed step, never fetched); a local file is never replaced without
  `--force` (kept files are not re-checked here; `check-deploy` checks them). `sites.json` is written last and
  only when nothing failed, so a rerun resumes an interrupted restore and fetches only the files still missing;
  exit 2 lists the failed downloads. With `--force` (the local `sites.json` removed first, ADR-047) the deployed
  files replace local ones, including a live season just built, so `update build` is run again afterwards. It
  holds the update lock (`data/update.lock`; exit 3 while a fetch or build runs) and is logged in
  `archive/ops/runs.jsonl` (counts: restored, kept). Tests use a fake HTTP getter; nothing in the tests reaches the
  network.

## ADR-046 No durable off-site copy of web/data, the ERA5 cache or engine states (open: owner's decision)
Recorded 2026-10-03 (review of the daily routine, ADR-045). These are derived and not in git, so between deploys
they exist only in the container that last ran:
- `web/data` (~93 MB, ~140 JSON files; ADR-035). `update build` regenerates only the live season; past seasons
  need `web-build` (~90 min) and the ERA5 cache. In practice the deployed site is the only full copy (Netlify's
  deploy history holds earlier deploys, but nothing here manages or tests restoring from it).
- The ERA5 cache (`data/interim/era5`, monthly box extracts). Every past season's forcing uses it (ERA5 fill and
  wind/radiation). It can be re-extracted from the public NSF NCAR mirror (`snowagent ingest era5`), slowly; the
  current daily container holds only the box heights, so `web-build` cannot regenerate past seasons there today.
- Engine states and checkpoints: the daily restart states of the season runs (ADR-035) are made in the engine
  scratch space (`artifacts/web_work`) and deleted after each run; Phase 2 checkpoint stores (`store/` of a
  workspace) are local. Each is rebuilt by rerunning from the snow-free start, which needs the ERA5 cache above.
  The issued live forecasts, which are never recomputed, are in git (`archive/live_forecasts`, ADR-037).
Options, none chosen (where they go, and any paid service, is the owner's decision under CLAUDE.md):
- A GitHub release asset: a tarball of `web/data` (and of the ERA5 cache) uploaded to a release of this repository,
  e.g. once a season for the closed seasons plus a periodic copy of the live one; no growth of the git history;
  needs release-upload rights in the routine.
- A data branch (orphan) in this repository: simple to restore with git, but each full copy adds ~93 MB to the
  repository and the live season's files change daily, so clones grow; Git LFS moves that to a storage quota.
- External storage (an object-storage bucket or a shared drive through a connector): durable and independent of
  the site, but a new service and a credential, possibly paid.
Interim: `snowagent update restore-web` restores `web/data` from the deployed site (ADR-045), and
`update check-deploy` refuses a deploy that would drop seasons, so the deployed copy is never shrunk by an
incomplete container; the ERA5 cache falls back to re-extraction from the mirror.

## ADR-047 Follow-ups to the daily update review (review 2026-10-03)
A second review of ADR-043 to ADR-046 found gaps in what they promised. Choices:
- FTS360 window. The daily fetch requests from the start of the previous calendar month on every day of the month
  (`ops.update.prev_month`), and the archive sync refreshes the current and the previous month at every run. Both
  used `MonthBegin(1)`, which from the 2nd rolls back only to the 1st of the current month (and on the 1st two
  months back), so a run missed or failed on the 1st, or an archive sync that failed then, lost the previous
  month's last hours from `archive/fts360` with no warning. `fetch_station` still stops requesting a month whose
  file exists 2 days after the month's end, so the cost is one more request per station on the 2nd and one gzip
  comparison per station and run.
- FTS360 request errors. `fetch_station` records a non-2xx reply (also after its retries of 429/5xx) or a
  connection dropped on every attempt in the station's `errors` without raising, so these never reached `warnings`
  or `failed_steps` and `update fetch` exited 0 when every station failed. Now each failed request is a warning
  (station, month, HTTP status and reply text), and a station none of whose requests was answered (closed months
  whose file exists are not requested) is a failed step `fts360:<station>`, so the run exits 2 as for a station
  that raised. A month that fails while others answer stays a warning: the station still delivers. A seasonal
  station in its off months (`seasonal_stations`, Lookout in summer; the owner, 2026-10-03: not retired) never
  fails the run: its failed requests are one `info` entry, as its staleness is (ADR-043). A record with an empty
  error text (a 5xx with an empty body) counts as failed.
- A refused credential is `ingest.fts360.CredentialRefused` (a `PermissionError` subclass), and only that skips
  the remaining stations; a `PermissionError` from the file system (a station folder the runner cannot write) is
  that station's failure, and the others are still fetched.
- Deploy check without a reference. `update check-deploy` without `--reference` ran only the local checks, which
  need just one season per plot, so a container that could not reach the deployed site (restore-web failed, and
  the reference download in runbook step 5.2 failed for the same reason) passed a `web/data` holding only the live
  season, and the runbook then said to deploy, dropping every past season from the only full copy (ADR-046). A
  missing reference is now a problem; `--no-reference` (`check_deploy(no_reference=True)`) runs the local checks
  alone, for a person's first deploy of a new site only. The runbook does not deploy on a day the deployed index
  cannot be downloaded. A local completeness rule (every season from a plot's first one to the live one) was not
  chosen: it would need each plot's first season in config and would still pass a folder whose files were
  replaced by truncated ones.
- Forced restore. `restore-web --force` after a build (the runbook's repair when past seasons are missing) kept
  the build's `sites.json` until the end, so after a partial forced restore the documented rerun found it, restored
  nothing and exited 0, and a forced rerun downloaded all ~140 files again. `--force` now removes the local
  `sites.json` first (it is derived: `update build` rewrites it from the season files present), so the index stays
  missing until a restore completes and a rerun without `--force` fetches only what is still missing. Files the
  forced run did not replace stay the local versions; `check-deploy` with the reference checks them.
- Lock takeover. Taking over a stale lock was read, unlink, create: two runs that read the same stale lock could
  both take it over, the second deleting the first one's fresh lock. Creating, taking over and releasing the lock
  now happen under an exclusive `flock` on `data/update.lock.guard` (`ops.update._lock_guard`), held only for
  those file operations and released by the kernel when a process dies, so the second run sees the first one's
  lock and exits 3. The lock file itself (holder, age, staleness) is unchanged, and `check-deploy` reads it
  without the guard. `flock` is POSIX; the routine runs on Linux.
- Run log merges. `archive/ops/runs.jsonl` is appended by every run, so two lines of history that both appended
  (the routine and a development session) conflicted at the end of the file on merge or rebase, and a push that
  needs `git pull --rebase` would stop on it and skip the day's deploy. `.gitattributes` gives it git's built-in
  `merge=union`: its lines are independent JSON records, so both sides' lines are kept.

## ADR-048 CAAML recognised by content; other XML listed as not read (review 2026-10-03)
The inbox (ADR-037) classified an uploaded `.xml` by content and gave a CAAML v5 file the receipt status
`filed_exact`, keeping its `.xml` name, but the observed-set reader (`obs.observed.add_structured`) opened only
`.caaml` (and SnowPro) files, so a CAAML v5 profile saved as `.xml`, a common export name, was filed and never
read, without a flag. Choices:
- One detector. `obs.caaml.xml_kind` (moved from `obs.inbox._xml_kind`, same rule: the CAAML v5 namespace in the
  first 4000 bytes -> `caaml_v5`; another `caaml` mention -> `caaml_other`; else `xml_unknown`) is used by both the
  inbox and the reader, so a `filed_exact` receipt always means the file is read.
- `.xml` and `.caaml` files under `profiles/` are routed by content, not by extension: `caaml_v5` goes to
  `parse_caaml_v5` whatever the name. A `.caaml` that is not CAAML v5 is now listed as not read instead of failing
  in the v5 parser as a `parse_error` record; the three `.caaml` files in `profiles/` (2018-19, niViz) are v5 and
  are read as before.
- Other XML (`caaml_other`, e.g. CAAML v6 from SnowScope, and `xml_unknown`) is kept unchanged and not parsed, but
  never skipped silently: `obs profiles` (and `build_observed`) report `structured_not_read` and a `not_read` list
  (file, sha256, format, reason), and the daily `update build` lists each one in status.json as an `info` entry
  (source `observed:not_read`), also for a file committed straight into `profiles/`. They are not written as
  observed records: with no layers or time they would only add to the observation counts kept in the run log. A
  CAAML v6 parser waits for the owner's word on whether it changes the data contract.
- Alternatives not chosen: try the v5 parser on every `.xml` and treat failures as parse errors (the reason, "CAAML
  v6, no parser", would be lost in an exception name); rename filed `.xml` files to `.caaml` (raw files are
  immutable and the receipt's `filed_as` would no longer match).
On 2026-10-03 `profiles/` holds no `.xml` file (3 `.caaml`, all CAAML v5) and no inbox receipts exist, so the
observed set is unchanged (`observed_profiles.jsonl` byte-identical before and after); no model output or
verification number changes.

## ADR-049 Printed date and site name of a transcribed pit checked against its filing (review 2026-10-03)
The structured-file path flags a file date that differs from the filename date (`file_date_<d>_differs_from_
filename_<d>`), but the transcription path used the printed date (`header.date_local`) without comparing it with
the filename/inventory date, and never compared the printed site/location name with the folder's study plot, so a
pit printed a year later than its filename, or a "Wawa Test Profile" filed under Simpson, passed without a flag.
Choices:
- Date: `printed_date_<printed>_differs_from_filename_<filename>` when both exist and differ. The printed date stays
  the observation date (as before): which of the two is right is the owner's call, per pit.
- Site: `obs.site_names.printed_site_flag`, conservative. A printed name is flagged only when no run of its words is
  a name of the folder's plot (key, `study_plots` name, `site_aliases`, and the new `printed_site_names` list in
  `config/observations.yaml`; spelling slips accepted at a 0.85 similarity for names of 5+ letters) AND it names a
  place, i.e. has a word that is not generic (study, plot, profile, a province, an elevation band, a month; digits
  are dropped). Flags: `printed_site_name_not_folder_plot:<plot>:<name as printed>`, or
  `printed_site_name_is_other_plot:<plot>-><other plot>:<name>` when it is a name of another study plot. A name
  that matches both the folder's and another plot is not flagged.
- `printed_site_names` holds names the printed fields use for a plot that are not folder aliases: Goat's Eye
  "goats", "ge", "ssv", "sunshine", "shot plot" (they occur with the plot's name, e.g. "SSV Goat's Eye Study Plot",
  "Goat's Eye Shot Plot", "Sunshine Goat's Eye Study Plot") and "brewster rock" (Avanet's place label, "Brewster
  Rock, Alberta", on a 2015 pit 0.45 km from the plot's median location, as close as pits printed "Goat's Eye Study
  Plot"); Bow Summit "bow pass" (the app's place name, "Bow Pass, Alberta", 9 pits); Tak Falls "tak". It is kept
  apart from `site_aliases` because those also classify folders and assign structured files to sites; this list is
  used only for the flag. The owner can move a name out of it.
- The pit keeps its folder's site, its observation time and its place in de-duplication: the new flags do not use the
  `site_folder_differs` prefix that `mark_observation_duplicates` ranks on, so the copy kept is unchanged. Transcribed
  records gain `site_name_as_written` (the printed name; the key structured records already have).
On 2026-10-03 data: 33 transcribed pits carry a date flag (22 filed under a study plot, 20 of them not duplicates)
and 4 a site flag, all under a study plot: Wawa Test Profile (Simpson), National Geographics, Observation Glades
TL and Below Bow Peak "West Nile" at treeline (Bow Summit). The observed set is otherwise unchanged (same 1133
records, ids, times, sites and duplicates; `obs profiles` statistics identical), so no model output or verification
number changes. The opt-in exclusion is ADR-050, the review list ADR-051.

## ADR-050 Exclusion of flagged pits from steering and scoring (owner ruled 2026-10-03: on)
`location_qc` was written to the observed records (inventory GPS flags, distance from the site's median location)
but read nowhere: the pits that steer the site runs (`learn/steer.py`, ADR-038/039) and the pits that are scored
(`baseline/evaluate.py`) are chosen without it, and the printed date/site flags of ADR-049 are new. Whether such a
pit should still steer or count is the owner's call, pit by pit, so nothing changes by default. Choices:
- One switch, `exclude_flagged_pits_from_steering_and_scoring` in `config/observations.yaml` (with the other
  observation QC settings), default `false`; any value other than true/false is an error. "Flagged" =
  `obs.observed.review_reasons`: any `location_qc` entry, or a flag starting `printed_date_` or `printed_site_name_`.
  The structured path's `file_date_..._differs_from_filename_...` is not included (the date there is the app's own
  record; the filename is typed by hand).
- Where: `baseline.evaluate.pits_at_plot` (behind `observed_at_plot`, the one selection used by the site build for
  steering and scoring, the `baseline` command, hindcast, calibration and the diagnostics) and
  `learn.steer.update_pits` (the update-pit selection `steered_run` made inline, now a function; same rule: the
  last pit before each 00 UTC update time wins). Both read the switch when not told explicitly.
- Flag, don't delete: with the switch on, excluded pits are returned with their reasons; the site build writes them
  to the season file as `pits_excluded` (and counts them in its summary), and `snowagent baseline` and calibration
  (each grid row) list them per plot-season, all through `baseline.evaluate.plot_pits`. Hindcast and the steer
  experiment keep the profile_id of every pit they use, and the hardness and observation-noise diagnostics re-select
  the pits of the baseline results they read, so they do not list exclusions themselves. With the switch off these
  lists are empty and the outputs are unchanged.
- With `false` the selection is the same as before (tested against the previous inline rule), so no model output or
  verification number changes.

Decision (2026-10-03, Ben in the project thread, on the 39-pit review list of ADR-051: "disregard these pits"): the
switch is `true`. Every study-plot pit with a `location_qc` entry or a printed date/site flag is kept in the observed
set and on the site as an observation, but neither steers a site run nor counts in scoring; the season files list
them as `pits_excluded`. 22 pits came out of 19 season files (21 that had steered a run, 2015-16 to 2025-26, plus one
scored-only location-flagged pit); those 11 seasons were rebuilt and deployed 2026-10-04. ADR-038/039 and the
precipitation-factor LOSO re-run on the remaining 136 pairs hold (re-initialisation grain 0.483 -> 0.568, hardness
0.913 -> 0.771, boundary F1 0.222 -> 0.277, depth 7.0 -> 9.0 cm; factors 0.9/1.15/1.15 unchanged), see
docs/verification/observation_steering.md. Pit-by-pit rulings (a corrected date, a pit confirmed at the plot) can
still be given later: a pit loses its flag at its source (the transcription record or the inventory QC), not by an
exception list.

## ADR-051 Review list of flagged study-plot pits (`obs flagged-pits`)
The owner rules on flagged pits one by one, so he needs them in one table with what each flag says and whether
the pit changes a site run today. `snowagent obs flagged-pits` reads the observed set and writes
`flagged_pits.csv` and a short `flagged_pits.md` (default `artifacts/pit_review/`, gitignored: derived, rebuilt
in seconds). Choices:
- Rows: every pit filed under a study plot (`category` study_plot) with `review_reasons` (ADR-050). Duplicates are
  listed and marked (`duplicate_of`), and a kept pit names its other copies (`other_copies`), e.g. the Simpson copy
  of the Wawa pit and its Test Profiles copy.
- Columns: profile_id, site, flag types (date/site/location), printed date (the observation date used) and filename
  date (from the date flag when there is one, else `parse_filename_date`), printed site name, location_qc, the flag
  texts, whether it steers a site run (run, update time), the update recorded in built season files, duplicate
  links, unusable, transcription confidence/reviewed/method, folder and source file.
- "Steers a site run" uses the site build's own selection (`pits_at_plot` and `update_pits` for the measured-weather
  seasons of the three site plots, season dates from `config/plot_forcing.yaml`) with the configured switch. Two
  conditions need the run itself (a season cut by incomplete forcing; no update when the model holds < 20 cm), so
  `--site-data` (default `web/data`, read only) adds what the built season files record for each pit.
- Nothing is changed or excluded by the command.
On 2026-10-03 (observed set of ADR-049, switch false): 39 flagged study-plot pits (37 not duplicates), 21 of which
steer a site run: printed date 22 pits (11 steer), printed site 4 (1 steers: the Wawa pit, Simpson 2021-22),
location_qc 16 (11 steer, among them the 17.6 km Wawa pit and the 6671 km Goat's Eye pit 2019-03-24). The rule
agreed with the live site's built season files on all 21 (each recorded as a layer update) and none of the other 18
appears there. An earlier review counted 18 date conflicts, 14 at study plots and 7 steering; this check counts
every transcribed pit whose printed date differs from its filename date (33; 22 under study plots, 20 of them not
duplicates), and the table lists each one.

## ADR-052 The inbox's upload-date prefix is not an observation date (review 2026-10-03)
The upload form's date is optional. With no form date and no date in the original name, the inbox (ADR-037) files
the upload as `<upload date>_<name>` and flags the receipt `observation_date_unknown_upload_date_used_for_filing`.
That receipt lives only in `observations/inbox/received.jsonl`, which the observed-set build did not read, so the
upload date was taken for a filename date: a pit uploaded a day or more after it was dug got a false
`printed_date_..._differs_from_filename_...` (a review reason, ADR-050) and a CAAML v5 upload a false
`file_date_..._differs_from_filename_...`. Choices:
- `build_observed` reads the receipts (`obs.inbox.upload_dated_files`, default `obs.inbox.RECEIPTS`). A file whose
  sha256 and filed name match such a receipt gets the flag `filename_date_is_upload_date`, and its name's date is
  not compared with the printed or file date. When the profile has no date of its own the upload date is still
  used (as before, `date_from_filename`), now with that flag, so the record says which date it is. Neither flag is a
  review reason.
- Filing is unchanged: renaming or not prefixing uploads would change the inbox's naming rule and the receipts'
  `filed_as` for files already filed, and the season folder still needs a date.
On 2026-10-03 there are no inbox receipts, so the observed set is unchanged.

## ADR-053 Phase 0 gate: one line branch, CI on GitHub Actions, setup script (2026-10-04)
README §9 accepts Phase 0 when `snowagent doctor` passes, the SNOWPACK example runs and the tests are green.
`docs/progress.md` recorded it done on 2026-10-01, but the suite was green only in the development container:
a fresh `pip install -e .[dev]` lacked scipy, which pandas needs for `Series.corr(method="spearman")` in the
Phase 2 acceptance checks (`forecast/acceptance.py`), so `test_phase2_acceptance_checks_pass_on_a_clean_run`
failed; nothing ran the tests outside that container; `scripts/build_snowpack.sh` ended with status 1 after every
successful build because the engine's `-v` exits 1; and a fresh container was set up from prose. Choices:
- One line branch: `main`, the owner's choice (it now exists on GitHub). The repository had been worked on
  `claude/...` branches; `main` is the branch the site and the daily update are to follow, named in the workflow
  (`on.push.branches`, one line in `.github/workflows/ci.yml`) and in `docs/project-brief.md`.
- CI on GitHub Actions: the repository is public, so runner minutes are free, and `ubuntu-latest` (4 vCPU, cmake
  and g++ preinstalled) builds the pinned engine in about 5 minutes. Two jobs. `unit`: Python 3.11 with a pip
  cache, `pip install -e .[dev]`, `ruff check src tests`, `pytest -q tests/unit`. `integration` (after `unit`):
  the installed engine prefix and the upstream test fixtures (`Source/snowpack/tests`, the MST96 example) are
  restored from the Actions cache, keyed on the pinned commit read from `scripts/build_snowpack.sh` and the
  script's hash; on a miss the script builds under `$HOME` (`/opt` needs sudo on the runner) and the build is
  saved to the cache straight away, so a failing test does not discard it; ~120 MB cached, the source tree and
  build directories not. `SNOWPACK_BIN` (the variable `engine.snowpack.find_engine` reads) points
  at the cached binary, whose RUNPATH `$ORIGIN/../lib` makes it relocatable; `/opt/snowpack-src` is a symlink to
  the cached copy because `tests/integration/test_engine_physics.py` reads the upstream example from that path.
  `snowagent doctor` runs before `pytest -q tests/integration`, so a missing engine fails the job instead of
  skipping every engine test. Pushes to the line branch, pull requests and manual runs trigger it; a newer run on
  the same ref cancels the running one.
- black: CLAUDE.md lists `ruff + black`, but 83 of 107 Python files are not black-formatted, so CI runs ruff only.
  A repository-wide reformat is one separate commit for the owner to approve; until then black is not enforced.
- scipy declared in `pyproject.toml` (`scipy>=1.11,<2`); it was an undeclared transitive need.
- `scripts/build_snowpack.sh` ends by checking that the installed binary runs and reports its version (`-v`
  exits 1 by design, so its output is checked, not its status) and exits 0.
- `scripts/setup_env.sh`: idempotent fresh-container setup (`.venv` if absent, `pip install -e .[dev]`, engine
  build only when `find_engine` fails, `snowagent doctor`; `--data` adds `snowagent update bootstrap` and, when
  `web/data/sites.json` is missing, `snowagent update restore-web`). It replaces the prose of runbook section 0
  and is meant as the cloud environment's setup command (set by the owner in the environment settings).
Not covered: the Docker engine image stays untested (ADR-002); R (`sarp.snowprofile.alignment`) is not installed
on the runner, so `test_agreement` skips there as it does locally; the workflow has not run on GitHub yet
(no push from the session that wrote it), so its first run is the check of this ADR.

## ADR-054 Season lifecycle: rollover at the configured start, finished seasons rebuilt once ERA5 is complete, as-issued forecasts preserved (review 2026-10-04)
Three gaps in how a season begins and ends on the site:
- `web.build.current_season_year` took the new season from 1 September, while a season starts on 15 September
  (`season_start` in `config/plot_forcing.yaml`, read by `season_forcing`; the hard-coded `09-15` of the update's
  GFS window and gap check agreed). From 1 to 14 September `update build` built a season whose forcing had not
  begun and failed, and the GFS fetch window began in the future (nothing fetched).
- A season keeps `mode` "live" when the ERA5 months of its last weeks were not published at its last build (the GFS
  day-1 composite fills the tail, ADR-037). On 2026-10-04 the three 2025-26 files are live with the nowcast ending
  2026-05-31 (ERA5 2026-06 not on the mirror yet). `fetch_era5` requested only the current season's months and
  nothing rebuilt a finished season, so it would have stayed on its live build for good.
- The forecasts stored as issued (`archive/live_forecasts`, ADR-037, ADR-039) were loaded only while `mode` was
  "live", so a rebuild on the full forcing would have recomputed every Nov-Apr forecast (no longer as issued) and
  dropped the stored forecasts of the other months.
Choices:
- Rollover at the configured start: `current_season_year(now)` switches at `season_start(now.year)` (new helper,
  default `09-15` when the config is not at hand, e.g. an import outside the repository), and `season_start(y)`
  replaces the hard-coded date in `update fetch` and `update build`. From 1 July to 14 September the season in
  progress is the one that just ended: the daily build completes it (its forcing stops at 30 June), the GFS fetch
  keeps archiving the daily runs through the summer as it already did in July and August, and the first build on or
  after 15 September starts the new season. Rejected: a separate off-season with no build, since the daily build is
  what completes the finished season and keeps status.json current.
- The build time is an argument: `build_season`, `build_all`, `write_index` and `_issued_store` take `now` (the
  wall clock by default) and pass it to `season_forcing`, the live block, `current_season_year` and a stored
  forecast's `produced_utc` / `computed_after_issue`; `update build` passes its own and stamps `status.json` with
  it. With `now` given, these paths read the clock nowhere and are tested on fixtures. `STATION_SEASONS` and
  `FORECAST_SEASONS` still end at the season in progress when `web.build` is imported (lists of seasons), so a
  build tests only their first year. `write_public` files a MIN report by the same rule: one observed 1-14
  September is in the previous season's `_public.json` (before, the new season's).
- Finished seasons: `update fetch` also requests the previous season's ERA5 months while any of them is missing from
  the cache (`fetch_era5` is bounded at the season's June; result `era5_previous`, its months counted with the
  current season's in the run log). `update build` then, after the live season, checks each plot's previous season
  file: while its `mode` is "live" and every ERA5 month September-June is cached, the season is rebuilt once with
  `build_season` in its own step boundary (`season_final:<plot>`) and leaves live mode; otherwise the missing months
  are reported. If the rebuilt forcing is still incomplete (e.g. an ERA5 month with flux gaps, which `update
  fetch` reports and never re-extracts), the file stays live with its cut as a `warning` and is rebuilt again at
  each build until the forcing is complete or a person rebuilds it. The result lists them under
  `finished_seasons` (site, season, `rebuilt`, reason, the build's counts and warnings) and `status.json` carries
  one `info` note per season naming the plots. Only the previous season is checked: older seasons were built from
  the full forcing by `web-build`. Rejected: rebuilding the finished season at every daily build until ERA5
  arrives (engine time for an unchanged result; the live build is right until then) and rebuilding from `update
  fetch` (the build owns `web/data`).
- As-issued forecasts survive the rebuild: `web.build.forecast_issues` loads the stored forecasts first whenever the
  plot-season's store exists, whatever the mode, with one issue per day for such a season; only issues without a
  stored forecast are computed, and those are stored once like a live season's (`computed_after_issue` true, so
  they are not taken for as-issued). Stored forecasts are never rewritten (ADR-037, ADR-039). The completed
  season's file keeps `mode` "station", has no `live` block (the GFS gap check and the live block are the current
  season's only) and its forcing notes say `completed on <date> from the full forcing ... after the live season;
  the N forecasts are those stored when issued`.
Expected on current data: the 2025-26 files at the three plots complete at the first `update build` after ERA5
2026-06 reaches the mirror (about three months after the month's end); until then the build reports them as still
live with the missing month. The run log's counts are unchanged (the rebuild is in the build output and
status.json). `web-build` of a season that was once live now also shows its stored forecasts. Runbook: sections 2,
4 and 6 of `docs/operations.md`.

## ADR-055 Snowpack Agent Lab: a module of this repository, SNOWPACK the incumbent
The owner asked (2026-10-05, "run it as a new module") for the build guide's "Rockies Snowpack Agent Evolution Lab": a
local benchmark in which snowpack-prediction agents predict the observed pit at Bow Summit (BOW), Goat's Eye (GOAT)
and Simpson (SIMP) from the data available at a cut-off, are scored against withheld pits, and are evolved through a
typed genome. Plan: `/mnt/project-files/plans/agent-lab-plan-2026-10-05.md` (owner's project files). Choices:
- A module, not the guide's separate `snowlab` repository: `src/snowagent/lab/` (schemas, ingest, storage,
  services, agents), CLI `snowagent lab ...` in the existing Typer app, Streamlit UI in `lab_app/`. It reuses the
  observed-profile set (`obs.observed`), the QC'd station records (`ingest.fts360.load_station`), the plot
  coordinates and season start (`config/plot_forcing.yaml`, not copied), and later the next-pit test and steering
  scores (ADR-038/039) and the GFS hindcasts.
- SNOWPACK, as the site runs it, enters as the incumbent agent from the start; the guide's "disabled placeholder"
  adapter is not used. Promotion into the daily site still needs the incumbent beaten on held-out seasons
  (CLAUDE.md principle 3).
- Layers made by the lab's rule, analogue or hybrid agents are benchmark entries only, never site output: the site
  keeps publishing SNOWPACK structure (CLAUDE.md principle 1). Nothing in the daily run or `web.build` imports the
  lab.
- Optional `lab` extra (streamlit, plotly, pyarrow, scikit-learn; free PyPI packages). The core package imports none
  of them; lab code imports them when a table is read or written or the UI runs (tested by importing the CLI with
  them blocked).
- Generated data live in `data/lab/` (gitignored) and can be deleted and rebuilt; inputs are only read. The UI runs
  locally (the owner's Mac); no cloud service, telemetry or key.
- Milestones: 1 foundation (this ADR's change), 2 benchmark harness, 3 first competition, 4 evolution, 5 extensions
  (SNOWPACK settings as genes, ML only after the harness works). Each is a PR with tests, changelog and ADR.

## ADR-056 Lab data contracts (new, additive)
The lab's pydantic contracts (`snowagent.lab.schemas`) are new; no existing contract or file changes, and nothing
outside `snowagent.lab` reads them. The owner approved building the module; adaptations of the guide's schemas:
- Units SI with the unit in the field name (CLAUDE.md), converted only in the UI: depths and snow depth in metres
  (`top_depth_m`, `snow_depth_m`), air temperature K, humidity fraction, wind m s-1, precipitation and SWE mm
  (kg m-2); snow temperature deg C, grain size mm and density kg m-3 as in `contracts.Layer`. Times UTC, naive times
  rejected. The genome's "temperature_bias_c" is `temperature_bias_k` (same size).
- Profiles: depth from the surface, 0 at the surface, increasing downward, layers ordered surface to ground. Hard
  errors only for what cannot be meant as written: bottom not below top (zero or negative thickness), negative depth,
  layers out of order, unknown codes in normalized fields. Incomplete history (no HS, gaps, overlaps, unknown grain)
  is a `validation_warnings` entry. The observed record is kept unchanged in `raw` (profile and each layer), and its
  flags, duplicate link and review reasons (ADR-050) are carried, not acted on: the benchmark decides what to use.
- Weather: one record per site, hour and source set. Each variable names the station that supplied it and its QC
  flag; values are as measured at that station (no elevation transfer, no fill). A value that failed QC is null and
  flagged `bad` (the raw file keeps it); `missing` is null with nothing reported. The record's `quality_flag` is
  the worst variable flag. Recipes: the plot's forcing lists (`ta`, `rh`, `psum`, `hs_check`, `swe_check`) in
  order, wind from `config/lab.yaml` (Bow Summit station; Lookout for Goat's Eye; Simpson Upper). Radiation and
  pressure are not measured at the plots and stay null. Reanalysis and forecast weather come in milestone 2 as
  other source sets (`kind`, `issued_at`).
- Availability: no input records when a pit or value became available, so `source_recorded_at` is null and
  `availability_assumption` is `observed_at` (the guide's prototyping rule, shown as a warning). The pit
  availability delay is a milestone-2 assumption.
- Prediction contract: quantiles p10 <= p50 <= p90, non-negative depths, bottom below top at every quantile,
  probabilities and confidence in [0, 1], layers ordered by p50 top depth, explicit `insufficient_data` with a
  reason. Every prediction carries the decision-support label.
- Visible/hidden separation by type: `VisibleBenchmarkCase` has no field for a target profile or hidden truth
  (unknown fields rejected) and refuses any record not available at as-of (weather by availability, forecasts by
  issue time, profiles and observations by observation and availability time); `HiddenTruth` is evaluator-only.
- Genome: typed and bounded genes (`gene_bounds` is the allow-list), per-site genes for all three sites, ensemble
  weights summing to 1, non-zero exactly for the enabled modules.
- Run manifest: run_id, kind, status, config hash (lab config with plot coordinates), data hash (over input file
  hashes), software version, git commit, SNOWPACK version when used, seed, frozen scoring weights (required for
  scored runs), profile_ids used, inputs with sha256. Stored write-once in a SQLite registry (`run_manifest` table,
  stdlib `sqlite3`) and as JSON beside the data.
- Season key `YYYY-YYYY` from the project's 15 Sep season start (`config/plot_forcing.yaml`), not the guide's
  1 Oct, at 00 UTC like the site's season windows. Split seasons are left empty for the owner to choose; a season in
  two splits is a configuration error.

## ADR-057 Lab: layers of concern from grain class (one reviewable table) and observer tags
The guide scores critical layers (surface hoar, facets, depth hoar, melt-freeze or rain crusts, human-marked layers)
and asks for one controlled mapping of grain codes to broad classes. The pits carry no "layer of concern" mark
(1,577 layers have free-text comments, not read). Choices (`lab.ingest.mapping`):
- One table, IACS 2009 code -> class: SH* surface hoar; FC, FCso, FCsf, FCxr facets (FCxr kept with facets, as the
  project's `PERSISTENT` set does); DH* depth hoar; MFcr, IFrc, IFsc, IF, IFil crust. Everything else is "other"
  (IFic ice columns and IFbi basal ice included), no grain form is "unknown". The primary form decides; the
  secondary only when no primary form was recorded (a transcriber's uncertain primary), marked `:secondary`.
- A layer is of concern when its class is one of the four, or when the observer gave it a name or date tag
  ("Nov crust", "Jan 24"): observers tag the layers they track. Each layer stores its class and the basis
  (`grain_class:<class>:<form>`, `observer_tag:<tag>`), so a later owner mark is another basis, not a rewrite.
- On the observed set of 2026-10-04 this marks 3,053 of the 4,703 placed layers at the three plots (facets 2,122,
  depth hoar 412, crust 336, surface hoar 147; Rockies snowpacks are faceted). Whether rounding facets (FCxr) and a
  secondary facet form on rounds should count is a question for the owner; the table is the one place to change.
- Mapping lab layers to the classes says nothing about instability; it is a structure label for scoring.

## ADR-058 The evolving forecast agent is the primary purpose (owner, 2026-10-05)
Ben, in the project thread: "this will be the primary purpose of this tool going forward is evolving this agent to
best ... accurately predict snowpack structure" and "redirect all work moving forward to this objective". Choices:
- The Snowpack Agent Lab (ADR-055..057) is the product's centre. SNOWPACK plot runs stay in daily use as the incumbent
  agent and the site's simulation; the daily update keeps running because it supplies the cases (pits, station
  weather, archived GFS runs) and the prospective 2026-27 season.
- An evolved agent reaches site output only through the existing gate: it beats the incumbent on held-out seasons
  (principle 3) and passes physical checks (principle 1). Until then the site shows SNOWPACK and labels the lab as
  research.
- Work is ranked by how much it helps evolve and verify the agent: lab milestones (benchmark cases and leakage checks;
  agents and scoring; evolution; SNOWPACK settings as genes) first, plus what keeps the case data flowing (CI, the
  season lifecycle, pit availability times). Terrain-domain work (rolling domain checkpoints, domain uncertainty,
  daily terrain forecasts, terrain realism) is parked unless it feeds the agent.
- CLAUDE.md's product goal and the site's opening section say so.


## ADR-059 Lab benchmark cases: one anonymous case per pit, split modes, assumed availability (2026-10-05)
Milestone 2 of the lab (ADR-055) builds the cases an agent is evaluated on (`snowagent.lab.benchmark`,
`snowagent lab build-cases`, protocol in `docs/lab/benchmark_protocol.md`). The guide's case design is adapted
twice by the owner. Ben, 2026-10-05 03:19 UTC: "why dont you evolve the agents on all season, and pits? ... One
training round should be taking the historical weather forecasts and weather actuals before every observed pit for
all seasons." Ben, 03:22 UTC: "we need to ensure agents just dont memorize these snowpacks." Choices:
- **Case types.** `forecast_h72` (the training case): as_of = pit time - 72 h; visible are the season's measured
  weather up to as_of, earlier pits available by then and the latest archived GFS run available at as_of (issued at
  most 24 h before). `next_pit`: as_of = availability of the previous usable pit with layers at the plot in the same
  season (the anchor); the weather from as_of to the pit is the measured record given as a forecast issued at as_of
  (`perfect_forecast` hours), so the case isolates the snowpack step from forecast error. One case per usable pit and
  type, at all three plots.
- **No archived forecast** (GFS archive: 2021-22 onward, and none without the plot's point): measured weather stands
  in, labelled `forecast_source: measured_standin` in the visible header and the manifest, with snow depth and SWE
  withheld from the stand-in hours (they would give the answer away). A stand-in needs measured temperature and
  precipitation for at least half of its hours, else the pit is excluded (`standin_weather_coverage_below_min`).
  Builds report archived vs stand-in counts per plot and season. `next_pit` cases are always stand-ins.
- **Availability** (the guide's rule, record visible only if available ≤ as_of). Nothing records when a pit or a
  value was published, so the builder stamps assumed delays and marks them `assumed_delay`: pits and their tests
  observed + 24 h (**provisional**, the owner to confirm), station hours + 1 h, ERA5-filled values + 120 h (the 5-day
  latency of ADR-043; younger ERA5 values are withheld, not shown), GFS runs issue + 5 h. The rules are in
  `config/lab.yaml` (`benchmark.availability`) and written into every manifest.
- **GFS tail gap.** The archived runs are 00 UTC with leads to 72 h, so with as_of = pit - 72 h the latest available
  run usually ends before the pit (real data: all 72 archived cases, 7-22 h, median 19 h). Kept as specified, the gap
  is a case warning; the owner may prefer "the latest run whose leads reach the pit" (open question).
- **Split modes** (`splits.mode`). `all` (default, the owner's 03:19 message): seasons 2015-16 to 2025-26 are all
  training, nothing is sealed, no provisional-split warning. `split`: the guide's development / validation /
  sealed-test seasons (still provisional; overlap is a configuration error). `loso`: one named season
  (`--holdout` or `loso_holdout`) is the holdout split; training cases never see its pits. Packages live under
  `benchmark/<case set>/<split>/<case_id>`, case set `all`, `split` or `loso_<season>`. Pits before 2015-16 are
  not in `all_seasons`, so never targets, but they are visible history to later cases.
- **Exclusions**, each reported with its reason: duplicates (`duplicate_of`), the owner's review list when the
  ADR-050 switch is on (read from `config/observations.yaml`, true), pits with neither layers nor snow depth, seasons
  the mode does not use, `next_pit` without an earlier pit in the season, stand-ins without weather. A pit with snow
  depth but no layers is a depth-only target. The same duplicates and flagged pits are never shown as history either.
- **Anonymous visible package** (03:22 message). The visible files carry no profile, layer, observation or observer
  id, no dated case id (a random 16-hex `case_key` instead), no pit file name, free text, observer layer tags
  ("Nov crust") or season label. Times are hours relative to as_of plus the UTC day of year, never a year; pits are
  `pit_01`.. with a season offset (0 = the case's season). The real ids (`case_id` = site, pit time, type;
  `pit_keys`; `target_profile_id`; per-case `season`) stay in the manifest and `hidden/`. A leakage check scans the
  visible files for forbidden fields, datetime columns, date-like strings and known ids.
- **Leakage checks** run on every case before it is moved into place (a failed check fails the build) and again
  with `snowagent lab check-leakage`: manifest valid, file hashes, header matches manifest, every visible record
  available at as_of, target and its copies not visible, no pit observed after as_of, forecasts issued before as_of,
  anonymity, held-out season not visible, visible case validates. Tests plant a future weather row and the target
  pit and expect the build to fail.
- **Sealed truth.** The agent loader (`load_visible_case`) reads `visible/` only and returns a
  `VisibleBenchmarkCase`. The hidden truth of a sealed-test case is read only with `--unseal` and the typed phrase
  `UNSEAL <case_id>`; the Benchmark Cases page shows truth for training and development cases only.
- **Measured weather** = the plot stations, with ERA5 (the plot's cell, as the baseline runs use) filling hours and
  variables the stations lack, flagged `filled` with source `era5_cell_<elev>m` (`weather.era5_backfill`, on; no
  silent filling, principle 5). Radiation and pressure come only from ERA5.
- **Contract changes** (additive, within the lab, ADR-056): `CaseManifest` gains the case key and set, split mode,
  holdout season, target scope and copies, anchor, forecast source and runs, stand-in description, availability
  rules, pit keys, visible profile ids, visible and excluded counts, build run id and hashes;
  `VisibleBenchmarkCase` becomes relative-time and anonymous (`VisibleWeatherHour`, `VisiblePit`, `VisibleLayer`,
  `VisibleObservation`, `VisibleForecastRun`); `Split` gains `training` and `holdout`; `AvailabilityAssumption`
  gains `assumed_delay` and `perfect_forecast_convention`. Nothing outside the lab reads them.

## ADR-060 Forecast cases start when the archived run reaching the pit is available (fixes the GFS tail gap)
Milestone 2 (ADR-059) set a `forecast_h72` case's as_of to pit - 72 h and gave it the latest archived GFS run
available then. The archive holds 00 UTC runs with leads to 72 h, so that run ended 7-22 h before the pit on every
archived case, and the agents had no forecast for the last hours. The owner asked (milestone 3 brief, 2026-10-05) for
"as_of = availability time of the latest archived run whose forecast leads reach the pit time". Choices:
- New default `forecast_h72.as_of_rule: run_reaches_valid`: among the runs available before the pit (issue + 5 h <
  pit), issued at most `search_window_h` (240 h) before it, carrying the plot's point, whose leads reach the pit
  (issue + max lead >= pit), the case uses one run and as_of = its availability time (issue + `gfs_latency_h`).
  Visible weather, pits and ERA5 latency follow the new as_of as before; the forecast now covers the whole horizon.
- Which run: `run_choice: longest_lead` (default) takes the earliest such run, i.e. the longest lead to the pit that
  still reaches it, which keeps the case closest to a 72 h forecast. With 72 h leads that is a lead of 48-72 h and a
  horizon (as_of to pit) of 43-67 h, not the 72-96 h the brief expected: no archived run issued more than 72 h
  before a pit reaches it. `run_choice: latest` (the literal "latest run", lead under 24 h) is available; the owner
  is asked which was meant.
- Pits with no reaching run (seasons before the archive, a missing run or point) keep as_of = pit - 72 h and the
  labelled measured stand-in. `as_of_rule: fixed_horizon` restores milestone 2 exactly (tested).
- Builder version 3 (manifests record it); the case type keeps its name `forecast_h72`. Tests: the fixture adds a
  run that reaches the target pit beside one that ends early; the reaching run is chosen, `latest` and
  `fixed_horizon` give the other runs, and the stand-in keeps 72 h.

## ADR-061 Agent genome: a family and its allow-listed genes, with block crossover
The owner (2026-10-05): "each agent needs a 'genome'" and "we need to ensure agents just dont memorize these
snowpacks"; milestone 4 will rank every agent on every case, keep the top two and mutate and cross them. Milestone 1's
genome (one monolithic set of modules and ensemble weights, ADR-056) is replaced, inside the lab only (nothing else
read it). Choices (`lab.schemas.genome`, `lab.genome`):
- A genome is `family` (persistence, weather_rule, analogue, snowpack, hybrid) plus `genes`, a flat map of scalar
  values, with an optional display `label`, `origin` (default, file, mutation, crossover) and up to two `parents`
  (genome hashes, lineage). Nothing else: no profile, layer, date, pit or case field exists.
- The allow-list lives in `config/lab.yaml` `genome` (part of the config hash): gene blocks (forcing, new_snow,
  settlement, crust, facets, surface_hoar, rule_pit, pit, analogue, engine_output, blend, uncertainty), each gene
  with kind (float, int, choice), range or choices, default, unit and meaning, and per family the blocks it carries
  (persistence 20 genes, weather_rule 31, analogue 13, snowpack 5, hybrid 20). Validation rejects unknown, missing,
  out-of-range or mistyped genes; a hybrid needs one blend weight above 0. Size guard: at most `max_genes` (64)
  genes and `max_bytes` (4096) of JSON, genes scalar only, label 1-64 characters of [A-Za-z0-9_.:+-].
- The SNOWPACK family carries only output-representation and uncertainty genes; SNOWPACK settings as genes are
  milestone 5. It runs the project's adopted settings (precipitation factors ADR-024/038, pit restart ADR-039).
- Genome hash = sha256 of schema version, family and genes (canonical JSON, floats to 12 significant digits);
  label and lineage are not identity. Agent id = `<family>-<first 10 hex>`.
- `mutate(genome, strength, rng)`: each gene changes with probability `strength` (at least one does); numbers take
  a normal step of sd strength x half the range, reflected at the bounds and clipped (integers rounded); choices
  switch to another choice. `crossover(a, b, rng)`: within a family each block comes whole from a or b (p = 1/2).
  Across families the simplest rule: the child keeps a's family (the better-ranked parent by convention) and takes
  from b only blocks both families carry (e.g. hybrid x weather_rule: forcing, new_snow, uncertainty), each with
  p = 1/2; with none shared it equals a. Both accept a `numpy.random.Generator` or an integer seed, are
  deterministic for it, and validate the child (tested over many seeds and strengths).

## ADR-062 Lab agents: five families, visible case only, one prediction envelope
Milestone 3 brief (2026-10-05): baseline agents of the five genome families (ADR-061), each predicting from a
`VisibleBenchmarkCase` and its genome only, into the existing `SnowpackPrediction`. Choices (`lab.agents`):
- **Envelope.** Agents never see a date or case id, so a prediction carries the case key and times on a fixed
  anonymous epoch (2000-01-01 UTC + horizon); the harness (`stamp_prediction`) checks the key and sets the case id
  and real as-of/valid times before anything is stored or scored. Depth quantiles from the uncertainty genes (p50 x
  (1 -/+ `depth_spread_frac`) -/+ floor), layer boundaries +/- `boundary_spread_m`, presence probability per layer;
  `insufficient_data` with a reason where an agent cannot answer. `model_metadata` holds family, genome hash, label
  and agent diagnostics (never a profile id).
- **Persistence**: the latest visible pit of the season carried to the valid time: measured depth change since the
  pit (sensor, `depth_change_weight`), `pit_trust` between the carried and the measured depth, a measured rise as a
  new DF layer, a fall as compression; then forecast (or stand-in) snowfall as storm layers, settlement and
  degree-day melt; layer presence halves every `pit_age_half_life_days`. No pit: depth only.
- **Weather rule**: a column built from the visible weather in 6 h steps from the start of the visible record
  (snowfall by the rain-snow genes and new-snow density, rain, degree-day melt and refreeze crusts, settlement under
  load, PP -> DF -> RG ageing, near-surface facets under a temperature-gradient proxy, depth hoar, surface hoar on
  clear calm humid nights, buried when snow falls), nudged toward the latest pit's depth (`pit_depth_nudge`).
- **Analogue**: the k nearest past cases by standardised weighted weather and depth features (no date, day of year
  or id features); depth from the neighbours' depth change (or depth), structure from the nearest neighbour's pit
  scaled to that depth. Its `AnalogueLibrary` is built by the harness, anonymous (no season, date or id), and for
  each case holds only cases of OTHER seasons (ADR-065) and only library splits (training; development in mode
  split), never holdout, validation or sealed pits. Tested: the library of the one-season fixture is empty for its
  own cases, so the agent says "no analogue case from another season".
- **SNOWPACK** (incumbent, ADR-063) and **Hybrid**: SNOWPACK depth and structure blended (`snowpack_weight`,
  `persistence_weight`, `rule_weight`) with the carried pit and the rule column; pit layers of concern absent from
  the engine structure within `boundary_match_m` are inserted (p = `pit_trust` x confidence) and near-surface rule
  layers of concern too (p = rule share x confidence). Without the engine the hybrid predicts from the other members
  and says so in its limits.
- An agent that cannot run at all (no SNOWPACK binary) raises `AgentUnavailable`: skipped and reported, not scored
  as a miss. `make_agent(genome, library, backend)` builds an agent from any valid genome (mutated ones included,
  tested). The rule defaults are hand-set, not fitted: tuning them is milestone 4's job, on the training seasons.

## ADR-063 SNOWPACK incumbent: the engine from the visible package; site runs reused only if they qualify
The brief asked to reuse the existing per-plot season outputs where only information available at as-of was used,
else run the engine from the visible package. Checked (`lab.competition.incumbent.site_run_check`): the site's
season runs (`web/data/<plot>/<season>.json`, `<season>_forecasts.json`) are station-mode runs, but every one takes
wind, direction and radiation (and fills) from ERA5 up to the profile time (`forcing_sources`), and the lab treats
ERA5 hours as available 120 h after their hour (ADR-059); their pit updates are applied at the first 00 UTC after a
pit, before the lab's 24 h pit availability. So no site profile qualifies today and the agent runs SNOWPACK itself;
the check stays (and is tested on a synthetic run without reanalysis forcing) so a qualifying run is reused
automatically, recorded per case with its refusal reasons. The engine run (`VisiblePackageEngine`):
- Forcing = the case's visible weather only: measured hours from the season start (15 Sep, snow-free) to as-of,
  then the forecast (archived GFS run, raw as the site's forecasts use it, at the GFS surface height lapsed to the
  plot) or the measured stand-in to the valid time, on an anonymous reference calendar (2001-09-15 onward with the
  case's day of year; the engine never sees the real year). Adopted settings: the site's engine template, the plot's
  precipitation factor on measured hours (ADR-024/038; Bow 1.15, Goat's Eye 0.9, Simpson 1.15), temperature lapsed
  from ERA5-cell and GFS heights to the plot. Gaps the engine cannot take are filled and counted in the prediction's
  metadata (interpolation up to 6 h then carry, clear-sky shortwave x the case's mean clearness, Brutsaert-type
  longwave, no precipitation).
- Pit restart (ADR-039 `reinit_mass`): at the first 00 UTC after each pit of the season visible at as-of, the
  layering is re-initialised from that pit holding the depth-updated mass (`learn.steer`); the run then continues
  to the valid time, where `PROF_START` makes the engine write the profile (lag recorded, |lag| < 15 min).
- Engine elements of one grain within `hardness_merge_tol` merge into one layer (as the site draws them).
- The binary is found as everywhere else (`SNOWPACK_BIN`, PATH, `/opt/snowpack`); without it the agent is
  skipped. A per-case cache lets the hybrid reuse the SNOWPACK agent's profile. Fake-engine backends test both.

## ADR-064 Scoring: five components, frozen weights, truth only for the scoring splits
Choices (`lab.competition.scoring`, `lab.competition.truth`; details in `docs/lab/agents_and_scoring.md`):
- Per case, each in [0, 1]: `snow_depth` = 0.75 exp(-|error| / 0.15 m) + 0.25 [observed in p10..p90];
  `layer_structure` = 0.5 ordered-match F1 (longest order-preserving pairing of same major grain class within 0.15
  relative depth) + 0.3 grain agreement + 0.2 hardness agreement at 20 relative depths; `critical_layers` = soft CSI
  over layers of concern (presence probabilities as hits and false alarms; no observed concern -> 1 / (1 + false
  alarm weight)); `uncertainty` = 0.5 (1 - Brier of the four class-present events) + 0.5 exp(-interval score /
  0.5 m) (alpha 0.2). Structure is compared on relative depth so a depth error is counted once, in `snow_depth`.
- Case composite = weighted mean of the components a target can verify (depth-only pits: depth and the interval
  part of uncertainty), weights from `config/lab.yaml` renormalised. Leaderboard composite = (1 - w_rob) x mean case
  composite + w_rob x robustness, robustness = (1 - failure rate) x min(1, P10 / mean of the case composites).
  `insufficient_data` and agent errors score 0 on every component and count as failures; skipped cases are not
  scored (counted).
- Truth gate: a competition reads the withheld pit only for the scoring splits of the case set's split mode (all:
  training; loso: training and holdout; split: development and validation), never a sealed-test case (refused
  before any file is opened; tested with a spy). The case selector never selects unscored cases.

## ADR-065 Competition runner and the held-out gap
`snowagent lab compete` (`lab.competition.runner`): every agent predicts every selected scorable case (filters case
set, split, plot, case type, forecast source, case ids, limit); per case one process (parallel `--workers`), agents
in genome order sharing one engine profile; seed per case and agent = sha256(run seed, case key, genome hash).
Outputs under `outputs/competitions/<run_id>/`: `run.json` (plan: case ids, case-set hash, genome hashes, seed,
weights, scoring and runner versions, engine mode, config hash; a resume with another plan is refused), `genomes/`,
`library.json` (harness side, with seasons), `cases/<case_id>.json` (stamped predictions, statuses, runtimes, scores,
engine provenance; written atomically, so an interrupted run resumes case by case), `scores.parquet`,
`leaderboard.json` (overall and by forecast source, plot, case type). The run manifest (kind `competition`, status
partial when an agent errored) gains two lab-schema fields, `genome_hashes` and `case_set_hash`, and lists the
profile ids used (visible pits and scored targets). Anti-memorisation hook: `heldout_gap(scores, season)` gives per
agent the composite on the other seasons, on the named season and their difference (`--heldout-season`); the
evolution loop will compare it with a leave-one-season-out case set, where the held-out season's truth is scored
but never in the analogue library.

## ADR-066 Local training loop: rounds, survivors, unique children, cache, lineage
Owner (2026-10-05): "I want to be able to run the 'training' locally, and have options to pick the number of times
we run a competition and mutate agents. One training round should be taking the historical weather forecasts and
weather actuals before every observed pit for all seasons. The two top agents then get mutated to create a new set
of agents we can have compete against each other." Milestone 4 brief: deterministic, resumable, each round committed
atomically, an unchanged genome never re-run. Choices (`lab.training`, `snowagent lab train`, docs/lab/training.md):
- **Rounds.** Round 1 scores the initial population as given (default the five family defaults; `--initial` takes
  genome files or family names) on every training case of the case set (split mode `all` by default: every season;
  `--plots`, `--case-types` filter). The plan's milestone-4 text (quick screen, full development, validation gate,
  niche elite archive) is superseded by the owner's design and not built.
- **Selection.** Rank by the leaderboard composite (frozen weights, ADR-064; the loop never changes them and refuses
  a resume under other weights), unrounded; ties by the mean case composite, then fewer failures, then the genome
  hash. An agent with nothing scored (skipped: no engine) ranks last and never survives.
- **Next population** (rounds 2..N): the top `--survivors` (default 2) unchanged, then `population - survivors`
  children: `round(children x --crossover-share)` (default 0.25) crossovers of survivor pairs, alternating the parent
  order (a x b, b x a: the child keeps the first parent's family, ADR-061), the rest mutations dealt to the
  survivors in rank order (`--mutation-strength`, default 0.2). Every child must be new to the run (its genome hash
  not evaluated in any earlier round or drawn this round): a duplicate is re-drawn up to `max_redraws` (100) times,
  and a crossover that keeps returning a parent (families sharing no differing block) is mutated after 10 draws and
  recorded as `crossover+mutation`. The random stream of round r is `SeedSequence([seed, r])`, so a population depends
  only on the seed, the round and the previous ranking.
- **Storage and commit.** `outputs/training/<run_id>/`: `run.json` (plan: options, case ids and case-set hash,
  initial genomes, monitor season, engine, config hash, weights), `rounds/rNN/` (population with lineage,
  leaderboard, scores, round summary, manifest) written to a temporary directory and renamed into place, then
  recorded in the run registry as kind `evolution`, run id `<run_id>-rNN` (a resume records a committed round the
  registry lacks), and a final `<run_id>` manifest with `summary.json`. A resume (`--resume`) uses the stored plan
  unchanged and refuses rebuilt cases; reusing a run id with another plan is refused.
- **Cache** (`outputs/cache/`). Predictions and scores per (genome hash, case hash, context hash): case hash = sha256
  of the case manifest (which holds the visible and hidden file hashes); context = code hash (every `snowagent`
  module except the loop, UI, CLI and services), lab config hash, scoring and runner versions, weights, run seed, and
  per family the engine identity (SNOWPACK version, engine settings files) or the analogue library hash. Engine
  profiles per engine-input hash (everything the engine run reads from a visible case: site, day of year, horizon,
  measured and forecast hours, forecast runs, the season's pits without their anonymous keys; plus binary version,
  settings files and code hash): the profile depends on no genome, so after round 1 SNOWPACK and hybrid agents cost
  milliseconds, and the same pit in a leave-one-season-out case set hits too. Deterministic engine failures are
  cached; a missing binary is not. Workers write each finished pair at once, so a killed round resumes pair by pair.
  The seed is part of the context although no agent uses it today (the agent contract allows it).
- **Site-run reuse is off in training** (ADR-063: no site run qualifies today), so the cache never depends on files
  outside the case and the engine identity.
- **Estimate** before the start: per-case timings (agent time per family without the engine, one engine run per case,
  the case load), from the latest competition run until training has measured its own (`outputs/cache/timings.json`,
  running means); round 1 exactly from the cache state, later rounds as a range over the cheapest and dearest
  family. A warning is printed when SNOWPACK-family work (engine runs plus SNOWPACK and hybrid agents) is more than
  half of the estimated cost.
- **Lineage.** Each genome's birth record (operator, parents, genes changed against the first parent, genes taken
  from the second, genes changed against the family default, mutation strength, round, run) is stored in the
  round's population; `snowagent lab lineage <hash | prefix | agent id | label>` prints the ancestry back to the
  initial genomes. The genome contract (ADR-061) is unchanged: `origin` and `parents` were already there.
- **Config.** A `training` section in `config/lab.yaml` holds the option defaults. It is excluded from the config
  hash (it chooses how a run searches, not what a case, agent or score is; the run's plan records the options), so
  the milestone-3 runs keep their hash.

## ADR-067 Anti-memorisation monitor in the training loop: a warning signal, not proof
Owner (2026-10-05): "we need to ensure agents just dont memorize these snowpacks". Every round the loop computes the
train-vs-held-out composite gap of the top two agents (`runner.heldout_gap`, ADR-065) on a monitor season and logs
it; the round's gap is their mean; it "widens" when it exceeds the previous round's gap by more than
`gap_tolerance` (0), and is flagged after `gap_flag_rounds` (K = 3) widening rounds in a row (log line `FLAG`,
round summary, run manifest warning, UI marker). Monitor season: `--monitor-season`, else `training.monitor_season`,
else the most recent completed season (its 15 Sep end has passed) whose cases cover every plot that has cases in the
selection, falling back to the most recent completed season (2025-2026 on the data of 2026-10-05: BOW 11, GOAT 9,
SIMP 3 pits). In split mode `all` the monitor season is ALSO training data: selection has already seen it, so a
small or stable gap proves nothing and only a widening gap is informative. The CLI, the log, the round records and
the Training page say so; the proof is the promotion check (ADR-068). In a check-loso fold the monitor is chosen
among that fold's training seasons, never the held-out one.

## ADR-068 Promotion check: leave-one-season-out re-training and its pass rule
CLAUDE.md principle 3 ("promoted only if it beats the incumbent on held-out seasons (leave-one-season-out)"). A genome
from a mode-`all` run has seen every season, so the check (`snowagent lab check-loso --genome <file | run/round/rank>`,
`lab.training.loso`) tests the procedure that produced it:
- For every season S of the run's training cases: build `loso_S` if missing or built by another builder version
  (in parallel, `--workers`); re-run the whole training with the same options and seed (a run/round/rank reference
  re-uses that run's stored options) on the training split of `loso_S` only; then score the fold's best agent and
  the SNOWPACK incumbent (the default `snowpack` genome) on S's holdout cases, the same cases for both. S's truth is
  read only after the fold's training finished (cases selected by split; the analogue library holds training cases
  only; tested with a spy on the truth loader). The checked genome itself is scored there too, labelled in-sample.
- **Rule: PASS** when the evolved agents' pooled held-out composite (leaderboard composite over all held-out cases,
  each predicted by its own fold's winner) is higher than the incumbent's on the same cases, AND the evolved agent
  loses on at most floor(n/2) of the n seasons with held-out cases (a loss: fold winner's composite below the
  incumbent's by more than 1e-4; closer is a tie). Otherwise FAIL. A fold whose winner is the incumbent itself is a
  tie. Only a PASS lets an evolved agent be considered for site output, and then still with the physical checks of
  principle 1; a FAIL keeps it a research entry.
- Folds are ordinary training runs `<check_id>-<season>` (resumable); finished folds (`folds/<season>.json`) are kept
  on resume; the result goes to `result.json` and the run registry (kind `evolution`). An estimate (case-set builds,
  then per fold: round 1 from the engine-cache state of the same cases in the `all` set, later rounds as a range,
  the held-out scoring) is printed first.

## ADR-069 Training page: training runs as a detached process
The Streamlit **Training** page has the CLI's options (rounds, population, survivors, mutation strength, crossover
share, seed, plots, case types, workers, engine, initial families) and starts `python -m snowagent.cli lab train`
with `start_new_session=True` and its output in `<run>/stdout.log`: never inside the Streamlit process, so closing
the app does not stop a run and a run cannot block the UI. The page reads `status.json` (state, round, cases done;
a "running" state whose process is gone shows as interrupted), the committed rounds (live leaderboard per round, best
composite per round, the gap with its flags and the warning-signal note of ADR-067), the lineage of the current best
agent and, when present, the promotion checks (per-season table, pooled result, PASS/FAIL with the rule). A Stop
button writes a `stop` file; the loop stops at its next case and `--resume` continues. Research and
decision-support label on the page as everywhere in the lab.

## ADR-070 SNOWPACK physics genes: verified keys, forcing genes, ranges, genome schema 3
Milestone 5 lets evolution change the simulated snowpack, not only how its output is read. The SNOWPACK family and
the hybrid's engine member get a gene block `snowpack_physics` (`config/lab.yaml`, units, ranges and a source line per
gene; `lab.agents.physics`). Two kinds of gene:
- **Engine keys** written into the run's `io.ini`. The map gene -> (section, key) in `lab.agents.physics.INI_KEYS` is
  the allow-list: a physics gene that maps to nothing, or an ini override outside the map, is refused (`PhysicsError`;
  tested), and every gene value is range- or choice-checked by the genome contract. Verified in the installed source
  (SNOWPACK 20261002.b324cbd, `/opt/snowpack-src/snowpack-model/Source/snowpack/snowpack`), and each one run on a real
  case (BOW_20180301T0700Z_NP) where it changed the profile; an invalid value (`HN_DENSITY_PARAMETERIZATION =
  NOT_A_MODEL`) makes the engine exit 1, so the key is read, not ignored:

  | gene | key | section | read in | engine default | range |
  |---|---|---|---|---|---|
  | sp_hn_density | HN_DENSITY | SnowpackAdvanced | Snowpack.cc:111, Laws_sn.cc:1275 | PARAMETERIZED (template too) | PARAMETERIZED, FIXED |
  | sp_hn_density_parameterization | HN_DENSITY_PARAMETERIZATION | SnowpackAdvanced | Snowpack.cc:114 | LEHNING_NEW | LEHNING_NEW, LEHNING_OLD, JORDY, BELLAIRE, ZWART, PAHAUT, NIED |
  | sp_hn_density_fixed_kg_m3 | HN_DENSITY_FIXEDVALUE | SnowpackAdvanced | Snowpack.cc:115 | 100 | 50-250 kg m-3 |
  | sp_viscosity_model | VISCOSITY_MODEL | SnowpackAdvanced | Snowpack.cc:208 | DEFAULT | DEFAULT, KOJIMA |
  | sp_roughness_length_m | ROUGHNESS_LENGTH | Snowpack | Meteo.cc:43 | template 0.002 | 0.0005-0.01 m |
  | sp_hoar_thresh_ta_c | HOAR_THRESH_TA | SnowpackAdvanced | VapourTransport.cc:116 | 1.2 | -2 to 3 degC |
  | sp_hoar_thresh_rh | HOAR_THRESH_RH | SnowpackAdvanced | VapourTransport.cc:101 | 0.97 | 0.85-1.0 |
  | sp_hoar_thresh_vw_ms | HOAR_THRESH_VW | SnowpackAdvanced | VapourTransport.cc:109 | 3.5 | 1-6 m s-1 |
  | sp_hoar_density_buried_kg_m3 | HOAR_DENSITY_BURIED | SnowpackAdvanced | Snowpack.cc:292 | 125 | 80-250 kg m-3 |
  | sp_hoar_min_size_buried_mm | HOAR_MIN_SIZE_BURIED | SnowpackAdvanced | Snowpack.cc:302 | 2 | 0.5-5 mm |

- **Forcing genes**, because the engine key that sounds right is not the one that acts in this setup:
  `sp_precip_mult_bow|goat|simp` (0.7-1.5) multiply the plot's adopted gauge factor (ADR-024/038) on measured hours,
  not GFS hours; `sp_rain_snow_mid_c` (-0.5 to 3 degC, default 1.2) and `sp_rain_snow_width_k` (0.5-4 K, default 2)
  set the PSUM_PH ramp the forcing builder supplies (all snow below mid - width/2, all rain above mid + width/2;
  the defaults give the builder's 0.2/2.2 degC); `sp_wind_mult` (0.5-1.5) scales measured wind speed.

Differences from the task's list, recorded per CLAUDE.md:
- **THRESH_RAIN** (DataClasses.cc:3551) is only the fallback when the forcing has no PSUM_PH; ours always has it, so
  the rain-snow threshold is the forcing ramp above.
- **WIND_SCALING_FACTOR** (DataClasses.cc:3552) scales only the drift wind `vw_drift`; erosion is off in the template,
  so it has no effect. Measured-wind scaling is the forcing gene `sp_wind_mult`.
- **Snow thermal conductivity has no key** in this version: conductivity follows from the microstructure (Laws_sn);
  `SOIL_THERMAL_CONDUCTIVITY` is for soil layers and `SNP_SOIL = false`. No gene.
- **Settlement** is `VISCOSITY_MODEL`: `CALIBRATION` is excluded (the source calls its fudge function a "playground",
  Laws_sn.cc:1471; on the real case it gave a mean density of 769 kg m-3 against 248 for DEFAULT); KOJIMA kept
  (455 kg m-3 on that case: a large but physical change that the scoring can judge).
- **METAMORPHISM_MODEL = NIED** crashes the engine at start (std::bad_alloc in SnowStation::initialize, exit 1), so
  there is no metamorphism gene.
- New-snow density: `HN_DENSITY = EVENT` (Antarctic, event-driven) and `MEASURED` (needs a density input we do not
  have) and the parameterisation VANKAMPENHOUT (Antarctic firn, Laws_sn.cc:1253) are excluded.

Normalisation: a gene at its default writes nothing, so the default genome renders the incumbent's `io.ini` and
forcing byte for byte (`INCUMBENT_INI` lists the template or engine default of each key; the gene defaults are tested
equal to it), and it was checked on four real cases that new code with default genes gives exactly the M3/M4 engine
profiles. Physics is per plot (a Bow run ignores the Goat precipitation gene) and only active genes count
(`HN_DENSITY_FIXEDVALUE` only with FIXED, the parameterisation only with PARAMETERIZED). The normalised physics has a
`key` (`default` for the incumbent); results carry it (`model_metadata.physics_key`, the engine profile's
`physics_key`, a config hash over the rendered ini) and the engine cache is keyed by it, so output-only mutants still
reuse profiles and a new physics key always runs the engine.

Genome schema `lab-genome-3` (the hash covers the schema, so every genome hash changes). A `lab-genome-2` genome
(milestone 4) still validates against the blocks it had (`GenomeSpec.for_version`); `load_genome` upgrades it by
adding the physics block at its defaults (origin `file`, parent = the old hash), which predicts exactly as before.
The upgraded M4 winner (`snowpack-793c87b12d`) becomes `snowpack-63e51c08aa`, with the old hash as its parent, and
scores exactly as in M4 (0.5198 on the 340 cases).

## ADR-071 Shared restart segments within a plot and season, keyed by every input
A physics child needs a fresh engine run on every case, about 13 s each, and the cases of one plot and season repeat
the same early segments (snow-free start to the first pit update, then pit to pit). `VisiblePackageEngine` with a
`SegmentStore` (`lab.agents.segments`) stores each non-final segment's restart state (`.sno` bytes and modelled depth)
under a key hashing: the engine version, a store context (hash of the prediction code), the terrain unit, the
rendered `io.ini` (physics included), the exact SMET text of the segment's forcing slice, its end time, and its
start state (snow-free at a named time, or the previous key plus the full content of the pit used for the restart,
without its anonymous profile id). The last segment, which writes the profile, is never shared.

Leakage argument (owner: agents must not memorise the snowpacks): a case can only find a state whose key its own
visible forcing and its own visible pits reproduce exactly, so a stored state never contains data the case could not
see, and pit restarts only use pits visible to that case. Any visible difference (an hour withheld by availability,
an ERA5 hour younger than its latency, gap fills that depend on the case's whole visible series, a pit one case
cannot see) changes the key from that segment on. Tests (`tests/unit/test_lab_segments.py`, an engine replaced by a
hash chain): reuse gives exactly the profile of running every segment; every loaded state's provenance (its chain of
pit hashes and SMET hashes) is a subset of the case's visible pits and equal to its own SMET chain; a planted
temperature change before the first update shares nothing, between the first and second update shares exactly the
first segment; a case blind to a pit never loads a state restarted from it; a torn entry (content hash mismatch) is
recomputed. On real data, default physics with reuse equals the cached M4 profiles exactly (four cases checked).
Note: the forcing fills missing shortwave and longwave from the case's mean clearness over all its visible hours (the
incumbent's behaviour, unchanged). Every real case has such hours only in its last ~5 days (ERA5 latency), so early
segments are shared; a synthetic fixture without longwave shares nothing, as it should.

Concurrency: a per-key `flock` makes workers compute a shared segment once; entries are written atomically (`.json`
last) and verified by content hash on read. Scheduling: the training evaluation interleaves cases round-robin over
(plot, season) groups so workers do not all wait on one lock. The store lives under `outputs/cache/segments` and is
emptied when a round is committed (by then every profile those states lead to is in the engine cache), so it never
grows beyond one round. On by default for training (`--no-segment-reuse` turns it off); competitions do not use it.

## ADR-072 `lab train --screen-cases K`: physics children are screened on a fixed sample first
Off by default (the owner's loop is unchanged unless asked). With K, a child whose physics key is new to the run is
first scored on a fixed stratified sample of K training cases (strata plot x case type, proportional largest-remainder
allocation with at least one case per stratum, systematic sampling over the stratum sorted by season and case id with
a seeded offset: `lab.training.screen`). It is scored on all cases and ranked only if its leaderboard composite on the
sample beats the worst survivor's on the same sample (strictly); otherwise it is recorded with role `screened_out`,
its sample score in the round record and `screen_scores.parquet`, and cannot survive. Survivors, cheap-family
children and output-only children skip the screen. The sample and K are stored in the run plan (resume and
`check-loso` folds use them; a fold draws its own sample from its training cases). Rationale: the simplest two-stage
rule that keeps the full-case ranking for everything that can survive; the cost is that a child good on all cases but
not on the sample is lost, which is why it is optional and the sample is stratified. The estimate counts it.

## ADR-073 `lab train --family-slots`: one mutant per family each round
Off by default. In M4, from round 3 every agent was a SNOWPACK agent because a crossover keeps the first parent's
family, so the other families were never tuned. With `--family-slots`, from round 2 five of the population's places go
to one mutation of each family's best fully scored agent so far (rounds 1..r-1; else its initial or default genome),
labelled `rNN-fII-<family>`, lineage `slot: family`. They come from their own random stream
(`SeedSequence([seed, round, 1])`), so the owner's survivors and children are drawn exactly as without the option
(only fewer of them: population - survivors - 5). They compete in the same ranking; they are not protected.

## ADR-074 Depth score without the coverage bonus (scoring version 2); scores apart from predictions in the cache
Owner (Ben, 2026-10-05 14:19 UTC), asked whether to "drop that bonus and let the depth score measure only how close the
middle estimate is, leaving range quality entirely to the uncertainty score; old and new scores would no longer
compare directly, so I would re-run the stage 3 to 5 results under the new rule", answered: "yes, go ahead with your
suggestion".
Why: scoring version 1 had `snow_depth` = 0.75 exp(-|p50 - observed| / 0.15 m) + 0.25 [observed inside p10..p90]
(ADR-064). The coverage term has no width cost, so evolution widened the p10-p90 ranges (coverage 0.76 -> 0.95 from
the incumbent to the M5 winner): the depth score rose while the uncertainty score, whose interval score does charge
the width, fell (0.627 -> 0.562). Range quality was counted twice, once with the wrong incentive.
Choices:
- `snow_depth` = exp(-|p50 - observed| / 0.15 m), nothing else. `depth_covered` stays in every score row and the
  leaderboard's p10-p90 coverage as a diagnostic, never as a score. The weights, the interval score (alpha 0.2,
  scale 0.5 m) and every other component are unchanged. `scoring.SCORING_VERSION` = `lab-scoring-2`.
- Old and new scores do not compare: every run plan records `scoring_version`; a training run or competition never
  resumes under another one (the plan hash changes), and the `check-loso` plan now records it too, so a check started
  under version 1 (`m5-loso-full`) cannot be resumed under version 2.
- Cache (ADR-066): a scoring change must not cost a prediction or an engine run. The scoring module is taken out of
  the prediction-code hash (`code_hash`, which keys predictions, engine profiles and restart segments) and gets its
  own `scoring_hash`; prediction keys no longer hold the scoring version or the weights. Each cached entry records
  the scoring identity it was scored under (scoring version, scoring code, weights); an entry under another identity
  is re-scored by the worker from its stored prediction (same truth gate, `runner.score_rows`) and rewritten, never
  re-predicted and never used as it is. The one-time change of `code_hash`'s definition re-keys the engine profiles
  once; nothing was lost by it here, because the M5 cache lived in `/dev/shm` and was empty after the container
  restart.
- Stored competitions are re-scored without running an agent: `snowagent lab rescore --run-id <run>` writes a new
  competition run `<run>-lab-scoring-2` from the stored predictions (the source run is not changed; refused if its
  cases changed or the weights differ).

## ADR-075 Fresh clone to full training on the owner's Mac: `lab prepare`, portable setup and build
Owner (2026-10-05 15:01 UTC): "remember, I'm looking to run the complete training locally. I just need you to build
the system." Walked from a fresh clone of the branch to `lab train` and `lab check-loso`; gaps found and choices:
- **Inputs not in git.** `lab import` reads, besides tracked files, the restored station files and converted
  logger/dashboard history (`update bootstrap`), the observed profiles (`obs profiles`) and the ERA5 box cache
  (`data/interim/era5`, filled only by `snowagent ingest era5` or the daily update). New `snowagent lab prepare` runs
  the three: bootstrap, profiles if missing, and the ERA5 months the lab reads: September to June of every season
  in `config/lab.yaml` up to the current month, from the existing NSF NCAR mirror (no new source). It never
  overwrites, keeps each variable-month as it completes (resumable), and reports months the mirror has not
  published as failures without failing the rest. On a fresh clone it reproduced the 340 published cases exactly
  (the one month fetched equals the project's cache; 2014-15 months, which no case uses, are not fetched).
- **SNOWPACK on macOS.** Upstream's CMake installs into an app-bundle directory beside the prefix on Apple
  (`EXE_DEST`/`LIB_DEST` "../MacOS"), where neither the SNOWPACK build nor snowagent finds the binary; and the
  default prefix `/opt/snowpack` needs sudo there. `scripts/build_snowpack.sh` now installs bin/ and lib/ on every
  platform (a two-line edit of the APPLE branch in the pinned checkout), defaults to `~/.local/snowpack` on macOS,
  sets an install rpath, takes `JOBS` from `getconf`, and checks its tools; `find_engine` also looks in
  `~/.local/snowpack/bin`. Rebuilt on Linux into a scratch prefix; the macOS branch could not be run here.
- **Environment.** `scripts/setup_env.sh` (did not exist) creates `.venv` with the dev and lab extras from wheels,
  picks Python 3.11+ and warns about a Rosetta Python on Apple silicon.
- **Process start.** macOS starts worker processes with `spawn`, not `fork`; the smoke training and check were run
  with `spawn` and need no change (workers recompute the code hash from the same files).
The commands, times and disk space are in `docs/lab/run_locally.md`.

## ADR-076 Pits of 1997-98 to 2014-15 as training cases, on ERA5 weather (owner, 2026-10-05)
Owner (Ben, 2026-10-05 23:47 UTC), asked whether to add the roughly 320 pits from 1997 to 2015 that were unused
because they predate the station weather, with reanalysis weather: "yes, with those pits." Choices:
- **What weather exists before 2015-16.** No hourly station record exists at the plots before December 2014: the
  FTS360 archive, the converted logger exports and the dashboard history start at Bow Summit station 2014-12-05
  (humidity, wind; snow depth 2015-02-01), Simpson Lower and Upper 2015-01-12/13, Sunshine Village (Goat's Eye
  temperature and gauge, Simpson's gauge) 2015-08-15, Lookout 2015-11-14 and the Bow Summit gauge 2016-03-22.
  `archive/ghcnd` has daily GHCN records (Sunshine CS 1997-2007; Bow Summit PC 1999-2007 and AE 1998-2007:
  temperature, precipitation, snow depth), daily and partial; the lab's weather is hourly, so they are not used
  (open question: an independent depth check, as in ADR-025). So 1997-98 to 2013-14 are ERA5 only at every plot;
  2014-15 is ERA5 only at Goat's Eye and mixed at Bow Summit and Simpson (station temperature from December or
  January, ERA5 precipitation); 2015-16 is mixed at all three (ERA5 fills the early season and, at Bow Summit, the
  precipitation until the gauge starts). ERA5 is the NSF NCAR mirror the project already uses (ADR-021; its 120 h
  latency, ADR-033).
- **One switch.** `splits.include_reanalysis_seasons` (on) adds `splits.reanalysis_seasons` (1997-1998 to
  2014-2015) to `all_seasons` (modes all and loso) and to `development_seasons` (mode split) when the config loads,
  so every consumer of those lists, the UI included, sees them. Off, nothing changes: the older pits stay visible
  history only. Pits of every earlier season remain visible history to later cases exactly as before.
- **Import.** With the switch on and ERA5 cached for those seasons, a site's hourly table starts at the first
  reanalysis season (or the first cached ERA5 month, if later) instead of the stations' first hour; those hours
  are ERA5 values flagged `filled` with their source (principle 5), months the cache lacks are explicit missing
  rows. Values stay at the ERA5 cell height (the agents lapse temperature to the plot, as for any ERA5 hour); the
  ERA5-only transfer constants of the site runs (ADR-025) are not applied, as they are not to ERA5-filled hours of
  the station seasons.
- **Provenance.** Every manifest records `weather_source` (`station`: plot stations supplied at least 90 % of the
  hours with a value, for both temperature and precipitation; `era5_only`: no station value of either; `mixed`:
  otherwise) and `weather_station_share`, over season start to the pit (to as-of when an archived GFS run follows).
  Temperature and precipitation decide it because they decide the simulated snowpack; wind, radiation and pressure
  are ERA5 at some plots in every season. Additive contract change within the lab (ADR-056): `CaseManifest` gains
  the two optional fields (None on older builds), score rows a `weather_source` column; builder version 4. Build
  reports, `lab cases`, leaderboards (`by_weather_source`), `lab compete --weather-source`, `lab train` and
  `lab check-loso --weather-sources` (recorded in the training plan only when used) and the Leaderboard page filter
  and split by it, as by forecast source. On the data of 2026-10-06 the 340 existing cases are unchanged (every
  visible file identical); 297 are `station` and 43, all of 2015-16, `mixed`.
- **Leakage rules unchanged.** The same ten checks, the same availability delays: an ERA5 value is visible 120 h
  after its hour. In an `era5_only` case that leaves no temperature or precipitation in the 120 h before as-of
  (the stand-in carries ERA5 from as-of to the pit), and the SNOWPACK agent fills missing precipitation with zero,
  so those cases lose up to five days of snowfall before as-of. Kept as decided; tested on an ERA5-only case,
  including an ERA5 value planted inside its latency (the build fails). The older seasons have no archived GFS, so
  all their `forecast_h72` cases are stand-ins.
- **`lab prepare`.** Fetches, for each reanalysis season, September to the month of its last pit at a lab plot
  (from the observed profiles it has just built), nothing for a season without one (2002-03); station seasons keep
  September to June. 129 more months (241 in all), about 0.1 GB; resumable, never overwriting.
- **Not tuned.** The plot precipitation factors (1.15 Bow Summit and Simpson, 0.9 Goat's Eye; ADR-024/038) were
  chosen on station precipitation, and the SNOWPACK agent applies them to every non-GFS hour, ERA5 hours included.
  Measured, not changed (incumbent SNOWPACK agent, scoring version 2): on the 97 ERA5-only cases of 2006-07,
  2011-12 and Goat's Eye 2014-15, depth MAE 0.116 m and bias -0.051 m (Bow Summit +0.009, Goat's Eye -0.105) against
  0.114 m and -0.026 m on 97 station cases of the same plots and case types; and on those same 97 station cases
  rebuilt with ERA5 in place of every station value, MAE 0.131 m, bias -0.083 m (Goat's Eye -0.137 m, about 11 % of
  the observed depth; `forecast_h72` -0.115 m). Layer and critical-layer scores do not get worse (0.51 -> 0.52 and
  0.26 -> 0.28 on the paired cases). ERA5 weather makes the depth moderately low-biased, mostly at Goat's Eye; the
  pits the agent restarts from (ADR-039) keep it from drifting further.
- **Cost.** The case set grows from 340 to about 945 (expected from the pit tables: 320 `forecast_h72` and 285
  `next_pit` older cases); `check-loso` gets 28 folds instead of 11 (2002-03 has no pit). Training and the promotion
  check take about 2.8 and 7 times as long; `docs/lab/run_locally.md` has the times. Switching the seasons off
  restores the 340-case set exactly.

## ADR-077 The whole lab loop from the browser: `snowagent lab app` and background jobs
Owner (2026-10-05 23:47 UTC): "the local version I want to use a web browser interface". Until now the app could
browse, build cases (inside the Streamlit process), start and stop training and read results; `lab prepare`,
`init`, `import`, `compete`, `check-loso`, `rescore` and most of `lineage` needed a terminal.
- **Launcher.** `snowagent lab app [--port 8501] [--host 127.0.0.1] [--data-root] [--config] [--open/--no-open]`
  finds `lab_app/Home.py` of the checkout the package runs from (or of the working directory), runs
  `python -m streamlit run` with the same interpreter, prints the address and opens the browser. Local only by
  default; `--host 0.0.0.0` for another device on the home network, with a printed caution that there is no login.
  Streamlit's usage statistics are off and its deploy toolbar hidden (the lab sends nothing out).
- **Jobs.** Every step the browser starts runs the same `snowagent lab ...` command a terminal would, under a
  small runner (`python -m snowagent.lab.services.jobs <job dir>`) started with `start_new_session=True` (the
  ADR-069 pattern, generalised): `<data root>/outputs/jobs/<job id>/` holds `job.json` (kind, steps and commands,
  working directory, ids it produces, pid), `status.json` (the runner writes the job's and each step's state and
  exit code) and the log (`output.log`; a training keeps `<run>/stdout.log`). A job whose runner is gone without a
  final state is shown as interrupted (`ps` confirms the pid is still that runner, so neither a zombie nor a reused
  pid looks alive). One job per kind runs at a time (one set-up, one case build, one competition, one training,
  one check, one estimate, one re-score); a second start is refused, and a training is also refused while another
  training process runs, wherever it was started. Stop: a training is asked to stop at its next case (its `stop`
  file); every other job's process group gets SIGTERM. Every command resumes, so Resume runs the same commands
  again (training with `--resume`; a resume now also clears the training's `stop` file, which earlier made
  `--resume` stop again at once).
- **Pages.** Home "Set up data" (prepare, init and import as one three-step job, with what exists and a time
  estimate from the ERA5 months still missing); Benchmark Cases builds as a job instead of inside the app;
  Leaderboard "Run a competition" (agents, case set, plots, case types, workers, engine, first N cases, seed) and a
  re-score of a run of an older scoring version; Training: Resume, the run's log, the lineage of any agent, and
  the promotion check: estimate (`--estimate-only`) first, then start under a check id, resume an unfinished check
  from its stored plan by id; a Jobs page lists running and finished jobs with logs, Stop and Resume. Nothing is
  promoted from the browser (ADR-058). The research and decision-support label stays on every page.
- Simplest options chosen: no task queue or database (files in the data root, like runs), no authentication (local
  only by default), no automatic refresh of job panels (a Refresh button; the Arena page polls, ADR-078).
The guide for the owner is `docs/lab/web_interface.md`.

## ADR-078 The Arena: a live event feed and a page that shows runs as they happen
Owner (2026-10-05 23:55 UTC): "I'd like a cool interface which visualizes the competitions occurring".
- **Feed.** Runs append small JSON lines to `events.jsonl` in the run directory (`snowagent.lab.events`): run
  started/finished, case started (training workers), one `case_scored` per agent and case (status, case composite,
  the four per-case components, plot, case type, season, split, pit time from the case id; training also round,
  cached or not, and the cache key), round started (agents with role, operator, parents, changed genes), screen
  (ADR-072) and round committed (best, next survivors, gap, ranking). One `write` per line on an `O_APPEND` file:
  flushed at once, whole lines from several worker processes. `SNOWAGENT_LAB_EVENTS=0` turns it off.
- **No effect on results or cache keys.** The training cache keys hash the source of every module that can change a
  prediction (`code_hash`); the feed module is excluded from it, the training loop and evaluation already were,
  and the competition runner is not edited: a competition's events are written by the parent process
  (`CompetitionFeed`, wired in the CLI) from each case record as it is committed. Tests run a competition and a
  training with and without the feed and compare scores, leaderboards and cache entries.
- **Replay of older runs.** A run without a feed is replayed from its own files (competition case records in the
  order written; training round score tables, case order within a round), labelled as such.
- **Page.** Arena: Race (mean case composite so far per agent, family colours, the default SNOWPACK genome's value
  as the dashed bar to beat; in later training rounds its round-1 value on the same cases, labelled), Heat strip
  (agents x cases by case composite, hover with plot, type, season, pit time and components), Duel (the latest
  training/development case: observed pit, leader and incumbent profiles; the Leaderboard's truth rule) and, for
  training, Evolution (family tree by round and rank, size = composite, mutation/crossover/kept edges, survivors
  ringed, screened-out children faded, changed genes on hover; best per round and the gap with its flags as two
  separate charts, no second axis). While a run is live the page polls the feed every 2 s (`st.fragment`); a
  finished run has a replay slider and Play/Pause. Plotly is bundled with Streamlit: no external script, works
  offline. Family colours: five fixed categorical slots validated for colour-vision deficiency in light and dark,
  chosen by the Streamlit theme; families also differ by marker and every bar is labelled.
- The race shows the mean case composite, not the leaderboard composite (which adds robustness); the page says
  so and points to the Leaderboard and Training pages for the ranking.

## ADR-079 ERA5 months for the lab as a bundle branch, not hours of range requests (2026-10-06)
On the owner's Mac, `lab prepare` timed out on every ERA5 month (FSTimeoutError, cut-off range responses): reading
the plot box from the mirror's global netCDF files needs many large range requests per month, about 5 minutes per
month on a cloud machine and far longer at home. What the lab keeps is under 1 MB per month.
- **Decision.** The extracted box months (`era5_box_YYYYMM.npz` + `.json` provenance, and `era5_box_z.npz`) are
  published on an orphan branch `claude/lab-era5-box` under `era5/`, about 0.2 GB, extracted by the project's own
  `snowagent.ingest.era5.extract_month` from the same source (NSF NCAR mirror, ERA5, Copernicus Climate Change
  Service, CC-BY 4.0; attribution in the branch README). `lab prepare` (with `--bundle`, the default) fetches the
  branch and copies only `era5/era5_box_*` files that are missing; it never overwrites and falls back to the mirror
  for anything the bundle lacks or when git, the network or the branch is unavailable.
- **Cost.** A full `git clone` also fetches that branch (about 0.2 GB more); `git clone --single-branch` avoids it.
  The branch can be replaced by a release asset later without changing the data. Raw data stay immutable: the
  bundle is a derived cache, not a new source.

## ADR-080 The daily update is outside the lab's code hash (2026-10-06)

- **Context.** The lab's cache keys every prediction and engine profile by `code_hash`, a hash of every
  `snowagent` module outside `CODE_EXCLUDE`. With the lab on `main` (owner, 2026-10-06: merge every pull request),
  each change to the daily routine (`ops/`) would change that hash and make a resumed training run recompute every
  engine profile, although `ops/` cannot change a prediction: no lab agent, case builder or engine module imports it
  (only `lab/services/prepare.py`, itself excluded, calls `ops.update.bootstrap` to restore input files, whose
  content the case hashes already cover).
- **Decision.** `ops/` joins `CODE_EXCLUDE`. A unit test fails if any hashed module starts importing it, at which
  point it must come back into the hash. The site build `web/` stays in: `learn.steer`, which the SNOWPACK agent's
  steering uses, imports its forcing and profile helpers, so a change there can change a prediction.
- **Cost.** One-time: the hash differs from every earlier one (as the merged season-lifecycle changes to `ops/` and
  `web/` would have made it anyway), so the first run after this change recomputes its engine profiles once.

## ADR-081 Lab app: grouped menu, presets and a live run panel (owner, 2026-10-06)

- **Context.** The owner runs the lab only from the browser app and asked to clean it up. On the first night the
  page opened on an older run instead of the running one, Resume was hard to find, "already running" gave no way to
  stop the run, nine number boxes had to be set for every run, and progress needed Refresh.
- **Decision.** `lab_app/Home.py` only builds a grouped page menu (`st.navigation`: Lab, Evolve agents, Results,
  Data, Background); the old home page is `pages/0_Overview.py`, now led by what is ready, what is running with its
  finish time, and the best agent so far. The Training page leads with a run panel that refreshes itself every 5
  seconds (`st.fragment`), holds Stop and Resume, and redraws the page when a round commits; the run that is running
  is chosen first. Starting a run uses presets (`services.training.PRESETS`: Overnight, Quick check, Custom) with the
  rarely changed options under Advanced; Screen cases defaults to 30. Time left comes from the run's own measured
  rounds (`time_left`). The agent card shows changed genes in plain words (`ui/genes.py`). On-screen text drops ADR
  numbers; competitions choose 20, 100 or all cases instead of "0 = all".
- **Not changed.** No page was merged into another: the grouped menu gives the same clarity without rewriting pages,
  and every page stays its own script (tests run each one). Nothing here is in the cache code hash (`lab/ui/`,
  `lab/services/` and `lab_app/` are outside it), so cached predictions stay valid.

## ADR-082 Training reports in plain language, downloadable from the app (owner, 2026-10-06)
- **Context.** Owner (2026-10-06): "I'd like the app to produce analysis reports within the interface that can be
  downloaded as a document", "the reports I want dumbed down, so they can be interpreted by a lamen", and "how long
  it took to run the last training round ... build this into the reports".
- **Decision.** `lab/services/reports.py` writes a training run's report from its committed files only: agent rank
  k of round r (default 1 and the last round) against its family's default agent of round 1 on the same cases (else
  round 1's best). Plain sections: In short (better on its training winters; on the monitor winter, judged against
  two paired standard errors; proven by a promotion check or not), What the score means, What got better (scores
  out of 100, depth error in cm), Is it learning or memorising, Progress, How long it took (whole run, last round,
  typical round, SNOWPACK runs and seconds each, rounds per 8-hour night), What the agent changed (one sentence per
  setting, related settings combined, changes under 5% of a range counted), Things to keep an eye on, What to do
  next (fixed rules), Words used here, and a Reference table (run id, agent and genome hash, plan hash, scoring and
  SNOWPACK versions). An optional appendix holds the technical tables. The Reports page (Results › Reports) shows
  it and offers HTML (one self-contained file: inline style and SVG charts, no scripts or links; opens in a
  browser or Word and prints to PDF) and Markdown; a copy is kept in `outputs/reports/`.
- **Why these formats.** Word (.docx) would need a new dependency and a reinstall on the owner's Mac; the HTML file
  opens in Word and prints to PDF. Every sentence is chosen by fixed rules from the numbers; no text is generated
  freely (CLAUDE.md principle 1). Not in the cache code hash (`lab/services/`), so cached predictions stay valid.

## ADR-083 Locked test winters in every new training run (owner, 2026-10-06)
- **Context.** Owner (2026-10-06): "because these are the only sites and data we have, I'd like to make sure the best
  we can that the agents aren't getting good at forecasting just for these seasons and snowpack obs", then chose
  "Locked test winters" first among the safeguards offered. Split mode `all` (2026-10-05) selected on every season,
  so the per-round gap (ADR-067) was a warning signal only and the slow promotion check (ADR-068) the only test.
- **Decision.** A new training run locks the N most recent seasons of its selected cases (default N = 3:
  2023-24 to 2025-26 on the data of 2026-10-06; `--locked-seasons`, the Training form's Advanced option; 0 = off).
  Locked cases are not training cases: rounds, survivors, the screen sample, the monitor season and the case-set
  hash use the other seasons only, and the analogue library leaves the locked seasons out
  (`build_library(..., exclude_seasons)`). After a round's ranking, its leaders (round 1: every initial agent; later:
  the survivors chosen) are scored on the locked cases; `round.json` `locked_test` and `locked_scores.parquet` keep
  the result. Selection never reads it. The plan records `locked_seasons` and `locked_case_ids`, so a resume keeps the
  same split. A selection with fewer than N + 2 seasons locks none. Promotion-check folds lock nothing (a fold holds
  out its own season). The Training page shows the round's best on the locked winters beside standard SNOWPACK's,
  and the report (ADR-082) leads with it.
- **Why the default lives in code.** `training` options are outside the configuration hash, but `settings.py` is
  inside the prediction cache's code hash (ADR-080): adding a configuration key would make every cached prediction
  recompute. `LOCKED_SEASONS = 3` in `lab/training/loop.py` (outside the hash) keeps the cache valid.
- **Limits.** Three plots only: a locked winter tests new weather at the same plots, not a new site; per-plot genes
  (snowfall multipliers) are site-specific by design. Looking at the locked score of many runs and keeping the best
  slowly turns the locked winters into training data; the promotion check remains the decision gate.


## ADR-084 Experimental evolved agents on the public site, sent from the lab app (owner, 2026-10-06)
- **Context.** Owner (2026-10-06): "take the best evolved agent and use them on the banff-snowpack.netlify.app site
  ... Or an option to choose the top 3 agents", then chose "Now, as experimental" on the decision card offered, then
  "is there a way we can auto upload these agents?", and asked for agents to have names inspired by The Wire, The
  Sopranos, Curb Your Enthusiasm and The Crown. ADR-058 and CLAUDE.md principle 1 let an evolved agent reach site
  output only after passing the promotion check.
- **Decision.** The owner's choice relaxes that gate for display only, never for the site's model: up to 3
  SNOWPACK-family agents can be shown on the site as extra "Weather input" choices, each labelled "Experimental
  evolved agent: not validated", with a banner and dashed line. Standard SNOWPACK stays the default, the
  pit-steered nowcast, the forecasts and the public reports are unchanged, and nothing is promoted.
- **How an agent gets there.** Training › "Put on the public site (experimental)" writes one JSON file per agent
  (TV name, genome, source run, round, rank, training and locked-winter scores) to `site_agents/<agent_id>.json` on
  the `site-agents` branch and pushes it with the computer's own GitHub sign-in (git plumbing with a temporary index:
  the checkout, its branch and files are never touched; never a forced push). Remove deletes the file. The daily
  update (`ops/site_agents.py`) reads the branch, validates every file strictly (fixed keys, a short plain name,
  the genome contract with its allow-listed genes and ranges, SNOWPACK family, at most 16 kB) and runs the 3 oldest
  valid agents for the live season at each plot. Anything else is skipped with a warning on the status page; a
  failing agent never stops the update.
- **What runs.** Weather only (no pit steering), like the site's "without pit updates" input, with the agent's
  physics genes applied as in the lab (ADR-070): precipitation and wind multipliers on measured and reanalysis hours
  (not the GFS fill), the rain/snow ramp, and the allow-listed io.ini keys. The output genes (layer reading,
  uncertainty) are lab scoring choices and do not apply to the site's raw SNOWPACK layers. Output:
  `web/data/<plot>/<season>_agents.json` (profiles, daily depth, pit scores, run id, forcing and config hashes,
  engine version) and the index `web/data/agents.json`.
- **Names.** `lab/services/names.py` gives each genome a stable display name (a character's first name and a
  surname or place from another of those shows; The Crown contributes places, never real people). Display only:
  labels, ids and hashes are unchanged, and a family default keeps its plain name ("standard SNOWPACK").
- **Limits.** In-sample scores are not evidence of skill; the site's banner says so. Anyone with push access to the
  repository can write the branch, which is why its files are treated as untrusted data. Cost: one extra season run
  per agent and plot each day (about a minute each).

## ADR-086 Blind live test: freeze agents before a winter's pits exist (owner, 2026-10-06)
- **Context.** Overfitting is the owner's stated concern (2026-10-06: "as sure as possible" agents are not just
  getting good at these seasons and pits). Locked winters (ADR-083) are unseen by training, but anyone looking at
  many runs' locked scores slowly selects on them. A winter whose pits have not been dug cannot leak at all. The
  owner accepted this as the first of the remaining safeguards (blind live test of a frozen agent on 2026-27).
- **Decision.** Training › "Blind test on this winter" freezes the agent in the agent card: its genome, source run,
  the git commit and prediction code hash it ran with, and the scoring version go to
  `blind_test/<winter>/<agent_id>.json` on the `site-agents` branch, pushed with the Mac's GitHub sign-in by the
  same plumbing as Send to site (ADR-084), so the freeze time is recorded on GitHub. Entries are never edited or
  removed (a second freeze of the same agent in a winter is refused); at most 5 agents a winter. Any family may
  enter. Only pits observed after an entry's freeze time count for it; standard SNOWPACK is the comparison.
- **Scoring (next step).** Scoring needs the winter's pits and station weather in the lab, which today arrive on the
  daily-update branch. A follow-up builds the winter's cases from pits after each freeze and scores the entries and
  standard SNOWPACK with the lab's scoring, before the first pits at the plots. A code-hash mismatch between an entry
  and the scoring code is reported, not hidden.
## ADR-088 Critical-layer score: confidence on its own earns nothing (scoring version 3) (owner, 2026-10-06)
- **Context.** The first overnight run's winner gained most of its lead in critical layers (0.28 -> 0.38) while
  its Brier score got worse: it raised its layer confidence gene from 0.70 to 0.96. Version 2's soft CSI weighted
  hits by the presence probability, so saying "certainly there" about every weak layer paid even when no more
  layers were found. The owner asked for safeguards against agents that only get good at the score; fixing this
  loophole was the third of the four offered (2026-10-06).
- **Decision.** Scoring version `lab-scoring-3`: a predicted layer of concern is forecast when its presence
  probability is at least 0.5 (`PRESENT_P`); hits, misses and false alarms are counts, CSI = hits / (hits + misses +
  false alarms), and 1 / (1 + false alarms) with no observed layer of concern. How sure the agent is counts only in
  `uncertainty`, whose Brier score is proper (it is best at the honest probability), so over-confidence now costs
  and never pays. Other components and the weights are unchanged.
- **Runs already going.** A training run keeps the scoring version in its plan: `EvalContext` carries it and each
  worker scores inside `scoring.scoring_version(...)`, so the owner's run started under version 2 resumes under
  version 2 and its rounds stay comparable. Only runs started after the update use version 3. Competitions and
  promotion checks use the current version, as before (a stored competition can be re-scored with `lab rescore`).
- **Cache.** The scoring version is part of the scoring identity, not the prediction key: cached predictions are
  re-scored, never re-run (ADR-074). Editing `scoring.py` changes the scoring hash, so a version-2 run re-scores its
  cached pairs once with identical results; the prediction code hash is unchanged.
- **Limits.** The 0.5 threshold is a convention; a layer forecast at 0.45 is a miss even when it is there. The
  first run's scores cannot be recomputed here (its predictions are on the owner's Mac); a new run reusing them
  re-scores them under version 3 automatically.
## ADR-087 Choose survivors that are good everywhere (owner, 2026-10-06)
- **Context.** With three plots and a few dozen winters, an agent can raise its average by fitting a few winters or
  one plot. The owner accepted "selecting on consistency across winters and plots" among the safeguards
  (2026-10-06). The first overnight run's winner gained most at Goat's Eye and Simpson through per-plot snowfall.
- **Decision.** A new run ranks agents by the leaderboard composite less `CONSISTENCY_K` = 0.5 times its
  unevenness: the standard deviation, over season-plot groups with at least 3 scored cases, of the agent's group
  mean less standard SNOWPACK's group mean from round 1 (the yardstick: a hard winter counts against no one; without
  standard SNOWPACK in the run, the mean of the agents ranked). Survivors, the round's best and the locked-winter
  test follow that order; the composite and every score are unchanged (scoring version unchanged). Training ›
  Advanced "Choose survivors by": "Even across winters and plots" (default) or "Highest average";
  `--selection consistent|composite`. The plan records it; a plan without the key (runs before this change)
  resumes on the composite, so a running run is unaffected. The promotion check's folds inherit the run's choice.
- **Check on the first run.** On its rounds 10 and 20 the even ranking keeps agents within 0.002 of the best
  composite but prefers ones whose gains are spread more evenly (round 20: r19-x01 first instead of r20-m05).
- **Limits.** The screen (ADR-072) still compares children on the composite of its small sample. K = 0.5 is a
  judgement, not fitted; changing it is a new decision.

## ADR-089 Survivors pay a small penalty for drifting from standard settings (owner, 2026-10-06)
- **Context.** The owner wants to be as sure as possible that agents are not just learning these three plots and
  these seasons. The first overnight run's winner had moved 20 settings away from standard SNOWPACK; many moves were
  small and probably neutral passengers of mutation, and the round-20 leaders differed by 0.0001 to 0.004 in
  composite. Of the safeguards offered on 2026-10-06, a penalty for drifting from standard settings was the fourth.
- **Decision.** Drift = sum over the family's genes of |value - default| / (max - min), a changed choice 1 (the
  gene ranges of `config/lab.yaml`). New runs rank survivors by the composite (less the unevenness penalty of
  ADR-087 when on) less `DRIFT_K` = 0.002 x drift: moving one setting across its whole range has to gain 0.002
  composite. The first run's winner (drift 4.45, lead 0.038 over standard SNOWPACK) would pay 0.009, so real gains
  survive while near-ties go to the plainer agent. The plan records `drift_penalty`; a plan without it (every run
  started earlier, the owner's running one included) resumes with none. `--drift-penalty` / Training › Advanced set
  it (0 to 0.05; 0 = off). Leaderboard rows carry `drift` and `selection_score`; the composite itself, scoring, the
  screen threshold, locked-winter scores and promotion checks are unchanged.
- **Limits.** Range-normalised distance treats every gene alike although some matter more than others; a choice
  gene counts as a full unit. The penalty favours parsimony, it does not prove a change is physical.
## ADR-085 Start a training run from an earlier run's agents (owner, 2026-10-06)
- **Context.** Owner (2026-10-06): "is there a choice to include a couple of the agents we evolved last night?",
  then "so the next version I'll be able to use past agents?". The CLI took genome files (`--initial`); the app
  offered only the five family defaults. Agents of a run that trained on every winter have seen the winters a new
  run locks (ADR-083), so their descendants' locked-winter scores are optimistic.
- **Decision.** `lab train --seed-from RUN --seed-top N` (Training › Advanced: "Start from an earlier run's agents",
  "How many") adds the N best evolved agents of that run's last committed round (family defaults skipped) to the
  initial population. The plan records `seeded_from` (run, round, rank, agent, genome hash and the seasons each was
  trained or selected on, inherited through that run's own seeds) and `seeded_saw_locked`, the locked winters they
  had already seen. When that list is not empty, the start message, the Training page's locked-winter section and
  the report say the locked-winter result is not a clean test. Resumes keep the seeds (the plan's initial genomes).
- **Why allowed at all.** A warm start saves nights of training; the cost is a weaker locked test, which is named
  wherever the result is shown. The promotion check (ADR-068) still decides promotion.

## ADR-090 Blind test scoring in the daily update (owner, 2026-10-06)
- **Context.** ADR-086 lets the owner freeze up to 5 agents per winter before its pits exist. They still had to be
  scored on those pits as they arrive, on the same footing as training (the lab's cases and scoring), and without
  the owner's Mac: the live season's pits and weather arrive through the daily update, on its branch.
- **Decision.** A daily-update step (`ops/blind_test.py`, after the site agents) reads the winter's frozen entries
  from the `site-agents` branch and the live season's pits from `data/interim/obs/observed_profiles.jsonl`. When
  there is an entry, a pit observed after the earliest freeze, and something new since the last scoring (pits or
  entries, kept in `archive/blind_test/<season>/state.json`), it imports the checkout into a separate lab root
  (`data/lab_blind`, never the owner's data), builds the lab's next-pit and 72-hour cases for just those pits with
  the live season enabled in a copy of the config (mode `all`; `config/lab.yaml` is unchanged), and scores every
  entry and standard SNOWPACK on them, under each entry's recorded scoring version. An entry counts only pits
  observed after its freeze. Rows go to `archive/blind_test/<season>/scores.jsonl`: tracked and append-only, a
  scored (case, agent, version) is never rescored or changed. The summary (per entry: cases, its composite and
  standard SNOWPACK's on the same cases, cases better and worse, per plot) goes to `web/data/blind_test.json` and to
  `blind_results/<season>.json` on the `site-agents` branch, where the lab app's Training page reads it.
- **Lab extra.** The step needs the lab's tables (pyarrow). ADR-055 kept the lab extra out of the daily run; here
  it is optional: without it the step reports an info warning and does nothing else, so the daily update never
  depends on it. The runbook's environment step now installs `.[dev,lab]`.
- **Limits.** Few pits a winter (about 30 to 60 across the plots), so early numbers are noisy; the first pit of a
  plot has no next-pit case. Cases use the GFS archive and measured weather as they were on the day, so a late
  correction of a pit or the weather is not re-scored.

## ADR-091 Weak layers by kind in the training report (owner, 2026-10-09)
Ben: the weak-layer score is the most important part of the snowpack to get right. The critical-layer score lumps
surface hoar, facets, depth hoar and crusts into one number, so a run cannot tell which kind an agent misses. Every
scored full-profile case now also records, per kind, the observed layers, the forecast ones (probability at least
0.5) and the observed ones found, matched exactly as the critical-layer score matches them
(`scoring.concern_by_class`, keys `wl_<kind>_observed|forecast|found`). They are diagnostics: no score, weight or
scoring version changes; the scoring module's hash does, so a resumed run re-scores its cached rows once (no agent
runs). The training report has a "Weak layers by kind" section on the training and locked winters. For a run scored
before this change the report counts them from the run's cached predictions (`services.weak_layers.from_cache`),
which works while the prediction code, lab config and engine files are the ones the run used; otherwise the section
is left out.

## ADR-092 Weak-layer genes, and a small SNOWPACK patch for faceting (owner, 2026-10-09)
Ben: weak layers are the most critical part of the snowpack, and the genome should carry what forms them: faceting
that is faster in a shallow pack under cold air (depth, temperature gradient, air temperature, time), more faceting
above and below crusts, which act as vapour barriers, and the conditions for surface hoar. The second training run
(50 rounds) left the weak-layer score at 0.40 and stopped improving after round 8: the genes could tune snowfall and
wind but nothing that sets the temperature gradient or the faceting itself.

New block `snowpack_weak_layers` (genome schema `lab-genome-4`; a `lab-genome-3` agent loads and upgrades with the new
genes at their defaults, so it runs as before; `LEGACY_BLOCKS`):
- Forcing genes (as the precipitation factor, on measured and reanalysis hours, not GFS): air temperature offset per
  plot, an incoming-longwave offset (clear-night surface cooling: the longwave of most hours is an estimate), and the
  ground temperature TSG (the builder's 0 degC lower boundary).
- Engine keys verified in the installed source (20261002.b324cbd): `HOAR_DENSITY_SURF`, `HOAR_MIN_SIZE_SURF`
  (SnowpackConfig.cc advanced defaults 100, 0.5), `ATMOSPHERIC_STABILITY` ([Snowpack], Meteo.cc; the template's
  MO_SCHLOEGL_MULTI_OFFSET), `ENABLE_VAPOUR_TRANSPORT` (VapourTransport.cc, default false: vapour moves between
  layers and dense layers slow it).
- SNOWPACK has no key for the faceting rate: the kinetic-growth threshold is the constant `Metamorphism::mm_tg_dpdz`
  (5 hPa m-1) and the rates are fixed. `scripts/snowpack-patches/lab-facet-knobs.patch` (applied by
  `build_snowpack.sh`, which records it in `<prefix>/share/banff-snowpack/patches.txt`) adds three optional
  [SnowpackAdvanced] keys to the DEFAULT metamorphism of dry snow: `LAB_FACET_DPDZ` (that threshold),
  `LAB_FACET_RATE` (multiplies kinetic grain and bond growth and the fall of sphericity) and `LAB_CRUST_FACET`
  (multiplies them again in an element directly above or below a melt-freeze crust or ice layer, types 772/880, as
  Hazard.cc identifies them). Absent, they take the unpatched values. The depth, gradient, temperature and time
  dependence stays the engine's own physics; the genes scale it.
- Checked on a real Goat's Eye season (2024-25, first 150 days): the patched engine with default genes gives a
  byte-identical .pro to the unpatched one, so cached engine profiles stay valid and the engine identity is
  unchanged; each new gene changes the profile (e.g. faceting rate x2: season-mean depth hoar 16 to 26 cm, facets
  10 to 2 cm; vapour transport on: crusts 2.0 to 4.2 cm).
- Safety: an engine without the patch ignores unknown keys silently, so a training run refuses to start on it
  (`loop.check_engine_patches`) and an agent needing the patch reports the engine unavailable (lab) or is skipped
  with a warning (site agents). Rebuild once with `bash scripts/build_snowpack.sh`.

## ADR-093 Scoring version 4: weak layers by structure (owner, 2026-10-09)
Ben: "another useful criterion for determining a critical weak layer is a density change greater than one step, for
example Pencil to 4F. Remember, all critical layers need a harder bed surface, a weak layer and a slab." Versions 2
and 3 counted a layer of concern by grain class alone (surface hoar, facets, depth hoar, crusts), so a facet layer
inside uniform soft snow, or a crust, counted the same as a buried weak layer on a hard bed.

Version 4 (`scoring.structural`) applies one rule to the pit and to the forecast before the critical-layer score, the
class events of the Brier score and the weak-layer breakdown: a surface hoar, facet or depth hoar layer keeps its
class only when (1) a layer lies above it (slab), (2) the layer below is harder, or it is the bottom layer (the ground
is its bed), and (3) the hand hardness changes by more than one step (index F 1, 4F 2, 1F 3, P 4, K 5, +/- a third) at
its top or its bottom; otherwise it is `other`. Where the layer's hardness or both neighbours' are not recorded, the
structure cannot be judged and the grain class stands (as in version 3), so pits without hardness do not lose their
weak layers. Crusts are bed surfaces: they no longer count in the critical-layer score, but stay one of the four
Brier events and a row of the breakdown. Choices made where the owner's rule left room: the slab only has to exist
(any layer above); the jump may be at either edge; matching is unchanged (same class, within 0.15 relative depth).

Runs keep their scoring version (a run started under version 3, like the 50-round run, resumes and reports under
3); new runs, competitions and promotion checks use 4; blind-test agents keep the version of their freeze day.
Version-4 critical-layer scores do not compare with version 3. Standard SNOWPACK's version-4 numbers come with the
first run or competition on it.

## ADR-094 Training stops on its own when the locked-winter score is flat (owner, 2026-10-09)
The 50-round run gained almost all it would by round 8; rounds 9 to 50 added under a point on the locked winters and
cost about 22 hours. Ben approved the suggested changes before the next run, this among them. A new run now stops
after any round in which the best locked-winter composite (among that round's tested agents, standard SNOWPACK
included in round 1) has not beaten the best before it by more than 0.001 for 8 rounds in a row
(`training.loop.flat_rounds`). It then finishes normally: the winner is the last round's rank 1, the summary records
`rounds` (done), `rounds_planned` and `stopped_early`, the status says why, and the report notes it. A resume of a run
that stopped this way does not carry on. Choices made: the locked winters are the signal because they are the only
score never used to choose agents, so watching them cannot bias selection, though it does let them decide how long
the search runs (a mild use, accepted; the blind live test stays fully clean); the stop needs locked winters (with
`--locked-seasons 0` it is off); `--stop-when-flat N` and Training › Advanced set N (0 = off); runs started earlier
have no such key in their plan and resume as before.

## ADR-095 The group check: agents pooled, disagreement tested on the locked winters (owner, 2026-10-09)
Ben asked to use the agents' predictions "as a group" to find the characteristics forecasters should consider and to
flag high uncertainty and inconsistency, and chose to check first whether that helps before any site view. The
group check (`lab.services.group`, `snowagent lab group-check`, Reports › "The agents as a group") pools a group of
agents on a training run's locked test winters and scores the result. Choices made, the simplest consistent with the
principles:
- **Members.** Chosen for variety, one vote each: standard SNOWPACK, the hybrid agent, the top two agents of the last
  round of every training run whose locked winters cover the test winters and that was not seeded with agents that saw
  them (ADR-085), and four weather nudges (standard SNOWPACK with the measured precipitation x1.15 and x0.85 and the
  measured air temperature +1 and -1 K at every plot, the existing physics genes), because survivors of one run are
  close cousins and agree even when wrong. The analogue, persistence and weather-rule agents can be added but are
  out by default: they score well below SNOWPACK on layers and would drag a majority vote down. An agent that trained
  on the test winters is refused. Duplicates (the same genome) vote once.
- **Consensus profile.** Snow depth: median of the members' middle depths, range from the 10th and 90th percentiles
  of all their p10, p50 and p90. Structure on 40 relative-depth slices: the grain class most members give there,
  merged into layers (same class, hardness within 0.5), presence = share of members agreeing. Weak layers and crusts
  are thin and would vanish in a slice vote, so they are pooled separately: each member's forecast layers of concern
  (probability at least 0.5, classed as the active scoring version classes them) are grouped by kind within 0.10
  relative depth, one per member; a group layer forecast by at least a third of the members enters the profile with
  presence = that share. So a layer the majority forecast counts as forecast and a split one does not, and the Brier
  part sees an honest probability. Members that returned no answer count as doubt. The consensus is scored by
  `scoring.score_case` like any agent.
- **Disagreement.** Per pit: the spread of the members' middle depths (10th to 90th percentile), the share of the
  pack where they give different grain classes, and the weak layers forecast by a third to two thirds of them.
- **The three questions and their rules.** (1) Group vs standard SNOWPACK on the same pits: better when the mean
  composite is higher by more than 0.005 and it wins more pits than it loses, worse in the mirror case, else about
  the same; also against the member that did best (named after the fact, a tough bar). (2) Pits split into thirds
  by disagreement: disagreement is a useful warning ("yes") when the most-disagreed third's depth error is at least
  25% above the least-disagreed third's (layering: 0.05 lower layer score) and the middle third sits between; "weak"
  when only the ends differ that way; "no" otherwise. (3) Group weak layers by support (under 40%, 40 to 69%, 70% or
  more), each checked against the pit (same kind within the scorer's 0.15 relative depth): agreement means something
  when the 70%+ group is found at least 10 points more often than the under-40% group, with at least 5 layers in
  each. The report also counts pit weak layers no member forecast.
- **Cost and safety.** Members run on the test run's cases through the training cache (same seed, engine and
  analogue library as that run), so saved predictions are reused and new ones are saved. All code lives in
  `lab/services/` and the CLI, outside the prediction code hash, so no cached prediction is invalidated. Results go
  to `outputs/group_checks/<id>/`; nothing reaches the site. A group view on the site would follow only if the check
  shows it helps, after the blind live test, and on Ben's decision.


## ADR-096 Depth hoar genes, and wider limits for roughness and crust faceting (owner, 2026-10-19)
The third training run (19 rounds, stopped on its own) found 6% of the depth hoar layers in the locked winters'
pits (standard SNOWPACK 12%), the lowest of the four kinds, and its winner sat at the edge of two allowed ranges
(surface roughness, faceting next to crusts). Ben chose to add depth hoar settings and widen those limits before the
next run.
- SNOWPACK calls dry, non-dendritic faceted snow depth hoar only when its grain size (2 rg) is at least 1.5 mm and
  its sphericity below 0.4 (`ElementData::snowType`, installed source 20261008.b324cbd); grains reach that size only
  through kinetic growth in `Metamorphism::metamorphismDEFAULT`. No key sets either, so
  `scripts/snowpack-patches/lab-grain-size.patch` (applied after `lab-facet-knobs.patch`, recorded in
  `patches.txt`) adds two optional [SnowpackAdvanced] keys: `LAB_DH_GROWTH` multiplies the grain growth of old dry
  snow whose sphericity is below 0.5 (faceted grains), and `LAB_DH_SIZE` replaces the 1.5 mm threshold (the 0.7 and
  1.1 mm classes below it still apply when it is larger). Absent, they take the unpatched values.
- New block `snowpack_depth_hoar` (genome schema `lab-genome-5`; `sp_dh_growth` 0.5-3, `sp_dh_size_mm` 0.8-2.5 mm)
  for the SNOWPACK and hybrid families. Earlier agents load with both at their defaults and run as before.
- Wider limits: `sp_roughness_length_m` 0.0001-0.02 m (was 0.0005-0.01), `sp_crust_facet` 0.25-8 (was 1-4; values
  below 1 let an agent test less faceting next to crusts). Existing agents keep their values. The drift penalty is
  per allowed range, so a move of the same size now costs a little less on these two.
- Checked on the upstream MST96 example (Weissfluhjoch 1995-96; no Banff weather in the build container): with both
  patches and default genes the .pro is identical to the facet-patched engine's apart from its run stamp, so engine
  identity and saved engine profiles stay valid. Season-mean depth hoar 4.4 cm; `LAB_DH_SIZE` 1.2: 8.7 cm;
  `LAB_DH_GROWTH` 2: 11.3 cm, 0.5: 2.2 cm; snow depth unchanged (83 cm).
- The genome config changes, so new runs do not reuse earlier runs' saved predictions (round 1 runs SNOWPACK afresh).
  A training run refuses an engine without the new patch; rebuild once with `bash scripts/build_snowpack.sh`.

## ADR-097 A promotion check refuses a run seeded with agents that saw its held-out winters (2026-10-20)
A promotion check re-runs a training run once per held-out winter from the run's own starting population (ADR-085
seeds included). When those seeds came from a run that trained on that winter, every fold starts from agents that
have seen it, so the fold's winner is not tested on an unseen winter. Found when the fourth run (seeded from the
third run's best three) was about to be checked on 2020-21 to 2022-23, which the third run trained on.
`loso.seeded_saw(plan, seasons)` names those winters; `check_loso` (estimate included) refuses with a message that
names the source run, and the Training page shows the same and disables Estimate and Start. Check an agent of the
source run instead. Folds of winters the seeds never saw are unaffected. Seeds are not dropped from the folds:
that would check another procedure than the run's. The training report's suggested check says the same, and its
suggested population now leaves room for family slots (survivors plus one child per family; it said 6, which a run
with 3 survivors and family slots refuses).
Also: a run started under genome schema 4 (the third run's starting agents) crashed in its second round under the
depth hoar code (`KeyError: 'sp_dh_growth'` when mutating), which broke a promotion check of that run. The loop now
breeds from copies of the survivors and family bests upgraded to the current schema (new genes at their defaults);
the survivors themselves are kept unchanged. In `lab/training`, so no cached prediction is invalidated.

## ADR-098 Training reports and the group check get their own pages, answers first (owner, 2026-10-20)
The owner found Results › Reports hard to follow: a radio button chose between a training report and the group
check and reset to the training report on every refresh; a running group check showed a frozen "starting ..." for
hours though it had finished in 5 minutes (the page did not update itself); the training-run list defaulted to a
promotion check's fold, under an internal id. Now Results holds two pages. **Training reports** lists real training
runs only (folds of promotion checks are hidden, recognised by their case set as well as their name), each by start
time, rounds done, state and best agent, and writes the report as soon as a run is picked (cached for the session);
round, rank and the technical appendix sit in a collapsed box. **Group check** leads with the newest check's four
answers as coloured cards (the same fixed rules as the report) and the forecaster advice, then the downloads and the
full report; a running check's log updates itself every 5 seconds and the answers replace it when it ends; starting
a new check is one box, open only when there is no check yet. Pages have their own address, so a refresh keeps the
page. Only `lab_app` and `lab/services` changed; no cached prediction is invalidated.

## ADR-099 Scoring version 5: weak-layer structure judged on the pit, facets and depth hoar one family (owner, 2026-10-22)
Three studies of version 4 (in the project's shared folder, `lab/analysis/`, 22 Oct 2026: depth hoar misses, sharp
hardness changes, scoring options) found that whether a faceted layer has the "more than one step" hardness change
is close to a coin toss for SNOWPACK away from crusts (its pass or fail is independent of the pit's), and that two
real pits a few days apart keep a "sharp" call only 46% of the time. Judging it on the forecast too turned small,
unpredictable hardness differences into misses and false alarms: a forecast with no weak layers at all scored 31.8
on weak layers, standard SNOWPACK 35.2. Facets and depth hoar also differ only in grain size (SNOWPACK renames at
1.5 mm, observers by eye), so a facets-for-depth-hoar swap at the right depth counted as a miss and a false alarm.
Of three options tried on all 945 cases (standard SNOWPACK and run 4's Hesh Bucco), the owner chose the one that
separates a real forecast from fake ones best (Ben, decision card, 2026-10-22: "Option 2").

Version 5:
- The pit's surface hoar, facet and depth hoar layers go through version 4's structure test (slab above, harder bed
  or the ground below, more than one step of hardness change at top or bottom; unrecorded hardness keeps the class).
  Those that pass are the targets; those that fail keep their class, marked not sharp (`structural(keep=True)`).
- The forecast's layers are taken as they are: their hardness no longer matters to the weak-layer score.
- Families: facets and depth hoar are one (`faceted`); surface hoar is its own.
- A forecast weak layer (presence probability at least 0.5) is a hit when it pairs with a target of its family within
  0.15 relative depth (one to one, nearest first). A left-over forecast layer is a false alarm only when the pit has no
  weak layer of its family, sharp or not, within 0.15; otherwise it is ignored (the pit has that snow there).
  CSI = hits / (targets + false alarms); with no target, 1 / (1 + false alarms), as before.
- Brier events become three, judged the same way: surface hoar, faceted snow and crust. A weak-layer event happens
  when the pit has a sharp layer of that family; an event whose family the pit has only without the structure is left
  out; the forecast's probability is 1 - prod(1 - p) over its layers of the family, whatever their hardness.
- The weak-layer breakdown counts sharp pit layers as observed, finds across the family, and records false alarms
  per kind (`wl_<kind>_false`, kept for every version from now on; older rows fall back to forecast less found).

Checks: on the 945 cases the critical-layer score equals the option-2 study case by case. Standard SNOWPACK: weak
layers 35.2 -> 56.8, overall 52.3 -> 58.2 (57.6 with the old Brier events); Hesh Bucco 37.3 -> 58.6 and 54.2 -> 60.0;
the best fake forecast (every layer facets, or facets over depth hoar) scores 37.9 on weak layers, 18.9 below
SNOWPACK. Depth hoar found by SNOWPACK: 218 of the 244 sharp pit layers (38 under version 4).

What it gives up: the score no longer says whether a forecast told depth hoar from facets (the breakdown still
does), nor which faceted layer is the sharp one (the pit decides that). Runs keep their scoring version (ADR-088):
finished and resumed runs report under the version they started with; new runs, competitions and promotion checks
use 5; stored competitions can be re-scored from their predictions (Leaderboard › Re-score, or `snowagent lab
rescore`); blind-test agents keep the version of their freeze day. Version-5 scores do not compare with version 4.

## ADR-100 Snowfall from the plot's snow depth sensor, as an agent gene (owner, 2026-10-24)
Five training runs tuned SNOWPACK's settings to about +2 points on their training winters and nothing on others (the
full check of Vito Highgrove failed; the fifth run, the first under scoring version 5, overfit). The owner chose to
look at snow depth next (decision card, 2026-10-24: "Snow depth"). The plots' snow depth sensors read within 3 to 6 cm
of the observers' depth on pit days (correlation 0.97 to 0.99, 2015 on), while free-running SNOWPACK drifts from
them: by spring 20 to 50 cm shallow at Goat's Eye and 10 to 25 cm at Simpson, about 10 cm deep at Bow Summit in
October to December. SNOWPACK has a mode driven by measured snow depth, the one operational station runs use. The
owner chose to add it as an agent gene rather than a lab-wide switch (decision card, 2026-10-24: "Agent setting").

The gene `sp_snowfall_source` (block `snowpack_snowfall`, SNOWPACK and hybrid families, genome schema 6), choices
`precipitation` (default, the incumbent) and `snow_depth`:
- The case's observed snow depth readings with QC `ok` are despiked: a reading is dropped when it is further from
  the last kept one than 0.10 m + 0.08 m per hour since, capped at 0.6 m, and the first kept one must be under 0.5 m.
  Station QC passed stuck values (Bow Summit read 2.21 m for days in January 2023), and the engine only adds snow,
  so one such reading would stay in the profile all winter. Then a 3-hour centred running median; gaps up to 48 h are
  interpolated, longer ones hold the last value (the engine stops on a missing HS while driven by it, and holding is
  what it would do), and hours before the first reading are 0.
- The switch is 00 UTC on the day of the last observed hour: a segment shorter than a day writes no profile. Segments
  ending at or before it run with `ENFORCE_MEASURED_SNOW_HEIGHTS = TRUE` and the forcing's HS column; later ones run
  on precipitation without the column. A switch on a pit update day is that update; otherwise it is one more segment
  boundary that carries the state on unchanged (its shared-restart key records the switch instead of a pit).
- In the driven segments `THRESH_RH = 0` and `THRESH_DTEMP_AIR_SNOW = 100` (both [SnowpackAdvanced]). Snowpack.cc
  compSnowFall only accepts a rise in measured depth as snowfall when humidity is above THRESH_RH and air minus
  surface temperature is below THRESH_DTEMP_AIR_SNOW. With the reanalysis longwave (low in storms) the modelled
  surface runs several degrees below the air and the gate refused real storms (Bow Summit, December 2025: the sensor
  rose 83 -> 152 cm and the model stayed near 100). The rain/snow phase PSUM_PH still gates it.
- Coverage: a case whose kept readings cover less than 60% of the hours from the run start to the switch runs on
  precipitation throughout; the run's notes record which source it used. Before 2015 no plot has a sensor.
- Keys verified in the pinned source (SnowpackConfig.cc: ENFORCE_MEASURED_SNOW_HEIGHTS [Snowpack], THRESH_RH and
  THRESH_DTEMP_AIR_SNOW [SnowpackAdvanced], defaults 0.5 and 3.0; Snowpack.cc compSnowFall). The study plots are
  flat units, so the measured-height branch applies (it needs slope <= min_slope_angle). HS is written into the
  forcing file only when the forcing has the column, so default runs are unchanged byte for byte.

The standard model `snowpack-snow-depth` (`lab.genome.NAMED_MODELS`) is the default SNOWPACK genome with only this
gene changed, so it can be compared in competitions like a family default.

Checks: 327 cases with a sensor (2015-16 to 2025-26), scoring version 5, against standard SNOWPACK: overall 56.2 ->
57.5; snow depth error 10.3 -> 8.3 cm; weak layers 51.3 -> 52.2; layering 51.2 -> 51.4; better in 170 cases, worse in
127; better in all 11 winters (2015-16 tied) and 25 of 30 plot-winters; mean gain per winter +1.8 points (bootstrap
95% +1.0 to +2.5). The locked winters 2023-24 to 2025-26 (68 cases): 56.0 -> 57.3, weak layers 57.6 -> 60.1. Nothing
was tuned on these cases (the despiking and coverage limits were set from what snow can do and from one stuck
sensor), so the gain is not a training-winter gain. The lab implementation reproduces the prototype's snow depth to
the millimetre on the cases compared. Write-up: shared folder `lab/analysis/snow-depth-driven-2026-10-24.md`.

Not done here: the site's daily runs still use precipitation; a site change follows only if the owner promotes this.
Side finding for later: the low storm longwave probably also chills the surface of standard SNOWPACK.

## ADR-101 Site agents follow the snow depth sensor like the lab (2026-10-24)

Context: the site's experimental agents (ADR-084) applied the physics genes but not `sp_snowfall_source` (ADR-100),
so an agent sent to the site with snowfall from the sensor would have run on precipitation under its own name. The
daily update already collects the plots' snow depth (FTS360, the `hs_check` stations of config/plot_forcing.yaml) and
the site shows it beside the model.

Decision: `ops.site_agents.run_agent_season` runs such an agent as the lab does, with the same rules
(`lab.agents.snowpack.snow_depth_forcing`, shared): per hour the first `hs_check` station with a QC-ok reading (the
lab's weather ingest picks the same way, ADR-056); despiked, smoothed, gaps bridged; a run driven by it from
snow-free to the switch, then a precipitation run from that state to the season's end (now, for the live season).
The switch is 00 UTC of the last reading's day, a day earlier when that leaves under 6 h (the second run must write a
profile; the site writes one every 6 h). With too few readings (coverage under 60%) or in a reanalysis-only season
the agent runs on precipitation. The output records which (`snowfall`), and the forcing hash covers both runs.

Check: Goat's Eye 2024-25 in the container, standard SNOWPACK vs the standard model with the sensor: daily snow depth
against the sensor, mean absolute error 24.0 -> 7.7 cm over 229 days; same profile count; mid-season runs switch at
the expected 00 UTC. Nothing changes on the site until an agent with this setting is sent there; standard SNOWPACK
and other agents run as before.

## ADR-102 The site's SNOWPACK takes its snowfall from the snow depth sensor (owner, 2026-10-24)

Context: the lab's standard model with snowfall from the plot's snow depth sensor (ADR-100) beat standard SNOWPACK
in the owner's own competition on his machine (competition-20261024T134445Z-aeeaf7, all 945 cases, scoring version
5): 58.6 vs 58.1 overall; on the 340 cases from 2015-16 on 56.8 vs 55.2, better in 9 of 11 winters (2015-16 tied,
2022-23 -0.6) and 25 of 32 plot-winters, mean gain per winter +1.7 (bootstrap 95% +0.8 to +2.6), the locked winters
2023-26 56.9 vs 55.7, snow depth error 8.3 vs 10.2 cm; identical before 2015 (no sensor). Nothing in it is fitted to
these cases, so every winter is held out (Principle 3). The owner chose "Main forecast" on the card: the site's
SNOWPACK follows the sensor, with the previous standard kept beside it to compare.

Decision (`web.build`, `SNOWFALL_SOURCE = "snow_depth"`; a site choice, so not in config/plot_forcing.yaml):
- Seasons with measured weather (station or live) run the free run driven by the plot's measured snow depth (the
  `hs_check` stations, first QC-ok reading each hour, despiked and smoothed by the lab's rules,
  `snow_depth_forcing`) from snow-free to 00 UTC of the last reading's day (a day earlier when under 6 h remain),
  then on precipitation from that state (`engine.column.run_span`). The driven part uses the lab's settings
  (`ENFORCE_MEASURED_SNOW_HEIGHTS`, `THRESH_RH = 0`, `THRESH_DTEMP_AIR_SNOW = 100`, ADR-100).
- Pit updates (ADR-038) work as before; a continuation from an update before the switch is again driven by the
  sensor up to the switch (`learn.steer.steered_run` with `hs`, `switch`), as the lab's steered agent does.
- Forecasts start from the daily states of that run and run on GFS precipitation as before; forecasts already
  stored as issued stay as they were (ADR-054).
- Too few readings (under 60% of the hours to the switch) or a reanalysis-only season: precipitation, as before.
  The season file says which (`snowfall`), and its run id hashes both runs' forcing and settings.
- The previous standard (precipitation, with pit updates) is built beside it for seasons where the sensor drove the
  run: `nowcast_precip`, `daily.hs_model_precip`, pit scores `nowcast_precip`; the page offers it as the weather
  input "Previous standard: snowfall from precipitation" and as a dashed line in the depth chart.
  The reading and switch helpers moved from `ops.site_agents` to `baseline.snow_depth`, since the site build is
  inside the lab's code hash and must not import `ops/` (ADR-080).
  `build_season(..., snowfall_source="precipitation")` gives the previous output byte for byte (checked on Goat's
  Eye 2024-25 against `main` before this change).

Check (Goat's Eye 2024-25, built in the container): pit-updated nowcast at the 5 pits, depth error 6.6 vs 10.1 cm,
grain agreement 0.67 vs 0.61, boundary F1 0.27 vs 0.20, hardness difference 0.64 vs 0.73 steps. The build takes
about 1.7 times as long per sensor season (the comparison run). The live season (Bow Summit 2026-27) and two
forecast issues ran from the driven states without errors.

## ADR-103 Weak layers SNOWPACK forms in snow since the last pit count as unlikely (2026-10-25)

Context: five training runs showed that retuning SNOWPACK's settings has run its course (about +2 points on training
winters, nothing on others), and the owner asked to start changing the model itself (2026-10-25). Where standard
SNOWPACK with the snow depth sensors (ADR-100) loses most is the weak-layer score, and most of that loss is false
alarms. Splitting its forecast weak layers (probability 0.5 or more) in 945 cases by where they sit: in snow that
fell since the case's last visible pit there were 1384, of which 56 matched a pit weak layer and 763 had no pit weak
layer of their family near them (0.15 relative depth); in older snow 864 matched and 719 were false alarms. SNOWPACK
facets new snow far more than the pits show. The temperature profiles point to why: against the pits' measured
temperatures (859 cases), the model's gradient over the top 30 cm has a median of 21 to 35 K/m (sensor era, before)
where the pits show 0 to 4 K/m, and its surface is 5 to 9 degC colder, so new snow passes the 5 K/m faceting
threshold of the metamorphism scheme early. Two physics changes aimed at that cause did not beat it on a sample of
237 cases: the existing surface genes (stability scheme MO_MICHLMAYR or NEUTRAL, +15 W m-2 longwave, roughness
0.01 m) scored -0.4 to -1.6 points, and a test build moving the rounding/faceting threshold to 7.5 or 10 K/m -0.3
and 0.0. So this change works on the output: it calibrates how sure the agent is of a layer, from what the pits
showed, and adds no layer or property.

Decision: a SNOWPACK-family output gene `sp_new_weak_factor` (block `snowpack_new_weak`, genome schema 7, range
0.2-1, default 1 = unchanged) multiplies the presence probability of facets, depth hoar and surface hoar whose
middle is higher above the ground than `NEW_SNOW_FRAC` = 0.9 x the snow depth of the season's latest visible pit
with layers (`lab.agents.snowpack.new_snow_floor`; 0.9 because the old snow settles under the new). Cases without
such a pit are unchanged. The layers stay in the profile with their grain and hardness; only their probability
drops, and a weak layer under probability 0.5 does not count as forecast in the critical-layer score (ADR-099). The
prediction metadata counts the lowered layers (`new_snow_weak_layers`) and its limits name the rule. It changes no
engine run, so agents differing only in it share the engine profile, and older genomes behave as before (missing
gene = 1). The hybrid's engine member keeps 1, and the site's agents draw SNOWPACK's raw layers as before (output
genes do not apply there, ADR-084). The standard model `snowpack-new-snow-check` is `snowpack-snow-depth` with the
factor 0.4 (probability 0.7 x 0.4 = 0.28).

Choice of rule (offline re-score of the 945 predictions of `snowpack-snow-depth`, 2026-10-25): probabilities from
0.1 to 0.45 for the lowered layers all gave +2.46 points (the score only asks whether a layer is above 0.5); leaving
surface hoar out gave +2.37; also lowering surface hoar in old snow gave +2.58 overall but only +1.07 on the locked
winters, so it was not taken. The 0.9 share was not tuned.

Check (cloud competition of `snowpack-snow-depth` and `snowpack-new-snow-check`, all 945 cases, scoring version 5,
2026-10-25): overall 58.3 -> 60.8 (+2.46, standard error 0.20; standard SNOWPACK on precipitation is 57.7); weak
layers 57.0 -> 65.9; snow depth and layering identical, uncertainty 65.8 -> 65.9. Better in 299 cases, worse in 136,
the same in 510; better in 26 of 28 winters (worst 1998-99, -0.2) and 57 of 66 plot-winters, mean gain per winter
+2.4 (bootstrap 95% +1.8 to +3.0); Bow Summit +1.7, Goat's Eye +3.0, Simpson +4.6; from 2015 on +3.5, before +1.9.
The locked winters 2023-26 (68 cases): 57.3 -> 59.3, weak layers 60.1 -> 67.3. Pit weak layers found: facets 725 ->
705, depth hoar 219 -> 218, surface hoar 63 -> 56 (28 fewer); false alarms: facets 1339 -> 691, depth hoar 205 -> 96,
surface hoar 154 -> 126 (785 fewer). `snowpack-snow-depth` scored exactly as in the run before this change on every
case, so the default path is unchanged. The full-run gain equals the offline re-score's.

Caveat: the rule was found by looking at all 945 cases, the locked winters included, so they are not a clean test
of it. It has no fitted number that matters (any probability from 0.1 to 0.45 scores the same; 0.9 untuned) and it
gains in 26 of 28 winters, so it is unlikely to be luck, but the clean test is the owner's run on his machine and
this winter's pits. The gene is evolvable, so training runs can move it.

## ADR-104 The site marks weak layers in snow since the last pit as unlikely (owner, 2026-10-25)

Context: the new-snow check (ADR-103) was confirmed in the owner's own competition on his machine
(competition-20261025T131525Z-c58ad1, all 945 cases, scoring version 5): 60.9 for `snowpack-new-snow-check` vs 58.6
for `snowpack-snow-depth`, snow depth and layering identical. The owner chose "Add it" on the card "Show the new-snow
check on the public site?". The site draws SNOWPACK's raw layers and has no layer probabilities (ADR-084); its only
weak-layer mark is the grain-form flag (bit 2: FC, FCxr, DH, SH, the same forms as the lab's facets, depth hoar and
surface hoar).

Decision (`web/app.js`, display only): on the main forecast (weather input "Measured weather (nowcast)" and the GFS
forecasts) a weak-layer-flag layer whose middle is higher above the ground than `NEW_SNOW_FRAC` = 0.9 x the snow depth
of the latest pit with layers is marked unlikely (flag bit 4): drawn faded with a dashed outline, "unlikely (new
snow)" in the tooltip and the table, counted in the profile's summary line, and the pit used named in its trace line.
The pit's depth is its recorded HS, else its top layer's height, as the lab's `pit_snow_depth`. Which pit: a forecast
uses any pit before its issue time (as the lab's cases); a nowcast uses a pit once its pit update has run (00 UTC the
next day, ADR-038; the profile at that hour is still from before it), so the profile shown at a pit's own time never
uses that pit. A season without such a pit is unchanged. Not applied to the comparison inputs ("without pit updates",
"Previous standard") or to experimental agents, whose genomes predate the gene (ADR-084).
The rule runs in the page on the season file's pits, so the site data format, the stored as-issued forecasts
(ADR-054), the pit scores and the lab's prediction code hash are all unchanged, and every season shows it without a
rebuild. The share 0.9 is checked against the lab's constant by `tests/unit/test_web_new_snow.py`, which runs the
rule's block of `app.js` in node.

Check (the site's 2025-26 data, nowcast nearest each pit): at the 10 pits with an earlier pit that winter, 20 of the
89 weak-layer-flag layers are marked unlikely, at 7 pits (Bow Summit 4, Goat's Eye 2, Simpson 1). At 6 of those 7
the observers recorded no facets, depth hoar or surface hoar in the snow since the earlier pit; at Bow Summit on
2026-03-04 they did (two facet layers and surface hoar), and the check marked the model's 4 facet layers there unlikely all the same.

## ADR-105 Standard models can enter the blind test (2026-10-25)

Context: the new-snow check (ADR-103) was found by looking at the same 945 cases it was then scored on, the locked
winters included, so no lab winter is a clean test of it; the owner's own run confirmed it on those cases (ADR-104).
The blind test (ADR-086, ADR-090) is the one test nothing can leak into, but it froze only agents of training runs,
and `snowpack-new-snow-check` is a standard model (`lab.genome.NAMED_MODELS`), not a run's agent. No pit of 2026-27
existed when this was written.

Decision: Training › "Blind test on this winter" also offers "Or a standard model": any of `NAMED_MODELS` (standard
SNOWPACK itself is the comparison and is not offered), frozen with `lab.services.blind_test.standard_entry_record`.
The entry is the same record as an agent's (genome, code, scoring version, freeze time) with an empty source, since
no training run made it; it counts against the winter's five places and is permanent like any other. The daily
scoring (ADR-090) treats it like any frozen agent: on pits dug after the freeze, beside standard SNOWPACK. The entry
format (`BlindEntry`, `SiteAgentSource`) is unchanged; the app lists such an entry as "standard model". No prediction
module changes, so the lab's code hash is unchanged.


## ADR-106 Reanalysis precipitation takes its plot's own factor, not the gauge's (2026-10-25)

Context: after the new-snow check (ADR-103) the next change was looked for where `snowpack-new-snow-check` still loses
points (945 cases, scoring version 5). Layering (54.0) is close to what the pits allow: the engine's grain types were
recorded on every scoring slice, and in old snow the mismatches (pit rounds or depth hoar drawn as facets, pit facets
drawn as rounds or depth hoar) mostly repeat the last pit's call, which the pit restart (ADR-038) copies; the last pit
at the same relative depth agrees with the new pit on 61.9% of old-snow slices, the model on 64.7%. Copying the last pit
as it is (the persistence agent) scores layering 56.4 where that pit is 7 to 14 days old, the model 56.9 on the same
cases. Grain classes from other sphericity and grain-size thresholds lowered agreement. Snow depth has more room: the
model's error is 9.5 cm, while pit and plot sensor depths differ by 5.2 cm at the pit time (167 pits). Its largest bias
is at Goat's Eye before 2015-16: -8.9 cm (72-hour cases -12.2 cm). There the lab's cases have no gauge record and take
ERA5 precipitation (cell at 2004 m, 276 m below the plot), and `engine_forcing` multiplied every measured hour by the
plot's `psum_factor` 0.9, which ADR-038 chose for the Sunshine gauge (over-catch, 2015-26 station forcing). Bow Summit's
lab cases also take ERA5 until its gauge record, with its 1.15 (ADR-024), and are unbiased (+0.6 cm before 2015). The
training runs' agreement on more snow at Goat's Eye (x1.12, 2026-10-08) came from this: the gene also raised the gauge
hours of later winters.

Decision: a physics choice gene `sp_era5_precip` (block `snowpack_era5_precip`, genome schema 8): `gauge_factor`
(default, unchanged) or `era5_factor`, carried by SNOWPACK agents and the hybrid's engine member. With `era5_factor`,
hours whose precipitation source is an ERA5 cell take the plot's `era5_psum_factor` (config/plot_forcing.yaml) instead
of `psum_factor`; the plot's precipitation gene multiplies both and GFS hours stay raw. Goat's Eye 1.3; Bow Summit and
Simpson keep 1.15, so only Goat's Eye changes. The standard model `snowpack-era5-fix` ("SNOWPACK with sensors, new-snow
check and snowfall fix") is `snowpack-new-snow-check` with `era5_factor`. Site agents (`ops/site_agents.py`) do not read
the gene: the site's winters take precipitation from the gauges and new snow from the sensors. A schema version changes
every genome's hash, so the new-snow check frozen for the 2026-27 blind test under schema 7 has another id than the same
model built now; `lab.services.blind_test.frozen` also gives each entry's id under the current schema (`same_as`), which
the Training page and `freeze` use, so it still counts as frozen and cannot take a second place.

Choice of 1.3 (cloud, the 275 Goat's Eye cases of 1998-99 to 2014-15, every measured hour ERA5's, so the existing
`sp_precip_mult_goat` gave the same forcing; nothing else changed from `snowpack-new-snow-check`): factors 0.9 (now),
1.0, 1.1, 1.2, 1.3 and 1.35 (the gene's limit) scored 58.97, 59.63, 60.47, 61.37, 61.61 and 61.75; depth bias -8.9,
-7.6, -6.3, -5.0, -3.8 and -3.2 cm (mean error 11.3 cm now, 9.0 cm at 1.3); the next-pit cases' bias crosses zero at 1.3
(+0.1 cm) while the 72-hour cases stay low (-7.3 cm). Leave-one-winter-out picked the top of the range in all 16 folds,
with the score flat from 1.2; 1.3 is taken where the next-pit depth is unbiased rather than at the edge of the range.
1.3 against 0.9: +2.64 points, better in 178 cases and worse in 97, better in 14 of 16 winters (2014-15 38.3 -> 61.5;
without it +2.1); layering 54.0 -> 55.3, weak layers 66.2 -> 67.3, uncertainty 62.4 -> 65.4.

Check (cloud competition of `snowpack-era5-fix`, all 945 cases, against `snowpack-new-snow-check` of the ADR-103 run,
2026-10-25): leaderboard 60.96 -> 61.92 (case mean +0.81, standard error 0.15; robustness 62.5 -> 64.9); snow depth 61.0
-> 62.7, layering 54.0 -> 54.4, weak layers 65.9 -> 66.3, uncertainty 65.9 -> 66.8. Depth error 9.5 -> 8.8 cm, bias -2.6
-> -1.1 cm; Goat's Eye before 2015-16 11.3 -> 9.0 cm, bias -8.9 -> -3.8 cm. Better in 199 cases, worse in 112, the same
in 634: every Bow Summit and Simpson case scored exactly as before, and 36 of Goat's Eye's 127 later cases moved a
little (+0.16 on average over the 127; ERA5 hours filling gauge gaps). Better in 22 of 28 winters, worse in 2 (2003-04
-0.09, 2010-11 -0.25), the same in 4. The locked winters 2023-26: 59.28 -> 59.39.

Caveat: 1.3 was chosen on the cases it is scored on. ERA5 short by about a quarter at a plot 276 m above its cell is
plausible (orographic enhancement the reanalysis's smooth terrain lacks) but it is a fitted number. The 72-hour cases
stay biased low at both plots (Goat's Eye -7 cm, Bow Summit -3 cm before 2015), a separate question.

## ADR-107 The site is published from a `site` branch that Netlify deploys (owner, 2026-10-27)

Context: the daily update deployed the site with the Netlify connector's deploy-site command (ADR-045). The update
runs unattended at 07:05 Mountain time, and the cloud session's safety check refuses that production deploy unless
the owner types "deploy" in the thread that day. The permission rules in `.claude/settings.json` (since 2026-10-02)
and the owner's standing permission in the scheduled task's prompt (2026-10-26) did not change that (2026-10-27).
Pushing to the repository is allowed.

Decision (owner's choice, 2026-10-27): `snowagent update publish` runs the deploy check (ADR-045, ADR-047) on a copy
of `web/` and, only when it passes, force-pushes the copy as one commit with no parent to the branch `site`. The
Netlify site is linked to the repository with `site` as its production branch, so Netlify publishes each push; it
reads `netlify.toml` at the branch's root, the same file as `web/netlify.toml`. The branch holds one commit, so the
repository does not keep about 100 MB of site data a day (earlier copies are no longer referenced). The copy takes
every file of `web/` except hidden ones (a command-line deploy leaves `.netlify/` in the folder it deployed), so a
new page such as `pit/` is published without changing the command. The connector's deploy-site route stays for a
person's own deploy; the next push to `site` replaces it. A `netlify.toml` at the root of the other branches
cancels any Netlify build of them (`ignore = "exit 0"`), so linking the wrong branch by mistake, or a pull request,
cannot publish the repository's own files in place of the site.


## ADR-108 New sites outside Banff: Parker Ridge (Jasper) and Fidelity (Rogers Pass) (owner, 2026-11-02)

Context: the owner asked (2026-11-02) whether models can be built for Parker Ridge in Jasper and the Fidelity study
plot in Glacier National Park, and said to start building before the pits arrive (Parker Ridge pits expected in
a couple of weeks; none yet for Fidelity). The owner's "NOAA through the wx actuals app" is the GOES satellite
(LRGS) feed in the Rockies Weather Explorer: live readings only, and no Glacier stations on it, so it supplies no
history. Overfitting to the three Banff plots is the owner's main concern, and new plots in other snow climates are
the cleanest out-of-sample test of what was tuned in Banff.

Data found (2026-11-02):
- Parker Ridge: the Big Bend station (FTS360 agency 450, `BIG BEND 2125m`, 52.178 N 117.073 W, 2.9 km from the
  plot) is a full plot station in the same FTS360 feed as Banff: air temperature, humidity, wind, a cumulative
  gauge (`Precip`), snow depth (`SD`), a new-snow board, snow temperatures. Hourly from 2018-11-20 (readings at :58
  until 2019-02-05). Tangle Ridge (3009 m) adds ridgetop temperature, humidity and wind from 2020-03. The plot is
  located from the owner's map marker (2026-11-02): 52.19587 N 117.10408 W, 2010 m, 2.9 km north-west of Big Bend
  and 115 m below it. The snow depth check is Big Bend's sensor, the nearest one.
- Fidelity: not in FTS360. The Avalanche Canada weather API (README §6) has station 93 (FID_60, 1905 m) hourly from
  2023-05 (temperature, snow height, precipitation; some months part-missing). Its humidity field repeats the air
  temperature or reads 0-6 %, so humidity comes from Round Hill (station 99, 2100 m, 0.5 km away). The long
  Fidelity record would have to come from the Rogers Pass team.
- Both plots are outside the Banff ERA5 box (50.5-52.0 N, 117.0-115.25 W).

Data problems found and handled (flag, don't delete):
- From 2021-11-21 to 2023-06-12 the FTS360 copies of Big Bend and Tangle Ridge carry whole numbers only and almost
  nothing below zero: temperatures lost their sign (December means of +15 deg C). Those temperature readings are
  written as missing in the converted table and every hour is listed in the station's note
  (`data/interim/new_sites/stations/<key>/<key>.json`; Big Bend 13 113 h, Tangle Ridge 13 086 h); ERA5 fills them.
  Humidity, wind, snow depth and the gauge total in those months are whole numbers but cannot lose a sign, and
  are kept. Rule: a 3-day window of at least 24 readings, at least 80 % whole numbers and under 10 % negative
  (normal records have decimals on ~90 % of readings).
- Big Bend's gauge climbs ~40 mm a day for weeks in October 2024 and June 2025 (a drift, not snow). The baseline
  assembler's existing daily plausibility check against ERA5 (four times the usual ratio and over 15 mm) replaces
  such days.

Decision:
- New sites live apart from Banff so nothing in Banff changes: plot recipes in `config/new_sites.yaml` (same layout
  as `config/plot_forcing.yaml`, factors all 1.0, nothing tuned); stations in new config sections
  (`fts360_new_sites`, `avcan_weather`) that the daily update does not read; ERA5 for a `north_west` box
  (50.9-52.5 N, 118.1-116.6 W) in `data/interim/era5_north_west`; converted station tables with the Banff
  FTS360 column names in `data/interim/new_sites/stations/`, which `load_station` and the baseline assembler
  read unchanged. Raw files are archived like Banff's (`archive/fts360/<key>/`, `archive/avcan_weather/`) so a
  checkout without the FTS360 credential (the Mac) can rebuild them with `--offline`.
- The code is in a new package `snowagent.newsites`, outside the lab's prediction code hash (`CODE_EXCLUDE`); no
  hashed module imports it (a test checks). The Banff code hash and engine-files hash are unchanged
  (`1ac18aaf...`, `cfcb86cb...`), so the owner's training cache stays valid. If a new site joins the lab, the
  package comes back into the hash.
- `snowagent baseline --plot-config config/new_sites.yaml` runs SNOWPACK at the new plots (station-first forcing,
  ERA5 fill) and scores it against the plot's own snow depth sensor.
- Pits: `config/observations.yaml` lists `parker_ridge` and `fidelity` as study plots with folder aliases, so a pit
  filed under `profiles/<season>/Parker Ridge/` (or `Fidelity/`) is read like a Banff pit and carries the new key.
  The lab skips such records as not one of its sites (`not_a_lab_site:parker_ridge`), so the Banff cases do not
  change; the baseline scores them. The site's upload form still offers only the Banff plots (its code is in the
  hash), so a batch of new-site pits is filed into the folder directly. Fidelity is in British Columbia: whether its
  pit times are written in Mountain or Pacific time is to be checked when its first pits arrive (the intake reads
  times without a zone as MST for every plot).
- First use, once pits arrive: an out-of-sample test of the Banff-tuned model with Banff settings unchanged. Training
  on the new sites, putting them on the public site, or adding them to the daily update are each the owner's call.

Verification (2026-11-02, uncorrected baseline, plots as configured, nothing tuned; snow depth only, no pits yet):
daily HS against the plot's sensor (Big Bend for Parker Ridge, 2.9 km away and 115 m higher). Parker Ridge, eight
winters 2018-19..2025-26: MAE 12.5, 9.0, 7.5, 17.9, 8.3, 5.8, 8.2, 25.5 cm (mean 11.8 cm, bias -5.8 cm); 2021-22
runs mostly on ERA5 temperature (sign-lost readings); 2025-26, a deep winter, falls behind in the December and March
storms and melts early (peak 143 vs 188 cm). Fidelity, 2023-24..2025-26: MAE 10.7, 14.8, 12.5 cm (mean 12.7 cm, bias
+3.5 cm), peaks within 3 cm each winter (240/243, 283/284, 390/392 cm). For scale, the Banff plots' held-out HS MAE
was 16-18 cm uncorrected and 12-13 cm with their snowfall factors (ADR-024, ADR-038). Write-up for the owner:
`/mnt/project-files/lab/analysis/new-sites-first-runs-2026-11-02.md` (project files).

Open: whether Rogers Pass can share Fidelity's long record; Fidelity's exact plot location (placed at the station);
pits for both plots.

## ADR-109 The Snowpack Translator reads a spoken observation with a fixed word list (owner, 2026-11-14)

Context: the owner asked (2026-11-14) for the Snowpack Translator to take verbal input and build a snow profile that
exports as a PDF in the Snow Scope and Propagation Labs style, and to turn spoken weather and conditions into a
satellite text message on an iPhone. He supplied a Snow Scope profile and six screens of its phone recorder as the
layout reference, and said the tool is used mainly on a phone. His standing requirements for the Translator
(2026-10-27) still hold: no AI deciding layers, installs on a phone, works with no signal.

Decision:
- Words to text is the phone's job. The page is a text box: the keyboard microphone dictates into it, and that
  works offline on recent iPhones. A home-screen web app on iOS has no Web Speech API (WebKit bug 225298), so
  live listening is used only where the browser offers it, and never required.
- Text to observation is a fixed vocabulary, not a model (`web/pit/js/parse.js`): keywords split the sentence
  (layer, temperature, test, site and weather fields), a phrase table maps hardness, grain forms and their common
  mis-hearings ("for fingers", "faucets"), and anything left over is reported as "not understood", never guessed.
  Nothing is added until the observer taps Add, and the reading is shown first. This keeps principle 1: the page
  records what the observer said; it does not infer structure.
- One observation model (`model.js`) stores depths below the surface with a `ref` of height or depth, so heights
  from the ground (the recorder's convention and the default) and depths give the same profile. In height mode a
  corrected HS keeps the spoken heights; an HS below a recorded height is refused.
- One display list draws the screen (SVG) and the PDF (`draw.js`). The PDF is written directly with the base-14
  Helvetica fonts: no library, nothing to fetch, about 20 kB. Bars can grow from the right (the recorder and the
  owner's own profiles) or the left.
- Yellow flags are the Jamieson and Schweizer (2005) checklist counted per layer boundary from what the observer
  entered: grain size over 1 mm, softer than 1F, persistent grain type, size difference over 0.5 mm, hardness
  difference over one step, 20 to 85 cm deep. It is a count of structure, shown with the decision-support label.
- The satellite text is composed, not sent: Canadian observation shorthand, kept to plain SMS characters with a
  160-character count, handed to Messages through an `sms:` link. Messages sends it, by satellite when the phone
  has no signal (iPhone 14 or later, iOS 18 or later). A web page cannot reach the satellite link itself.
- Everything stays on the phone (browser storage, no server, no upload). CAAML 6.0.3 is the hand-off, the same
  shape the photo page writes; the daily update does not read it yet, so no data contract changes.

Consequences: the word list has to be widened when observers say things it does not know; the "not understood"
lines and `tests/unit/test_pit_voice.py` are where that shows. Dictation accuracy in wind is the phone's; the
sheets give a typed route for every field. The satellite hand-off needs a field check on the owner's iPhone.


## ADR-110 CAAML v6 snow profiles are read exactly, including the Snowpack Translator's (owner, 2026-11-14)

Context: the owner moved the Snowpack Translator out of this repository into its own (benfirth-parks/snowpack-translator,
2026-11-14): "the only connection between these two would be the profiles that are generated from this app be put
into the snowpack app". The Translator sends each profile's CAAML 6.0.3 to the site's observation upload (ADR-037),
but the observed set read only CAAML v5 (ADR-048) and listed v6 as kept, not read. Snow Scope exports v6 too.

Decision: `obs.caaml.parse_caaml_v6` reads CAAML v6 SnowProfileIACS (namespace `.../SnowProfileIACS/v6[.x]`) into
the same record as the v5 parser, with provenance `structured:caaml_v6` and confidence exact; `xml_kind` returns
`caaml_v6`, and the inbox files it as `filed_exact`. The reader also accepts two deviations of the Translator's export
from the schema: test results directly under the test instead of under `failedOn`, and a `locRef` without an
`ObsPoint`. Other CAAML versions stay kept and not read. No v6 file is in `profiles/` today, so the observed set and
the verification numbers do not change until one arrives.


## ADR-111 Profile times from 2026-11-01 are Alberta Time, UTC-6 (owner, 2026-11-15)

Context: profile exports carry no zone designator, and the owner confirmed on 2026-09-30 that they are in MST, so
`config/observations.yaml` reads them as fixed UTC-7. Alberta moved to Alberta Time, UTC-6 all year, on 2026-11-01.
Asked whether the exports now use local time or still MST, the owner answered "local time, now UTC-6".

Decision: `obs.localtime` reads a time written in the configured MST zone as UTC-6 when its local date is on or after
2026-11-01, and as UTC-7 before. Every place that turns an export's local time into UTC, or a UTC time back into the
observer's local date (inventory, observed set, CAAML without an offset, inbox upload dates, pairing and season
summaries), goes through it. Each observed record's `time_zone` names the zone actually used. A fixed date, not
`America/Edmonton`, so results don't depend on the machine's time-zone data and earlier observations keep exactly the
offset they were processed with. No 2026-27 profile is in `profiles/` yet, so the observed set and the verification
numbers are unchanged.

## ADR-112 SnowpackBC, an outside SNOWPACK forecast, is saved daily and scored in the blind test (owner, 2026-11-18)

Context: the owner asked how snowpackbc.com could fit the project. It runs SNOWPACK through the AWSOME framework
(gitlab.com/avalanche-warning, AGPL-3.0) for Banff, Rogers Pass, Whistler and the Mt St Anne area: station runs on
measured weather (Bow Summit, Lookout, Bosworth, Fidelity, among others) and runs on a 2-3 km weather-model grid,
about two days ahead, every 6 hours. Its map data and full layered profiles come from an open, unadvertised data
service (`/nivix/xs`), with no terms of use and no named operator. It keeps only the season in progress (from
2026-10-04) and replaces its forecast part every run, so it cannot join the 945-pit competitions, and what it
issued is lost unless it is saved the same day (research report
/mnt/project-files/lab/analysis/snowpackbc-dashboard-2026-11-18.md). The owner chose (2026-11-18, "yes, lets do
that") to save its forecasts daily and score them against this winter's pits beside the frozen agents, and to keep
them off the public site. A source not in README §6 needs the owner's OK (CLAUDE.md); that was it, and README §6
now lists it.

Decision:
- `update fetch` saves one snapshot a day (`ops.snowpackbc.fetch_snapshots`; a point saved less than 6 h ago is not
  asked again) of six points: the station runs at Bow Summit, Lookout (for Goat's Eye; the station is 360 m above
  the plot) and Fidelity (saved only: Fidelity has no lab plot or pits yet), and the grid point within 3 km of each
  Banff plot with the closest elevation (Simpson's is 185 m low, the nearest it has). Each is one request for the
  flat-field grain form, hardness, density and grain size over the last 3 days and the forecast beyond. The answer
  is stored unchanged (gzip) in `archive/snowpackbc/<season>/<run>/<location>/<time>.json.gz`, with URL,
  retrieval time and sha256 in `index.jsonl` (README §6: raw unchanged, source logged). About 100 KB a day. A
  failed point is an info warning, never a failed step: this is a comparison, never a model input.
- `update build`'s blind test scores two outside competitors, "SnowpackBC station runs" and "SnowpackBC
  weather-model grid", on the same next-pit and 72-hour cases as the frozen agents, under the current scoring
  version, on pits after the first snapshot, beside standard SNOWPACK on the same cases. As issued: a snapshot
  counts for a case only if it was saved by the case's as-of time (a `next_pit` case: by the pit time, since our
  agents get measured weather up to the pit there), and at most 72 h before it. Its profile at the pit (its last
  forecast step when the pit lies beyond its two-day reach, the gap recorded as `profile_lag_h`) goes through
  standard SNOWPACK's own output step (layer merging, presence, depth spread; `FixedEngineResult`, the reuse path),
  so the two differ only in the run. A case with no usable snapshot is not scored, rather than counted as a miss:
  that gap is ours. Rows go to the same append-only `archive/blind_test/<season>/scores.jsonl`; results under
  `outside` in `blind_test.json`, shown on the lab app's Training page under the frozen agents. It uses none of
  the five freezes.
- Not on the public site, and none of their code is copied (AGPL): we read their published data only. Asking the
  operator remains open (no contact found); the fetch identifies itself as a daily research client.
- The new module is in `ops/`, outside the lab's prediction code hash: no cached prediction is invalidated.

Consequences: the first comparison of our SNOWPACK setup with an independent operational one on unseen pits. Its
forecast reach (about 48 h) is shorter than our 72-hour cases, so in those it is held at its last step; its station
runs started 2026-10-04, three weeks after the first snow. If the site closes or changes its data format, the
snapshots stop with an info warning and what was saved is still scored.

## ADR-113 A weighing gauge that stops reporting while snow falls is flagged suspect and filled (2026-11-18)

Context: the owner asked why the precipitation-only model ("Previous standard") shows no snow at Bow Summit this
winter. The Bow Summit AB Env weighing gauge (`bow_summit_precip_ab_env`, `PC`) was emptied at its autumn service on
2026-10-21 (763 -> 200 mm, flagged bad as a reset) and has read exactly 200.0 mm every 15 minutes since 2026-10-22
19 UTC, while the depth sensor on the same mast (`SD`) went from 1 to 19 cm and the Bow Summit station's sensor
from -2 to 20 cm. The feed is current; the gauge reports no catch. QC accepted each flat hour as a measured 0 mm, so
no fill applied and the model saw no precipitation for four weeks. The main forecast takes its snowfall from the
depth sensor (ADR-102) and was unaffected. The same thing, shorter, followed the January 2026 emptying (exactly
200.0 for 6 days).

Decision: `ingest.fts360.stuck_gauge_hours` marks a gauge's hourly increments `suspect` over a run of at least 48
consecutive zero increments when the station's own snow depth (QC ok) gains at least 10 cm over the run: median of
the run's last 24 h minus median of its first 24 h. Start-to-end medians, not the largest swing: Sunshine's depth
sensor swings 20-30 cm from day to day, and a largest-swing rule marked eight quiet Sunshine spells (the snow pillow
showed no gain in them). Across every gauge-season in the record (Bow 2016-03 on, Sunshine 2015-08 on, API files
plus logger exports) the rule marks one run only: Bow Summit 2026-10-22 19 UTC to now (642 h). `baseline.assemble`
fills suspect gauge hours with the reanalysis (ERA5, or the GFS day-1 composite in the live season) times the
gauge's usual catch ratio, as it does for implausible gauge days, and writes a forcing note; the plot factor
(ADR-024) applies on top as before. Raw files are unchanged (flag, don't delete). The lab's canonical weather keeps
a suspect value with its flag when no other station is ok (ADR-056), so lab precipitation values are unchanged.

Consequences: the precipitation-only model at Bow gets snowfall again while the gauge is stuck, from GFS, which ran
too dry here (ADR-040), so it will likely stay shallow. A stuck gauge with no depth gain (a dry spell, or snowfall
that settles as fast as it falls) is not caught; nor is a stuck gauge at a station without a depth sensor. The
flag clears itself once the gauge moves again, but the stuck run stays flagged. Both files are in the lab's
prediction code hash, so cached lab predictions are recomputed on their next run; no stored case changes.

## ADR-114 The site shows a season timeline of every simulated profile (owner, 2026-11-18)

Context: the owner chose item 1 of the SnowpackBC product review (`lab/analysis/snowpackbc-product-review-2026-11-18.md`):
a picture of the whole season's layers per plot, like SnowpackBC's season view, with pit days marked. SnowpackBC's
viewer is AGPL, so none of its code is used.

Decision: a new card "Layers through the season" on the site page, drawn in the browser (`renderTimeline` in
`web/app.js`) from the season file the page already loads: one column per simulated profile (6-hourly, daily in
ERA5 seasons), height above ground up, IACS grain colours. It shares the snow depth chart's time axis and side
margins so the two line up. Observed pits with layers are drawn as outlined strips of their own layers at the pit
time, with a marker above. It shows the run the weather input names (main forecast, without pit updates, previous
standard, or an experimental agent); for GFS forecast inputs it shows the main forecast. On the main forecast the
new-snow check (ADR-104) fades unlikely weak layers, using for each column the pit a nowcast at that time could
know of. Hover shows the layer; a click opens that time's profile or that pit. Canvas, not SVG: a season has about
1,100 columns of 10 to 30 layers.

Consequences: no new data, no rebuild and no change to `snowagent.web.build`; the site data contract is unchanged.
Display only: scores, stored forecasts and the lab's prediction code hash are unchanged.

## ADR-115 Forecaster notes are distilled into bounded rules that must pass the usual checks (owner, 2026-11-18)

Context: the owner wants forecasters to give regular feedback on the modelled snowpack (agreement, disagreement,
considerations) and an LLM agent to keep these as heuristics that sit beside SNOWPACK. Three risks shape the
design: overfitting (three plots, few winters), leakage (a note written after a pit can tell an agent the answer)
and privacy (names and notes are internal; this repository is public). Principle 1 forbids the LLM inventing layers.

Decision:
1. Notes are written in the Rockies Weather Data Explorer's paired panes, under the study-plot profile (repo
   weather-explorer, `/api/plot-feedback`, Netlify Blobs, team code to post, admin token to list). Each note keeps
   the server receive time, the model run shown and the pit shown.
2. `snowagent lab feedback fetch` copies notes into `$SNOWAGENT_FEEDBACK_DIR` (default git-ignored
   `data/feedback/notes.jsonl`); notes are never committed or published. Raw notes are immutable: a key already
   held is never overwritten.
3. `snowagent lab feedback distil` asks Claude (`claude-opus-5-5`, structured output) to propose rules from the notes
   with names replaced by keyed pseudonyms (`FEEDBACK_SALT`). A rule may only multiply SNOWPACK's presence
   probability for surface hoar, facets, depth hoar or crusts in one depth zone (top 30 cm, deeper, or any) at named
   plots, by 0.5 to 1.5. It cannot add, remove or reshape layers. Every rule is saved as `proposed`; only the
   owner's `feedback decide <id> approve` makes it usable in the lab.
4. Leakage: a rule's `usable_from` is the receive time of its newest supporting note. It may touch a case only if
   the case's cut-off is after that, or the case predates forecaster notes (before 2026-11-01), where no note can
   know the pit. Notes themselves are filtered by receive time (strictly before the cut-off).
5. Promotion: an approved rule is one model change, tested like the new-snow check (ADR-103): one 945-pit
   competition with and without it plus the locked winters, then the blind test on this winter's pits, and the
   owner's call before it reaches the site. General rules can be tested on past winters; rules about single days can
   only be judged on 2026-27 pits.

Consequences: `lab/feedback/` is outside the lab's prediction code hash until an agent applies rules (the wiring
into `SnowpackAgent`, next step, moves `adjust_layers` into the hash and records the approved rulebook hash in
prediction metadata). The distiller needs the optional `feedback` extra and an Anthropic API key (paid, a few cents
a run). No model behaviour change yet.

## ADR-116 HRDPS forecasts: archived daily, an option for the site and the lab, scored beside the blind test (owner, 2026-11-18)

Context: the owner asked to replace GFS (0.25 deg, ~25 km) with ECCC's HRDPS (2.5 km) as the forecast weather,
retroactively too. GFS here is 2-4 K too cold and too dry (ADR-040/042); SnowpackBC runs on HRDPS. The MSC Datamart
(README §6) is the only open source of the full HRDPS runs and keeps them briefly: on 2026-11-18 hpfx.collab.science.gc.ca
held 2026-09-28 onward (~52 days), dd.weather.gc.ca ~30 days. Older HRDPS: Open-Meteo (README §6) keeps a stitched
short-lead series from ~2023-03 and the previous day's forecast (24-47 h) from ~2024-01/02, not the as-issued runs;
before 2023 only CaSPAr (account and request forms, not reachable from this environment, not in README §6).
HRDPS runs reach 48 h, our forecasts 72 h.

Decision:
- Archive. `ops/hrdps.py` extracts each 00 UTC run (leads 0-48, hourly) at the same points as the GFS archive: 2 m
  temperature and humidity, 10 m wind speed and direction, accumulated total and snow precipitation, accumulated
  shortwave and longwave radiation, surface pressure, and the model surface height; inverse-distance weights over
  the 4 nearest grid points. Files `archive/forecasts/hrdps/hrdps_YYYYMMDDHH.csv` with a gzipped provenance file
  (URL and sha256 of every GRIB file read), as for GFS (`scripts/sync_forecast_archive.sh hrdps`). `update fetch`
  gains an `hrdps` step: every run of the live season missing from the archive is extracted while the Datamart
  still holds it (45 days); a run still being published is retried next time; older gaps are reported as a
  warning and those days keep GFS. hpfx first, dd.weather.gc.ca second. The 2026-27 runs from 2026-09-28 were
  extracted on 2026-11-18, so this winter is covered in full.
- 72 h forecast weather from HRDPS (`forecast/hrdps_point.py`): HRDPS hours de-accumulated to hourly, then the GFS
  run of the same issue time for 48-72 h with its temperature moved to the HRDPS surface height (the forcing
  builder's fallback lapse rate); GFS alone when the HRDPS run is missing. The case or forecast timing does not
  change: HRDPS 00 UTC is published ~04 UTC, before the GFS run's assumed 5 h.
- Site: `forecast_source` in `config/plot_forcing.yaml` (`gfs` when absent, the default for now) or
  `build_season(forecast_source=...)`. A forecast on HRDPS records `weather: {source, hours per model}`. Forecasts
  already issued are never recomputed (ADR-054), so a switch applies from the next issue on. The site's default
  changes only after the owner sees the check below.
- Lab: `benchmark.forecast_h72.forecast_source` (`gfs` default; `hrdps` blends as above). The case (run, as-of
  time, pit) is chosen exactly as for GFS, so case ids are the same; the HRDPS file joins the case's forecast runs
  and data hash.
- Blind test (owner's choice 2026-11-18: keep GFS for the frozen agents this winter): the daily scoring also
  builds the 72 h cases of the new pits with HRDPS in a separate lab root (`data/lab_blind_hrdps`, the processed
  tables copied from `data/lab_blind`) and scores standard SNOWPACK there under the current scoring version, as
  `standard_hrdps` in the same append-only store; `blind_test.json` gains `hrdps`, the comparison with standard
  SNOWPACK on GFS on the same cases, shown on the lab app's Training page. Frozen agents' cases and scores are
  unchanged; standard SNOWPACK is now always scored under the current version too (the yardstick).
- Past winters stay on GFS: the Open-Meteo series are not as-issued runs (the stitched series uses runs issued
  after the forecast's start), so they are used for the station check only.

Consequences: `forecast/`, `lab/benchmark/` and `lab/settings.py` are in the lab's prediction code hash and the
lab config gains two fields, so cached lab predictions are recomputed once on their next run (no prediction
changes with the defaults).

Station check (2026-11-18, Nov-Apr 2023-24..2025-26, Open-Meteo HRDPS vs our archived GFS 00 UTC runs, temperature
moved to the station height at 6.5 K/km; precipitation on days every source and the gauge have): mean temperature
error HRDPS -0.7 to -1.8 K vs GFS -2.0 to -3.8 K; typical hourly error 1.9-2.6 vs 2.7-3.9 K (Sunshine, Simpson
Lower, Bow Summit). Precipitation, Sunshine gauge 1,350 mm: GFS 755-792, HRDPS 834 (short lead) and 1,048 (second
day); Bow Summit gauge 917 mm: GFS ~1,000, HRDPS 825-967.

Site switch (owner, 2026-11-18 23:35 UTC, "switch now", after seeing the depth check above): `forecast_source:
hrdps` in `config/plot_forcing.yaml`, from the next daily issue on. Forecasts already issued stay as issued; past
seasons never built live keep GFS (no HRDPS archive before 2026-09-28). The lab's cases keep their own setting
(`benchmark.forecast_h72.forecast_source`, GFS), so lab scores are unchanged; the file is in the lab's engine-files
hash, so cached lab predictions are recomputed once.

## ADR-117 The Jasper test set: plots outside Banff are scored in a lab of their own (owner, 2026-11-18)

Context: the owner now has the Jasper pits (Parker Ridge, and Bald Hills by Maligne Lake) and asked how to use them
to evolve the model (2026-11-18). Overfitting to three Banff plots is his main concern (ADR-087, ADR-097). The plan
given to him: first run today's best standard model at Parker Ridge with nothing tuned to Jasper, as an out-of-area
test; keep the Jasper pits as a held-out second scoring set while Banff stays the training set; aim the next model
changes at weak layers; and keep only agents that win in both places. The lab's sites were a fixed list of the three
Banff plots (`lab.schemas.common.SiteCode`), and its import read only the FTS360 API files and kept every station
value at the station's own height.

Decision:
- Two new site codes, PARK (Parker Ridge) and BALD (Bald Hills). config/lab.yaml does not list them, so the Banff
  lab's cases, config hash (`b8f5c5eb...`, unchanged) and training are as before.
- `config/lab_jasper.yaml` is the Jasper lab. It `extends: lab.yaml` (new in `load_lab_config`: the base file's keys,
  with this file's top-level keys replacing them whole), so the genome allow-list, scoring weights and benchmark
  rules are Banff's and cannot drift: an agent is the same genome and a score means the same thing in both labs. It
  sets its own plots (`plot_forcing_config: new_sites.yaml`, ADR-108), ERA5 folder (`data/interim/era5_north_west`)
  and seasons (2018-19 on, from Big Bend's record). It runs in its own data root (`--data-root data/lab_jasper`), so
  its cases never enter the Banff case set and no Banff training run can see a Jasper pit.
- Stations: the import reads the plots' station files from the plot file's `station_dir` when it names one (the
  converted Jasper tables, ADR-108), else from the FTS360 API files as before.
- Heights: Banff's stations stand at their plots, and the lab keeps values as measured. Jasper's do not: Big Bend is
  115 m above Parker Ridge and its fallback, Tangle Ridge, 1000 m. With `lab_move_to_plot_height: true` in the plot
  file (new_sites.yaml only), the import moves temperature (lapse rate) and humidity (dew point kept, at the
  station's own temperature) to the plot's height, as the baseline run does, and sources each such value
  `<station>_moved_from_<height>m`. Wind, precipitation and snow depth stay as measured.
- There is no archived GFS for the Jasper plots yet, so their 72-hour cases take measured weather as a stand-in
  (`measured_standin`), as Banff's cases without an archived run do.
- The SNOWPACK agent reads plot factors from config/plot_forcing.yaml, which has no Jasper plot, so they are 1.0
  there: nothing is tuned to Jasper (as in new_sites.yaml).

Check (2026-11-18): the Parker Ridge weather imports from 2018-11-20 (69,699 hours): temperature 54,545 hours from
Big Bend moved to the plot (+0.75 K), 375 from Tangle Ridge moved (+6.5 K), 12,252 from ERA5 (the months whose minus
signs were lost, ADR-108); humidity is kept as measured in the 13,113 hours without a Big Bend temperature.

The pits (owner's archive, 2026-11-18: a zip of every Jasper pit file, winters 1993-94 to 2025-26):
- 93 exact SnowPro files of the Parker Ridge study plot, 2001-02 to 2013-14 (2010 m in their headers, the owner's
  plot height), are filed under `profiles/<season>/Parker Ridge/` (90 pits after the obs pipeline's duplicate check).
  Those winters predate Big Bend, so they join the Jasper lab as reanalysis seasons (ERA5 alone, September to each
  season's last pit, as ADR-076 does for Banff). The 40 SnowPro pits of 1994-97 say 1849 m, about 160 m below the
  plot, and are not filed. About 38 pits of 2019-20 to 2024-25 are PDF charts and photos (Propagation Labs, SnowPilot,
  niViz) and wait for transcription; about 12 photos of 2015-18 likewise. Bald Hills' pits come from spots at heights
  from about 1900 to 2400 m and wait for the plot's location.
- Jasper's SnowPro file names put the year first ("psp05feb12" is 2005-02-12). The file-name date reader took
  "05feb12" as 5 February 2012; it now takes the year-first reading when the day-first one falls outside the season
  folder and the other inside it (`filename_date_yy_mon_dd_from_season`). No Banff file's date changes (all 1,558
  checked).

Training (owner's choice 2026-11-18, decision card "Score only, never train"): Banff stays the only training set.
`lab train --area-check jasper` (the Training page's checkbox) scores, after each round's selection, the same agents
as the locked winters (round 1: every initial agent, so standard SNOWPACK is the bar) on the Jasper cases too
(`lab/training/area_check.py`). Like the locked winters these scores never choose an agent or stop a run (a test
checks that a run with the check picks exactly the agents a run without it picks); the analogue library stays the
run's Banff library. The plan stores the area's config, data root and case ids, so a resume scores the same cases;
cases built later are left out, and a resume on a machine without them skips the check and says so. Each round writes
`area_scores.parquet` and `round.json` `area_check`; the summary, the Training page (chart against standard SNOWPACK)
and the training report ("Somewhere it has never been") show them.

Inputs on the owner's Mac: it has no FTS360 key and its connection times out on ERA5 downloads, so the Jasper weather
reaches it through a bundle branch, `claude/lab-jasper-data` (owner's OK 2026-11-18), as the Banff ERA5 box does
(ADR-079): `era5_north_west/` (the ERA5 box months the Jasper cases read) and `stations/` (the converted Big Bend and
Tangle Ridge tables). `snowagent lab prepare-area jasper` restores it (ERA5 months never overwritten, station tables
replaced when the bundle's copy differs) and builds the Jasper lab's own observed-profile set in its data root, so the
Banff lab's copy is untouched; then `lab init`, `lab import --profiles-file` and `lab build-cases` with the Jasper
config build its cases. The Training page's "Set up Parker Ridge check" button runs the four steps as one job.

Consequences: `lab/schemas/`, `lab/ingest/`, `lab/settings.py` and `obs/filenames.py` are in the lab's prediction
code hash, so it changes and cached predictions are recomputed on their next run; Banff predictions do not change
(same config, same weather tables, same pit dates). `lab/training/`, `lab/services/`, the CLI and the app are outside
it. Training with the check costs one more evaluation of the tested agents per round (165 cases at Parker Ridge,
89 three-day forecasts and 76 next-pit cases, against about 900 Banff training cases). Verification numbers are
unchanged for Banff. First Parker Ridge scores (all 165 cases, ERA5 winters 2001-02 to 2013-14, nothing tuned to
Jasper): standard SNOWPACK 0.539, with the new-snow check 0.550 (weak layers 0.546 to 0.586), depth error 16.1 cm,
snow too shallow on 72% of the pits; details in docs/lab/jasper_test_set.md.

Update (2026-11-18, the transcribed charts): the 37 Parker Ridge study plot charts of 2019-20 to 2024-25 are
transcribed (`observations/transcriptions/<season>/*_parker_ridge_*.json`, from the sources now filed under
`profiles/<season>/Parker Ridge/`; one more file, an annotated photo, is kept as a non-profile record). Left out as
not the study plot: ridge, nose, treeline and Parker 6 pits, and the Big Bend weather station's pits (200118 "BB Wx
Station" prints the station's co-ordinates). niViz saved one chart as SVG with its snow-symbol font embedded, so the
inventory now takes `.svg` and the page renderer draws it in headless Chromium at its viewBox's aspect. "PSP",
Jasper's short name for the Parker Study Plot, is listed as a printed name of the plot (`printed_site_names`), so the
pit printed "201216 PSP" is not flagged; no Banff record prints it. Three new pits are on the owner's review list and
are left out by his standing ruling (`exclude_flagged_pits_from_steering_and_scoring`): two print "Big Bend" or "Big
Bend Wx" (the second with the same co-ordinates as a "Parker Study Plot" pit, 0.6 km from the plot marker, so probably
the plot under another name) and one has a GPS fix 1.4 km from the plot. The Jasper lab now has 227 cases (123
three-day forecasts, 104 next-pit), 62 of them in the station winters 2019-20 to 2024-25 (39 on Big Bend's weather, 23
mixed with ERA5). Scores (scoring v5, nothing tuned to Jasper; the 165 ERA5-winter cases score exactly as before): on
the 62 station-winter cases standard SNOWPACK 0.632 (depth error 10.3 cm), the new-snow check 0.629, the snow-depth
model 0.610. The two sensor models are worse on depth (13.4 cm, 4.8 cm too shallow on average), most likely because
the only snow depth sensor is Big Bend's, 2.9 km away and 115 m higher, not the plot's (in Banff the same change
helped depth). The new-snow check's weak-layer gain carries over (0.618 to 0.712); on the precipitation base it would
score 0.647 (a test genome, not a standard model). All 227 cases: standard 0.561, new-snow check 0.573. Details in
docs/lab/jasper_test_set.md. `obs/inventory.py` and `obs/transcription.py` are in the lab's prediction code hash, so
cached predictions are recomputed on their next run; no prediction changes.

Open: the 12 or so Parker Ridge photos of 2015-18 (winters before Big Bend's record); whether the Jasper lab should
read Big Bend's snow depth as the plot's (the station is not at the plot); Bald Hills' plot location, its station (the
Maligne station at the north end of Maligne Lake, FTS360 agency 450 `6160c6b36469946308727b36`, 1700 m, hourly from
early 2020, the only one nearby) and a wider ERA5 box (Bald Hills is north of the north-west box's 52.5 N); the
archived GFS at the Jasper plots; keeping only agents that win in both labs when an agent is promoted.

## ADR-118 A column model of the lab's own, with no SNOWPACK, competes as an opt-in agent family (owner, 2026-11-18)

Context: settings tuning of SNOWPACK overfit and stalled (full 28-winter check 52.1 against 52.2); the gains that
held came from inputs (snow depth sensor, new-snow check, snowfall fix). The owner asked for an experiment with no
SNOWPACK in it, "to see if we can evolve something better for these specific locations", and chose the plan with
shared physics and only snowfall set per plot (decision card, 2026-11-18 22:28 UTC). The old weather-rule agent is
not that experiment: it starts every winter snow-free, ignores the pit's layers and takes no sensor, which is why it
scored 32 against SNOWPACK's 51 in milestone 3.

Decision:
1. New agent family `column` (`snowagent.lab.agents.column`): one flat column of snow elements stepped hourly from
   the latest visible pit of the season (its layers, grain forms and hardness; snow-free from the season start
   without one) to the valid time, with snowfall from the plot's snow depth sensor until the last observed hour
   (the SNOWPACK agent's ADR-100 sensor processing) and from precipitation after (the plot's adopted gauge or ERA5
   factor, ADR-024/038/106; raw GFS), a surface energy balance, implicit heat conduction, bucket water percolation
   with refreezing (melt-freeze crusts), viscous settlement (Anderson 1976 / Jordan 1991), temperature-gradient
   faceting and depth hoar, decomposition and rounding, and surface hoar. The forcing is the SNOWPACK agent's own
   builder (`engine_forcing`), so both models see the same hours and gap fills.
2. 42 genes in ten blocks (`col_*` plus the shared `new_snow`, `engine_output` and `uncertainty`), defaults textbook
   values set before any scoring. Physics is shared by the plots; only the precipitation factor is per plot.
3. Opt-in: `STANDARD_FAMILIES` (the five earlier families) make the default population and the family slots, so runs
   started before this ADR, and their resumes, keep exactly their populations. A run whose initial population is
   smaller than its survivors (the column default alone) is filled to the population with seeded mutants of it
   (`fill_initial`, labelled `start-mNN-column`); family slots then add the column family to the standard five.
   Training preset "Column model, no SNOWPACK" (30 rounds, population 20, survivors 3).
4. Judged as any agent: the same score, cases, leakage checks and locked winters, with standard SNOWPACK scored on
   the locked winters each round; then a 945-pit competition against the best standard SNOWPACK and, if it leads
   on the locked winters, the full leave-one-winter-out check, which is cheap here (no engine runs). Like every
   lab agent, its layers are benchmark entries only, never site output, unless it passes promotion (principle 1)
   and the owner's call.

Consequences: no change to any existing agent or run. Column agents need no SNOWPACK binary, so a round costs
seconds per agent and case (measured in the changelog entry); standard SNOWPACK still runs for the locked-winter
reference. Not yet built: per-plot physics, wind drift, slope aspect (study plots are flat).

## ADR-119 Lab app: it says what to do next, shows what is running on every page, splits the Training page and updates itself (owner, 2026-11-18)

Context: the owner, after starting the Parker Ridge set-up and an overnight run from the app: "is it finished? This
app isnt very intuative, might be a good idea to audit and make it better". The audit found five things. (1) A job
showed only inside the box that started it, often folded away, beside an older run's "finished, 15 / 15"; that is
how the Parker Ridge set-up was taken as finished while it was still running. Most panels needed a Refresh button.
(2) Nothing said what to do next. (3) Updating the app meant Ctrl-C, `git pull` and a relaunch in Terminal, and a
pull without the relaunch left the app running a mix of old and new code. (4) The Training page did five jobs on
one long page (start a run, follow it, read its agents, the site and the blind test, the promotion check) and
opened on whatever run it judged most informative, which could be an old one. (5) Job ids, file paths, commands and
internal words stood in the open. He chose to have all five fixed in one change (decision card), and approved names
for the lab's evolutions and competitions the same evening.

Decision:
1. **What is running, on every page.** `services/activity.py` reads the background jobs (ADR-077) and the training
   runs' own status files into plain lines: what it is, the step it is on and how long so far (a training run: the
   round, the time left and the finish time, from ADR-081's estimate), and how long the same job took last time.
   `ui/status.py` draws them under every page's title (`page_header`), running ones first, with a progress bar and a
   link to the job's page. A finished job stays an hour, a failed, stopped or cut-short one a day, or until **Hide**
   (a marker file beside the job; nothing is deleted); an older try of a job that was started again is left out.
   The bar re-reads the jobs every 5 seconds while one runs and redraws the whole page when one starts or ends, so
   the Refresh buttons are gone. `ui/jobs.py`'s job block says the same line, shows the steps as a checklist and
   keeps Stop and Resume; the log is under "Technical details".
2. **What to do next.** The Overview opens with one sentence from `activity.next_step`, in the order a person
   works: a run that is going (nothing to do), another job that is going, set up the data, build the cases, build
   the engine, start the first run, resume a stopped run, or see how the last run did; each with links.
3. **The Training page becomes five pages**, listed with every other page in `ui/pages.py` (one list for the menu
   and for links between pages): Start a run (`4_Training.py`, which keeps its file name so that its history and the
   other open branches still merge), Watch the run, Run results, Blind test and site, Promotion check. The run
   chosen on one is the run the next opens on (`ui/training.pick_run`: the run that is going, else the one last
   chosen, else the newest; promotion-check folds are left out). Watch the run says first whether the run is going
   now or when it ended ("It finished at 03:12 on 25 Oct, after 15 of 15 rounds. Nothing is running here now."),
   then the locked-winter verdict by the agent's name; the other charts, the settings and the log are folded. The
   menu follows the work: Lab, Evolutions (training), Competitions and checks, Data, Background.
4. **The app updates itself, on a press.** `services/update.py`. Started by `snowagent lab app`
   (`SNOWAGENT_LAB_UPDATE_CHECK=1`; `--no-update-check` turns it off; tests and a plain `streamlit run` never reach
   the network), the app runs `git fetch origin <its branch>` on a background thread when a page is opened, at most
   every 15 minutes, or on "Check for a newer version". When the branch on GitHub is ahead, every page shows what is
   new (the titles of the merged pull requests) and **Update and restart**: one background job, a fetch into `origin/<branch>`
   and `git merge --ff-only origin/<branch>` (2026-11-19: not `git pull`, see the changelog), then `pip install -e .[dev,lab]` only if `pyproject.toml` changed and `scripts/build_snowpack.sh` only if it or
   the engine patches changed. The button is disabled while any job runs, so no run gets new code half way. A copy
   with commits of its own is never updated by the app. Nothing is fetched or installed without the press.
   - Restart: the app process ends with exit code 75 and `snowagent lab app`, which was waiting on it, starts it
     again in the same Terminal window (`SNOWAGENT_LAB_SUPERVISED=1` tells the app that someone is waiting; started
     any other way it says how to restart by hand). The browser reconnects by itself. Replacing the process in place
     (`os.execv`) was built first and failed in the browser test: Streamlit's listening socket is inheritable, so the
     new server found its own port taken and exited. A fresh process has no such leftovers.
   - The app restarts without asking only when its own update job has finished since it started. After a `git pull`
     in Terminal it shows **Restart the app** instead. The version it started with is kept in its environment.
   - `snowagent lab app` now starts Streamlit with `--server.fileWatcherType none`: one version of the code until a
     restart. With the watcher, a pull reloaded the pages against modules already imported from the older version.
   - A page error while the files on disk are newer than the running app offers the restart (`lab_app/Home.py`).
5. **Build the SNOWPACK engine from the app.** The Overview's engine tick says "not built yet" or "needs a rebuild"
   (the engine lacks one of the repository's patches, the check a training run already makes before it starts) and
   offers `scripts/build_snowpack.sh` as a background job.
6. **Plain words.** Job ids, file paths and Terminal commands move under "Technical details" or "The same from
   Terminal"; states read "was cut short" for interrupted; times are the computer's local time; rarely needed
   options are folded. The decision-support label stays on every page (principle 6), once, under the title, with
   "research only" under the menu. Freezing an agent for the blind test, which cannot be undone (ADR-086), now needs
   a tick first.
7. **Names** (`services/labels.py`, display only). Evolutions have plain descriptive names in the repository:
   Tuned SNOWPACK (SNOWPACK with evolved settings), Column model (ADR-118), Column + site traits (not built yet).
   Competitions carry field units' names: Banff (the model-change trials on the Leaderboard), Jasper (the Parker
   Ridge check, ADR-117), Lake Louise, Yoho and Kootenay (the live blind test, ADR-086), Mount Revelstoke and
   Glacier (the forecaster rules trial, ADR-115, not in the app yet). A run's label and the kinds of run on Start
   a run carry the evolution's name; no run id, file, case or score depends on a name.
8. **A person's own names stay on their computer** (owner, 2026-11-19). The owner names the evolutions after
   colleagues and chose to keep those names out of the public repository. The Overview's "The names used in this
   lab" has a box per evolution; what is typed is kept in `names.json` in the lab's data folder (`data/lab/`,
   which `.gitignore` excludes) and shown in place of the plain name on that computer only
   (`labels.local_names`). Rejected: names in `config/lab.yaml` or any other followed file (they would be
   published), and an environment variable (the owner works in the browser, not the Terminal). New evolutions get a
   descriptive name in `labels.py`; a personal name never goes into code, tests, docs or screenshots.

Consequences: no change to scoring, training, cases, the job runner or any stored file; every command line works as
before (`lab app` gained `--no-update-check`). The update takes whatever is on the checkout's branch on GitHub, the
same trust as `git pull` by hand. The first update to this version is by hand once more; later ones are a button.
One Streamlit internal is relied on, and guarded: `PagesManager.uses_pages_directory`, which
`ui/app._through_the_menu` turns off so that a first page view that goes straight to a page (a refreshed tab, the
restart) is drawn through `lab_app/Home.py` and not with Streamlit's older automatic menu; if a later Streamlit
renames it, the old behaviour returns (a wrong menu until the next click) and nothing breaks. Old bookmarks of `/Training` open Start a run.

## ADR-120 Three more genome structures on the column model: process switches, layer stories, weather recipes (owner, 2026-11-19)

Context: the column model (ADR-118) has one genome shape, a flat list of physics settings. The owner asked to
"look at a few others with differing genome structures", after listing as many candidate variables as possible
(128, with what each needs, in the project's planning notes), and then: "Ok, start building these". The question
is which shape of genome learns something that holds on winters it has not seen, so the structures must differ in
shape and be judged the same way. A fourth structure, a weighted scorecard on site traits, waits for the dated site
sheet and is not part of this ADR.

Decision:
1. Three opt-in agent families, each alone in its own training run, judged exactly as the column model (same cases,
   score, leakage checks, locked winters with standard SNOWPACK as the bar, Parker Ridge score-only):
   - `process` (45 genes): the column model plus eight named processes, each an `on`/`off` choice gene with its own
     strength genes (kinetics by snow temperature and density, crust-adjacent faceting, dry snow on wet snow,
     near-surface faceting, surface hoar by wind and vapour, wind packing, colder ground under thin snow,
     gradient-dependent rounding). Evolution chooses which processes exist as well as how strong they are. With
     every switch off it is exactly the column model at default energy, water and settlement settings.
   - `story` (31 genes): the column model's heat, water and settlement, with grain forms decided by weighted sums
     of each element's fading history (ten features) and surface hoar by a stock of hoar-weather hours. The genes
     are the weights: each is constrained by every layer of every case, not by one plot.
   - `recipe` (39 genes): no heat physics. Forecaster-style weather recipes on the latest pit (hoar nights, clear
     cold spells, wind slab, melt and rain crusts, facets on a buried crust, a bulk-gradient rule low in the pack),
     with the column model's snowfall and settlement.
2. The column model gains hooks (`ColumnExt`: start, ground temperature, before snowfall, metamorphism, after step,
   hoar growth) and an optional per-element history table. Without an extension `simulate` is unchanged (checked:
   identical depth and elements on training-winter cases, and by test against `process` with all switches off).
3. Genome size: the families carry `plot_snowfall` (three per-plot snowfall multipliers, the only per-plot genes,
   as ADR-118 agreed) and only the blocks they evolve; the column settings they do not evolve are read at their
   `config/lab.yaml` defaults (`common.block_defaults`), so every genome stays under the 64-gene guard.
4. Starting values: published or textbook numbers (sources in each module's docstring and the gene docs), written
   before scoring, with four exceptions set on the training winters only (2023-24, 2024-25 and 2025-26 and the
   Parker Ridge set were not scored): `proc_sh_vapour_ref_hpa` (first 0.5, then 2.0), `sty_sh_hours` (first 8,
   then 60) and `rcp_sh_hours` (first 12, then 36), because the first values laid down five to ten times the
   column model's surface hoar layers, and the story model's hoar rule was changed from a weighted sum to a stock
   of hours. Recorded here so that nobody reads these defaults as untouched by data.
5. Names in code, config and docs are descriptive (`process`, `story`, `recipe`). The owner's working names for
   the evolutions are people's names and stay out of this public repository (owner, 2026-11-19).
6. Arena plots: the validated six-colour palette is not extended; the new families take the neutral colour and a
   marker of their own. Training presets "Process switches / Weather recipes / Layer life stories, no SNOWPACK"
   (30 rounds, population 20, survivors 3, as the column preset, so the runs are comparable).

Check on the training winters (877 cases, every non-locked winter, defaults, no training; composite 0-1):
column 0.599; process 0.587 (all switches off 0.599, identical to the column model); recipe 0.579; story 0.567.
All three defaults start behind the column model. The gap is mostly surface hoar: recipe and story lay down about
600 hoar layers against the column's 227 and find 68 and 71 of the 125 observed against 54, at the cost of about
450 false ones against 126. Recipe forecasts fewer facet layers (3133 against 4155) with half the false ones (494
against 903) but finds fewer (611 against 692 of 767), and its depth is worse (9.9 cm against 9.3 cm). These are
starting points for training, not results: nothing here says which structure generalises.

Consequences: no change to any existing agent, run or default population. The comparison that answers the
owner's question is four training runs of equal budget (column, process, story, recipe), read on the locked
winters and Parker Ridge. Not built: the site-trait scorecard, per-plot physics, slope and aspect.

## ADR-121 No observer names or e-mail addresses in the repository's text (owner, 2026-11-19)

Context: the repository is public. Transcription notes (`observations/transcriptions/`, `transcription_qa/`)
copied the observer name or work e-mail printed on each pit chart. The owner chose to clean the current files
only, with no history rewrite (decision card, 2026-11-19).

Decision:
- In the current transcriptions, observer names, initials, e-mail addresses and people named in comment boxes are
  replaced by `[name removed]`. Unit names ("Banff Visitor Safety", "Snow Safety") and blanks (`--`) stay. Measured
  fields are untouched: the observed set built from these files is byte-identical before and after, so no score
  changes.
- `snowagent.obs.redact.strip_observer_text` removes e-mail addresses and the value of observer labels
  (`Observer:`, `Obs:`, `observer '...'`, "Name printed in the org position:"). `transcribe_cli validate` applies it
  to a record's free text in place before validating, `transcribe_cli scrub` cleans a whole folder, and
  `validate_transcription` flags `observer_text_in:<field>`. Inbox receipts and the redacted inventory strip
  their notes the same way. The transcription guide's golden rule 7 forbids copying names; a name written outside
  a label cannot be recognised by rule, so the guide is the safeguard there. `tests/unit/test_obs_redact.py`
  checks every committed transcription.
- Raw pit files under `profiles/` (SnowPro/CAAML text, PDFs, photos) are raw data and were not changed; the owner
  decides separately what to do with them. Git history still holds the old text.

## ADR-122 A scorecard of fixed site traits steers the column model; site traits are dated before training (owner, 2026-11-19)

Context: the owner's original idea for an evolved model was a weighted scorecard of factors, including what never
changes about a plot (ground, plants, trees, wind, sun). With three training plots, any per-plot number is
memorisation of that plot's pits, so ADR-118 allowed only snowfall per plot. The owner filled in a site sheet for
the four plots and marked it final (2026-11-19 00:57 UTC), then asked whether satellite imagery and lidar could add
terrain details, and to keep going.

Decision:
1. `config/site_traits.yaml` holds each plot's traits: the sheet answers that differ between plots, coded by one
   rule for all plots, and terrain numbers from a 30 m elevation map (NRCan MRDEM-30) at the plot coordinates:
   height against the ground within 500 m, aspect, and shelter from each of eight wind directions within 300 m
   and 2 km. Both were fixed before any agent of this family was trained or scored and are never changed because
   of a score. Three sheet answers the owner left blank (cold-air pooling at Simpson and Parker Ridge, wind
   exposure at both) were read from the map and told to the owner the same evening; the file marks them.
2. New opt-in agent family `scorecard` (`snowagent.lab.agents.scorecard`, 39 genes): the column model with no
   per-plot gene at all. Three indices (ground, exposure, canopy; 0 to 1) summarise a plot. Eighteen shared
   weights set six dials (snowfall, wind, faceting, surface hoar, crusting, ground temperature) and three genes
   use the map's shelter from each hour's wind direction (slower wind, lee loading). Every weight starts at 0, so
   the default is exactly the column model: nothing about a plot is assumed before training.
3. What it can and cannot show. Within Banff the three indices can do no more than tell three plots apart, and
   the wind that matters blows from the south-west or west in 74 to 99 percent of windy hours at every plot, so
   the direction genes are close to a per-plot constant too. A gain on the Banff winters is therefore no evidence
   that site traits work. The evidence is Parker Ridge (never trained on, ADR-117): its dials come from its own
   traits through weights learned elsewhere. A plot without traits gets every dial at 1.
4. New sources, all free and public, added at the owner's request (README section 6): MRDEM-30 (model input, as
   above), the Meta/WRI 1 m canopy height map (a cross-check of the sheet only: it agrees on tree distance at Bow
   Summit and Simpson, reads tree heights at about half the owner's, and is blurred at Goat's Eye and Parker
   Ridge) and Sentinel-2 scene classification (spring melt-out brackets 2017 to 2026, a check, never trained
   on). Canada's public 1 m lidar has no coverage at or within 10 km of any plot. Scripts in
   `scripts/site_terrain/`, results in `scripts/site_terrain/site_terrain_2026-11-18.json`.
5. Not used: sheet answers that are the same at every plot, land cover (the 10 m map calls all four plots
   forest), tree heights from the map, and slope (the map reads 6 to 8 degrees over 90 m where the owner reports
   flat plots; the plots stay flat columns).

Open point for the owner: the tree map places the coordinates on file for Goat's Eye and Parker Ridge inside
trees, with open ground about 50 m away. If the coordinates are corrected, the map numbers of those plots are
recomputed and the change recorded here.

Consequences: no change to any existing agent or run. Training preset "Column + site traits, no SNOWPACK" with the
column preset's budget; in the app it is the evolution "Column + site traits" of ADR-119 (`labels.EVOLUTIONS`). Check on training winters at defaults: identical to the column model on every case
(147 cases compared).

## ADR-123 Trimmed genomes: a sensitivity screen on the training winters picks the settings a trimmed run evolves (owner, 2026-11-19)

Context: the five genome structures on the lab's own model carry 31 to 45 genes each (column 42, process 45, recipe
39, story 31, scorecard 39). The training winters hold about 877 cases, and they are far from independent: each pit
is scored as two case types, and the pits of one winter at one plot share that winter's history. Snow-model
calibrations usually tune 5 to 15 parameters, and a common rule of thumb asks for 10 to 20 independent cases per
tuned setting. The owner asked: "is there a way we can trim the current ones to bring it to a more acceptable
setting".

Decision:
1. A gene sensitivity screen, `snowagent lab sensitivity <family>` (`lab/training/sensitivity.py`). From the
   family's default genome each gene is moved on its own, a number halfway to its lower bound and halfway to its
   upper bound, a choice to each other choice, and every probe genome predicts a stratified sample (plot x case
   type) of the training winters. A gene's sensitivity is the largest absolute change of the mean case composite
   among its probes: whether the gene matters, not whether moving it helps. The locked test winters (ADR-083) are
   split off before sampling and never loaded; Parker Ridge (ADR-117) is in another lab and cannot be reached.
   Predictions go to a throwaway cache.
2. The rule, fixed before any result was read: a trimmed run evolves the twelve most sensitive genes of the family,
   leaving out any that move the score by less than 0.05 points of 100. The site scorecard gets two of its twelve
   places for site genes, however they rank, because without a site gene it is the column model; with three
   training plots no more than one or two site weights are learnable.
3. Genes are fixed, not removed. `genome.trimmed` in `config/lab.yaml` holds each family's short list.
   `lab train --trim` (and the plan of the run, `trimmed_genes`) makes mutation change only those genes; a genome
   still carries every gene, so agents, genome hashes, the cache, lineage and reports work unchanged, a resume and
   a promotion check trim the same way, and a gene goes back into play by adding it to the list. Crossover needs no
   change: it copies whole blocks between parents that already agree on the fixed genes. A run without `--trim`
   draws exactly the mutations it drew before.
4. Each structure gets a second kind of run beside the full one, with the same budget: "Column model, trimmed",
   "Process switches, trimmed", "Weather recipes, trimmed", "Layer life stories, trimmed", "Column + site traits,
   trimmed". A trimmed run's label says so.
5. The screen of 2026-11-19 (150 training-winter cases, seed 0, run in the cloud; the table of every gene is
   `docs/lab/gene_sensitivity_2026-11-19.md`): column 42 genes, 12 kept; process 45, 12 kept; recipe 39, 12 kept; story 31, 12 kept; scorecard 39, 12 kept (ten
   column settings and the site genes `sc_hoar_ground` and `sc_hoar_exposure`). What it found: the surface hoar
   growth rate matters most by far (raising `col_sh_growth_m_h` to 0.0011 costs 16 points of 100 in the column
   model and 6 with the process switches), which is also why the scorecard's two site genes are its hoar weights
   (9 points each); the genes that write the answer rank high in every structure (`col_new_weak_factor` at 0.6
   gains 1.8 to 3.9 points on the sample, the same effect ADR-103 found for SNOWPACK, and `depth_spread_frac`);
   Goat's Eye is the per-plot snowfall gene that is kept wherever a structure has one; and a third to a half of
   each genome moves the score by less than a quarter of a point. The twelfth and thirteenth genes are nearly
   tied in every structure, so twelve is a budget, not a finding. No default was changed: trimmed and full runs
   start from the same values, and `col_new_weak_factor` is in every short list for evolution to find.

What it does and does not show. Fewer free settings leave evolution less room to fit the training pits by chance,
and a short list makes a 30-round run search where the score responds. It does not make a result true: the lists
were chosen on the training winters, one gene at a time (a gene that matters only together with another is
missed), on a sample. The test of a trimmed run is the same as of a full one, the locked winters and the Parker
Ridge check, and the useful comparison is trimmed against full for the same structure and budget. The lists are
dated before any trimmed run and are not changed because of a locked-winter or Parker Ridge score; rerunning the
screen on more training cases (`--cases 0` on the owner's Mac) is allowed, since it reads training winters only,
and a changed list is recorded here.

Not done: a screen of SNOWPACK's own settings (the tuned-SNOWPACK evolution; it needs the engine, about 66 probes
of every sampled case, so it belongs on the owner's Mac: `lab sensitivity snowpack --engine auto`), methods that
measure interactions (Morris, Sobol: several times the cost), and removing genes from the families (a data
contract change that would also orphan every stored genome).

Consequences: no change to any existing agent, score or run; verification numbers unchanged. 9 new unit tests
(`tests/unit/test_lab_trim.py`).

## ADR-124 The pit reset stays as adopted; switches to test other resets (owner, 2026-11-19)

Context: the owner asked to try correcting the model with each new pit, running forward from it, and to score that
honestly. SNOWPACK runs have done this since ADR-038/039, the column model starts from the latest pit (ADR-118), and a
case never sees the pit it is scored against. What the lab lacked was a measure of what the reset is worth and a test
of better ones. This record covers both; the numbers and the method are in docs/lab/pit_reset.md.

Decision:
1. `VisiblePackageEngine` takes `pit_reset` (`layers`, the adopted reset; `depth`, ADR-038's; `layers_model_mass`,
   the pit's layers holding the model's own water equivalent; `off`), `sensor_trust` (0 to 1: where the snow depth
   sensor drives the snowfall, the reset goes to a depth between the sensor's and the pit's and the sensor then drives
   changes from it) and `learn_precip_cm` (the winter's snowfall error learned from its pits, with that many cm of
   snow at no error as the starting belief; the multiplier stays within 0.5 to 2.0). `lab compete` passes them as
   `--pit-reset`, `--sensor-trust` and `--learn-precip-cm`. The defaults are the adopted run: restart-segment keys,
   engine-profile keys, run plans and site-run reuse are as they were. A test names itself in the run plan, never
   reuses a site run and keeps restart segments and engine profiles of its own (`VisiblePackageEngine.reset_test`).
   Training has no switch.
2. Nothing is adopted. The rule, fixed before the runs: adopt only what helps on the 877 training-winter cases, then
   the locked winters and Parker Ridge confirm. Against the adopted reset, case-score points out of 100 on the
   training winters (90% winter-resampled interval): depth only -4.87 (-5.59 to -4.12), the model's own mass -0.75
   (-1.20 to -0.31), sensor trust 1.0 +0.11 (-0.05 to +0.29), sensor trust 0.5 +0.06 (-0.06 to +0.18), snowfall
   learning at 30 cm -0.26 (-0.46 to -0.07).
3. The reset is worth +7.9 to SNOWPACK on the training winters (better in 25 of 25), +7.9 on the locked winters
   (3 of 3) and +15.7 at Parker Ridge (18 of 18 winters with an earlier pit); the column model's starting pit is worth
   +4.1, +3.0 and +13.2. The pit's depth gives about a third of SNOWPACK's gain in Banff's training winters and most of
   it at Parker Ridge; the pit's layers give the rest.
4. Sensor trust 1.0 and snowfall learning help at Parker Ridge only. Sensor trust: +0.41 over its 227 cases, +1.51 on
   the 62 of winters with a sensor, which stands 2.9 km away and reads 25 cm shallower than the pits. Snowfall
   learning: +1.05, from the winters without a sensor (it acts only where precipitation drives the snowfall), where
   no snowfall factor exists. Made wrong on purpose, Banff's snowfall (factors times 0.7 or 1.4, 597 reanalysis-winter
   cases) costs about 2 points, and learning wins back a third to two thirds of that. Whether Parker Ridge runs with
   them is the owner's call. The evidence for them is Parker Ridge's own scores, so adopting them on it spends part of
   that set's independence; Bald Hills can test them cleanly.
5. Carrying the pit's hand hardness (and its layer edges) forward in old snow was tested by rescoring the stored
   adopted runs and not built. It gains on the training winters (+0.22 to +0.61) and nothing on the locked winters
   (-0.07 to -0.15), and its gain is in the reanalysis winters only (to 2013-14: Banff +0.82, Parker Ridge +2.21;
   station winters: +0.14 and -0.78). Those winters are 68% of the training cases. A gain on the training winters
   that the station winters do not show is a fit to the older pits, not evidence.
6. Using the pit's temperature readings at the reset, in place of the model's snow temperatures, was tested with a
   scratch engine and not built: +0.11 (+0.02 to +0.21) on the training winters, +0.18 (-0.07 to +0.45) on the locked
   winters and -0.02 (-0.31 to +0.24) at Parker Ridge. The model's temperatures below the top 30 cm are already
   within about 1 K of the pits'. A tenth of a point that Parker Ridge does not show is not enough to change the
   adopted run.

Consequences: no default, agent, score or stored result changes. `lab/agents/` and `lab/competition/` are in the
lab's prediction code hash, so cached predictions, engine profiles and restart segments are recomputed once on the
next run, as after any change to the model code. Checked on 12 cases: `lab compete` with `--pit-reset depth`, and with
`--sensor-trust 1.0 --learn-precip-cm 30`, gives the experiment runs' scores exactly.

## ADR-125 Winter-by-winter test: leave winters out to test a kind of training, with the locked winters kept out (owner, 2026-11-19)

Context: overfitting is the owner's main worry. The three locked winters (ADR-083) give one small reading (68
pits), and the promotion check (ADR-068) re-trains once per winter, but it checks one agent of a finished run and,
with SNOWPACK in training, took about 14 hours for a reduced check. The owner asked for leave-one-winter-out
testing, run on its own, next to the other work. The column-model families (ADR-118, ADR-120, ADR-122) run about
0.36 s a case with no SNOWPACK, which makes re-training per fold affordable. (Numbered 125: ADR-123 and ADR-124
are taken by two pieces of work open at the same time, trimmed genomes and the pit reset.)

Decision:
1. `snowagent lab winter-test` (the app's Competitions and checks › Winter-by-winter test) tests a training
   PROCEDURE, not an agent: the training options are given directly (the starting model, rounds, population), and
   there is no checked genome. It is `training.loso.check_loso` with `genome_ref` None. The promotion check is
   unchanged: its plan gains keys only for a winter test, grouped folds or excluded winters, so earlier checks keep
   their plans and resume as before.
2. Folds group winters: winter i (date order) is held out in fold i mod N (default N = 5), so every fold mixes
   the early reanalysis winters and the station winters and no fold holds one era out at once. `--every-winter` is
   one fold per winter (the promotion check's shape). A group's case set is `loso_<w1>+<w2>+...`: the builder's
   held-out season may now be a `+`-joined group, and a training case of that set sees no pit of any winter of
   the group (`visible_pits`), which the leakage check verifies for each winter. A single season works as before.
   The manifest field `holdout_season` keeps its type (a string); in a group's case set it holds the group
   (`2018-2019+2020-2021`), and `settings.holdout_group` reads either form. No existing case set changes.
3. The locked test winters (the three most recent of the whole case set, as ADR-083 picks them, whatever plots or
   case types a test is limited to) are kept out of every fold: not trained on, not held out, not in the analogue
   library, their truth never read (`TrainOptions.exclude_seasons`, recorded in a fold run's plan as
   `excluded_seasons`; a plan without it is unchanged). They stay a clean final test. Parker Ridge stays its own
   check (ADR-117). `--include-locked` puts them back in for a test where that does not matter.
4. Each held-out winter gets three scores: the fold's trained winner (rank 1 of the last round), the best untrained
   starting agent (the starting settings that ranked first in round 1 on the fold's training winters) and the best
   standard SNOWPACK (`snowpack-era5-fix`, the bar the locked-winters reading already uses for a training without
   SNOWPACK agents, ADR-118). A promotion check keeps the default SNOWPACK genome as its incumbent (ADR-068).
5. The result gives, per winter and across winters, trained minus SNOWPACK and trained minus untrained (what
   training added), each with its average, standard deviation, lowest and highest winter, winters better and
   worse, and a 90% range of the average from resampling the winters (2,000 resamples, seeded). Wins and losses
   for the ADR-068 rule are counted per winter.
6. Memorising is measured as what training added on the winters it trained on (each fold's winner less the same
   fold's untrained start on that fold's training winters, averaged over the folds) less what it added over all
   the held-out pits. Both are differences from the same untrained start, so some winters being easier than
   others cancels out; a first version compared the scores on trained and held-out winters directly and mostly
   measured which winters were easy. Under half a point the summary says there is no clear sign of memorising.
7. Cheap defaults, so a test fits one evening on the owner's Mac: the column model, 5 folds, 6 rounds of 10 agents,
   2 survivors. The page offers Quick (those), Overnight (12 rounds of 12) and Every winter on its own, for each
   column-model family and for tuned SNOWPACK (slow: days). A test of 6 rounds tests a 6-round training: the
   result says what that procedure does, not what a 30-round run would do. (ADR-126 replaced Overnight with
   Full training, 30 rounds of 20 agents, once the Mac's real speed was known.)
8. A test says how long it will take before it starts (`--estimate-only`; on the page the test cannot start
   before its estimate has finished) and how long it took. The estimate counts the mutants that fill a small
   starting set in round 1, the SNOWPACK runs of the bar on the held-out pits, the case sets to build at the
   computer's own build time (what its `all` set took), and only the families the children can have
   (`estimate.child_families`): a column-only training has no SNOWPACK run in a later round, where every estimate
   used to range up to the dearest family of the configuration. `lab train` estimates narrow the same way. The
   result records the computing time from the rounds' own records (`work_s`, `round_s_median`) and the time the
   case sets took (`build_s`), so a test that was stopped and resumed counts every part.
9. In the app a running test is one line of the bar at the top of every page ("part 2 of 5, about 1 h 40 min left,
   done around 23:10"), and so is a promotion check now; the part's own training is not listed as a second run.
   A test cannot be started while a training run is going. The summary is in sentences, the same on the page and
   from the command (`services.winter_test.plain_summary`).

Measured, 4 cloud cores, 2026-11-19: a smoke test (Simpson only, 9 winters in 2 folds, 2 rounds of 4 column agents)
took 11 min 3 s from an empty cache, 7 min 11 s of it building the two case sets (945 cases each, leakage 945 pass,
0 fail), and gave the same numbers twice: over 65 held-out pits the trained agents 52.3, their untrained start 51.6,
SNOWPACK 54.1; against SNOWPACK -1.5 points a winter on average (standard deviation 5.9, from -10.2 to +7.7,
better in 3 winters, worse in 6, 90% range -4.3 to +1.6). It proves the machinery, not the model: two rounds of
four agents on one plot. On the full data (877 cases of 25 winters outside the locked ones; an agent 0.36 s a case)
the estimates are 5.7 h for Quick, 12.4 h for Overnight and 30 h for every winter on its own. The owner's Mac has
not run it: with 8 workers it did one and a half to two times the cloud machine's work on SNOWPACK runs
(2026-10-06), which puts the three at about 3 h, 7 h and 16 h there. (Three to four times too long: the Mac's
first Quick test took 53 min. ADR-126 has the cause and the corrected times.)

Consequences: no change to scoring, agents, existing cases or existing runs. `lab/settings.py` and
`lab/benchmark/` (the builder and the leakage check) are in the lab's prediction code hash, so it changes and
cached predictions and SNOWPACK profiles are recomputed once on their next run, with the same results; the
builder version is unchanged, so no existing case set is rebuilt. A test builds one case set per fold (0.35 GB
each; 7.5 minutes each on a cloud core, several at once). The promotion check page and the training reports list
promotion checks only. Not done: a winter test of a run that starts from seeded agents (ADR-085), a winter test
of a trimmed training (ADR-123 reached main after this was built: `lab winter-test` has no `--trim`, so it
tests full runs; a promotion check of a trimmed run trims as that run did, through the run's plan), and feeding
the held-out scoring's SNOWPACK run times back into the estimates (done since, ADR-126).

## ADR-126 Time estimates rest on the computer's own measured speeds: an untimed family is timed first, and a guess is called a guess (owner's first winter test on the Mac, 2026-11-19)

Context: the owner's first winter test on his Mac (ADR-125: Quick, the column model, 8 workers) was estimated by
the page at "about 3 h 18 min on this computer, about 38 min for each of the 5 groups of winters" and took 53 min:
49 min of computing, a training round about a minute, and 4 min to prepare the cases. The labels of the kinds of
test said about 3, 7 and 16 hours. Two things were wrong, and checking them turned up a third.

1. The cause of the page's figure. `training/estimate.py` multiplies per-case costs that training rounds measure
   (`timings.json`, running means). The Mac had only ever trained SNOWPACK agents, so it had no time for a
   column agent, and `Timings.agent` silently returned the built-in number for the family: 0.5 s an agent a
   case, "a guess until measured" (ADR-118). The Mac needs about 0.06 to 0.10 s. That number alone is 3 h 03 min
   of the estimate (50 agent runs a case over the six rounds, 10 in the first and 8 new agents in each later
   one; 3,508 training cases over the five groups; 8 workers). Re-running the estimator with it, the test's case
   counts (719, 707, 695, 680 and 707 training cases, 877 held-out cases in all) and about 0.2 s to load a case
   gives 3 h 18 min and 38 min a group. The page called that "on this computer".
2. The labels were scaled from a cloud smoke run that measured 0.36 s an agent a case: a slow moment of a shared
   machine, with few agents a case and a cold disk. The same machine measures 0.12 to 0.17 s now.
3. Found on the way, an error in the other direction: an estimate could take SNOWPACK runs for cached that
   were still to come. The engine index kept, per case, the first profile key it ever saw; after an update of
   the prediction code every profile has a new key (the code hash is part of it) and is run again, but the
   older profile's file is still there and the index said "cached". In a winter test the bar's SNOWPACK runs on
   the 877 held-out pits are 5 to 15 minutes on the Mac (3 to 8 s a run, 8 at once). The Mac had been updated
   just before its test, so those runs were made; the page's figures fit an estimate that left them out, but do
   not prove it (a short SNOWPACK time and a shorter load fit too).

Decision:
1. Time before estimating (`training/calibrate.py`). When an estimate (a winter test, a promotion check, a
   training run) would rest on a family this computer has never timed, and it is an estimate of at least a
   minute, the family is timed first: its starting settings and seven seeded mutants (eight agents a case, about
   what a round runs, because a case's first agent also prepares the case's weather for the others) on a
   stratified sample of the run's training cases (24, or 6 for each worker), with the run's workers, through the
   worker code of a round. The predictions and scores go to a throwaway cache that is removed at once: nothing of
   them is kept, shown or used to choose anything. Each worker's first case is left out of the means (it also
   loads the code and warms the disk; the worker results now carry the process id for this). The locked test
   winters are never among the cases. Families that need SNOWPACK or the analogue library are not timed this
   way: the SNOWPACK families' cost is the engine run.
   The same is done for a family timed only by runs much smaller than the one to estimate (fewer agent runs
   than one round of it), because a family's mean is one number for all cases and a small trial's cases may be
   other ones: Simpson's take one and a half times the others'. The timing replaces such a mean and weighs in
   `timings.json` as one round of the run it was made for, so the same estimate made again does not time again,
   a later small trial cannot outweigh it, and the run's own rounds soon do.
   Nothing is timed while a training run of this lab is going in another process (a run of its own, or the
   training inside a winter test or a promotion check; `calibrate.run_going` tells it as the app does, by the
   run's `status.json` and its process). Its workers have the cores, so a timing beside it would measure a busy
   computer, call the agents slower than they are and keep that as the family's mean. The estimate names the run
   (`busy`) and says that it still guesses; the estimate made after the run has ended times.
2. A guess is called a guess. An estimate carries `guessed` (the families, and `engine` for the SNOWPACK run,
   that still rest on a built-in number where the estimate counts them) and `timed_here` (what it has just
   timed). The log says so in a line, and the page's sentence says one of: the speed was measured just now on N
   pits; the estimate is a rough guess from another machine's speed and can be several times off (with the
   reason where a run going now kept it from timing, and the advice to estimate again after it); the part for
   standard SNOWPACK uses another machine's speed. No built-in number is called this computer's.
3. The built-in numbers of the column model and the structures on it are replaced by measured ones (4 cloud
   cores, 10 agents a case, 2026-11-19): column 0.13 s, process 0.15, story 0.15, recipe 0.07, scorecard 0.13
   (they were 0.5, 0.5, 0.5, 0.2 and 0.5). They are still another machine's numbers and are treated as such.
4. An engine profile counts as cached only when the index shows one made by this code and this engine. The index
   entry of a case and physics now holds the latest profile key and a stamp (`cache.engine_stamp`: cache
   version, engine identity, prediction-code hash, engine settings files) and is rewritten when either changes;
   an estimate asks with the current stamp. Another stamp means the profile will be run again (not cached); an
   entry written before stamps says nothing and is counted as a run to come. A fold's held-out pits are looked
   up under the fold's own case set where it is built, because the scoring runs those cases and a winter test
   never refreshes the `all` set's entries. Over the folds of one check an incumbent profile is counted once (a
   later fold finds what an earlier one made). With `--engine none` no engine run is counted. A test of the pit
   reset (ADR-124) has profiles of its own and leaves the index alone.
5. The held-out scoring's times join `timings.json` too. It is where a training without SNOWPACK agents runs
   SNOWPACK (the bar), so where such a computer learns what an engine run takes.
6. The kinds of test, with times from the Mac's test (the column model, 8 workers): Quick, about 1 hour
   (measured 53 min); Full training, overnight, 5 to 7 hours: 5 groups, 30 rounds of 20 agents, 3 survivors, the
   budget of a full training run of the column model, so it tests what a real run does on unseen winters; Every
   winter on its own, 4 to 5 hours and 9 GB of disk. Full training replaces Overnight (12 rounds of 12 agents),
   which was sized to fill a night at the wrong speed, takes about an hour and a half at the real one, and
   tested a training nobody runs. The ranges are as wide as one test leaves them: its 49 min do not say how
   much was training and how much the bar's SNOWPACK runs (a later round took between 60 and 119 s). The page's
   own estimate is the one to go by.

Measured, 4 cloud cores, 2026-11-19: timing the layer-story agents, which this machine had never run, took 9 s
(20 cases counted, 8 agents each: 0.154 s an agent a case) and the whole estimate job 12 s. Repeated timings of
the column model gave 0.12 to 0.17 s (24 to 48 cases): a shared machine varies that much, which is the reason to
measure where and when the run happens. A case's first agent takes about twice the others' time (0.21 to 0.26 s
against 0.11 to 0.15 s), so the mean an agent depends a little on the agents a case; one mean per family stays.
By plot the column model takes 0.13 s (Bow Summit), 0.12 s (Goat's Eye) and 0.19 s (Simpson) an agent a case.
A small real test with SNOWPACK (Simpson only, 9 winters in 2 folds, 2 rounds of 4 column agents; the smoke test
of ADR-125) gave that test's scores again to the decimal (trained 52.3, untrained 51.6, SNOWPACK 54.1) in 1 min
41 s. Its estimate beforehand was 3.6 min and named its guess: this machine had never timed a SNOWPACK run, and
the built-in 11.8 s stood where a run took 2.9 s. Estimated again afterwards: no SNOWPACK run to come (the 65
profiles are in the index under this code's stamp), 37 s. That one-plot test left the column model's mean at
0.24 s (680 agent runs, most of them Simpson's); the next estimate of a full test timed it again on the full
set's cases (0.136 s) and the one after that did not.

Consequences: no prediction, score, case or cached result changes. `lab/training/`, `lab/services/` and the
command line are outside the prediction-code hash, so no cache is invalidated by this change. The first estimate
of a family on a computer takes some seconds longer. The index entries a computer has were written without
stamps, so the first estimate after this update counts their engine runs as to come: up to a quarter of an hour
too long for a winter test on the Mac if nothing else changed since its last test, exact once the cases have
been run or served once. Not done: timing the engine on a computer that has never run SNOWPACK (it is named as
a guess instead); a cost model of the weather preparation a case's agents share; counting once, over the folds,
the engine runs of children with new physics; means kept apart by the cases they came from (a large run on
one plot still shifts the one mean a family has); and telling other work that has the cores (a competition,
another program): a timing made beside it measures a slower computer and does not know it.

Addendum, the time left while a run goes (2026-11-19, the owner's promotion check of the 13th round's best agent, 3
winters, 5 rounds of 6 agents). An hour and a half in, the bar said "part 1 of 3, about 33 h 26 min left" where the
estimate before the start had said 7 min to 3 h. Two flaws, whatever the check's real length turns out to be. The
estimate before the start counted the SNOWPACK runs of the first round as made: the update that morning had changed
the prediction code, so none of the original run's profiles could be used, and the index kept the first key it had
seen (item 3 above, fixed by this ADR). And the time left divided a round's whole time by the first cases of its
full scoring: a round that screens new physics on a few cases first (ADR-072) counts those cases, then the full
scoring from 0, while the time ran from the round's start, so right after a screen of some minutes the first ten
cases of the full scoring stood for the whole round, and the line could show tens of hours for some minutes. A
round now notes when the step its counter belongs to began (`step_started_at`, at the round's start, the screen and
the full scoring), the time left takes that step's own pace, and the share done leaves the screen's counter out. A
run of older code has no such note and is read as before. Still rough: until a round of a part has ended, a part's
later rounds are taken to cost what its first round costs (with SNOWPACK agents the first round is the dearest: it
makes the profiles the later rounds reuse), and the parts still to come what the current one costs (a later fold
reuses most of an earlier fold's profiles).

## ADR-127 Stability check: do SNOWPACK's stability numbers point at the layers that broke in the pits' tests? (owner, 2026-11-19)

Context: the owner saw SnowpackBC's charts of Punstable and SK38 / critical crack length and asked how to use such
measures. An inventory of the observed set (2026-11-19, duplicates removed) found 448 of the 663 study-plot pits of
the four plots with a usable stability test (type, result and the height where it broke): 868 tests, 816 of them
compression tests, 27 extended column, 19 deep tap, 5 propagation saw, 1 rutschblock. Outside the locked winters
394 pits hold 714 broken layers. "No break" results are almost never recorded, so a test says where the column
broke, not where it held. The SNOWPACK profile files already hold SK38 (0533), critical cut length (0606) and RTA
(0607) for every element (lab template `OUT_STAB = TRUE`; codes as written in the profile header of the pinned
build), but the engine reader kept only SK38 and no score or agent used any. Punstable (Mayer and others 2022) is a
separate model; downloading SLF's would be a new data source and is not part of this change. The owner chose
"Build the check only" (2026-11-19): a check that scores nothing in the competition and tunes nothing.

Decision: `snowagent lab stability-check` and the app page Competitions and checks › Stability check
(`lab/services/stability.py`). For every case of the `all` set whose withheld pit has a test that broke at a known
depth (the locked winters left out unless `--include-locked`), each chosen agent's SNOWPACK run is made again
exactly as the agents make it (`VisiblePackageEngine`, the agent's physics genes), and the profile at the valid time
is read for SK38, critical crack length and RTA. A fourth number is the lemons count of the model's own layers
(McCammon and Schweizer 2002: persistent grains, grain size >= 1.25 mm, hardness <= 1.3, grain size difference
>= 0.75 mm, hardness difference >= 1.7, top 0.18 to 0.94 m deep). Per pit and number: does the layer it calls
weakest lie within 10 cm of a break (a share when layers tie), against chance (the share of the column within
10 cm of a break: what a pick at a random depth scores); and what share of the breaks it flags by its published
threshold (SK38 <= 1, crack length <= 0.4 m, RTA >= 0.8, 5 lemons) against the share of the rest of the column
it flags. Both for every break and for breaks that were easy and sudden (compression or deep tap test in 20 taps
or fewer, or while cutting, with a sudden planar, sudden collapse or pop break; an extended column or propagation
saw test that propagated). Pooled per agent, with a 90% range of (hit rate - chance) from resampling whole winters,
and per plot. The simplest choices consistent with the principles:
- Model depths are stretched to the pit's measured snow depth before matching (where in the column, not how deep;
  the depth score covers depth).
- The column looked at ends at 1.2 m or 10 cm below the deepest break, whichever is deeper (compression test
  columns rarely go deeper, so a model weak layer below that could not have been tested).
- Tests at depths within 2 cm in one pit are one broken layer.
- The SnowPro 3.0 and 3.1 files keep 339 compression test results only in the raw text ("Compression H - Hard
  21 SC"); `parse_test` reads them there. The pit reader (`obs/snowpro.py`) is left as it is for now: changing it
  changes the prediction-code hash and the observed set, so every cached forecast and case on the owner's Mac
  would be made again for no change in any forecast. Worth doing with the next change that rebuilds the cases.
- Agents that do not run SNOWPACK (column, analogue, persistence, weather rule) have no stability numbers and are
  not offered.

Consequences: no prediction, score, case or cached result changes; the module, the command line and the page are
outside the prediction-code hash. The check costs one SNOWPACK run per pit and agent (restart states shared within
the check, then deleted); each finished run is saved, so the same check id resumes. Its answers are a reading of
SNOWPACK's numbers against flat-plot tests, not a calibration: SK38 assumes a 38 degree slope, flat-plot tests
break less readily than slope tests, and Punstable was trained on slope rutschblocks. If a number finds the broken
layers clearly better than chance, the next steps are the owner's call: an instability-aware weak-layer score
(a scoring change), or a Rockies Punstable trained winter by winter on the open winters, with the locked winters
kept for its final test.

## ADR-128 The last pit's facets stay soft and faceted: a fixed on/off switch for the column model (owner, 2026-11-19)

Context: the owner's first winter-by-winter test of the column model (ADR-125, Quick) tied SNOWPACK overall (62.2
against 62.1) but lost 2014-15, 2018-19 and 2020-21 by 5 to 9 points. A cloud rerun of the three groups holding
those winters reproduced the Mac's trained and untrained scores exactly. On training winters only (the locked
winters and Parker Ridge were never read), the losses sit in forecasts that start from an old pit: over the 510
held-out pits of the rerun, the trained agents were +2.6 points against SNOWPACK with no earlier pit, +0.5 within two
weeks, -1.2 at two to four weeks and -9.5 beyond four weeks, and the three winters have the most long waits. In
the clearest case (Bow Summit, 2019-03-09, 57 days after the last pit) the model turned the pit's soft basal facets
(1F to 4F) into rounded grains at Pencil to Knife; the pit showed soft depth hoar. Two causes in the column model:
an element of the start pit takes the observer's hardness shifted by the density-hardness relation as it settles,
which hardens faceted snow that in reality stays soft, and facets round back to rounded grains after
`col_round_hours` under a weak gradient, which the model's basal gradient reaches in a few weeks. Write-up for the
owner (Claude Doc): https://claude.ai/artifact/8S38oatgzpm3Z5wfBrc7J8; scripts and per-case results in the
project's shared folder (lab/analysis/bad-winters-2026-11-19/).

Rule fixed before the checks: a change counts only if it also helps the 22 training winters other than these three.
The candidate below has no tuned number. On the untrained column model (with the new-snow weak factor at 0.4, the
setting training effectively finds) it added +0.38 points on the other 22 winters (90% range from resampling
winters +0.21 to +0.59, better in 18 of 22) and +1.25 on the three; on the three rerun groups' trained winners, on
winters they had never seen, +0.49, +0.37 and +0.72 (better in 13 of 15 winters). Rejected in the same way, because
they helped only the three winters: evaporative cooling in the surface energy balance (other winters -0.10), a
slower rounding setting alone (+0.02); also tried: the model's depth pulled down to the snow depth sensor (-0.11), no
faceting in dense snow (+0.06), faceting fastest near -5 degC (+0.19).

Decision:
1. A gene may be `fixed: true` in `config/lab.yaml`: a run-level switch, not a setting to evolve. Mutation and
   crossover never change it and draw no random numbers for it (a seeded run without the switch repeats exactly; a
   test pins the mutant and crossover hashes of the code before this change), the sensitivity screen (ADR-123)
   skips it and the drift measure (ADR-089) does not count it. A genome without the gene (any file saved before)
   takes its default, and at its default the gene is left out of the genome hash, so every existing genome keeps its
   id, name and lineage.
2. The column family gains the block `col_switches` with one such gene, `col_old_facets` (off/on, default off). On:
   the start pit's facets do not round back to rounded grains in dry snow (facets the model makes itself still can),
   and the pit's facet and depth hoar layers keep the observer's hardness instead of hardening as they settle.
   The other column structures (process, scorecard) do not carry the block and behave as before.
3. A run starts with it on from the standard model `column-old-facets` (`--initial column-old-facets`, or any
   genome file with the gene on); every agent of that run keeps it on. Off is the default everywhere, so nothing
   the owner has run or frozen changes. As with any code change, the training cache's prediction context changes,
   so stored predictions are made again once (column agents take seconds).
4. Whether to start the next column-model trainings with it on is the owner's call after the winter test with the
   switch on and off (results below).

Winter test, cloud, column model, Quick (5 groups, 6 rounds, 10 agents, seed 0), 2026-11-19, 877 pits of
25 training winters, locked winters not read. Off = the owner's run reproduced (same scores as on the Mac).
Mean of the held-out pit scores:

| | switch off | switch on | SNOWPACK |
|---|---|---|---|
| untrained model | 59.9 | 60.3 (21 of 25 winters better) | 61.8 |
| trained winners | 61.8 | 61.5 (13 of 25 winters better) | 61.8 |

Trained, the three bad winters: 2014-15 -0.9, 2018-19 +1.5, 2020-21 +2.3 (on minus off). By time since the
last pit, untrained: +2.2 beyond four weeks, +0.2 to +0.4 otherwise; trained: -1.2 to +0.4 with no pattern.
Two trainings that differ only in this switch end up with winners up to 4 points apart in a winter either way,
so the trained difference (-0.3) is within the training's own randomness, and training with the switch on
gained less (+1.2 against +1.9). Verdict: the switch helps the model it starts from, steadily but by little;
after this short training it makes no clear difference. It stays off by default; nothing is turned on.

## ADR-129 Score weights moved toward weak layers and layering; a run keeps the weights it started with (owner, 2026-11-19)
The owner: "I think we need to drop depth to 10% and raise weak layer, and layering ... Weak layers and layering is
really what we are looking for in the end", and approved the split depth 10, layering 35, weak layers 30,
confidence 15, robustness 10 (before: 20, 30, 25, 15, 10). The components themselves are unchanged (scoring
version 5 stays); only `config/lab.yaml` `scoring.weights` changes, so new runs, competitions, checks, the blind
test summary and the Jasper lab (which extends this config) use the new weights.

- A training run keeps the weights in its plan (like its scoring version, ADR-088): resuming a run started under
  other weights ranks, selects and stops as it began, with a note in its log, instead of refusing. So the run on
  the owner's Mac when this merges finishes under the old weights; its result is re-scored separately.
- The training report reads the run's own weights for "biggest improvement" and the specialists' table.
- Stored per-case component scores are enough to re-score any finished run, check or competition under any
  weights without running a model; the 2026-11-19 re-score did that for every run with saved scores.

Re-score of the saved results (scores out of 100, old weights then new; "SNOWPACK" is standard SNOWPACK on the same
pits):

| result | old | new |
|---|---|---|
| Training run of 2026-11-19, winner vs SNOWPACK, 25 training winters | 61.1 vs 58.3 | 60.2 vs 57.9 |
| same, locked winters 2023-26 (68 pits) | 58.9 vs 56.1 | 58.1 vs 55.5 |
| same, Parker Ridge (227 pits) | 58.4 vs 56.2 | 59.1 vs 56.7 |
| Promotion check of that winner (91 held-out pits, 3 winters) | 56.7 vs 56.3, pass | 55.7 vs 55.0, pass |
| Winter-by-winter test, column model (877 pits) | 62.2 vs 62.1, 14 of 25 winters better | 62.0 vs 61.6, 13 of 25 |
| Full check of the third run's winner (945 pits) | 52.1 vs 52.2, fail | 50.5 vs 50.8, fail |
| 945-pit comparison, snowfall fix vs new-snow check | 61.9 vs 60.9 | 61.4 vs 60.7 |
| 945-pit comparison, new-snow check vs snow-depth sensors | 60.9 vs 58.6 | 60.7 vs 58.1 |

No verdict flips on the stored scores. Two effects of selection under the new weights, which a re-score alone
cannot show:
1. Replaying the 2026-11-19 run's own selection rule (evenness across winters and plots, ADR-087, and the drift
   penalty, ADR-089) on all 58 agents it scored, standard SNOWPACK ranks first under the new weights (57.9 against
   57.8 for the best evolved agent); the winner ranks sixth. The winner's lead on the training winters came partly
   from snow depth (63.4 vs 59.2), which now counts half as much, while the evenness penalty is unchanged. In the
   three training runs of the promotion check the same replay picks standard SNOWPACK in all three folds, so the
   check rerun under the new weights would most likely end level with SNOWPACK, not pass. The October runs 3 to 6
   keep an evolved agent on top under the replay (their winners rank first to third).
2. On the locked winters and Parker Ridge the winner's lead holds or grows (+2.6 and +2.5 points), and on weak
   layers alone it is ahead on both (60.5 vs 56.8, 64.0 vs 56.8).

Cheat test (ADR-099's four fake forecasts made from standard SNOWPACK's 945 forecasts of the snowfall-fix
comparison, re-scored in the cloud from the stored forecasts and pits): the real forecast scores 61.9 against
52.1 for the best fake under the old weights, 61.4 against 49.8 under the new ones; its margin grows from 9.8 to
11.6 points. A fake keeps SNOWPACK's depth and loses on layering and weak layers, which now weigh more. Among the
real families, only the crude weather rule closes on SNOWPACK (19.5 to 18.3 points behind on training winters);
persistence and the analogue fall further behind.

## ADR-130 Training the lab's own models in a cloud computer: the same runs, checkpointed after every round (owner, 2026-11-19)

Context: the owner asked (2026-11-19) to run real training of the lab's own models in the cloud while his Mac does the
SNOWPACK work. A cloud computer here has 4 cores, is thrown away after a while, and work there pauses for hours at
usage limits. The lab's training already commits every round atomically and resumes from its run folder (the run
registry is repaired on resume), but that folder lives in the computer's own `data/lab`.

Decision: two small scripts, no change to the training loop, scoring or cases.
- `scripts/cloud_train.sh RUN_ID [lab train options]` runs `snowagent lab train` and, after every finished round,
  writes the run's folder and the scored-pair cache as two archives to a lasting folder (`CHECKPOINT_DIR`, default the
  project's shared folder `lab/cloud-runs`), each written beside and renamed so a reader never sees half of one. Called
  again on a computer that lacks the run, it restores both and adds `--resume` (the stored plan wins).
- `scripts/cloud_queue.sh` runs the lab's five no-SNOWPACK structures (column, process, recipe, story, scorecard),
  full and then trimmed, one after another with one budget, each with the Parker Ridge check; rerunning it skips
  finished runs and resumes a cut one.
- The first queue's budget: 15 rounds of 20 agents, 3 survivors, seed 0, the default locked winters (2023-24 to
  2025-26) and automatic stop (8 flat rounds). Half the rounds of the app's 30-round presets, chosen from measured
  speeds on the cloud computer (0.13 s an agent a case for recipe to 0.32 s for story, 4 workers: 9 to 21 min a
  round, about 2.3 to 5.3 h a run, about 20 h for the five full runs). Earlier runs gained their score by rounds 5 to 8
  and auto-stopped near round 13, so 15 rounds is expected to reach the same place for most of them.

Consequences: results are the same kind as a Mac run (same cases, scoring version and locked winters) and can be
copied into the Mac's `data/lab/outputs/training` to show in the app. The fresh-computer setup is
`bash scripts/setup_env.sh --data`, `snowagent lab prepare --workers 4`, `lab init`, `lab import`, `lab build-cases`
and the Jasper area setup (about 35 min; the SNOWPACK engine builds with the lab's patches, so SNOWPACK runs are
possible in the cloud too, slower than the Mac).

Addendum (2026-11-19): the first cloud queue (`cloud1`) started on main 211ee7b, before ADR-128 (old-facets switch)
and ADR-129 (score weights) merged. Its runs keep the scoring weights they started with, so they compare with each
other and with Mac runs of that code, not with runs started under ADR-129's weights.

## ADR-131 Keep one challenger among the survivors (2026-11-20)

Context: the owner's overnight run of 2026-11-20 (Tuned SNOWPACK, seed 3, Family slots on, 20 rounds, stopped at
round 18) named standard SNOWPACK the best agent in every round. Survivors are ranked by the composite less the
unevenness penalty (ADR-087) and the drift penalty (ADR-089), both measured against standard SNOWPACK, so standard
SNOWPACK always pays nothing: a challenger had to lead it by about 0.015 to 0.026 composite just to rank above it.
That is the safeguard working as designed for the choice of the best. The waste was in the second survivor slot:
in rounds 5 to 9 it went to near-copies of standard SNOWPACK (drift 0.07 to 0.12, spread 0.004) that scored below
it (0.5830 vs 0.5833), ahead of agents that beat it (0.5938, 0.5946), so the run bred from SNOWPACK and its copy.
From round 10 a real challenger held the slot (0.6087; locked winters 0.5786 vs 0.5607; Parker Ridge 0.5413 vs
0.5620) and stayed 0.0008 short of the best's selection score until the run stopped itself.

Decision: new runs keep one challenger. The best and the ranking are unchanged; when some agent beats the best on
the raw composite and no other survivor does, the last survivor slot goes to the first such agent in the ranking
(so the evenness and drift penalties still order the challengers). The locked-winter test and the Parker Ridge check
score the survivors as before, the challenger included. The plan records `challenger_slot: true`; a plan without it
(every earlier run and the promotion checks of those runs) keeps the plain order, so resumes are unchanged.

Limits: with two survivors this gives up the second slot's preference for evenness only when that slot would hold an
agent that does not beat the best. It does not lower the bar for being named the best or for promotion.

## ADR-132 The challenger slot keeps the highest average, not just any agent ahead of the best (2026-11-20)

Context: the owner's run of 2026-11-20 13:30 (seed 4, Family slots, ADR-129 weights, ADR-131 on) stopped itself at
round 10 with standard SNOWPACK best in every round (0.5792; locked winters 0.5548). From round 3 the second
survivor was r03-x01, a near-copy of standard SNOWPACK (drift 0.05, spread 0.002) that scored 0.5798, 0.0006 ahead of
it. ADR-131 acts only when no other survivor beats the best, so that hair's lead switched it off, and agents up to
0.6019 (rounds 5, 7, 10) were dropped every round. The near-copy's locked-winter score (0.5540) never moved, so the
stop rule ended the run after 8 flat rounds.

Decision: new runs record `challenger_slot: "highest"`. When the agent with the highest raw composite is not among
the survivors and beats the round's best on that composite, it takes the last survivor slot. The best, the ranking,
the scores, the locked-winter test, the Parker Ridge check and promotion are unchanged. Runs that recorded
`challenger_slot: true` (ADR-131) resume with that rule; plans without the key keep the plain order.

Limits: the challenger is chosen on the training average alone, so it can be uneven; it is scored on the locked
winters and at Parker Ridge every round like any survivor, and it can only be named best through the even ranking.
Under the ADR-129 weights the even ranking asks a challenger to lead standard SNOWPACK by about 0.025 composite
(spread ~0.03 x 0.5 plus drift ~5 x 0.002); whether that bar is right is the owner's call, not part of this change.

## ADR-133 A page that shows how agents' settings change as they evolve (owner, 2026-11-21)

Context: the owner asked for "something for the app that shows/visualizes the variables, genomes and weight
adjustments as the agents are evolved". A run already records every agent's full settings, parents, how it was made
and what it changed (`rounds/rNN/population.json`, ADR-066) and, while a round is being tested, its agents in the event
log (`round_started`). The Run results page shows one agent's changes as a table; nothing showed the run as a whole.

Decision: a new app page, Evolutions (training) › How agents change (`lab_app/pages/15_Agent_genes.py`, reading
through `snowagent.lab.services.genomes`). It shows, for the chosen run (the running one by default):
- what the run may change: the kind of model, how many settings its agents carry and how many the run may change
  (a trimmed run's short list, ADR-123; run switches never change, ADR-128);
- the score weights the run ranks agents with (frozen into its plan) next to today's (ADR-129), with a warning when
  they differ. These are the "weight adjustments" a reader might expect; they are not agent settings and training
  never changes them, which the page says;
- how far each round's best agent has moved from its family's standard settings: the total (the ADR-089 drift) and,
  per setting, a heat map in shares of each setting's allowed range (blue raised, red lowered, grey standard);
- one setting across every agent of every round, with the best agent's line and the standard value;
- one agent's settings (plain name, topic, standard, its value, change, allowed range, whether the run can change it,
  what it does) and its family tree inside the run, each step with how it was made and what it changed.
While a run is going, the round being tested appears at once (unscored, from the end of the event log, read
backwards because the log grows past 100 MB) and the page redraws itself when a round starts or finishes.
A run trained elsewhere (a cloud checkpoint `.tar.gz`, ADR-130) can be added from the page: one run folder holding
`run.json` and `rounds/`, extracted with the `data` filter; links, paths outside the folder, a cache archive or a run
already present are refused, and a checkpoint taken mid-run is marked stopped.

Plain names: every setting of the lab's own families now has a plain name (`ui/genes.py`), also used by the Run
results agent card; units read as words (°C per m, m/s). Agents keep their TV-character names; the evolutions' own
names come from the git-ignored `names.json` as before.

Limits: display only. Nothing is scored or changed, no run file is written except a run added from an archive.
"Standard settings" are the family's defaults, so a run started from a named standard model (for example
snowpack-era5-fix) or seeded from an earlier winner shows those starting changes as drift from round 1.

## ADR-134 Training starts from the best standard SNOWPACK and is measured against it (2026-11-21)

Context: the spot test of 2026-11-21 (top agents on 22 unseen winters, 295 pits) found every tuned SNOWPACK agent
+2.4 to +2.9 points ahead of plain SNOWPACK but only +0.4 to +0.8 ahead of the best standard SNOWPACK
(`snowpack-era5-fix`: snow depth sensors, new-snow check, snowfall fix), inside chance. The agents' one clear edge is
fewer false facet and depth hoar alarms. Training was built around plain SNOWPACK: round 1 started from it, the
drift penalty (ADR-089) counted distance from its settings, the evenness yardstick (ADR-087) was its winter-by-winter
scores, and the reports, the Watch the run page and the promotion check compared with it. The best standard sits
2.75 units of drift from plain SNOWPACK, a 0.55-point handicap in selection at the default penalty, as large as the
agents' whole lead over it. The top agents (Adriana Kensington, the seed-6 winner) found two of its three fixes but
not the new-snow check (new-snow weak factor 0.98 to 1.0 against 0.4), which is the fix that removes false weak
layers in new snow; the training reports credited them with gains the standard fixes already give.

Decision: new runs with SNOWPACK agents record `standard: snowpack-era5-fix` (`lab train --standard`, default the best
standard; `none` keeps plain SNOWPACK). Then: the standard model joins round 1 beside plain SNOWPACK (also in a
seeded run, so crossover can carry its fixes into the seeds); the drift penalty counts SNOWPACK agents' distance from
its settings; the evenness yardstick is its scores; the Watch the run page, the training report ("Compared with: best
standard SNOWPACK") and the Parker Ridge summary compare with it; a promotion check of such a run uses it as the bar.
The score, its weights, the locked winters, Parker Ridge (never trained on) and the blind test are unchanged. Plans
without the key (every run before this) resume and are checked as before. A run without SNOWPACK agents is unchanged
(ADR-118 already shows the best standard as its bar). A winter test of a kind of run with SNOWPACK agents now starts
its folds the same way, as new runs do.

Limits: round 1 costs one more agent (and SNOWPACK runs for its physics). The standard was itself picked from a few
standard versions using all winters, so beating it is a slightly harder bar than it looks. Whether the agents gain
on top of it is what the next run tests; the cloud check of the two top agents with the new-snow check added is in
the changelog entry.

## ADR-135 Training can aim survivor selection at the agent's weak spots (2026-11-22)

Context: the process switches winner passed its promotion check against plain SNOWPACK (pooled 61.5 vs 54.9 on 91
held-out pits) but on random unseen dates it is only about level with the best standard SNOWPACK
(`snowpack-era5-fix`, +0.5 to +1.1, not proven). It is better on facets, depth hoar and false weak-layer alarms, and
worse on crusts (44% found against 52%), on spring pits and on next-pit forecasts. Survivors are chosen on the
average over all cases, where those three kinds are a minority, so training has little reason to fix them.

Decision: `lab train --focus F` (0 to 3, default 0 = off). With F > 0 each case counts in survivor selection with
weight 1 + F x (next-pit forecast + pit dug March to June + pit with an observed crust), and the score adds
0.05 x F x the share of observed crusts the agent found. The consistency and drift penalties then apply as before.
The plan stores the value, so a resumed run keeps it; plans without it select as before. The score itself, its
weights, the locked winters, the Parker Ridge check, the reports and the promotion check are unchanged: focus only
changes which agents breed, and every result is still the plain score.

Limits: weighting the weak spots can cost the facet and depth hoar gain; the run's report compares both. F = 1
roughly doubles the weight of those cases; higher values were not tried.

## ADR-136 Process switches + melt, and training on spring and warm-spell pits (owner, 2026-11-22)

Context: on random unseen pits the process switches winner (Carver Daniels) is about level with the best standard
SNOWPACK (+0.5, not proven); it is better on facets, depth hoar and false alarms and worse on crusts (44% found
against 52%), spring pits and next-pit forecasts. The weak-spot run (ADR-135) gained nothing: the settings that make
spring crusts (melt, refreeze, water) are fixed at their textbook defaults in the process family (``FIXED_BLOCKS``),
so training cannot change them. The owner asked (2026-11-22) to "evolve Carver more", chose to unlock those
settings, "and then we will train and test on specific periods where there is warming or spring".

Decision:
- A new opt-in agent family `process_melt` ("Process switches + melt"): the process family's blocks plus the column
  model's `col_energy` (albedo, turbulent exchange, longwave offset, ground temperature, conductivity) and
  `col_water` (water held, refrozen share that makes a crust) blocks, 55 genes. The agent is the process agent; the
  genome's own energy and water genes replace the fixed defaults. At those defaults it predicts exactly as process
  switches (tested), so `process` genomes keep their meaning and ids, and nothing else changes for older runs.
- A process genome seeds a `process_melt` run with the new genes at their defaults (`genome.as_family`,
  `widen_seeds`: only when the new run starts with `process_melt` and not with `process`). Its parent is the
  process genome. So "Start from an earlier run's agents" with Carver's run starts from Carver exactly.
- `lab train --period spring | warm | spring_or_warm` (the Training page's "Pits to train and test on"): the run
  keeps only the cases of that part of the winter, for training, the locked test winters and the Parker Ridge check
  alike, so every score it reports is on that kind of pit. Spring: the pit was dug March to June (UTC month of the
  valid time). Warm spell: at least 6 hours with the air at or above 0 degC in the 72 hours before the pit, from the
  case's own visible weather (measured or reanalysis hours first, forecast hours where those are missing). These
  definitions were written before any run used them. The plan stores the period, so a resume keeps it; a plan
  without it uses every case. Selecting cases this way reads no score and no hidden pit.
- The Training page also gets the ADR-135 weak-spot weight ("Aim at weak spots", 0 = off), which was command-line
  only.

Limits: a period run's numbers are on its own pits and are not comparable with whole-winter runs; a winner still has
to be checked on all pits (and against the best standard SNOWPACK) before it replaces anything. Fewer cases make
the locked-winter and Parker Ridge results noisier. The new genes can also fit the training winters by chance;
the locked winters and Parker Ridge are the guard, as before.
## ADR-137 Carver Daniels, an evolved lab column model, is the site's main simulated snowpack (owner, 2026-11-22)

Context: the owner chose (2026-11-22, "lets put Carver as the main simulated snowpack on our app", then on the
decision card "Main, SNOWPACK one click away", and "keep it at Carver Daniels" for the name) to show the process
switches winner first on every plot. Carver Daniels is genome `2975778e…` (`r14-x04-process` of cloud run
`w129-process-seeded`): the lab's own column model with switchable processes (ADR-118, ADR-120), no SNOWPACK. The
evidence (`lab/analysis/process-vs-best-std-2026-11-21/report.md` in the project files): its lineage passed the
promotion check against plain SNOWPACK (pooled 61.5 vs 54.9 on 91 held-out pits); on 295 unseen pit cases it is about
level with the best standard SNOWPACK (`snowpack-era5-fix`, +0.5, 90% range crossing 0), better on facets, depth hoar
and false weak-layer alarms, worse on crusts, spring pits and next-pit forecasts. CLAUDE.md principle 1 and ADR-058
let an evolved agent reach site output only through that gate, and ADR-084 kept evolved agents to an experimental
option beside SNOWPACK.

Decision: the owner's choice overrides that gate for the site's default view only. What changes and what does not:

- **Which model.** `config/site_main_model.json` names the main model (display name, genome, source run, the
  evidence in a sentence). Swapping the genome for a better evolved one is replacing that file (the lab keeps
  evolving it); no page or pipeline code names the model. Only column-model families (`process`, `process_melt`, `column`) can be
  the main model; a SNOWPACK genome stays on the experimental route (ADR-084). No file: the site shows standard
  SNOWPACK first, as before.
- **How it runs** (`ops/main_model.py`). The lab agent's own code, not a copy: `lab.agents.column.run_hours` (the
  hourly loop of `simulate`, split out so the site can read the column at its output hours; the lab's predictions
  are unchanged, checked case by case on the synthetic lab) with the genome's genes and process extension. The
  season runs as the lab's cases see it: from bare ground at the season start until the first pit with layers, then
  from each pit (its layers, grains, hardness, temperatures) to the next. Weather: the site's plot forcing at the
  plot unit, with the lab's precipitation factors (gauge factor on gauge hours, ERA5 factor on reanalysis hours, raw
  on GFS hours, ADR-106) and the genome's plot snowfall gene; snowfall from the snow depth sensor to the switch
  (ADR-101), then precipitation. Profiles every 6 h (daily at 18 UTC in a reanalysis season) in the site's layer
  layout, daily snow depth, and a score at each pit against the last profile before it (from the run of the pit
  before, never the pit itself). A pit restarts the run at its observation time; the lab's cases see a pit only some
  hours later (profile delay), so the site's profile in those hours already uses it.
- **Traceability.** Each file carries run id, genome hash, forcing hash (of the hourly inputs), code version (hash
  of the column model's modules and this one), the model's name and family, and the pits the runs restarted from.
  Nothing writes back to any standard SNOWPACK state.
- **Files.** `web/data/<plot>/<season>_main.json`, listed in `sites.json` under the season's `main` key (so the
  deploy check and `restore-web` keep it, ADR-045/047), and `web/data/main_model.json` (the model's name, source and
  this decision). The daily `update build` runs the live season (about 11 s a plot); `snowagent update main-model
  --season Y` backfills past seasons, or reruns them after a swap. A plot whose run fails keeps its last file and the
  status page says so; the update never stops for it.
- **The page.** Weather input opens on the main model ("Carver Daniels: main model (evolved, no SNOWPACK)");
  every standard input is labelled "Standard SNOWPACK: ...". A banner under the profile says it is an evolved lab
  model, research and decision support only, not an avalanche forecast, with SNOWPACK one choice away. The season
  chart draws its depth first, with standard SNOWPACK beside it; the timeline follows the chosen input. A season
  without the main model's run opens on standard SNOWPACK.
- **Not changed.** Forecasts (fc1-3) are standard SNOWPACK's only; the main model shows measured-weather profiles
  until its own forecast runs are built. The pit-steered SNOWPACK nowcast, the new-snow check (ADR-104), the blind
  test, the public reports and the experimental agents are unchanged. Advisory label as before (principle 6).

Limits: the evidence is "about level, different strengths", not "better". The site's forcing is the site's
assembly, not the lab case builder's: the same stations and factors, but gap filling can differ hour to hour (on
the live season the two models' depths agree within 1 cm at all three plots on 2026-11-21). Past seasons show the
main model only after a backfill, which needs that season's ERA5 months where the run is.

Check on a past winter (2024-25, the site's own station forcing, 12 pits at Goat's Eye and Bow Summit, each scored
against the last profile before it): grain-class agreement 0.61 (standard SNOWPACK without pit updates 0.44,
pit-updated 0.63), hand-hardness error 0.68 steps (0.86, 0.71), layer-boundary F1 0.31 (0.27, 0.24), snow depth error
4.1 cm (6.7, 5.4). Few pits, and SNOWPACK's pit-updated scores use its nearest profile, so this shows the site run
behaves as in the lab, not that it is better.

## ADR-138 Training on Parker Ridge pits, with a plot left out for testing (owner, 2026-11-22)

Context: the owner asked (2026-11-22): "can we try training at Parkers and some others and then test at simpson?".
Until now Parker Ridge was score only (ADR-117, owner 2026-11-18): Banff's three plots trained and Parker checked. A
test at a plot the agents never saw needs both halves: Parker's pits as training cases, and Simpson kept out of
training, selection and tuning altogether.

Decision: `lab train --train-area jasper` adds the Jasper lab's cases (its own config and data root, as the area check
reads them) to every round's training cases, scored with the run's weights, seed and engine in that lab's own cache.
The run's locked winters apply to the area too: its pits from those winters are fixed in the plan
(`train_area.locked_case_ids`) and scored with the run's locked test after selection, so the stop rule and the
locked scores cover both areas. With `--plots` (e.g. `--plots BOW --plots GOAT`) a configured plot left out trains,
selects and locks nothing (as before), and now also keeps its own genes (named with its code, e.g. `snow_mult_simp`)
at their starting values: they are left out of the run's free-gene lists (plan `trimmed_genes`, with
`held_out_genes` recording why), since selection never sees that plot and mutation would only move them at random.
`--train-area` and `--area-check` cannot name the same area. The Simpson test itself is an ordinary
`lab compete --plots SIMP` of the winner beside the best standard SNOWPACK.

Limits: Simpson's snowfall comes from the Sunshine gauge near Goat's Eye (`plot_forcing.yaml`), so it shares storms
with a training plot and is not as independent as a new area. Agents must start from family defaults (not seeded
from runs that trained on Simpson). The analogue family's library still holds every plot's pits from training
winters; only the no-library families give a clean plot hold-out. Time estimates count the run's own cases only. A
run with free-gene lists shows in the app like a trimmed run.

## ADR-139 Parker Ridge on the public site, and a side-by-side view of the models (owner, 2026-11-22)

Owner, 2026-11-22: "can you add parkers profiles and study plot to the banff-snowpack app", "we also need to update
this site with how it works", "lets have the best evolved snowpack, Carver and observed beside each other ... as
visualizations on the main page", "you can show layers for the season for each simulated model as well"; and on a
decision card, "Yes, Claude adds it" (Bobby Bada Bing on the site-agents branch).

Decision:
- **A fourth plot, from its own recipe file.** `web.build.SITES` gains `parker_ridge`; its recipe stays in
  `config/new_sites.yaml` (ADR-108), whose new `site` block names the site plots and their forecast folders. Not
  moved into `config/plot_forcing.yaml`, which the lab reads as Banff's forcing (its engine-files hash and the
  Banff cases would change). `web.build._plot_cfg(plot)` gives a plot its file; `_cfg()` without a plot is
  plot_forcing.yaml with the new-site plot entries added, so `_cfg()["plots"][plot]` finds every site plot.
  `_dirs(cfg)` gives the station tables (`station_dir`), the ERA5 box (`era5_dir`) and the GFS/HRDPS folders.
- **Forcing.** Station weather from 2018-19 (Big Bend from November 2018; `STATION_SEASONS`), moved to the plot's
  height as the baseline does, with the north-west ERA5 box filling. Winters before that run on ERA5 alone with a
  neutral transfer (`era5_transfer: {ta_offset_k: 0, psum_ratio: 1}`), as the lab's Jasper test set runs them: only
  the winters with pits (2001-02 to 2013-14, each to the month of its last pit, as the data branch holds them).
  Nothing is fitted to Parker Ridge; it stays an out-of-sample test (ADR-117). The snow-depth sensor driving the
  site's SNOWPACK (ADR-102) is Big Bend's, 2.9 km away.
- **Forecasts and the live fill.** GFS and HRDPS at `parker_ridge_plot` (the plot's location) are extracted into
  `archive/forecasts/new_sites/{gfs,hrdps}`, never into the Banff archives (whose files the lab hashes). The GFS
  extracts of 2026-27 (14 September onward) were backfilled from the AWS archive with this change; the daily fetch
  retries GFS over the whole live season (AWS keeps every run) and HRDPS for 45 days. Past seasons have no forecasts
  at Parker Ridge.
- **Daily update.** `update fetch` gains a `new_sites` step (each part its own error boundary): Big Bend and Tangle
  Ridge from FTS360 since the start of the previous month (`newsites.jasper.update_stations`, shared with `ingest
  fts360 --section fts360_new_sites`: raw months archived in `archive/fts360/<key>`, converted to
  `data/interim/new_sites/stations`), GFS and HRDPS at the new points, and ERA5 months of the north-west box for the
  live season (and the previous one while a month is missing). `update bootstrap` restores the box's months from the
  Jasper data branch (`claude/lab-jasper-data`). `update build` needs nothing new: its loops run over `SITES`;
  `finish_season` checks the plot's own ERA5 box; the status page lists Big Bend and Tangle Ridge. The deploy check
  does not require a new site in `sites.json` until the deployed site lists it (then the reference check keeps it).
- **Models at Parker Ridge.** Standard SNOWPACK (pit-steered nowcast and free run), the main model (ADR-137) and the
  experimental agents (ADR-084, site code `PARK`, the lab's Jasper code; genes for other plots stay at their
  defaults there). The main model's pit restarts read Big Bend's snow depth like SNOWPACK.
- **Side by side.** A card at the top of the page: the main model, the best evolved SNOWPACK agent (the first
  SNOWPACK-family site agent with a run this season; standard SNOWPACK where none ran) and the observed pit at the
  selected time on one height scale, then the season's layers of every simulated model (main, agent, standard
  SNOWPACK) on one time axis. Names come from the data files. Bobby Bada Bing (training run
  training-20261119T001733Z-d5f826, round 12, snowpack-777a7e7a28; the only evolved SNOWPACK agent that passed a
  promotion check) was put on the site-agents branch on the owner's choice; site agents run the live season only,
  so past seasons show standard SNOWPACK in its place.
- **Text.** The brief and "How this works" describe the three kinds of model, Parker Ridge and its limits.

Check (this change's build, standard SNOWPACK, nothing tuned): Parker Ridge's modelled snow depth follows Big Bend's
sensor within 2-13 cm a day in most station winters (2022-23: 21 cm). Against the 34 plot pits of the station
winters (free run at the pit's time), snow depth is 26 cm too shallow on average (the plot, in the trees, holds
more snow than Big Bend's sensor reads), grain-class agreement 0.45, hardness error 1.1 steps; against the 86 pits of
the ERA5-only winters 42 cm too shallow, agreement 0.42, hardness 1.4 steps (the known ERA5 snowfall shortfall at
Parker, ADR-117). Banff (945 pits): agreement about 0.6. So Parker Ridge is shown as an honest out-of-area test,
not a calibrated plot; a depth or snowfall correction would be a separate, tested change.

## ADR-140 Named-layer check: observers' dated layer names score whether a forecast has that layer (owner, 2026-11-22)

Context: observers often name a layer by the day it formed ("Nov 5 crust complex", "April 10th suncrust", "Jan 7
interface"). The owner asked whether these notes could help training and chose "Build a layer-timing check". The
names are already kept on the evaluator side (`SnowLayer.date_tag`, or the layer comment in the SnowPro records) and
are forbidden in every visible file, so no agent can see them.

Decision: a read-only check, `snowagent lab named-layers --run <competition>` and the app page Competitions and checks
› Named-layer check. It reads a finished competition's stored forecasts and the withheld pits' named layers and asks,
for each named layer, whether the forecast holds a layer of the same kind (crusts with melt-freeze or ice forms,
facets and depth hoar together, surface hoar; or the grain class the pit recorded for that layer) present with
probability at least 0.5, within a tenth of the snow depth of it, depths as fractions of each column's own snow depth.
Chance is the share of random placements of a layer that thick that would also hit. Results are split by kind, by
when the layer formed (October-November, December-February, March-June), by training and locked winters, and by
whether the layer formed after the newest pit the agent saw (it had to build it from the weather) or before (it could
carry it forward). A tag is a named layer when a month can be read from it; the year is the pit's winter. Rules fixed
before any agent was scored with them.

Why not a formation date on forecast layers: forecasts carry no dates by design (agents see and return no absolute times), and
adding one would change the prediction contract. The pit's date is enough to tell built layers from carried ones.

Not a score change and not used in selection. It can become a score part only after it proves to separate agents.

First trial (cloud, all 945 cases; 193 named layers in 119 pits, 221 cases): Carver Daniels (process switches) 73%
hit, chance 50%; snowpack-era5-fix 77%, chance 53%; process default 73%, chance 53%. Layers formed since the last pit:
Carver 60% (chance 44%), era5-fix 64% (chance 45%), 98 cases. Locked winters: Carver 78%, era5-fix 72% (50 cases).
Above chance the two are tied (+23 and +24 points, 90% range by winter about +19 to +28 for both). Facets and depth
hoar hit by chance almost always (chance 78-91%), because both models put faceted layers through much of the column:
that row says little. The named-layer table is also exported for studies (crust formation and decay).

## ADR-141 Crust gate for the process switches: which warm spells leave a crust (owner, 2026-11-22)

Context: the crust study (2026-11-22) found that crusts at the plots form in warm spells (and in spring sun), but only
about one warm spell in six leaves a crust the next pit records; Carver Daniels made a new crust in 36 pit intervals
of which 8 matched. The owner approved teaching Carver which spells make crusts, learned on training winters only.

Decision: a fixed process switch `proc_sw_crustgate` (off) with four fixed genes. A warm spell starts with an hour
above 0 degC and ends after 24 hours below; its rain and hours above 0 degC count. Strong-sun days (24-hour mean
sunlight at least `proc_cg_sun_wm2`) count in a row. The gate is open when the spell had at least `proc_cg_rain_mm`
of rain or `proc_cg_warm_h` hours above 0 degC, or after `proc_cg_sun_days` strong-sun days in a row. A closed gate:
a refreeze leaves no crust. An open gate: the column's crusts form as before, and if none did, the topmost refrozen
element becomes one (once per spell or strong-sun day). Fixed genes keep every saved process genome's hash and
predictions (ADR-128); `genes_of` fills the new genes from the allow-list defaults.

Learning (training winters only, Banff 1997-2023): 18 settings (rain 1/3/30 mm x 8/24/96 h x 2/3 sun days), picked by
the F1 of new crusts between pits (the crust study's rule) with the composite no lower. Picked: 1 mm, 24 h, 2 days
(the genes' defaults stay neutral; the setting is in the genome). Leave-one-training-winter-out: the same pick 17 of
20 times; held-out new crusts 9 right / 17 extra vs Carver 4 / 19.

Result (one look at locked and Parker after the pick): new crusts right/extra, training 10/12 vs Carver 4/19 (best
standard SNOWPACK 14/18); locked 1/3 vs 0/5 (2 real); Parker 2/3 vs 2/4 (5 real). Composite per case vs Carver:
locked -0.24 (90% -0.86..+0.19), Parker -0.05: a tie. Not adopted for the site; the switch stays off. Report:
project files lab/analysis/crust-gate-2026-11-22/report.md.

## ADR-142 Facet and depth-hoar placement of the process switches: the old-facets switch joins the process families (owner, 2026-11-22)

Context: the named-layer check (ADR-140) on the owner's Mac competition of 2026-11-22 (945 Banff cases) gave Carver
Daniels (process switches, r14-x04) 78% hits on named facet and depth-hoar layers at 78% chance, and
snowpack-era5-fix 96% at 91%. The owner agreed to make facet placement Carver's next fix. Two checks first, both
from stored forecasts, on training winters only:

1. A depth-slice check (each column cut into 50 slices of its own snow depth; observed faceted slices against
   forecast faceted slices, presence probability at least 0.5). The pits are faceted in 66% of the column (87% of
   the bottom third), so any model that follows them hits a named facet layer "by chance" most of the time; the
   named-layer row rests on 31 pits. Carver forecasts facets in 63% of the column (found 78.5% of the faceted
   slices, 82.7% of its faceted slices right), era5-fix in 70.5% (85.5%, 80.3%). Above a random placement of the
   same amount both gain the same (+0.156 and +0.149). The gap is the upper third, the snow since the last pit:
   Carver finds 49% of its faceted snow, era5-fix 62%, both about half right there. Carver's missed slices are
   mostly forecast as rounded grains or decomposing snow. Facets Carver grows in new snow carry probability 0.499
   (presence 0.55 x new-snow weak factor 0.90), just under the forecast line, which training found pays.
2. Fourteen single changes to Carver, scored on the 877 training-winter cases (per-case composite against Carver,
   90% range from resampling winters): the last pit's facets kept faceted and soft (the ADR-128 switch) +0.42
   (+0.21 to +0.62, 21 of 25 winters better), with faster weak-gradient rounding off as well +0.49; showing the new
   facets (weak factor 0.95) -1.24; near-surface faceting on -0.9 to -1.0; a lower faceting gradient -0.46;
   faceting in denser snow -0.06 to +0.35 with the switch. Adding facets anywhere adds more false ones than real
   ones under the standard score.

Decision: the process and process_melt families carry the block `col_switches` (gene `col_old_facets`, fixed, off),
as ADR-128 did for the column family, and `process-old-facets` is a standard model. Off is the default, so every
saved process genome keeps its id, name and predictions. A cloud run (`oldfac-process-seeded`) retrains Carver's
genes with the switch on. Whether the site's main model changes is the owner's call after its locked-winter and
Parker Ridge results.

Results (cloud, 2026-11-23). The run `oldfac-process-seeded` (start: Carver with the switch on; 20 agents a round,
3 survivors, seed 11, Parker Ridge check) stopped itself after round 9 (no locked-winter gain for 8 rounds), 2 h 48
min. Per-case composite against snowpack-era5-fix on the same forecasts (training 877 / locked 68 / Parker Ridge
227): Carver +1.15 / +1.88 / +0.05; Carver with the switch +1.57 / +2.02 / +0.40; the run's winner r07-x01 +1.78 /
+1.66 / +1.44. The winner trades locked winters for Parker Ridge and forecasts even fewer facets in the upper third
(found 46%), so it is not used. Carver with the switch is a small gain in all three sets; the upper-third facets
need a model change (how facets grow in new snow), not tuning. Report: project files
lab/analysis/facet-placement-2026-11-22/report.md.

Owner's choice (2026-11-23, "stick with the Carver with the old facets"): the site's main model (ADR-137) is Carver
Daniels with the switch on, genome `0c817319d3…`, still shown as Carver Daniels.

## ADR-143 Facets by hours: faceting counts the time under a strong gradient, not how strong it is (owner, 2026-11-23)

Context: after ADR-142 the owner agreed to work on facets in the snow since the last pit, where Carver and
snowpack-era5-fix each find about half of the faceted snow. Diagnosis on training winters only (Carver with old
facets kept, every element of the snow since the last pit tracked hour by hour and labelled with the withheld pit's
grain at the same relative depth; 877 cases): the model's facets in new snow were right 47% of the time and found
54% of the faceted snow. The model's top 10 cm ran about 5 degC colder than the pits' thermometers (2 to 3 degC at 10
to 30 cm), but warming it (+20 W m-2 longwave, or stronger turbulent exchange) left placement unchanged. What
separates faceted from unfaceted new snow is how many hours it spent under a gradient above the faceting threshold
(alone, area under the curve 0.82), more than the model's faceting progress (0.76) or its facet/no-facet call
(0.68). The model's progress grows with how far the gradient is above the threshold, so brief, very strong
near-surface gradients made false facets and long moderate ones were missed.

Decision: a fixed switch `proc_sw_duration` (off by default, every saved genome unchanged) caps the excess at
`proc_facet_excess_cap` (fixed, default 1): above the threshold an hour counts about the same whatever the gradient.
Tested on Carver with old facets kept, training winters only (per-case composite, 90% range from resampling
winters): duration alone +0.37 to +0.66; the temperature and density brake (`proc_sw_kinetics`) off alone -0.43;
both together, by hours needed for facets, 70 h +0.65, 100 h +0.95 (23 of 25 winters), 120 h +1.18 (+0.84 to +1.57,
21 of 25), 140 h +1.25. 120 h, the middle of the three, was chosen before the locked winters and Parker Ridge were
read: cap 0.2, `col_facet_hours` 24, kinetics off. Standard model `process-facet-hours` (from the process
defaults with these settings and old facets kept).

Results, once, against snowpack-era5-fix per case (training 877 / locked 68 / Parker Ridge 227): Carver +1.15 /
+1.88 / +0.05; Carver with old facets kept +1.57 / +2.02 / +0.40; with facets by hours +2.75 / +3.32 (won 45, lost
23) / +1.99 (90% range +0.11 to +3.73, won 131, lost 96). Depth slices, training: faceted snow found 82% (was 79%),
right 84% (was 83.5%); top third found 51% (49%), right 58% (53%). Named-layer check: layers formed since the last
pit 66% at chance 46% (was 60% at 44%); facets and depth hoar 83% at 79%; rain crusts 45% (was 55%, 22 cases). Not
the site model until the owner says so.

Owner's choice (2026-11-23, decision card "Yes, put it on the site"): the site's main model (ADR-137) is Carver
Daniels with old facets kept and facets by hours, genome `b7caa2e918…`, still shown as Carver Daniels.

## ADR-145 The site shows Alberta Time (UTC-6) from 1 November 2026, MST before (owner, 2026-11-23)

Context: the site showed every time in fixed MST (UTC-7, the station-log convention, `MST_H = 7` in `web/app.js`).
Alberta stays on UTC-6 all year from 1 November 2026 (ADR-111 already reads profile exports that way), so a time
shown as "07:15 MST" was an hour behind the clocks of the people using the page. Asked whether to switch the site to
UTC-6, the owner answered "yes, switch the time zone".

Decision: the page uses the same fixed switch as `obs.localtime` (ADR-111): times before 2026-11-01 07:00 UTC
(midnight MST) are shown in MST (UTC-7) as before, and times from then on in Alberta Time, "AT" (UTC-6). A fixed date,
not `America/Edmonton` from the browser, so the page does not depend on the browser's time-zone data and past seasons
read exactly as they always have. The local-time helpers sit in one block of `web/app.js` (`toLocal`, `fmtLocal`,
`localDateStr`, `localToUtc`, `outputHours`). The hours offered in the Time menu are the local hours of the model's
outputs (00, 06, 12, 18 UTC) on the selected day: 05, 11, 17 and 23 MST before the switch, 00, 06, 12 and 18 AT
after; seasons with one daily output (18 UTC) offer 11 MST or 12 AT. The local clock goes from 23:59 MST to 01:00 AT,
so 00:xx on 1 November 2026 does not exist; `localToUtc` takes it as the switch itself (an earlier draft mapped it
an hour back, and the month-by-month loops that draw the axes then never moved past October). The Date label loses
"(MST)", and "How this works" says which zone applies when. Data files, the build and the scores are unchanged:
data stay in UTC.
