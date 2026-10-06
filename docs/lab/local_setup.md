# Snowpack Agent Lab: local setup (macOS first)

The lab runs on your own machine: no cloud service, no API key, no telemetry. It is a research and
decision-support tool, never an avalanche forecast. Design: ADR-055 to ADR-069 in `docs/decisions.md`.

**To run the complete training on a Mac from a fresh clone, follow `run_locally.md`** (one ordered list of commands
with times and disk space; `snowagent lab prepare` fetches every input a clone lacks). This page explains each step.

What it needs: Python 3.11 or newer, git, and about 1 GB of disk for the restored station files and the lab's
tables (about 2 GB more for training: the case sets of the promotion check and the prediction cache). The SNOWPACK
engine is needed for the SNOWPACK and hybrid agents (competitions and training; `docs/lab/training.md` shows how
to build it on a Mac); everything else runs without it.

## 1. Clone and create an environment

```bash
git clone <repo-url> banff-snowpack
cd banff-snowpack
bash scripts/setup_env.sh      # or by hand: python3 -m venv .venv && .venv/bin/pip install -e '.[dev,lab]'
source .venv/bin/activate
```

`[lab]` adds streamlit, plotly, pyarrow and scikit-learn (all free, from PyPI). The daily update never needs them.
With uv instead: `uv venv .venv -p 3.11 && uv pip install -p .venv/bin/python -e '.[dev,lab]'`.

## 2. Build the inputs from the files in git

`snowagent lab prepare` does all of this step (and fetches the ERA5 months the import uses to fill station gaps;
ADR-075). By hand: the lab reads two things a fresh clone does not have yet (both are rebuilt from tracked files,
nothing is edited):

```bash
snowagent update bootstrap   # restores data/raw/fts360 from archive/fts360 and converts the logger exports and
                             # dashboard history into data/interim (never overwrites; also downloads the public
                             # ERA5 surface-height file the site runs use, and reports the SNOWPACK engine as
                             # missing, which is fine here)
snowagent obs profiles       # builds data/interim/obs/observed_profiles.jsonl from profiles/ and the transcriptions
```

## 3. Initialise the lab and import

```bash
snowagent lab init           # data/lab/ directories, run registry (data/lab/registry.sqlite), config check
snowagent lab import         # profiles + station weather of BOW, GOAT, SIMP -> data/lab/processed/*.parquet
snowagent lab coverage       # profiles and weather per site and season
```

`lab import` options: `--only profiles` or `--only weather`; `--source <checkout>` to read another checkout's
`data/` (read only); `--data-root <dir>` to write somewhere other than `data/lab`. Each import records a run
manifest (config hash, data hash, input files with sha256, profile ids) in the registry and in
`data/lab/manifests/`, and fails if an input file changed while it ran. On the full set it takes about a minute.
Station gaps are filled from the ERA5 cache in `data/interim/era5` (flagged `filled`, source `era5_cell_<elev>m`;
`weather.era5_backfill` in `config/lab.yaml`); without the cache the import warns and leaves the gaps.

## 3b. Build the benchmark cases

```bash
snowagent lab build-cases    # one case per usable pit (forecast_h72 and next_pit), about 4-5 minutes
snowagent lab cases          # counts per case set, split, site, type and forecast source
snowagent lab check-leakage  # re-runs the leakage checks on every built case (exit 3 on a leak)
```

The cases go to `data/lab/benchmark/<case set>/<split>/<case_id>/` (`visible/` for agents, `hidden/` for the
evaluator) with `build_report.json` per set. The archived GFS runs are read from `archive/forecasts/gfs`
(`--source <checkout>` for another checkout, read only). The split mode is `splits.mode` in `config/lab.yaml`:
`all` (default, every season 2015-16 to 2025-26 is training), `split` (development / validation / sealed test) or
`loso` (`--holdout <season>`). `snowagent lab build-case --profile-id <id>` builds one pit's cases;
`snowagent lab case-truth --case-id <id>` shows the withheld pit (a sealed-test case needs `--unseal` and the typed
phrase `UNSEAL <case_id>`). Protocol: `docs/lab/benchmark_protocol.md`.

## 3c. Run a competition

```bash
snowagent lab compete --workers 4   # the default agent of each family on every scorable case
snowagent lab leaderboard           # print the latest run again
```

The SNOWPACK agent needs the engine binary (`scripts/build_snowpack.sh`, or `SNOWPACK_BIN=/path/to/snowpack`);
without it the agent is skipped (reported, not scored) and the hybrid predicts from its other members
(`--engine none` does this on purpose). With the engine, the full set takes about 20 minutes with four
workers, most of it SNOWPACK runs. `--run-id <id>` resumes an interrupted run. Details:
`docs/lab/agents_and_scoring.md`.

## 3d. Train agents locally (milestone 4)

