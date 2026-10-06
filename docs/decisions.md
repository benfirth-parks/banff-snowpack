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
