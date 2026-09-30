# Claude Code Kickoff: Terrain-to-Snowpack Predictor

Build the first working implementation of this repository. Do not stop at a plan, architecture document, ingestion pipeline or dashboard.

## The product I want

I want a system that takes weather forecasts and terrain and predicts a realistic, spatially varying, evolving snowpack for the Canadian Rockies. At a selected location and future time, it should return a layered profile: thickness, grain forms and sizes, density, hardness, temperature, liquid water, crusts and candidate weak layers.

Historical weather and field snow profiles will train, calibrate and periodically correct the system. They are not the end product, and a new snowpit must not be required for each forecast. I will provide historical datasets, actual locations and field observations later.

The user-facing contract is:

```text
terrain + weather forecast
    -> future snowpack field + queryable profiles + uncertainty
```

Internally, maintain the current snowpack from weather history and any available observations. Forecast by advancing that state, not by inventing an entire midwinter snowpack from the next few days of weather.

## Read and inspect first

Locate the repository root and read:

1. `CLAUDE.md`
2. `docs/terrain-forecast-product-spec.md`
3. `README.md`
4. `docs/snowpack-structure-research-paper.md`
5. Existing configuration and input templates.

The terrain-forecast product specification supersedes conflicting station-only assumptions in the older guide. Station runs and generic virtual slopes are development benchmarks, not the production prediction domain.

Inspect existing code and the development environment before choosing tools. Preserve unrelated files and working functionality. Treat suggested configuration keys, physics coefficients, thresholds and package APIs as unverified until checked against the installed implementation; the research documents are a design basis, not executable truth.

Give me a brief implementation sequence, then start building without waiting for approval of routine, reversible local work.

## First milestone: runnable terrain-to-profile prediction

Build the smallest genuine end-to-end forecast pipeline before adding sophisticated learning, operational integrations or an LLM interface.

### Terrain and input contracts

Implement typed, validated records for terrain units, weather observations, forecast runs, state checkpoints, layered profiles and forecast results.

Support a local DEM plus domain boundary, with explicit CRS, units and no-data handling. Derive slope, aspect and terrain shading/horizon information, and retain elevation, area, sky view and available land cover. Use a modest terrain grid or facets; make resolution configurable rather than assuming a fine raster implies fine-scale skill.

Start with local files. Keep weather adapters separate so my datasets can replace fixtures without changing the model. Preserve forecast issue time, availability time, valid time, accumulation interval, source elevation, units and provenance. Reject ambiguous units or times rather than guessing.

### A real snowpack engine

Integrate a pinned, reproducible SNOWPACK build through a container or a verified native installation. Run an upstream example successfully and add an integration test before adapting it to terrain.

Do not replace the engine with hard-coded layer-generation rules and call it a realistic model. Mocks are allowed only in explicitly labelled unit tests; they cannot satisfy end-to-end acceptance.

If installation is blocked, implement and test the surrounding interfaces, document the exact blocker and give me the minimum recovery command. Do not claim a successful physical forecast or fabricate model output.

### Persistent state and forecast branching

Implement history replay, checkpoints and forecast branches. Automatically load the latest valid terrain-state checkpoint when forecasting.

When there is no checkpoint, use supplied historical weather to initialize it. A snow-free start must be explicit and appropriate to the initialization scenario. If neither adequate history nor a defensible starting state exists, return `initialization_required`.

Forecast and what-if branches must never mutate the analysis checkpoint. An observation arriving later must create a new analysis version, not rewrite the historical forecast.

### Terrain-conditioned forcing

Use elevation and actual slope/aspect/horizon to alter the forcing that drives each snow column. Terrain must change the calculations, not merely the map display.

Implement documented elevation adjustments and direct/diffuse shortwave treatment with shading. Assign each correction to one component so the snow engine does not apply it a second time. Record assumptions when radiation components or other inputs need estimation.

Explicitly distinguish vertical depth from slope-normal thickness and horizontal-area from slope-area flux conventions.

For this milestone, independent terrain-conditioned columns are acceptable, but flag `transport_status: unresolved`. Do not infer lee-slope deposition from exposure alone. Design the transport interface now for later conservative erosion/deposition coupling; disable incompatible virtual-slope redistribution. Mark unsupported canopy or other processes explicitly.