```bash
snowagent lab train --rounds 10 --population 10 --seed 0 --workers 4   # prints a time estimate, then runs
snowagent lab train --resume                                           # continue the latest interrupted run
snowagent lab lineage <agent id or genome hash>                        # ancestry of an evolved agent
snowagent lab check-loso --genome <run>/<round>/1 --workers 4          # promotion check, hours
```

Round 1 runs SNOWPACK once per case (about 20 minutes with four workers on the full set); the engine profile is
cached, so later rounds take a few minutes each. The full guide, including building SNOWPACK on a Mac and what the
per-round gap does and does not show, is `docs/lab/training.md`.

## 4. Tests and lint

```bash
pytest tests/unit            # the lab's tests are tests/unit/test_lab_*.py (a real-engine test skips without SNOWPACK)
ruff check src tests
```

The UI smoke test (`test_lab_ui.py`) is skipped with a reason when the lab extra is not installed.

## 5. Launch the app

```bash
streamlit run lab_app/Home.py
```

It opens at http://localhost:8501. Pages: **Home** (disclaimer, coverage per site, warnings, scoring weights,
split mode, latest runs), **Data Explorer** (profiles with the vertical profile plot and raw vs normalized fields;
station weather) and **Benchmark Cases** (built cases by case set, site, split and type; the visible inputs as an agent
sees them, eligible vs excluded records, leakage checks; the withheld pit for training and development cases only,
never sealed; a sidebar button builds the cases) and **Leaderboard** (competition runs: composite and component
scores per agent with plot, case type and forecast-source filters; a scored case's predicted profile beside the
observed pit) and **Training** (start a training run, which runs as its own process; per-round leaderboard, best
composite and gap charts, lineage of the best agent, promotion-check results). Times are shown in America/Edmonton; everything is stored in UTC. To look at another lab data
directory: `SNOWAGENT_LAB_DATA_ROOT=/path/to/lab streamlit run lab_app/Home.py`.

## Troubleshooting

- **`zsh: command not found: brew`**: Homebrew is not installed or not on PATH. Install it and add it to PATH as in
  `run_locally.md` section 0 (the official installer, then `eval "$(/opt/homebrew/bin/brew shellenv)"` in `~/.zprofile`
  and the current terminal).
- **`python3` is older than 3.11** (`python3 --version`): install 3.11+ (python.org installer or
  `brew install python@3.12`) and create the venv with that interpreter, e.g. `python3.12 -m venv .venv`.
- **`snowagent: command not found`**: the venv is not active. `source .venv/bin/activate` (each new terminal), or call
  `.venv/bin/snowagent`. In fish: `source .venv/bin/activate.fish`.
- **Package installation fails** on a compiled dependency (rasterio, eccodes, h5py): update pip first
  (`pip install -U pip`), then retry; on Apple silicon use an arm64 Python. `brew install gdal eccodes` helps when a
  wheel is missing for your Python version.
- **`pyarrow is not installed; install the lab extra`**: `pip install -e '.[lab]'`.
- **Port 8501 in use**: `streamlit run lab_app/Home.py --server.port 8502`, or stop the other app.
- **Home says "No lab data"**: run `snowagent lab init` and `snowagent lab import` from the repository root (the app
  reads `<repo>/data/lab` wherever it is started from).
- **`observed profiles not found`**: run `snowagent obs profiles` first (step 2).
- **No weather for a site**: the station files are missing; run `snowagent update bootstrap` (step 2).
- **`ERA5 backfill configured but no ERA5 cache` warning on import**: the station weather is imported without the ERA5 fill. The cache
  (`data/interim/era5/era5_box_*.npz`) is written by `snowagent lab prepare` (the lab's months) or `snowagent update fetch`;
  `--source <checkout>` reads it from another checkout.
- **Benchmark Cases says "No cases built yet"**: run `snowagent lab build-cases` from the repository root.
- **`build-cases` exits 3**: a case failed a leakage check; the output names the case and the check, and nothing of
  that case was written. Report it, do not work around it.
- **A training run was interrupted** (laptop asleep, terminal closed, Ctrl-C): `snowagent lab train --resume`
  (or `--resume --run-id <id>`) continues at the first unfinished round; finished agent-case pairs come from the
  cache. The Training page shows such a run as interrupted.
- **Training is slow on round 1**: that is SNOWPACK running once per case; use `--workers` up to your core count
  (`sysctl -n hw.ncpu`). `--engine none` trains without SNOWPACK (the SNOWPACK agent is skipped).
- **Reset only the lab's outputs** (never the raw data): `rm -rf data/lab` and run `snowagent lab init` and
  `snowagent lab import` again. Everything under `data/lab` is derived. Do not delete `data/raw`, `data/interim`,
  `archive/`, `profiles/` or `observations/`: those are the inputs (and `archive/`, `profiles/`, `observations/` are
  the tracked originals).
