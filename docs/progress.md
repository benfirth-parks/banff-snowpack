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
- Phase 0 done. Phase 1: real 30 m DEM, station QC; real land cover not ingested.
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
