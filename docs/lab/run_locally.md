# Running the complete lab training on your Mac, from a fresh clone

Research and decision support only, not an avalanche forecast. This is the whole path from `git clone` to a trained
agent and its promotion check, in order, for macOS on Apple silicon. Every command runs from the repository root in
Terminal (zsh). Background: `local_setup.md` (each step in more detail), `training.md` (what training does),
`agents_and_scoring.md` (agents and scoring version 2, ADR-074). Prefer buttons to commands? After section 0 and steps 1 to 3,
`web_interface.md` runs steps 4 to 9 from your web browser (`snowagent lab app`).

The times below were measured on 2026-10-05 in a fresh clone on a Linux machine with 4 cores (the reference
column) and scaled for a typical Apple-silicon laptop (M1-M3, 8 or more cores, home internet): the engine steps
scale with the number of performance cores you give `--workers`, the download steps with your connection. macOS
itself was not available for the measurement; the macOS-specific parts (the SNOWPACK build layout, the `spawn`
process start, default paths) were checked as far as Linux allows (see "What was checked"). The steps the older
seasons change (ADR-076: the pits of 1997-98 to 2014-15 are training cases, on ERA5 weather) were measured on
2026-10-06 on the same machine; the full-set figures are scaled from those runs.

## 0. One-time tools (about 10 minutes, mostly downloads)

A new Mac has neither Homebrew nor the `brew` command, so install it first. If `brew --version` already prints a
version, skip to the last line.

```bash
xcode-select --install          # Apple's compiler, make, git ("already installed" is fine)

# Homebrew (asks for your Mac password once; press Return when it asks to continue)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Put brew on PATH, now and in every new terminal (Apple silicon installs to /opt/homebrew)
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
brew --version                  # should print "Homebrew 4.x" or later

brew install python@3.12 cmake
```

On an Intel Mac Homebrew installs to `/usr/local` instead, so use `/usr/local/bin/brew shellenv` in the two `eval`
lines. If you see `zsh: command not found: brew` later, open a new terminal or rerun the `eval` line.

## The commands

```bash
# 1. Clone (about 1-3 min, 0.6 GB)
git clone https://github.com/benfirth-parks/banff-snowpack.git
cd banff-snowpack
git fetch origin                             # brings in new branches if you cloned earlier
git checkout claude/agent-lab-web             # until the lab branches are merged (it carries all of them)

# 2. Python environment with the lab extra (about 2-5 min, 1.3 GB)
bash scripts/setup_env.sh                    # first line should end in 3.12.x (Apple's python3 3.9 is skipped)
source .venv/bin/activate                    # in every new terminal

# 3. SNOWPACK engine (about 5-8 min, 0.7 GB; installs to ~/.local/snowpack, no sudo, found automatically)
bash scripts/build_snowpack.sh
snowagent update bootstrap | tail -3         # the last line names the engine it found

# 4. Inputs a clone does not carry (from the project's own sources; see "Inputs" below)
snowagent lab prepare --workers 4            # about 1 min, then ERA5: about 6 h the first time (rerun until no month says FAILED)

# 5. Lab tables and benchmark cases (about 10 min, 0.4 GB)
snowagent lab init
snowagent lab import                         # about 1-2 min
snowagent lab build-cases                    # about 8 min, about 945 cases (340 from 2015-16 on, 605 older)
snowagent lab cases                          # counts per set, split, plot, type, forecast and weather source

# 6. Optional check that everything works (about 15 min): a small training run and a two-season promotion check
snowagent lab train --rounds 2 --population 4 --plots SIMP --case-types next_pit --workers 4 --run-id smoke
snowagent lab check-loso --genome smoke/2/1 --rounds 1 --season 2022-2023 --season 2023-2024 --workers 4 \
  --check-id smoke-loso                      # a FAIL on 3 held-out cases is expected: this only tests the pipeline

# 7. The training run (see "Recommended options"; resumable)
caffeinate -i snowagent lab train --rounds 6 --population 8 --screen-cases 30 --seed 0 --workers 8 \
  --run-id overnight-r6-p8 2>&1 | tee train.log

# 8. Look at it
snowagent lab app                            # http://localhost:8501; the browser guide is web_interface.md
snowagent lab lineage <winner label or agent id>

# 9. The full promotion check of the winner (many hours; resumable; see below)
caffeinate -i snowagent lab check-loso --genome overnight-r6-p8/6/1 --workers 8 --check-id overnight-r6-p8-loso \
  2>&1 | tee loso.log
```

`caffeinate -i` keeps the Mac awake while the command runs (closing the lid still sleeps a laptop; keep it open
and on power). If anything stops a run (sleep, Ctrl-C, a reboot), the same command with `--resume` continues the
training (`snowagent lab train --resume --run-id overnight-r6-p8`) and the same `check-loso` command with the same
`--check-id` continues the check; finished work comes from the cache.

