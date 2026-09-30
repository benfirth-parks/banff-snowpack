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
