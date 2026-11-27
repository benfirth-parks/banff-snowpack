# Training agents locally (milestones 4 and 5)

Research and decision support only, not an avalanche forecast. Evolved agents are benchmark entries: the site keeps
publishing SNOWPACK structure unless an agent passes the promotion check below (CLAUDE.md principles 1 and 3).
Design: ADR-066 (loop, cache, lineage), ADR-067 (anti-memorisation monitor), ADR-068 (promotion check), ADR-069
(the training pages; ADR-119 split them into Start a run, Watch the run, Run results, Blind test and site and
Promotion check); milestone 5: ADR-070 (SNOWPACK physics genes), ADR-071 (shared restart segments), ADR-072
(`--screen-cases`), ADR-073 (`--family-slots`).

## What a training run does

The owner's design (2026-10-05): "One training round should be taking the historical weather forecasts and weather
actuals before every observed pit for all seasons. The two top agents then get mutated to create a new set of agents
we can have compete against each other."

1. **Round 1** scores the initial population (the default genome of each of the five families, or `--initial`
   genome files / family names) on every training case: with the default split mode `all`, one `forecast_h72` and
   one `next_pit` case per usable pit of every season 2015-16 to 2025-26 (340 cases on the data of 2026-10-05) and,
   since ADR-076 (switch `splits.include_reanalysis_seasons`, on), of every season 1997-98 to 2014-15, whose weather
   is ERA5 alone (about 605 more cases once `lab prepare` has fetched their ERA5 months).
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
`--engine auto|none`, `--snowpack-bin`, `--estimate-only`, `--weather-sources station|mixed|era5_only` (repeat;
ADR-076: only cases of those weather sources; recorded in the run's plan, inherited by `check-loso`). Defaults live
in `config/lab.yaml` (`training`). Every round's `leaderboard.json` splits the scores by weather source
(`by_weather_source`) as by forecast source, plot and case type.
Milestone 5 adds, all off or unchanged by default:

- `--screen-cases K` (ADR-072): a child with SNOWPACK physics genes new to the run is first scored on a fixed
  stratified sample of K cases (every plot and case type, spread over the seasons); only one beating the worst
  survivor there is scored on all cases and can survive. The others are listed as `screened_out` with their sample
  score. Use it when the estimate is too long; K = 30-40 keeps every stratum represented.
- `--family-slots` (ADR-073): from round 2, five of the population's places go to one mutant of each family's best
  agent so far, so the other families keep being tuned (in milestone 4 every agent from round 3 was SNOWPACK). Needs
  population - survivors >= 5. The other children are drawn exactly as without it, only fewer.
- `--no-segment-reuse`: run every engine segment (ADR-071; for checking only, results are identical).

**The column model (ADR-118).** `snowagent lab train --initial column --rounds 30 --population 20 --survivors 3`
(Start a run page, kind of run "Column model, no SNOWPACK") evolves only the lab's own snowpack model, with no SNOWPACK in
the agents. With locked winters, round 1's locked test also scores the best standard SNOWPACK
(`snowpack-era5-fix`, plan key `locked_reference`) as the bar; it is never in the population and does not count
as the run's own best for the automatic stop. When the initial population
is smaller than `--survivors`, it is filled to `--population` with seeded mutants of it (`start-mNN-column`). With
`--family-slots` the column family joins the five standard families' slots. Runs that do not choose it are
unchanged.

**Other genome structures (ADR-120).** `--initial process`, `--initial recipe` and `--initial story` (Training page
presets "Process switches, no SNOWPACK", "Weather recipes, no SNOWPACK", "Layer life stories, no SNOWPACK") work
the same way: one opt-in family alone, filled with seeded mutants (`start-mNN-<family>`), standard SNOWPACK as the
locked-winter bar. Give each the same rounds, population and winters as the column run it is compared with, and
compare them on the locked winters and the Parker Ridge check, not on the training score.

**The site scorecard (ADR-122).** `--initial scorecard --area-check jasper` (preset "Column + site traits, no SNOWPACK",
with the Parker Ridge box ticked). Its default is the column model, and its gains on the Banff winters say only
that the plots differ; judge it on the Parker Ridge check.

**Trimmed runs (ADR-123).** `--trim` (presets "Column model, trimmed", "Process switches, trimmed", "Weather
recipes, trimmed", "Layer life stories, trimmed", "Column + site traits, trimmed"; or the Trimmed box under
Advanced) evolves only each family's short list of genes, `genome.trimmed` in `config/lab.yaml`. Every other gene
keeps the value it starts with: it is fixed, not removed, and goes back into play by adding it to the list. The run's
plan records the lists (`trimmed_genes`), so a resume and a promotion check trim the same way, and the run's label
says "trimmed". The lists come from `snowagent lab sensitivity <family>`: each gene is moved on its own, halfway
to each of its bounds, on a stratified sample of the training winters (the locked winters are never loaded), and
the twelve genes that move the score most are kept (the site scorecard: two site genes and ten others). Rerun it
on every training case with `--cases 0 --workers 8`; it prints the table and the configuration line and writes
`outputs/sensitivity/<family>.json`. A trimmed run is judged like a full one, on the locked winters and the Parker
Ridge check, against the full run of the same structure with the same budget.

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

The cost is almost all SNOWPACK and grows with the number of cases: the figures below are for the 340 cases of
2015-16 to 2025-26. With the older seasons (ADR-076, about 945 cases) multiply round and training times by about
2.8 and the full promotion check by about 7 (28 folds instead of 11, each on about 2.8 times the cases);
`run_locally.md` has the scaled table. On the 340 cases (Linux, 4 cores; a recent Mac is similar or faster per core):

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
brew install cmake                         # needs Homebrew: run_locally.md section 0
bash scripts/build_snowpack.sh             # pinned SNOWPACK b324cbd into ~/.local/snowpack, about 5 minutes
~/.local/snowpack/bin/snowpack -v          # prints the SNOWPACK, libsnowpack and MeteoIO versions
```

On macOS the script installs to `~/.local/snowpack` (no sudo) with `bin/` and `lib/` as on Linux (upstream's macOS
layout would put them in an app-bundle directory beside the prefix; ADR-075), and snowagent finds it there without
`SNOWPACK_BIN`. Another location: `PREFIX=/path bash scripts/build_snowpack.sh` and
`export SNOWPACK_BIN=/path/bin/snowpack`. `snowagent lab train --estimate-only` then shows SNOWPACK runs in the
estimate; when the binary is missing the agents are reported as skipped. The whole fresh-clone path is
`run_locally.md`.

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
one fold per season with training cases: 28 with the older seasons, 11 without;
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

### Winter-by-winter test (ADR-125)

`snowagent lab winter-test --initial column --workers 8` tests a training procedure on winters it never saw. The
winters (minus the three locked ones, which stay out of everything) are dealt out into 5 folds, winter i (in date
order) to fold i mod 5, so every fold has old and recent winters. Each fold trains on the other folds' winters only
(its case set `loso_<w1>+<w2>+...` hides the fold's pits from every training case, and the leakage check verifies
it for each held-out winter) and then scores three agents on each held-out winter: the fold's winner, the best
untrained starting agent (the starting settings that ranked first in round 1) and the best standard SNOWPACK
(`snowpack-era5-fix`, the bar the locked-winters reading uses too).

The result, `outputs/loso_checks/<test>/result.json`:

| key | what it is |
|---|---|
| `per_winter` | one row a held-out winter: `cases` (pits), `evolved`, `start` and `snowpack` (composites 0-1), `difference` (evolved minus SNOWPACK), `training_gain` (evolved minus start), `fold` |
| `vs_snowpack`, `training_gain` | the per-winter differences summed up: `mean`, `sd`, `lowest`, `highest`, `better` and `worse` (winters), `range90` (90% range of the mean from 2,000 resamples of the winters) |
| `pooled_evolved_composite`, `pooled_incumbent_composite`, `pooled_start_composite` | the three agents over all held-out pits (each pit forecast by its own fold's agents) |
| `pooled_training_gain` | trained minus untrained over all held-out pits |
| `gain_on_training_winters` | trained minus untrained on the winters each fold trained on, averaged over the folds |
| `memorising_gap` | `gain_on_training_winters` minus `pooled_training_gain`: what training added that did not carry to unseen winters. Both gains are against the same untrained start, so winters being easier or harder cancels out |
| `work_s`, `round_s_median`, `build_s` | computing time of the training rounds and the held-out scoring (from the rounds' own records, so a resumed test counts every part), the median later round, and the time the folds' case sets took to build |

`snowagent lab winter-test` prints the table of winters and the same sentences as the app page. Options:
`--folds N` (5), `--every-winter` (one fold a winter), `--rounds` (6), `--population` (10), `--survivors` (2),
`--initial` (repeat; default `column`), `--include-locked` (put the locked winters back in, for a test where a
clean final test does not matter), `--season` (repeat: only these held-out winters), `--plots`, `--case-types`,
`--estimate-only`, `--check-id` (a name; the same name resumes, finished folds are kept). There is no `--trim`
yet: it tests full runs, not trimmed ones (ADR-123).

Times, column model (no SNOWPACK while training; SNOWPACK runs once per held-out pit for the bar). Measured on
the owner's Mac with 8 workers, 2026-11-19, the default (5 folds, 6 rounds of 10 agents; each fold trains on about
700 of the 877 cases of 25 winters): 49 min of computing, a training round about a minute, and 4 min to build the
five case sets (0.35 GB each): 53 min. From that, 5 to 7 hours for a full training's budget in each fold (30
rounds of 20 agents, 3 survivors) and 4 to 5 hours for one fold a winter. An agent takes 0.06 to 0.10 s a case
there; on 4 cloud cores 0.12 to 0.17 s, and that shared machine has measured 0.36 s at a slow moment.
`--estimate-only` (the page's **1. Estimate the time**) gives the figure for the computer it runs on, before
anything starts.

How an estimate gets its speeds (ADR-126). Training rounds and the held-out scoring time what they run (an agent
of each family on a case, a SNOWPACK run, loading a case) and keep running means in
`<data root>/outputs/cache/timings.json`. A family the computer has never run has no mean, only a built-in number
from another machine. Before an estimate of a minute or more relies on one, the family is timed on the spot
(`training/calibrate.py`): its starting settings and seven seeded mutants on a sample of the run's training cases
(24, or 6 a worker; never the locked winters), with the run's workers, into a throwaway cache that is removed.
A family timed only by runs much smaller than the one to estimate (fewer agent runs than one round of it, a
trial on one plot for instance) is timed again the same way, and the new mean counts as one round of the run.
The estimate's `timed_here` says what was timed and `guessed` what still rests on a built-in number (a family
that needs SNOWPACK or the analogue library, or `engine` on a computer that has never run SNOWPACK); the log and
the page say the same in words. Nothing is timed while a training run is going in another process (a run of its
own, or the training inside a winter test or a promotion check): its workers have the cores, so the timing would
measure a busy computer. The estimate's `busy` then names that run, and the estimate made after it has ended
times. An engine profile counts as cached only if the cache's index shows one made by this code and this
SNOWPACK build: after an update of the prediction code every profile is run again, and the estimate counts
those runs.

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

## Scoring version 2 (ADR-074, 2026-10-05)

The results above are scoring version 1. The owner dropped the depth score's coverage bonus (ADR-074): `snow_depth`
is now exp(-|p50 - observed| / 0.15 m) only, and the p10-p90 range is judged by the uncertainty component alone. The
two versions do not compare; every run records its `scoring_version`.

### Milestones 3-5 re-scored, no agent re-run

The M3 competition from its stored predictions (`snowagent lab rescore --run-id m3-default-agents`), the M4 winner
from its predictions in the M4 cache (they reproduce the stored M4 rows exactly), and every agent of the M4 and M5
training runs from their stored score rows (the new depth score is a function of the stored `depth_error_m`; the
other components are unchanged):

| agent | composite v1 -> v2 | snow depth v1 -> v2 | layer structure | critical layers | uncertainty | robustness v1 -> v2 | depth MAE | p10-p90 coverage |
|---|---|---|---|---|---|---|---|---|
| SNOWPACK incumbent | 0.5089 -> 0.5022 | 0.6380 -> 0.5967 | 0.5129 | 0.2661 | 0.6265 | 0.6700 -> 0.6855 | 0.102 m | 0.76 |
| M4 winner `r10-m01-snowpack` | 0.5198 -> 0.5020 | 0.6733 -> 0.5967 | 0.5126 | 0.3014 | 0.5750 | 0.6982 -> 0.6729 | 0.102 m | 0.90 |
| M5 winner `r05-m03-snowpack` | 0.5399 -> 0.5216 | 0.7034 -> 0.6232 | 0.5334 | 0.3389 | 0.5620 | 0.7020 -> 0.6795 | 0.092 m | 0.95 |
| M5 `r02-m03-snowpack` (reduced-check fold winner) | 0.5260 -> 0.5105 | 0.6931 -> 0.6221 | 0.5186 | 0.3075 | 0.5790 | 0.6805 -> 0.6671 | 0.096 m | 0.91 |

Reading:
- The M4 winner's whole gain was the bonus. It draws the incumbent's engine profile with wider depth ranges: under
  version 2 its depth score equals the incumbent's (same p50), its better critical-layer confidence (+0.035) is
  offset by the uncertainty it gives up (-0.052) and lower robustness, and it ends level with the incumbent (0.5020
  vs 0.5022). In the M4 run the version-2 best of the last round would have been `r09-x02-snowpack` (0.5031); the
  version-1 winner ranks 6th of 10 there.
- The M5 winner keeps a clear lead (+0.019) because its gain is in the simulated snowpack (depth MAE 0.102 -> 0.092
  m, layer structure, critical layers), not in the ranges. Under version 2 the M5 run's per-round best is the same
  agent in every round from round 2 (0.5105, 0.5148, 0.5148, 0.5216, 0.5216).
- The milestone-4 and reduced milestone-5 promotion checks were judged under version 1 and are not re-judged here.

## Promotion check of the 2026-11-23 SNOWPACK training winner (reduced, 3 winters)

Run on the owner's Mac through the Mac queue (ADR-148), entry `training-20261123T000134Z-3c2aa9-r11-k1-loso-reduced-3s`:
`snowagent lab check-loso --genome training-20261123T000134Z-3c2aa9/11/1 --engine auto --season 2020-2021
--season 2021-2022 --season 2022-2023 --rounds 5 --population 6 --source .` (6 workers, 3 h 47 min of computing,
code `e1c17e44`). The checked agent is `snowpack-era5-fix` (`snowpack-1fdd0e8215`): the training run's winner was
its starting standard SNOWPACK, which since ADR-134 is also the check's incumbent.

| Held-out season | Cases | Fold winner | Winner | SNOWPACK | Difference | Result |
|---|---|---|---|---|---|---|
| 2020-2021 | 18 | snowpack-era5-fix | 0.6766 | 0.6766 | 0.0000 | tie |
| 2021-2022 | 26 | snowpack-era5-fix | 0.5978 | 0.5978 | 0.0000 | tie |
| 2022-2023 | 20 | snowpack-era5-fix | 0.6469 | 0.6469 | 0.0000 | tie |

Pooled over 64 held-out cases: 0.6328 vs 0.6328; 0 wins, 0 losses, 3 ties. **Result: FAIL** under the ADR-068 rule
(the pooled score does not beat the incumbent's), for the reduced procedure (5 rounds of 6, not 20 of 10).

Reading:
- In every fold, five rounds of SNOWPACK physics children found nothing that beat standard SNOWPACK on the fold's
  training winters, so each fold's winner is the incumbent itself and the held-out scores are identical by
  construction. The check confirms that this training run did not find an agent better than `snowpack-era5-fix`;
  it says nothing about held-out skill beyond that.
- Some children did better on the Parker Ridge check without winning on the training winters (2022-2023 fold,
  round 5: `r05-m02-snowpack` 0.5966 vs 0.5825).
- When a training run's winner is its own standard SNOWPACK, its promotion check can only tie; such a check is
  not worth Mac time and is not queued again.

Research and decision support only, not an avalanche forecast.

## Promotion check of Adriana Kensington (2026-11-19 SNOWPACK training winner, reduced, 3 winters)

Run on the owner's Mac through the Mac queue (ADR-148), entry `training-20261119T191922Z-2a9be2-r17-k1-loso-reduced-3s-p7`:
`snowagent lab check-loso --genome training-20261119T191922Z-2a9be2/17/1 --engine auto --season 2020-2021
--season 2021-2022 --season 2022-2023 --rounds 5 --population 7 --source .` (6 workers, 2 h 46 min of computing,
code `54947f7f`). Population 7, not 6: the 2026-11-19 run used family slots, which need at least 5 children a
round (population minus 2 survivors); the first try with population 6 stopped at once on that settings error.
The run predates ADR-134, so its folds start from, and are measured against, plain SNOWPACK (`snowpack-default`).

| Held-out season | Cases | Fold winner | Winner | SNOWPACK | Difference | Result |
|---|---|---|---|---|---|---|
| 2020-2021 | 29 | snowpack-default | 0.5566 | 0.5566 | 0.0000 | tie |
| 2021-2022 | 37 | snowpack-default | 0.5163 | 0.5163 | 0.0000 | tie |
| 2022-2023 | 25 | snowpack-default | 0.5946 | 0.5946 | 0.0000 | tie |

Pooled over 91 held-out cases: 0.5496 vs 0.5496; 0 wins, 0 losses, 3 ties. **Result: FAIL** under the ADR-068 rule,
for the reduced procedure (5 rounds of 7, not 20 of 10).

Reading:
- In every fold, five rounds of SNOWPACK physics children found nothing that beat plain SNOWPACK on the fold's
  training winters (fold composites 0.578, 0.580, 0.577), so each fold's winner is the incumbent and the held-out
  scores are identical by construction. Adriana Kensington came out of 17 rounds; five rounds do not repeat that
  search, so this reduced check cannot test her and says nothing about her held-out skill.
- As in the era5-fix check, a child did better on the Parker Ridge check (227 cases) without winning on the
  training winters: `r02-f04-snowpack` 0.5851 vs 0.5666 in the 2020-2021 and 2021-2022 folds.
- Both reduced checks of SNOWPACK training winners have now tied on every fold. A meaningful check of a SNOWPACK
  winner needs more rounds per fold (the full procedure), which is several times the Mac time.

Research and decision support only, not an avalanche forecast.

## Winter-by-winter tests of two training starts: facets-by-hours vs plain process (Quick, seed 0)

Run on the owner's Mac through the Mac queue (ADR-148), 6 workers, the Quick settings of ADR-125 (5 groups,
6 rounds of 10 agents, 2 survivors, seed 0; 877 pits of 25 winters; the locked winters 2023-2024 to 2025-2026
kept out of every part). Entries `winter-test-process-facet-hours-quick-s0` (`snowagent lab winter-test
--initial process-facet-hours --seed 0`, code `c3bfb88a`) and `winter-test-process-quick-s0` (`--initial process
--seed 0`, code `98eb4fa8`). `process-facet-hours` is the untrained base of the site's main model, Carver
Daniels (ADR-143).

| | from `process-facet-hours` | from `process` |
|---|---|---|
| Pooled, all held-out pits: trained / untrained / SNOWPACK | 0.6318 / 0.5990 / 0.6161 | 0.6136 / 0.5861 / 0.6161 |
| Against SNOWPACK, mean per winter (90% range) | +0.0122 (-0.0002 to +0.0247) | -0.0071 (-0.0192 to +0.0042) |
| Winters better / worse than SNOWPACK | 13 / 12 | 11 / 14 |
| Training gain, mean per winter (90% range) | +0.0334 (+0.0249 to +0.0423) | +0.0257 (+0.0174 to +0.0340) |
| Winters training helped / hurt | 23 / 2 | 18 / 7 |
| Memorising gap | 0.005 | 0.006 |
| Computing time (median round) | 60 min (1.9 min) | 94 min (3.0 min) |

Reading:
- Starting from facets-by-hours, training on other winters beat standard SNOWPACK on unseen winters by about
  1.2 points a winter; the 90% range just touches zero, so on one seed it is a likely edge, not a sure one.
  Starting from plain process, trained agents were level with or slightly below SNOWPACK (-0.7 a winter).
- Training itself carries to unseen winters from both starts (+3.3 and +2.6 points a winter), with no sign of
  memorising (half a point between the training winters and the unseen ones). Part of the difference between
  the two starts is in the start: untrained facets-by-hours already scores 1.3 points above untrained process.
- Both are seed 0 only. Seeds 1 and 2 of the facet-hours test were approved by the owner (results below);
  nothing here changes the site, the scores or `config/site_main_model.json`.
- The weakest winter for both is 2014-2015 (15 pits; -8.4 and -10.7 points against SNOWPACK).

Research and decision support only, not an avalanche forecast.

## Facets-by-hours winter-by-winter test: seeds 1 and 2 (Quick)

Same settings as the seed-0 test above (ADR-125 Quick, 5 groups, 6 rounds of 10, 2 survivors, 877 pits of 25
winters, locked winters 2023-2024 to 2025-2026 kept out), run on the owner's Mac through the Mac queue, 6 workers.
Entries `winter-test-process-facet-hours-quick-s1` (`--initial process-facet-hours --seed 1`, code `98eb4fa8`) and
`winter-test-process-facet-hours-quick-s2` (`--seed 2`, code `f2626e66`), both approved by the owner.

| from `process-facet-hours` | seed 0 | seed 1 | seed 2 |
|---|---|---|---|
| Pooled, all held-out pits: trained / SNOWPACK (untrained 0.5990) | 0.6318 / 0.6161 | 0.6380 / 0.6161 | 0.6391 / 0.6161 |
| Against SNOWPACK, mean per winter (90% range) | +0.0122 (-0.0002 to +0.0247) | +0.0179 (+0.0077 to +0.0278) | +0.0191 (+0.0074 to +0.0303) |
| Winters better / worse than SNOWPACK | 13 / 12 | 16 / 9 | 18 / 7 |
| Training gain, mean per winter (90% range) | +0.0334 (+0.0249 to +0.0423) | +0.0392 (+0.0329 to +0.0458) | +0.0404 (+0.0334 to +0.0476) |
| Winters training helped / hurt | 23 / 2 | 25 / 0 | 25 / 0 |
| Memorising gap | 0.005 | 0.000 | -0.000 |
| Promotion rule of the check (pooled beats SNOWPACK, loses at most 12 of 25) | pass | pass | pass |

Reading:
- All three seeds beat standard SNOWPACK on unseen winters: on average +1.6 points a winter (+1.2, +1.8, +1.9),
  pooled about 0.636 against 0.616. On seeds 1 and 2 the 90% range is clear of zero, so the seed-0 "likely edge"
  holds up across seeds.
- Training carries to new winters on every seed (+3.3 to +4.0 points a winter; seeds 1 and 2 helped in all 25
  winters), with no sign of memorising.
- 2014-2015 (15 pits) stays the weakest winter on every seed (-8.4, -3.9, -7.7 against SNOWPACK); 2018-2019 and
  2008-2009 were level on seed 0 but below SNOWPACK on seeds 1 and 2.
- Computing time: seed 2 took 1 h 24 min (median round 2.7 min). Seed 1 took 9 h 36 min of wall time with the same
  ~3 min rounds; the slow stretch was the last group's final rounds overnight, most likely the Mac sleeping.
- These are tests of the training method from this start, not of a single agent; they change nothing on the site,
  in the scores or in `config/site_main_model.json`. Next, approved by the owner: the thorough one-winter-at-a-time
  test (`--every-winter`, seed 0) and a 10-round, 10-agent training run from `process-facet-hours`.

Research and decision support only, not an avalanche forecast.

## Facets-by-hours: thorough winter-by-winter test, a 10x10 training run and its winner's stability check

Run on the owner's Mac through the Mac queue (ADR-148), 6 workers, all approved by the owner. The locked winters
2023-2024 to 2025-2026 were kept out of the winter test and the stability check.

**Thorough winter-by-winter test** (`winter-test-process-facet-hours-every-s0-b`: `snowagent lab winter-test
--initial process-facet-hours --every-winter --seed 0`, code `0a1ce04e`; each of 25 winters held out on its own,
6 rounds of 10, 877 pits; a re-run of the first attempt that the full disk stopped on 2026-11-24).

| from `process-facet-hours`, every winter held out | |
|---|---|
| Pooled, all held-out pits: trained / untrained / SNOWPACK | 0.6277 / 0.5990 / 0.6161 |
| Against SNOWPACK, mean per winter (90% range) | +0.6 points (-0.5 to +1.8) |
| Winters better / worse / level (check rule, 1e-4) | 12 / 12 / 1 (to 0.1 point: 11 / 12 / 2) |
| Training gain, mean per winter (90% range) | +2.7 points (+2.0 to +3.6); helped in 21, hurt in 3 |
| Memorising gap | 0.009 |
| Promotion rule of the check (pooled beats SNOWPACK, loses at most 12 of 25) | pass, at the limit (12 losses) |
| Computing time | 9 h 33 min (median round 3.6 min) |

- With one winter held out at a time, training from facets-by-hours still beats standard SNOWPACK pooled
  (0.628 vs 0.616), but the per-winter edge shrinks from about +1.6 (three Quick seeds) to +0.6, and its 90% range
  includes zero. It passes only at the limit, losing 12 of 25 winters.
- Training itself carries to unseen winters (+2.7 a winter), with about 0.9 points memorised.
- Best winters 2016-2017 (+8.9) and 2019-2020 (+7.6); worst 2014-2015 (15 pits, -6.2), 1999-2000 (-4.1) and
  2013-2014 (-3.6).

**10 rounds of 10 from `process-facet-hours`** (`train-facet-hours-r10-p10-2026-11-25-b`: `snowagent lab train
--initial process-facet-hours --rounds 10 --population 10 --seed 0`, code `54947f7f`, 28 min of rounds; a re-run of
the run the full disk stopped). Winner `r10-x01-process` (`train-facet-hours-r10-p10-2026-11-25-b/10/1`), training
composite 0.6418 (round 1 best 0.6331; gains flattened after round 6, 0.6406). Locked test winters (68 cases):
0.6278. Main changes from the default genes: facets form faster (`col_facet_hours` 72 to 44), depth hoar slower
(400 to 572), rounding faster (720 to 610), new snow and decomposing grains faster (24 to 18, 120 to 70).

**Stability check of the winner beside Carver Daniels** (`stability-train-facet-hours-r10-p10-2026-11-25-b`:
`snowagent lab stability-check --agent train-facet-hours-r10-p10-2026-11-25-b/10/1 --agent main`, code `54947f7f`;
590 pits with a test that broke at a known depth, 1076 broken layers, 350 of them easy and sudden; 2 h 19 min).

| | 10x10 winner | Carver Daniels | Chance |
|---|---|---|---|
| SK38 weakest-spot hits / depth alone (layering skill) | 56% / 53% (+2.8 pts) | 56% / 53% (+3.0 pts) | 33% |
| Four-sign count hits | 38% | 39% | 33% |
| Easy and sudden: SK38 / depth alone | 52% / 49% | 51% / 49% | 25% |
| Easy and sudden: four-sign count | 28% (no better than chance) | 29% | 25% |
| SK38 by plot: BOW / GOAT / SIMP | 49% / 62% / 60% | 49% / 62% / 59% | 35% / 33% / 29% |

- The 10x10 winner and the site's main model pick weak layers equally well; nothing here favours either.
- Carver's numbers differ slightly from the 2026-11-23 smoke check (+3.6 pts layering skill) because of code
  changes in between (`54947f7f` here).
- The 10x10 winner's promotion check (`check-loso`) is the next step (result below: PASS). Nothing here changes the site, the scores or
  `config/site_main_model.json`.

Research and decision support only, not an avalanche forecast.

## Promotion check of the 10x10 facet-hours winner (reduced, 3 winters): PASS

Run on the owner's Mac through the Mac queue (ADR-148), entry
`train-facet-hours-r10-p10-2026-11-25-b-r10-k1-loso-reduced-3s`: `snowagent lab check-loso --genome
train-facet-hours-r10-p10-2026-11-25-b/10/1 --engine auto --season 2020-2021 --season 2021-2022 --season 2022-2023
--rounds 5 --population 6 --source .` (6 workers, 44 min of computing, code `b15016c6`). The checked agent is
`r10-x01-process` (`process-05eb26e86f`). The run has no standard model (ADR-134 applies to runs that start from a
SNOWPACK standard), so the bar is plain SNOWPACK (`snowpack-default`): the same 91 held-out cases and SNOWPACK scores
as Adriana Kensington's check above.

| Held-out season | Cases | Fold winner | Evolved | SNOWPACK | Difference | Result |
|---|---|---|---|---|---|---|
| 2020-2021 | 29 | r03-x01-process | 0.6400 | 0.5566 | +0.0834 | win |
| 2021-2022 | 37 | r05-m02-process | 0.6243 | 0.5163 | +0.1080 | win |
| 2022-2023 | 25 | r04-m02-process | 0.6028 | 0.5946 | +0.0082 | win |

Pooled over 91 held-out cases: 0.6189 vs 0.5496 (+0.0693); 3 wins, 0 losses, 0 ties. **Result: PASS** under the
ADR-068 rule, for the reduced procedure (5 rounds of 6, not 10 of 10).

| Pooled component | Evolved | SNOWPACK |
|---|---|---|
| Snow depth | 0.615 | 0.597 |
| Layer structure | 0.574 | 0.510 |
| Critical layers | 0.643 | 0.489 |
| Uncertainty | 0.656 | 0.646 |
| Robustness | 0.653 | 0.678 |
| Depth MAE | 0.085 m | 0.097 m |

Reading:
- The first promotion check in the lab that an evolved agent's training procedure passes: training from
  `process-facet-hours`, with each test winter held out, beats plain SNOWPACK in all three winters, most of it on
  critical layers and layer structure, with a smaller depth error. Robustness is the one component below SNOWPACK.
- The margin is large in 2020-2021 and 2021-2022 (+8 and +11 points) and small in 2022-2023 (+0.8 points).
- What this tests: the reduced procedure from the run's start, not `r10-x01-process` itself. Each fold's winner is
  a 5-round agent (fold training composites 0.632 to 0.637, against 0.642 for the 10x10 winner), and three winters
  are a small sample. A PASS is necessary, not sufficient (principle 1's physical checks still apply).
- The bar is plain SNOWPACK, not the best standard (`snowpack-era5-fix`) or the site's main model, Carver Daniels.
  The stability check (above) has the winner level with Carver.
- Nothing here changes the site, the scores or `config/site_main_model.json`.

Research and decision support only, not an avalanche forecast.

## Plain-process winter-by-winter tests: seeds 1 and 2 (Quick)

Same Quick settings as the seed-0 test (ADR-125: 5 groups, 6 rounds of 10, 2 survivors, 877 pits of 25 winters,
locked winters 2023-2024 to 2025-2026 kept out), on the owner's Mac through the Mac queue, 6 workers, both approved
by the owner. Entries `winter-test-process-quick-s1` (`snowagent lab winter-test --initial process --seed 1`, code
`54947f7f`) and `winter-test-process-quick-s2` (`--seed 2`, code `821e765d`).

| from `process` | seed 0 | seed 1 | seed 2 |
|---|---|---|---|
| Pooled, all held-out pits: trained / SNOWPACK (untrained 0.5861) | 0.6136 / 0.6161 | 0.6144 / 0.6161 | 0.6212 / 0.6161 |
| Against SNOWPACK, mean per winter (90% range) | -0.0071 (-0.0192 to +0.0042) | -0.0044 (-0.0144 to +0.0052) | +0.0023 (-0.0076 to +0.0123) |
| Winters better / worse than SNOWPACK | 11 / 14 | 11 / 14 | 12 / 13 |
| Training gain, mean per winter (90% range) | +0.0257 (+0.0174 to +0.0340) | +0.0284 (+0.0218 to +0.0350) | +0.0352 (+0.0268 to +0.0435) |
| Winters training helped / hurt | 18 / 7 | 23 / 2 | 22 / 3 |
| Promotion rule of the check (pooled beats SNOWPACK, loses at most 12 of 25) | fail | fail | fail (13 losses) |

Reading:
- From plain `process`, trained agents are level with standard SNOWPACK on unseen winters on every seed (-0.7,
  -0.4, +0.2 a winter; every 90% range includes zero), and all three fail the check's rule. From
  `process-facet-hours` the three Quick seeds gave +1.2, +1.8 and +1.9. Facets-by-hours is the better training start.
- Training carries to new winters from plain `process` too (+2.6 to +3.5 a winter), with no sign of memorising.
- Seed 2's pooled score beats SNOWPACK's, but it is below SNOWPACK in 13 of 25 winters, one more than the rule
  allows. It gains on layer structure (0.562 vs 0.547) and critical layers (0.675 vs 0.661) and loses on snow depth
  (0.613 vs 0.630; depth MAE 0.094 vs 0.087 m).
- Seed 2 took 8 h 51 min of computing time although its rounds took about 3 min each (seed 1: 1 h 31 min); the Mac
  was most likely asleep for part of it.

Research and decision support only, not an avalanche forecast.

## 10x10 facet-hours runs, seeds 1 and 2, and their reduced promotion checks: both PASS

Two repeats of the 10x10 run from `process-facet-hours` (ADR-161: adopt only what several seeds agree on), on the
owner's Mac through the Mac queue, 6 workers, code `b15016c6`: `snowagent lab train --initial process-facet-hours
--rounds 10 --population 10 --seed 1` (entry `train-facet-hours-r10-p10-s1-2026-11-27`, 33 min) and `--seed 2`
(`train-facet-hours-r10-p10-s2-2026-11-27`, 39 min of computing).

| 10x10 from `process-facet-hours` | seed 0 | seed 1 | seed 2 |
|---|---|---|---|
| Winner | `r10-x01-process` | `r10-x01-process` | `r08-x01-process` |
| Training composite | 0.6418 | 0.6415 | 0.6412 |
| Locked winters (68 cases) | 0.6278 | 0.6176 | 0.6144 |
| Reduced promotion check (3 winters, 5 rounds of 6): pooled evolved / SNOWPACK, 91 cases | 0.6189 / 0.5496 | 0.6299 / 0.5496 | 0.6245 / 0.5496 |
| Check wins / losses / ties; result | 3 / 0 / 0; PASS | 3 / 0 / 0; PASS | 3 / 0 / 0; PASS |

Checks: entries `train-facet-hours-r10-p10-s1-2026-11-27-r10-k1-loso-reduced-3s` and `...-s2-...`: `snowagent lab
check-loso --genome <run>/10/1 --engine auto --season 2020-2021 --season 2021-2022 --season 2022-2023 --rounds 5
--population 6 --source .` (38 and 43 min of computing). The check re-runs training with the run's own seed, so
these are two independent repeats of seed 0's PASS. The bar is plain SNOWPACK (`snowpack-default`), the same 91
held-out cases and scores as seed 0's check.

| Held-out season (cases) | SNOWPACK | seed 0 | seed 1 | seed 2 |
|---|---|---|---|---|
| 2020-2021 (29) | 0.5566 | 0.6400 | 0.6441 | 0.6523 |
| 2021-2022 (37) | 0.5163 | 0.6243 | 0.6157 | 0.6067 |
| 2022-2023 (25) | 0.5946 | 0.6028 | 0.6391 | 0.6178 |

| Pooled component | SNOWPACK | seed 0 | seed 1 | seed 2 |
|---|---|---|---|---|
| Snow depth | 0.597 | 0.615 | 0.613 | 0.624 |
| Layer structure | 0.510 | 0.574 | 0.572 | 0.587 |
| Critical layers | 0.489 | 0.643 | 0.657 | 0.633 |
| Uncertainty | 0.646 | 0.656 | 0.663 | 0.659 |
| Robustness | 0.678 | 0.653 | 0.718 | 0.679 |
| Depth MAE | 0.097 m | 0.085 m | 0.085 m | 0.082 m |

Reading:
- The PASS repeats on all three seeds: 9 of 9 held-out winters won, pooled margins +6.9, +8.0 and +7.5 points.
  2022-2023 stays the closest winter (+0.8, +4.4, +2.3 points). Most of the gain is again on critical layers and
  layer structure, with a smaller depth error.
- Seed 0's low robustness (below SNOWPACK) does not repeat: seed 1 is above SNOWPACK and seed 2 level.
- The three runs reach the same training score (within 0.0006). On the locked winters seeds 1 and 2 are 1.0 to 1.3
  points below seed 0, so seed 0's locked score was partly luck.
- What the seeds agree on, beyond the `process-facet-hours` start: all three winners turn off the surface and wind
  process switches. Faceting hours (44 / 24 / 24), depth hoar hours (572 / 404 / 400) and the plot snowfall factors
  disagree, so they are not adopted from these runs.
- Same caveats as seed 0: the reduced procedure (5 rounds of 6, not 10 of 10), three winters, and plain SNOWPACK as
  the bar rather than `snowpack-era5-fix` or Carver Daniels. Next, approved by the owner: the full-strength check of
  the seed-0 winner (10 rounds of 10 per fold), then the stability check of the seed-1 and seed-2 winners.
- Nothing here changes the site, the scores or `config/site_main_model.json`.

Research and decision support only, not an avalanche forecast.

## Stability check of the 10x10 facet-hours seed-1 and seed-2 winners: level with seed 0 and Carver

Run on the owner's Mac through the Mac queue (ADR-148), entry `stability-train-facet-hours-r10-p10-s1-s2-2026-11-27`:
`snowagent lab stability-check --agent train-facet-hours-r10-p10-s1-2026-11-27/10/1 --agent
train-facet-hours-r10-p10-s2-2026-11-27/10/1` (6 workers, code `ce1e6e10`, 1 min 16 s of computing, no failed runs;
the same 590 pits with a test that broke at a known depth, 1076 broken layers, 350 of them easy and sudden, locked
winters left out). The lab names the seed-1 winner Kima Baccalieri and the seed-2 winner Avon Funkhouser. Seed 0 and
Carver Daniels are from the seed-0 check above (code `54947f7f`); Carver was not re-run.

| | seed 0 (`r10-x01`) | seed 1 (`r10-x01`, Kima) | seed 2 (`r08-x01`, Avon) | Carver Daniels | Chance |
|---|---|---|---|---|---|
| SK38 weakest-spot hits / depth alone (layering skill) | 56% / 53% (+2.8 pts) | 56% / 53% (+3.7 pts) | 57% / 53% (+4.0 pts) | 56% / 53% (+3.0 pts) | 33% |
| Four-sign count hits | 38% | 39% | 40% | 39% | 33% |
| Easy and sudden: SK38 / depth alone | 52% / 49% | 52% / 49% | 52% / 49% | 51% / 49% | 25% |
| Easy and sudden: four-sign count | 28% (no better than chance) | 29% (no better than chance) | 30% | 29% | 25% |
| SK38 by plot: BOW / GOAT / SIMP | 49% / 62% / 60% | 49% / 63% / 60% | 50% / 63% / 60% | 49% / 62% / 59% | 35% / 33% / 29% |

Reading:
- All three 10x10 winners pick weak layers about as well as each other and as Carver Daniels: SK38 hits within one
  point, layering skill +2.8 to +4.0 points over depth alone. The spread between seeds is about the size of the
  spread between code versions, so nothing here separates them.
- The 9 of 9 promotion-check wins come from depth, structure and critical-layer scores, not from better
  weakest-spot picking; on this check the winners stay level with the site's main model.
- The check took about a minute, against 2 h 19 min for the seed-0 check (which also ran Carver); the earlier
  estimate of 2 to 3 h was too high.
- Nothing here changes the site, the scores or `config/site_main_model.json`.

Research and decision support only, not an avalanche forecast.

## Known-answer test (ADR-157)

`snowagent lab known-answer --out data/lab_ka --truth config/site_main_model.json --workers 4` builds a copy of the
cases whose withheld pits are a known agent's predictions plus observer noise (`--noise 0` for none, `--wobble
snow_mult_bow=0.1` to vary true genes per case). Train on it with `--data-root data/lab_ka` as usual, then
`snowagent lab known-answer-read <run> --start <start genome> --data-root data/lab_ka` prints, per gene, the start,
true and found value and how much of the way the run went. First result (2026-11-24, Carver as the truth): training
closes nearly all of the score gap, the locked winters come out about 1 point lower than the training winters from
overfitting alone, and only about a third of the settings that matter are recovered; use a new seed for every run
meant to confirm another (the random stream depends only on seed and round).