## Expected time and disk per step

| step | reference (Linux, 4 cores, measured) | typical Apple-silicon laptop | disk |
|---|---|---|---|
| 1 clone | 14 s (local) | 1-3 min | 0.6 GB |
| 2 `setup_env.sh` | 1 min 49 s | 2-5 min | 1.3 GB (`.venv`) |
| 3 `build_snowpack.sh` | 3 min 21 s (compile) + source download | 5-8 min | 0.6 GB source and build, 0.1 GB install |
| 4 `lab prepare`: bootstrap and profiles | 50 s | about 1 min | 0.3 GB (`data/raw`, `data/interim`) |
| 4 `lab prepare`: ERA5, 241 months (112 of the station seasons, 129 of the older seasons: September to each season's last pit) | 6 min 20 s for one month (network-bound, 1 min of CPU); 20 older months in 30 min with 4 processes (4.5-10 min per month each); all months about 6 h with 4 processes | about 6 h with `--workers 4` if your connection keeps up | 0.2 GB |
| 5 `lab import` | 58 s (station seasons); 54 s with two older seasons | about 1-2 min | 15-20 MB |
| 5 `lab build-cases` | 4 min 43 s for 340 cases; 3 min 42 s for 445 (two older seasons), so about 8 min for the full 945 | 6-10 min | 0.35 GB |
| 6 smoke training (30 cases, 2 rounds, 60 engine runs) | 4 min 7 s | 3-5 min | small |
| 6 smoke `check-loso --rounds 1`, 2 seasons (2 case-set builds of the full set) | 6 min 23 s with 340 cases; about 12 min expected with 945 | 8-15 min | 0.7 GB |
| 7 training, round 1 (7 agents x 945 cases, about 1,900 engine runs) | 18 min with 340 cases (`m6-warm-r1`), about 45-50 min expected (ERA5-only engine runs were faster: 5.9 s against 8.5 s) | 25-50 min with `--workers 8` | |
| 7 training, `--rounds 6 --population 8 --screen-cases 30` | 2.5 h after round 1 with 340 cases (milestone 5), about 7 h expected with 945 | 4-8 h in all | about 0.8 GB of cache |
| 9 full promotion check of that run | about 21 h with 340 cases and 11 folds; with 945 cases and 28 folds about 150 h expected (4 workers) | 3-6 days with `--workers 8`, spread over nights with `--season` | about 10 GB of case sets, 6-8 GB of cache |

Total disk: about 5 GB for steps 1-7, about 20 GB with the full promotion check. Everything the lab writes is under
`data/lab/` (derived: deleting it only costs time); the training cache is `data/lab/outputs/cache/`.

Use as many `--workers` as your Mac has performance cores (`sysctl -n hw.perflevel0.physicalcpu`; efficiency cores
add little). Every command prints its own time estimate before it starts; it uses the timings measured on your
machine once a first run has finished, and it is an upper bound (it assumes every child carries new physics).

## Inputs: what the clone has and what `lab prepare` fetches

In git: the pit profiles (`profiles/`, `observations/`), the station archive (`archive/fts360`, the logger exports
and dashboard history), the archived GFS forecasts (`archive/forecasts/gfs`), the configuration and SNOWPACK
templates. `snowagent lab prepare` builds the rest from those and from the ERA5 source the project already uses
(ADR-075):

1. `update bootstrap`: station files restored into `data/raw/fts360`, the logger exports and dashboard history
   converted into `data/interim`, and the ERA5 cell heights (`data/interim/era5/era5_box_z.npz`, one small download).
2. `data/interim/obs/observed_profiles.jsonl` from `profiles/` and `observations/transcriptions` (seconds).
3. The ERA5 months the lab reads: September to June of every station season in `config/lab.yaml` up to the current
   month, and for the older seasons (1997-98 to 2014-15, ADR-076) September to the month of the season's last pit
   (none for 2002-03, which has no pit), from the NSF NCAR ERA5 mirror on AWS Open Data (public, no account). They
   fill station gaps (wind, radiation, pressure, precipitation) and are the whole weather of the older seasons,
   flagged `filled`. Months the mirror has not published yet (the last two or three) are
   reported and stay missing, as they are on the project's own machines; rerun `lab prepare` later to add them. Each
   variable-month is kept as it completes, so an interrupted fetch resumes.

`snowagent lab prepare --no-era5` skips step 3 (minutes instead of hours), but the station gaps then stay unfilled,
the older seasons have no weather (their pits are excluded as `standin_weather_coverage_below_min`) and the cases,
and so every score, differ from the published runs. Checked on 2026-10-05: a fresh clone with `lab prepare`
reproduces the published 340 cases exactly (every visible input equal; only run ids and anonymous case keys differ);
on 2026-10-06 the build with the older seasons left those 340 cases unchanged.

**The older seasons** (`splits.include_reanalysis_seasons: true` in `config/lab.yaml`, the owner's choice) nearly
triple the cases and every training and check time. Their weather is ERA5 alone (`weather_source: era5_only` in each
case; the Leaderboard page and `lab compete --weather-source` split scores by it). To train on the station seasons
only, set the switch to `false` before `lab build-cases` (the 340-case set and the times of milestone 5), or keep it
and pass `--weather-sources station --weather-sources mixed` to `lab train` (the station seasons plus the eight
`mixed` cases of 2014-15; its `check-loso` then has 12 folds).

## Recommended options

**Long training** (with the older seasons, more than a night on 8 cores):

```bash
caffeinate -i snowagent lab train --rounds 10 --population 10 --screen-cases 30 --seed 0 --workers 8 \
  --initial persistence --initial weather_rule --initial analogue --initial snowpack --initial hybrid \
  --run-id night1-r10-p10
```

Ten rounds of ten agents, physics children screened on 30 cases first (`--screen-cases`, ADR-072). From the
milestone-5 rounds (14-47 min each for 8 agents on 4 cores, 340 cases) this was about 4-9 h on 4 cores; with the
older seasons (about 945 cases) expect about 11-25 h on 4 cores, roughly half that on 8 performance cores. The
printed estimate is an upper bound. For one night: `--rounds 6 --population 8 --screen-cases 30` (about 4-8 h on
8 cores with the older seasons; milestone 5: 2.5 h on 4 cores with 340 cases). Add
`--family-slots` (ADR-073) if you want the other agent families tuned too (otherwise SNOWPACK agents take over from
round 2). Change `--seed` for an independent second run; engine profiles already computed are reused.

**Full promotion check** of a run's winner, the evidence CLAUDE.md principle 3 asks for:

```bash
snowagent lab check-loso --genome night1-r10-p10/10/1 --workers 8 --estimate-only    # read the estimate first
caffeinate -i snowagent lab check-loso --genome night1-r10-p10/10/1 --workers 8 --check-id night1-loso
```

It re-runs the same training once per season with that season held out (28 folds with the older seasons, 11 without)
and judges the fold winners on the held-out seasons (ADR-068). With the older seasons plan several days or a week
of nights: with the same `--check-id` it resumes where it
stopped, keeps the finished folds, and `--season 2015-2016 --season 2016-2017` limits a night to some folds (the
verdict needs all of them). A check with fewer `--rounds` than the run is cheaper but weaker, and says so.

## Scoring version

Runs score with `lab-scoring-2` (ADR-074: the depth score measures only how close the middle estimate is; the
range is judged by the uncertainty score). Results of earlier versions are not comparable; the Leaderboard and
Training pages show each run's version.

## What was checked (2026-10-05)

On a fresh clone of this branch: `setup_env.sh` (Python 3.13), `lab prepare` (bootstrap, profiles, one ERA5 month
fetched, identical to the project's cache), `lab import`, `lab build-cases` (cases identical to the published ones),
the smoke training run and a two-season `check-loso --rounds 1`, both with the `spawn` process start that macOS uses,
and the Streamlit app (every page loads). `build_snowpack.sh` was rebuilt on Linux into a scratch prefix; its macOS
branch (install layout, rpath, default prefix) could not be run here. If the build fails on your Mac, the output of
`bash -x scripts/build_snowpack.sh` is what to send.

On 2026-10-06, for the older seasons (ADR-076): `lab prepare`'s month selection (tests), ERA5 for 2006-07 and
2011-12 fetched from the mirror with the same code, `lab import` and `lab build-cases` (445 cases, every leakage
check passed, the 340 earlier cases unchanged), a smoke training (`--rounds 2 --population 4`, 17 cases of both
weather sources) and `check-loso --rounds 1` over 2006-07 and 2024-25.

## Troubleshooting

- `snowagent: command not found`: `source .venv/bin/activate`.
- `SNOWPACK binary not found`: rerun step 3, or `export SNOWPACK_BIN=/path/to/snowpack` if you built elsewhere.
- `ERA5 2016-10 FAILED ClientPayloadError ... not enough data` or `FSTimeoutError`: the connection dropped part of a
  download. Each read is now retried four times; months that still fail are fetched by rerunning the same
  `snowagent lab prepare` (finished months and finished variables are kept). If many fail, use `--workers 2`.
- `ERA5 backfill configured but no ERA5 cache` on import: step 4 did not finish; rerun `snowagent lab prepare`.
- A run is slower than its estimate on the first rounds: the estimate starts from default timings until your machine
  has measured its own; the second run's estimate is better.
- Anything else: `local_setup.md`, Troubleshooting.
