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
- `*.~PR`/`*.~rx` autosave backups are ignored; byte-identical copies are counted once.

## ADR-015 Vermilion study plot (user, 2026-09-30)
Vermilion is an old study plot, distinct from Simpson; no station. Its location is unknown to the user and
no SnowPro file stores coordinates; recorded elevations vary (2000-2273 m, some "Vermillion Lower"), so the
plot may have moved. No coordinates are guessed. Its 89 profiles (1999-2013) are usable only where a
forcing source can be justified for an explicitly stated location. Older files without a site folder are
assigned by exact in-file site name only (e.g. "Bow Summit" yes, "Bow Summit Ski Hill" no).
