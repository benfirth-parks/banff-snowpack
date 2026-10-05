# Training agents locally (milestones 4 and 5)

Research and decision support only, not an avalanche forecast. Evolved agents are benchmark entries: the site keeps
publishing SNOWPACK structure unless an agent passes the promotion check below (CLAUDE.md principles 1 and 3).
Design: ADR-066 (loop, cache, lineage), ADR-067 (anti-memorisation monitor), ADR-068 (promotion check), ADR-069
(Training page); milestone 5: ADR-070 (SNOWPACK physics genes), ADR-071 (shared restart segments), ADR-072
(`--screen-cases`), ADR-073 (`--family-slots`).

## What a training run does

The owner's design (2026-10-05): "One training round should be taking the historical weather forecasts and weather
actuals before every observed pit for all seasons. The two top agents then get mutated to create a new set of agents
we can have compete against each other."

1. **Round 1** scores the initial population (the default genome of each of the five families, or `--initial`
   genome files / family names) on every training case: with the default split mode `all`, one `forecast_h72` and
   one `next_pit` case per usable pit of every season 2015-16 to 2025-26 (340 cases on the data of 2026-10-05).
2. Agents are ranked by the leaderboard composite (the frozen scoring weights of `config/lab.yaml`; the loop never
   changes them). Ties: mean case composite, then fewer failures, then the genome hash.
3. **Each later round** keeps the top two (`--survivors`) unchanged and fills the population (`--population`) with
   children: a quarter (`--crossover-share`) block crossovers of the two survivors, the rest mutations of each
   survivor (`--mutation-strength`). A child whose genome was already evaluated in the run is re-drawn, so every
   round tries new genomes.
4. Every round logs the train-vs-held-out composite gap of the top two on a monitor season and flags it when it
   widens three rounds in a row (see "What the gap does and does not show").

A genome is a family plus a few dozen bounded numbers (ADR-061): it cannot hold a profile, a date or a case. Since
milestone 5 the SNOWPACK family and the hybrid's engine member also carry SNOWPACK physics genes (new-snow density,
settlement, roughness, surface hoar, the rain-snow split, precipitation per plot, measured-wind scaling; see
`agents_and_scoring.md`), so evolution can change the simulated snowpack, not only how its output is read.

## Running it

```bash
snowagent lab train --rounds 10 --population 10 --seed 0 --workers 4
```

Options: `--rounds N`, `--population M`, `--survivors` (2), `--mutation-strength` (0.2), `--crossover-share`
(0.25), `--seed` (same seed, same run), `--plots BOW --plots GOAT`, `--case-types forecast_h72`, `--workers`,
`--run-id`, `--resume`, `--initial <file or family>` (repeat), `--monitor-season`, `--gap-flag-rounds`,
`--engine auto|none`, `--snowpack-bin`, `--estimate-only`. Defaults live in `config/lab.yaml` (`training`).
Milestone 5 adds, all off or unchanged by default:

- `--screen-cases K` (ADR-072): a child with SNOWPACK physics genes new to the run is first scored on a fixed
  stratified sample of K cases (every plot and case type, spread over the seasons); only one beating the worst
  survivor there is scored on all cases and can survive. The others are listed as `screened_out` with their sample
  score. Use it when the estimate is too long; K = 30-40 keeps every stratum represented.
- `--family-slots` (ADR-073): from round 2, five of the population's places go to one mutant of each family's best
  agent so far, so the other families keep being tuned (in milestone 4 every agent from round 3 was SNOWPACK). Needs
  population - survivors >= 5. The other children are drawn exactly as without it, only fewer.
- `--no-segment-reuse`: run every engine segment (ADR-071; for checking only, results are identical).

A genome file from milestone 4 (`lab-genome-2`) works as `--initial`: it is upgraded with the physics genes at their
defaults, which predict exactly as before.

Before round 1 the command prints a time estimate from measured per-case timings (at first those of the latest
`snowagent lab compete` run, then its own) and warns when SNOWPACK-family agents dominate the cost. At the end it
prints the best composite per round with the gap, the winner with its changed genes, and the cache hit rate. Then:

```bash
snowagent lab lineage <agent id | genome hash prefix | label>   # e.g. snowagent lab lineage r07-m02-hybrid
streamlit run lab_app/Home.py                                    # page "Training"
```

