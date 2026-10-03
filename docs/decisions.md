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