### Structured outputs and uncertainty

Produce machine-readable forecast profiles per terrain unit, forecast step and ensemble member, plus map-ready summaries and a point/profile query.

Retain layer lineage through deposition, burial, settlement, splitting and merging where the engine exposes it; otherwise record identity uncertainty. Do not track layers by depth alone or average unrelated layer indices across members.

Expose supported engine outputs; return unavailable diagnostics as null with a reason. Do not invent hardness, weak-layer probabilities or a pretrained instability classifier when the necessary model or features are missing.

Support a small, reproducible scenario ensemble with configurable seeds and perturbations. Call its spread scenario uncertainty, not calibrated probability or guaranteed confidence.

## Demo before my data arrive

Create an explicitly synthetic terrain/weather fixture and deterministic replay scenario. Include contrasting aspects, shaded and exposed units, and different elevations, with a historical forcing period followed by a forecast period.

Use this fixture to exercise the real engine. Synthetic inputs and their resulting profiles demonstrate software and process behaviour only; they do not demonstrate Banff accuracy or constitute real observations.

Provide one command to run the demo and one to query a predicted profile. Target interfaces:

```bash
snowagent doctor
snowagent demo --output artifacts/demo
snowagent predict --domain <domain> --forecast <forecast-run> --state latest_valid
snowagent profile --run <run-id> --lat <latitude> --lon <longitude> --lead-hours 24
```

Resolve point queries to explicit terrain units and report that unit's geometry/resolution. Reject out-of-domain requests rather than silently extrapolating. Include profile plots and map-ready files for inspection, but defer a substantive web UI until I have reviewed a visual preview.

## Tests that define success

- The real engine generates future layered profiles for multiple terrain units without a new pit.
- Controlled sunny/shaded cases change local radiation and produce appropriate model responses; do not assert that one aspect must always have a fixed snow property.
- History replay and checkpoint restart agree within documented numerical tolerances.
- Forecasts cannot access weather actuals or observations that became available after issue time.
- Layer geometry and state variables satisfy physical bounds; mass-budget diagnostics include all enabled sources, sinks and boundary transfers, with documented tolerances.
- Forecast branches leave the initial checkpoint unchanged.
- Identical inputs, versions and seeds reproduce results.
- Missing initialization, invalid units, failed model runs and unsupported terrain return explicit errors or capability flags, never plausible-looking fabricated profiles.
- All outputs identify engine/configuration, terrain, input and state versions; synthetic status and unvalidated transport are visible.

## Learning after the predictor works

Prepare the architecture for observation ingestion, profile alignment, terrain/forcing correction, state updates and learned transition residuals. A later emulator may learn from physical simulations and field data, but must remain distinguishable from the physical engine.

Do not train a placeholder model on synthetic pits and claim improved real-world skill. Keep synthetic supervision separate from field observations. Future evaluation must hold out entire locations and seasons and score actual forecast leads, native-depth layer errors, crust/weak-layer presence and timing, and uncertainty calibration.

Do not equate a candidate weak layer, a test result or an uncalibrated classifier score with avalanche danger. Label outputs experimental snowpack-structure predictions for expert decision support, not operational avalanche guidance.

## Delivery and working rules

Use the simplest maintainable local stack consistent with the repository: Python, typed contracts, pytest, file-backed storage and an isolated snow-engine adapter. Avoid paid services, credentials, cloud deployment, public publishing and large downloads unless I approve them. Do not add an LLM dependency to deterministic model execution.

Keep a short progress log and implementation decisions. Work through recoverable errors; ask me only when a missing decision materially blocks correct work.

Finish with:

- Working code and tests, plus the commands actually executed and their results.
- Exact installation, demo and profile-query instructions.
- Generated sample outputs from the real engine, or a clear statement that engine execution remains blocked.
- A distinction between implemented, tested, scientifically unvalidated and deferred features.
- A concise data-intake checklist for the historical weather, terrain, locations and field profiles I will supply next.

Start now by inspecting the repository and proving the snow engine runs. Then carry the first terrain-to-profile forecast milestone through implementation and verification.
