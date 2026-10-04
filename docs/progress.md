# Progress log

## 2026-09-30 — first milestone: runnable terrain-to-profile forecast

Implementation sequence followed:
1. Inspect starter repo, product spec, environment (Python 3.11, g++/cmake, Docker client; egress blocks
   WSL GitLab, allows GitHub git).
2. Prove the engine: clone pinned GitHub mirror, native build, run upstream MST96 example vs reference.
3. Probe engine semantics that drive design: slope projection (`PERP_TO_SLOPE`), .pro/.met geometry and
   units, grain codes, restart behaviour, mass-budget columns, `[E]`-with-exit-0 behaviour.
4. Contracts -> terrain -> weather adapter -> terrain forcing -> engine adapter -> state store/replay ->
   forecast branching/ensemble -> outputs/query/plots -> CLI.
5. Synthetic fixture + demo; unit tests; real-engine integration tests; fixes found by tests
   (bare-ground rain budget term, numerical-abort retry, stdout error capture, crust definition).

User inputs received mid-session: three station coordinates and the Sunshine Village -> Goat's Eye
study-plot association (recorded in `config/stations.yaml`, not yet validated or used).

Next: ingest real terrain (DEM + land cover + boundary), station actuals, archived forecasts, and field
profiles (`docs/data-intake-checklist.md`); then Phase 4 observation evaluation.

## 2026-09-30 (later) — first real observations
User uploaded 2025-26 profiles to `profiles/` (Propagation Labs exports). Built header intake + QC.
Blocking for observation evaluation: machine-readable layers (see ADR-012), station weather history,
time-zone confirmation, DEM. Comparison of model vs field profiles cannot start until weather forcing for
the study plots exists.

## 2026-09-30 (later) — layer transcription from images (user-directed)
Profiles uploaded for 2010-11 .. 2025-26 (still arriving). Formats: Avanet, SnowPilot, Propagation Labs,
niViz, photos. Layers transcribed by vision-model readers per `docs/transcription/GUIDE.md` in waves of
parallel batches; pilot spot-checked against charts (boundaries/hardness/temps/tests matched).
Content-hash de-duplication added (found PDF/JPEG copies and one image saved under two different dates).
Open: observation-level duplicates that are not byte-identical (same pit exported as PDF and JPG) must be
merged at ingestion; independent re-read of a random sample to estimate transcription error.

## 2026-10-01 — status against README §9 (user-requested review)
Hypothesis (user, restated): corrections trained on long records of historical forecasts, weather actuals at the
pit sites and pits make a weather-driven system predict snowpack structure better, with only weather (and
terrain) needed at forecast time. Test: SNOWPACK on raw forecasts (baseline, = the hindcast) vs the same with
trained corrections, scored on held-out seasons and held-out locations; the paper (§9) rules out end-to-end
learning of stratigraphy with ~730 pits, so structure stays physics-generated.
- Phase 0: `snowagent doctor` passed and the SNOWPACK example ran, but the tests were green only in this container (a fresh install lacked scipy and failed one integration test) and nothing ran them elsewhere; see 2026-10-04 (ADR-053). Phase 1: real 30 m DEM, station QC; real land cover not ingested.
- Phase 2/3: implemented and tested on synthetic data only; NOT yet run on real terrain with a real archived
  forecast (Phase 2 acceptance unmet). Plot work (Phase 4/6) ran ahead of it to get real forcing and scoring.
- Phase 4: 732 usable pits; transcription QA; plot baselines 2021-26; GFS hindcast (146 pit-leads); ERA5-only
  seasons 1996-2026 (constant transfer biased low, not adopted, ADR-025). Missing: DTW-aligned scores (CRAN
  blocked), leave-location-out, field-side observation noise (only transcription noise is measured).
- Phase 6: one adopted correction (precip factor 1.15, Bow Summit and Simpson, LOSO). Phases 5, 7, 8 not started.
Data gaps: no plot-level wind/radiation (ERA5 always); no Simpson gauge; Goat's Eye scored against a sensor 4 km
away that is ~20 cm deeper than the plot pits; plot coordinates unconfirmed (Simpson) or absent (Tak Falls,
Vermilion); no forecasts before 2021; blocked hosts (CaSR, ECCC, CRAN, Avalanche Canada, ACIS).
Next: phase-aware ERA5 transfer by LOSO; observation-noise measurements (pit pairs, layer persistence, stability
tests, hardness bias vs scatter); Phase 2 on real terrain; GFS->actuals correction by lead (LOSO).

