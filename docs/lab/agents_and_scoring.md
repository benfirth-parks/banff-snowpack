# Snowpack Agent Lab: agents, scoring and competitions

Research and decision support only, not an avalanche forecast. Milestone 3 (ADR-060 to ADR-065). The benchmark
cases these agents run on are described in `benchmark_protocol.md`.

## 1. Genomes

An agent is its genome: a family and a flat map of genes (`lab.schemas.genome`, ADR-061). The allow-list is
`config/lab.yaml` `genome:` (part of the config hash): each gene has a kind (float, int, choice), a range or choices,
a default, a unit and a meaning; each family names the gene blocks it carries.

| family | blocks | genes |
|---|---|---|
| persistence | pit, forcing, new_snow, settlement, uncertainty | 20 |
| weather_rule | forcing, new_snow, settlement, crust, facets, surface_hoar, rule_pit, uncertainty | 31 |
| analogue | analogue, uncertainty | 13 |
| snowpack | engine_output, uncertainty | 5 |
| hybrid | blend, pit, forcing, new_snow, uncertainty | 20 |

A genome holds no profile, layer, date, pit or case field, at most 64 genes and 4096 bytes of JSON; unknown,
missing or out-of-range genes are rejected. Identity is the genome hash (sha256 of schema version, family and
genes); the agent id is `<family>-<first 10 hex>`. `lab.genome`: `default_genome(family)`, `load_genome(path)`,
`save_genome`, `mutate(genome, strength, rng)` and `crossover(a, b, rng)` (pure, seeded, always valid).

A genome file:

```json
{"schema_version": "lab-genome-2", "family": "persistence", "label": "persistence-trusting",
 "genes": {"pit_trust": 0.9, "depth_change_weight": 0.8, "...": "every gene of the family's blocks"}}
```

## 2. Agents (`lab.agents`, ADR-062)

Every agent implements `predict(case: VisibleBenchmarkCase, seed) -> SnowpackPrediction` and sees nothing else: the
anonymous visible package (hours relative to as-of, day of year, pit keys) and its genome. It answers with snow
depth quantiles, layers (depth quantiles, grain, hardness, presence probability, critical class) and its limits, or
`insufficient_data` with a reason. The harness stamps the case id and real times on the answer.

- **persistence**: the latest pit of the season carried forward: measured depth change since the pit, new snow on
  top, forecast snowfall, settlement, degree-day melt; layer presence fades with the pit's age.
- **weather_rule**: a column built from the weather in 6 h steps: storms, rain, melt-freeze crusts, settlement,
  rounding, near-surface facets, depth hoar, surface hoar; nudged toward the latest pit depth.
- **analogue**: k nearest past cases by weather and depth features; their depth change and the nearest one's pit
  structure. The library holds cases of other seasons only (never the scored case's season, never holdout,
  validation or sealed pits) and no season, date or id: an analogue agent cannot memorise the pits it is scored on.
- **snowpack** (the incumbent): SNOWPACK run from the visible package with the adopted settings and the pit restart
  (ADR-063); skipped when the binary is missing. A site season run is reused only when it used nothing unavailable
  at as-of (today none qualifies: they use ERA5 wind and radiation up to the profile time).
- **hybrid**: SNOWPACK structure, depth blended with the carried pit and the rule column, plus pit and near-surface
  rule layers of concern the engine does not have.

## 3. Scoring (`lab.competition.scoring`, ADR-064)

Each component is in [0, 1], 1 = perfect.

| component | per case |
|---|---|
| snow_depth | 0.75 exp(-\|p50 - observed\| / 0.15 m) + 0.25 [observed inside p10..p90] |
| layer_structure | 0.5 ordered layer match F1 + 0.3 grain agreement + 0.2 hardness agreement, on relative depth |
| critical_layers | soft critical success index over layers of concern (SH, FC, DH, crusts) with presence probabilities |
| uncertainty | 0.5 (1 - Brier of the four class-present events) + 0.5 exp(-interval score / 0.5 m) |
| robustness | 1 if the agent answered, 0 if not; on the leaderboard (1 - failure rate) x min(1, P10 / mean) |

- Ordered match: the longest order-preserving pairing of predicted and observed layers of the same major grain class
  (first two letters of the IACS code) whose mid-depths are within 0.15 of the column (relative depth = depth / own
  snow depth, so a depth error is counted once). F1 = 2 pairs / (predicted + observed layers).
- Grain and hardness agreement: at 20 relative depths, equal major class; 1 - |hardness index difference| / 2.
- Critical layers: a predicted layer of concern matches an observed one of the same class within 0.15 relative
  depth (each once, nearest first). Hits = matched probabilities, misses = unmatched observed + (1 - p) of matched,
  false alarms = p of unmatched predicted; CSI = hits / (hits + misses + false alarms). No observed layer of concern:
  1 / (1 + false-alarm weight).
- Interval score (alpha 0.2): (p90 - p10) + 10 x how far the observed depth lies outside.
- Depth-only targets (pits without placed layers) score depth and the interval part only; the weights are
  renormalised. `insufficient_data` and agent errors score 0; cases an agent could not run on are skipped.