The **Training** page has the same options and starts the run as a separate process (closing the app does not stop
it); it shows the live per-round leaderboard, the best composite per round, the gap with its flags, the lineage of
the current best agent and the promotion-check results. Its Stop button asks the run to stop at its next case.

**Resuming.** A run killed at any point (Ctrl-C, laptop asleep, power) continues with
`snowagent lab train --resume` (the latest unfinished run) or `--resume --run-id <id>`: committed rounds are kept,
and in the interrupted round every agent-case pair that finished comes from the cache. A resume uses the run's
stored options.

**Where things go.** `data/lab/outputs/training/<run_id>/` (`run.json`, `status.json`, `train.log`, `rounds/rNN/`,
`summary.json`); every round is also a run-registry entry (`<run_id>-rNN`, kind `evolution`).
`data/lab/outputs/cache/` holds the prediction, score and engine-profile cache shared by all runs (about 4 kB per
agent-case pair and 10 kB per engine profile; engine profiles are keyed by case and physics genes); deleting it
only costs time. `outputs/cache/segments/` holds the shared restart states of the running round (ADR-071, about
10-20 MB per physics genome) and is emptied when the round is committed.

## Run time

The cost is almost all SNOWPACK. On the 340 cases (Linux, 4 cores; a recent Mac is similar or faster per core):

| step | time |
|---|---|
| round 1 (five defaults; 340 SNOWPACK runs of about 12 s, shared by the SNOWPACK and hybrid agents) | about 18-22 min |
| each later round (8 new agents; engine profiles cached) | about 2-3 min |
| a rerun of the same genomes (seed, cases and code unchanged) | seconds (all cached) |
| `--rounds 10 --population 10` from scratch | about 40-45 min |
| promotion check, full (11 folds x the same training, plus 11 case-set builds) | several hours, see below |

With SNOWPACK physics genes (milestone 5) a child whose physics is new to the cache needs one engine run per case,
so later rounds are no longer cheap. Measured on the same machine (restart-segment reuse on; a recent Mac with 4
workers should be similar, it was not measured there):

| step (340 cases, 4 workers) | time |
|---|---|
| round 1 from an empty cache (six initial agents; 340 engine runs at about 6.7 s with restart reuse, 13 s without) | about 12 min |
| one child with new physics, scored on all cases (340 engine runs) | about 9-10 min |
| screening with `--screen-cases 30` (4-5 new-physics children on 30 cases) | about 5-7 min |
| a later round, `--population 8 --screen-cases 30` (1-5 children pass the screen) | about 14-47 min |
| a later round, `--population 10`, no screening (up to 8 new-physics children) | up to about 80 min |
| `--rounds 6 --population 8 --screen-cases 30` after round 1 (the milestone-5 run) | 2.5 h |
| `--rounds 10 --population 10` without screening | up to 12 h |

The estimate printed before a run uses the measured mean engine time and assumes that every child carries new
physics and that half of the screened ones pass; it is an upper bound in practice (output-only children, the
survivors and the cheap families cost seconds). Engine profiles are cached by case inputs and physics, so a rerun or a
promotion-check fold that meets the same physics on the same cases does not run the engine again.

Every engine profile is cached by its inputs, so a second training run (another seed, more rounds) skips the
18 minutes of round 1 engine work, and the promotion check's folds reuse the same profiles. Changing any code that
can affect a prediction (anything in `src/snowagent` outside the loop, UI and CLI) invalidates the cache on purpose.

## Building SNOWPACK on a Mac

The SNOWPACK and hybrid agents need the engine; without it they are skipped (`--engine none` does so explicitly) and
the other three families still train.

```bash
xcode-select --install                     # Apple's compiler (clang, C++17) and make
brew install cmake git
PREFIX="$HOME/.local/snowpack" SNOWPACK_SRC="$HOME/src/snowpack-model" JOBS="$(sysctl -n hw.ncpu)" \
  bash scripts/build_snowpack.sh           # pinned SNOWPACK b324cbd, about 5 minutes
export SNOWPACK_BIN="$HOME/.local/snowpack/bin/snowpack"   # add to ~/.zshrc
"$SNOWPACK_BIN" -v                          # prints the SNOWPACK, libsnowpack and MeteoIO versions
```

