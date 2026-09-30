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