## 2026-10-01 (later) — Phase 2 on real terrain
- Phase 1: real land cover ingested (ESA WorldCover 2021, ADR-032).
- Phase 2: first real-data run. Goat's Eye 6 km domain (600 m units, 51 supported + the plot as a site unit),
  season replay from a snow-free 15 Sep 2025 with station + ERA5 (to the last day ERA5 was published at issue),
  advanced with station + GFS day-1 to the forecast init, branched on the archived GFS run of 2026-03-23 00 UTC
  (5 members, 72 h). Acceptance met: 51/51 unique profiles at every lead at unobserved units, differences follow
  terrain (melt/wetting with slope shortwave, crusts with elevation); no observations used; 10 leakage checks and
  2 refusal probes pass; analysis checkpoint unchanged by the forecast. docs/verification/phase2_goats_eye_2026032300.md
- Withheld check: GFS under-forecast the storm by half (16.6 vs 31.6 mm); forecast HS +2 cm vs station +23 cm;
  5-member spread ~1 cm (ensemble not calibrated); plot pit 33 cm below the forecast (station-site vs plot gap).
Next: Phase 3 (layer tracking, map products, uncertainty that covers forecast precipitation error); GFS correction
by lead with LOSO; many-case evaluation of the domain product (all pits with a GFS run 1-3 days before).

## 2026-10-01 (later) — three-site tool, more station history, public profile sources
- Dashboard history fills Nov 2018 - May 2021: measured temperature and precipitation at all three plots for every
  season 2016-17 .. 2025-26. Per-station logger tables add Bow Summit humidity 2015-21 and the Sunshine gauge for
  2015-16 (measured-weather seasons now start 2015-16 at Goat's Eye and Simpson, 2016-17 at Bow Summit).
- Site tool for Goat's Eye, Simpson and Bow Summit (banff-snowpack.netlify.app): 30 seasons of simulated profiles
  (measured weather from 2015-16 or 2016-17, ERA5 before), daily archived GFS forecasts (Nov-Apr 2021-26, 72 h), and
  every pit with comparison scores.
- Phase 2 met at Simpson (GFS 2026-03-08; 25 simulated units, 75 of 100 blocks forest) and Bow Summit (GFS
  2026-03-02; 43 units): withheld pits within 8 and 1 cm of the forecast HS; structure scores at the level of the
  measured-weather runs. Goat's Eye 2025-26: the simulation is 9-53 cm deeper than the six plot pits (mean 22 cm,
  as in 2021-26) and 20-25 cm deeper than the Sunshine sensor from mid-February; the pits are within 15 cm of the
  sensor except on 25 March (-29 cm). Cause not established (gauge-site precipitation applied at the plot, new-snow
  density/settlement).
- Public profiles: Snow Scope (Propagation Labs) has an API with organisation keys (issued in the Snow Scope app)
  and public-data keys (for research/non-profit, on request); CAAML 6 export of manual profiles. Avalanche Canada MIN
  is open: ~450 public snowpack reports within 15 km of the plots 2016-26 (structured HS, test failure depth and
  crystal type, profile images). SnowPilot blocks this environment.

## 2026-10-04 — Phase 0 gate (ADR-053)
- Tests green from a fresh install: scipy declared (the Phase 2 acceptance checks' Spearman correlations); unit
  suite 203 passed, 1 skipped (R alignment not installed), real-engine integration suite 17 passed, both in this
  container and in a throwaway venv built from `pip install -e .[dev]` alone (unit 203 passed, 1 skipped).
- CI: GitHub Actions (`.github/workflows/ci.yml`), ruff + unit tests then the integration suite with the pinned
  engine cached; not yet run on GitHub at the time of writing (first push pending the owner's branch decision).
- `scripts/build_snowpack.sh` exits 0 after a successful build; `scripts/setup_env.sh` sets up a fresh container
  (runbook section 0; the cloud environment's setup command).
- Still open from the Phase 0 row of README §9: the Docker engine image is untested (ADR-002); the R environment
  is not part of CI. The line branch (`main` recommended) is the owner's call.