The script's default prefix `/opt/snowpack` needs sudo on a Mac, hence `PREFIX`. If the binary does not start with a
library error, run `export DYLD_LIBRARY_PATH="$HOME/.local/snowpack/lib"` (or rebuild after `brew upgrade cmake`).
`snowagent lab train --estimate-only` then shows SNOWPACK runs in the estimate; when the binary is missing the agents
are reported as skipped.

## What the gap does and does not show

Each round the loop compares the top two agents' composite on all other seasons with their composite on the monitor
season (default: the most recent completed season with cases at every plot that has cases, 2025-26 today). **In split
mode `all` the monitor season is also training data**: the agents were selected on it, so a small or steady gap
proves nothing. A gap that widens round after round (flagged after three) suggests selection is fitting the seasons
rather than the snowpack process; it is a warning signal only. The evidence is the promotion check.

## The promotion check

```bash
snowagent lab check-loso --genome <run_id>/<round>/<rank> --workers 4 --estimate-only   # the estimate
snowagent lab check-loso --genome <run_id>/<round>/<rank> --workers 4 --source /path/to/checkout
```

It re-runs the whole training (same options and seed) once per season with that season held out (split mode `loso`;
missing case sets are built from the checkout's archived GFS runs, about 4-5 minutes each), scores each fold's best
agent and the SNOWPACK incumbent on the held-out cases, and reports per season, pooled and PASS/FAIL. Rule
(ADR-068): PASS only if the evolved agents' pooled held-out composite beats SNOWPACK's on the same cases and the
evolved agent does not lose on a majority of seasons. A PASS is necessary, not sufficient, for site output (the
physical checks of principle 1 still apply). It is resumable (`--check-id`); `--season` limits the folds; fewer
`--rounds` / `--population` give a cheaper (weaker) check, stated as such in the result.

## Results on the data of 2026-10-05

### Training run `m4-train-r10-p10-s0`

`snowagent lab train --rounds 10 --population 10 --seed 0 --workers 4` on the 340 cases of mode `all` (186
`forecast_h72`, 154 `next_pit`; seasons 2015-16 to 2025-26), SNOWPACK 20261002.b324cbd, Linux, 4 cores. Monitor season
2025-2026 (23 cases).

| round | best agent | composite | gap (monitor) | flag | cache hit rate | wall |
|---|---|---|---|---|---|---|
| 1 | snowpack-default | 0.5089 | +0.0518 | | 0% (340 engine runs) | 22.1 min |
| 2 | r02-x01-snowpack | 0.5089 | +0.0534 | | 20% | 2.6 min |
| 3 | r03-m02-snowpack | 0.5110 | +0.0544 | | 20% | 56 s |
| 4 | r04-x01-snowpack | 0.5117 | +0.0564 | FLAG | 20% | 65 s |
| 5 | r05-x01-snowpack | 0.5155 | +0.0586 | FLAG | 20% | 69 s |
| 6 | r05-x01-snowpack | 0.5155 | +0.0605 | FLAG | 20% | 68 s |
| 7 | r07-m04-snowpack | 0.5159 | +0.0612 | FLAG | 20% | 56 s |
| 8 | r08-m01-snowpack | 0.5169 | +0.0611 | | 20% | 58 s |
| 9 | r09-x02-snowpack | 0.5188 | +0.0632 | | 20% | 56 s |
| 10 | r10-m01-snowpack | 0.5198 | +0.0602 | | 20% | 56 s |

Round 1 reproduces the milestone-3 competition exactly (SNOWPACK 0.5089, hybrid 0.4996, analogue 0.4893,
persistence 0.4070, weather rule 0.3236). Wall time of the rounds 32.7 min (round 1 was slowed by the unit tests
running beside it: 22 min against the 18 min estimate); 6,120 of 32,300 agent-case pairs came from the cache (19%:
the survivors), and SNOWPACK ran 340 times in total (round 1 only).

Winner `snowpack-793c87b12d` (`r10-m01-snowpack`), composite 0.5198 vs 0.5089 for the default SNOWPACK agent
(+0.011). Its genes against the default: `hardness_merge_tol` 0.50 -> 0.40, `depth_spread_frac` 0.12 -> 0.16,
`depth_spread_floor_m` 0.05 -> 0.20, `boundary_spread_m` 0.05 -> 0.02, `presence_confidence` 0.70 -> 0.85. Components
(winner vs default): snow depth 0.673 vs 0.638 (p10-p90 coverage 0.90 vs 0.76, MAE unchanged 0.102 m), layer
structure 0.513 vs 0.513, critical layers 0.301 vs 0.266, uncertainty 0.575 vs 0.627, robustness 0.698 vs 0.670.

Reading:
- The gain is calibration, not snowpack physics. The SNOWPACK family carries only output and uncertainty genes
  (ADR-061), so the evolved agent draws the same engine profile with wider depth intervals, slightly finer layer
  merging and more confident layer presence. Depth error and layer structure are unchanged. SNOWPACK settings as
  genes (milestone 5) are what can change the simulated snowpack.
- From round 3 every agent was a SNOWPACK-family agent: with two SNOWPACK survivors, every child is SNOWPACK (a
  crossover keeps the first parent's family). The other families were never tuned. This is the owner's design taken
  literally; an open question is whether to keep a slot per family.
- The gap of the top two on 2025-26 widened six rounds in a row (rounds 2-7, flagged in rounds 4-7) from +0.052 to
  +0.061, then levelled off (+0.060 in round 10). Per ADR-067 this is a warning signal only (2025-26 was also
  training data); it is consistent with the loop fitting the other seasons a little better than 2025-26, and is why
  the promotion check exists.

### Promotion check

`snowagent lab check-loso --genome m4-train-r10-p10-s0/10/1 --workers 4` (check `m4-loso-full`, full configuration:
10 rounds, population 10, seed 0, re-run once per held-out season; wall time 2.4 h in the cloud container).

| Held-out season | Cases | Fold winner | Winner | SNOWPACK | Difference | Result |
|---|---|---|---|---|---|---|
| 2015-2016 | 43 | r10-m01-snowpack | 0.5570 | 0.5503 | +0.0067 | win |
| 2016-2017 | 33 | r10-m03-snowpack | 0.4998 | 0.4878 | +0.0120 | win |
| 2017-2018 | 33 | r10-m01-snowpack | 0.5270 | 0.5176 | +0.0094 | win |
| 2018-2019 | 27 | r10-m01-snowpack | 0.5407 | 0.5175 | +0.0231 | win |
| 2019-2020 | 45 | r10-m01-snowpack | 0.4973 | 0.4885 | +0.0088 | win |
| 2020-2021 | 29 | r10-m01-snowpack | 0.5329 | 0.5062 | +0.0267 | win |
| 2021-2022 | 37 | r10-m01-snowpack | 0.5017 | 0.5028 | -0.0011 | loss |
| 2022-2023 | 25 | r10-m01-snowpack | 0.5421 | 0.5378 | +0.0043 | win |
| 2023-2024 | 21 | r09-x01-snowpack | 0.4878 | 0.4754 | +0.0124 | win |
| 2024-2025 | 24 | r10-m01-snowpack | 0.5575 | 0.5458 | +0.0117 | win |
| 2025-2026 | 23 | r08-m01-snowpack | 0.4723 | 0.4587 | +0.0136 | win |

Pooled over 340 held-out cases: evolved 0.5187 vs SNOWPACK 0.5089
(+0.0098); 10 wins, 1 loss, 0 ties. **Result: PASS** under the ADR-068 rule.

Pooled components (evolved vs SNOWPACK): snow depth 0.671 vs 0.638, layer structure
0.513 vs 0.513, critical layers 0.294 vs 0.266, uncertainty 0.582 vs
0.626, robustness 0.698 vs 0.670; depth MAE 0.102 m for both.

Reading: the calibration gain holds on seasons the loop never saw, so it is not memorisation. It is small (+0.01
composite), it comes from depth intervals, critical-layer confidence and robustness, and it costs some uncertainty
score. Depth error and layer structure are unchanged, as expected when only output genes evolve. Passing this check
makes the agent eligible under CLAUDE.md principle 3; it does not by itself change site output (ADR-058).

### Training run `m5-train-r6-p8-sc30-s0` (milestone 5: physics genes)

`snowagent lab train --rounds 6 --population 8 --screen-cases 30 --seed 0 --workers 4 --initial <M4 winner file>
--initial persistence --initial weather_rule --initial analogue --initial snowpack --initial hybrid` on the same 340
cases. The M4 winner (`snowpack-793c87b12d`, upgraded to schema 3 as `snowpack-63e51c08aa`, physics at defaults)
and the five family defaults form round 1; round 1 had been run once before as a warm-up (`m5-warm-r1`, 12 min, 340
engine runs, 691 of 1378 restart segments reused) and came from the cache. Printed estimate 5 min-4.4 h (upper bound:
every child new physics, half passing the screen); wall time 2.5 h.

| round | best agent | composite | depth MAE | screen: passed / new-physics children | engine runs | cache hit rate | segments reused | wall |
|---|---|---|---|---|---|---|---|---|
| 1 | r10-m01-snowpack (M4 winner) | 0.5198 | 0.102 m | - | 0 (cached) | 100% | - | 2 s |
| 2 | r02-m03-snowpack | 0.5260 | 0.096 m | 2 / 4 | 740 | 36% | 54% | 25 min |
| 3 | r03-m02-snowpack | 0.5339 | 0.098 m | 5 / 5 | 1,487 | 31% | 54% | 41 min |
| 4 | r03-m02-snowpack | 0.5339 | 0.098 m | 1 / 4 | 430 | 42% | 54% | 14 min |
| 5 | r05-m03-snowpack | 0.5399 | 0.092 m | 2 / 4 | 740 | 36% | 54% | 22 min |
| 6 | r05-m03-snowpack | 0.5399 | 0.092 m | 5 / 5 | 1,700 | 31% | 54% | 47 min |

Engine runs include the screening runs (30 per screened child). Over the run: 5,097 engine runs (33,000 s of engine
time on 4 workers), 6,190 of 14,220 agent-case pairs from the cache (44%), 10,073 of 20,681 restart segments reused
(49%); 22 children screened, 7 screened out. The gap on 2025-26 stayed between +0.051 and +0.060 and was never flagged.

Winner `snowpack-c276ebef53` (`r05-m03-snowpack`), against the incumbent (`snowpack-default`) and the M4 winner:

| | composite | snow depth | layer structure | critical layers | uncertainty | robustness | depth MAE | depth bias | p10-p90 coverage |
|---|---|---|---|---|---|---|---|---|---|
| M5 winner | 0.5399 | 0.703 | 0.533 | 0.339 | 0.562 | 0.702 | 0.092 m | +0.012 m | 0.95 |
| M4 winner | 0.5198 | 0.673 | 0.513 | 0.301 | 0.575 | 0.698 | 0.102 m | -0.013 m | 0.90 |
| SNOWPACK incumbent | 0.5089 | 0.638 | 0.513 | 0.266 | 0.627 | 0.670 | 0.102 m | -0.013 m | 0.76 |

Per plot (composite; depth MAE) the winner beats both at every plot: Bow 0.561 / 0.089 m (incumbent 0.536 / 0.097 m),
Goat's Eye 0.544 / 0.088 m (0.507 / 0.099 m), Simpson 0.498 / 0.106 m (0.458 / 0.119 m); and for both case types:
forecast_h72 0.523 (0.487), next_pit 0.559 (0.531).

Its genes against the default. Physics: `sp_hn_density_parameterization` LEHNING_NEW -> PAHAUT,
`sp_rain_snow_mid_c` 1.2 -> 1.36 degC (ramp 0.36-2.36 degC instead of 0.2-2.2), `sp_roughness_length_m` 0.002 ->
0.00085 m, `sp_hoar_density_buried_kg_m3` 125 -> 106. (`sp_hn_density_fixed_kg_m3` 100 -> 81 also changed but is
inactive with PARAMETERIZED, so the physics key ignores it.) Output genes, all inherited from the M4 winner except
the first: `depth_spread_frac` 0.12 -> 0.19, `depth_spread_floor_m` 0.05 -> 0.20, `boundary_spread_m` 0.05 -> 0.02,
`presence_confidence` 0.70 -> 0.85; `hardness_merge_tol` is back at its default 0.5. No precipitation, wind,
viscosity or surface-hoar-formation gene changed.

Reading:
- For the first time the simulated snowpack changed and got better: depth MAE 0.102 -> 0.092 m (-10%) and layer
  structure 0.513 -> 0.533, where the M4 winner could only re-read the incumbent's profile. Critical layers improved
  further (0.301 -> 0.339). Uncertainty is again the component that loses a little.
- Physical check of the winner's engine profiles against the incumbent's on the 339 cases where both ran: mean bulk
  density 268 vs 273 kg m-3, top 10 cm 119 vs 103 kg m-3 (PAHAUT makes denser new snow: on the case used to verify the keys
  its surface element was 62 kg m-3 against 32 for LEHNING_NEW), maximum 372 vs 377 kg m-3, mean modelled depth 1.30 vs 1.27 m; more faceted thickness
  (50% vs 43%) and less rounded (9% vs 12%) and melt-freeze (0.9% vs 1.7%). No unphysical values; principle 1's
  physical checks still need a snow scientist's look before any site use.
- Every agent from round 2 was again a SNOWPACK-family agent (`--family-slots` was off to keep the owner's loop).
- `--screen-cases 30` held 7 of 22 new-physics children back. Rounds where all five passed cost 41-47 min against
  14-25 min otherwise; the screen threshold is the worst survivor, which is lenient once the survivors are close.
- Restart reuse halved the engine work of each case in a (plot, season) group: about 6.5 s per run against 13 s.

### Promotion check, reduced (milestone 5)

The full check of this run (`check-loso --genome m5-train-r6-p8-sc30-s0/6/1`, 6 rounds per fold) printed 1.1-29.5 h
(20.9 h expected from the run's measured rounds), so a **reduced, weaker** check was run instead:
`snowagent lab check-loso --genome m5-train-r6-p8-sc30-s0/6/1 --rounds 2 --workers 4` (check `m5-loso-r2`;
population 8, `--screen-cases 30` and seed 0 from the run; printed 27 min-6.1 h, wall time 48 min because the fold
runs met engine profiles already in the cache). It tests round 1 plus one round of physics children, not the six
rounds that produced the winner.

| Held-out season | Cases | Fold winner | Winner | SNOWPACK | Difference | Result |
|---|---|---|---|---|---|---|
| 2015-2016 | 43 | r02-m03-snowpack | 0.5610 | 0.5503 | +0.0107 | win |
| 2016-2017 | 33 | r02-m03-snowpack | 0.5113 | 0.4878 | +0.0234 | win |
| 2017-2018 | 33 | r02-m03-snowpack | 0.5417 | 0.5176 | +0.0241 | win |
| 2018-2019 | 27 | r02-m03-snowpack | 0.5470 | 0.5175 | +0.0295 | win |
| 2019-2020 | 45 | r02-m03-snowpack | 0.5048 | 0.4885 | +0.0163 | win |
| 2020-2021 | 29 | r02-m03-snowpack | 0.5557 | 0.5062 | +0.0495 | win |
| 2021-2022 | 37 | r02-m03-snowpack | 0.5071 | 0.5028 | +0.0043 | win |
| 2022-2023 | 25 | r02-m03-snowpack | 0.5444 | 0.5378 | +0.0066 | win |
| 2023-2024 | 21 | r02-m03-snowpack | 0.4969 | 0.4754 | +0.0215 | win |
| 2024-2025 | 24 | r02-m03-snowpack | 0.5622 | 0.5458 | +0.0164 | win |
| 2025-2026 | 23 | r02-m03-snowpack | 0.4710 | 0.4587 | +0.0124 | win |

Pooled over 340 held-out cases: evolved 0.5260 vs SNOWPACK 0.5089 (+0.0171); 11 wins, 0 losses. **Result: PASS**
under the ADR-068 rule, for the reduced procedure. Pooled components (evolved vs SNOWPACK): snow depth 0.693 vs
0.638, layer structure 0.519 vs 0.513, critical layers 0.308 vs 0.266, uncertainty 0.579 vs 0.627, robustness 0.681
vs 0.670; depth MAE 0.096 vs 0.102 m.

Reading:
- Every fold picked the same agent, `r02-m03-snowpack` (the M4 winner's output genes plus new-snow density FIXED at
  89 kg m-3 and Bow precipitation x0.98), which the main run also found in round 2. It was chosen in each fold
  without that fold's season, and it beats the incumbent on all 11 held-out seasons, so its physics change is not
  fitted to one season. Because the fold winner is the same genome everywhere, the pooled held-out score equals that
  genome's score on all cases.
- The check does not cover the run's final winner `r05-m03-snowpack` (PAHAUT density, rain-snow ramp, roughness,
  buried-hoar density; 0.5399 in-sample). Rounds 3-6 remain untested on held-out seasons. A full check would follow
  the same trajectory in most folds and so meet many cached engine profiles; it is likely much shorter than its
  printed estimate, which assumes every later-round engine run is new.
- As in M4, a PASS makes an agent eligible under principle 3; it changes no site output (ADR-058), and principle 1's
  physical checks still apply.
