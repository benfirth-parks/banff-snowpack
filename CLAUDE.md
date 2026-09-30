# CLAUDE.md — working rules for this repository

Read `docs/terrain-forecast-product-spec.md` first, then `README.md` and the research paper. The terrain-forecast spec is authoritative where the original station-oriented guide conflicts.

## Product goal
Build a system that takes weather forecasts and terrain and outputs a realistic, evolving layered snowpack across that terrain. Weather history maintains the initial state; field profiles provide training, calibration and optional correction, not a mandatory input to every forecast. Station runs are development benchmarks, not the completed product. A briefing is not a substitute for predicted profiles.

## Principles
1. An explicit numerical state-transition model produces structure. Use SNOWPACK initially; allow a learned terrain-conditioned transition model or emulator only after it passes independent spatial/temporal validation and physical checks. The LLM agent never invents layers, properties or probabilities.
2. Traceability. Every output carries run_id, config hash, forcing hash, SNOWPACK version, and the profile_ids used. Raw data are immutable.
3. Baseline first. Before any learned component, produce the uncorrected-baseline verification. A component is promoted only if it beats the incumbent on held-out seasons (leave-one-season-out; never random splits within a season).
4. Nowcast state is sacred. Forecast and scenario runs copy the restart file; they never write back.
5. Flag, don't delete. QC marks data ok/suspect/bad/filled. No silent gap filling.
6. Advisory only. All user-facing output is labelled decision support, not an avalanche forecast.

## Workflow
- Follow the phases in README §9 in order; do not start a phase until its acceptance tests pass.
- Small commits, one concern each; update tests with code.
- Verify SNOWPACK .ini key names against the installed version's docs before relying on them; record any differences in `docs/decisions.md`.
- When a design choice is not covered by the README, write a short ADR in `docs/decisions.md` and choose the simplest option consistent with the principles.
- Ask the user before: changing data contracts, deleting data, adding paid services, or pulling data from sources not listed in README §6.

## Code conventions
- Python 3.11+, type hints, pydantic models for data contracts, ruff + black, pytest.
- Internal units SI, times UTC. Convert only at I/O boundaries.
- R used only through `r/*.R` scripts with JSON in/out.
- No secrets in code or config; read from environment variables.

## Definition of done for any feature
- Tests on fixtures pass; a short entry in `docs/changelog.md`; verification numbers updated if model behaviour changed.