- Composite: case composite = weighted mean of the components present with the frozen weights of
  `config/lab.yaml` (`scoring.weights`: depth 0.20, structure 0.30, critical 0.25, uncertainty 0.15, robustness 0.10);
  leaderboard composite = 0.9 x mean case composite + 0.1 x robustness.
- Truth: read only for the scoring splits of the case set's mode (all: training; loso: training and holdout; split:
  development and validation), never sealed-test truth.

## 4. Competitions (`snowagent lab compete`, ADR-065)

```bash
snowagent lab compete --workers 4                                   # default agents, every scorable case
snowagent lab compete --agents my_genome.json --agents persistence  # genome files and/or family defaults
snowagent lab compete --plots BOW --case-type forecast_h72 --forecast-source archived_gfs --limit 20
snowagent lab compete --engine none                                 # no SNOWPACK binary: skipped, hybrid falls back
snowagent lab compete --run-id <run_id>                             # resume an interrupted run (same plan)
snowagent lab compete --heldout-season 2023-2024                    # also print the train-vs-held-out gap
snowagent lab leaderboard [--run-id <run_id>] [--heldout-season S]  # print a finished run
```

`--source <checkout>` names the checkout whose site runs (`web/data`) may be reused (read only). Outputs:
`data/lab/outputs/competitions/<run_id>/` with `run.json`, `genomes/`, `library.json`, `cases/<case_id>.json`,
`scores.parquet`, `leaderboard.json`, and a `competition` run manifest in the registry (run id, config hash, genome
hashes, case-set hash, seed, scoring weights, SNOWPACK version, code version, profile ids used). The Streamlit page
**Leaderboard** shows the scores with plot, case type and forecast-source filters, and a scored case's predicted
profile beside the observed pit.

Held-out gap: `heldout_gap(scores, season)` (and `--heldout-season`) returns per agent the composite on the other
seasons, on the named season and the gap. A large positive gap is what memorising would look like; milestone 4 runs
it on a leave-one-season-out case set before promoting an evolved agent.

## 5. First competition (2026-10-05, the 340 cases of mode `all`)

Run `m3-default-agents`: the default genome of each family on all 340 cases (186 `forecast_h72`: 72 archived GFS,
114 stand-in; 154 `next_pit`; BOW 143, GOAT 127, SIMP 70), seed 0, 4 workers, 19.4 min wall. SNOWPACK
20261002.b324cbd ran from the visible package on every case (no site run qualified: the site runs of all 340 cases use ERA5
forcing; 23 cases fall in a live-mode and 15 in an ERA5-mode season); profile lag at most 7 min. No case was skipped; the only failure is one
persistence case with neither a pit nor a measured depth.

| agent | composite | depth | structure | critical | uncertainty | robustness | depth MAE (m) | bias (m) | p10-p90 coverage |
|---|---|---|---|---|---|---|---|---|---|
| snowpack | **0.509** | 0.638 | 0.513 | 0.266 | 0.627 | 0.670 | 0.102 | -0.013 | 0.76 |
| hybrid | 0.500 | 0.618 | 0.505 | 0.269 | 0.621 | 0.641 | 0.116 | -0.067 | 0.75 |
| analogue | 0.489 | 0.657 | 0.486 | 0.222 | 0.593 | 0.677 | 0.104 | 0.001 | 0.77 |
| persistence | 0.407 | 0.539 | 0.461 | 0.164 | 0.559 | 0.362 | 0.190 | -0.111 | 0.65 |
| weather_rule | 0.324 | 0.286 | 0.337 | 0.150 | 0.464 | 0.582 | 0.288 | -0.260 | 0.26 |

Composite by forecast source, plot and case type:

| agent | archived GFS (72) | stand-in (268) | BOW | GOAT | SIMP | forecast_h72 | next_pit |
|---|---|---|---|---|---|---|---|
| snowpack | 0.496 | 0.514 | 0.536 | 0.507 | 0.458 | 0.487 | 0.531 |
| hybrid | 0.484 | 0.503 | 0.534 | 0.505 | 0.436 | 0.487 | 0.512 |
| analogue | 0.485 | 0.493 | 0.491 | 0.496 | 0.475 | 0.499 | 0.477 |
| persistence | 0.394 | 0.422 | 0.422 | 0.424 | 0.357 | 0.408 | 0.410 |
| weather_rule | 0.332 | 0.322 | 0.320 | 0.377 | 0.230 | 0.324 | 0.324 |

Runtime per case (mean / median / p90, s): snowpack 11.8 / 10.8 / 20.6 (the engine run, longer late in the season);
hybrid 0.24 (it reuses the case's engine profile); weather_rule 0.23; persistence 0.05; analogue 0.02.

Reading: the uncorrected SNOWPACK incumbent leads overall and on next-pit cases; the analogue agent (other seasons
only) has the best depth score and leads on `forecast_h72` and at Simpson; critical layers are weak for every agent
(at best 0.27). The default weather-rule genes are hand-set and under-predict depth by 0.26 m on average, a target
for the evolution loop. The hybrid's fixed default blend does not beat SNOWPACK yet. `heldout_gap` for 2023-24 (21
cases): snowpack +0.036, hybrid +0.035, analogue -0.031, persistence +0.067, weather_rule -0.018; none of these
agents was tuned on any season, so the gaps show how 2023-24 differs, the baseline the evolution loop's gaps are
compared with.
