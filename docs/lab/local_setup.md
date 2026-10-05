# Snowpack Agent Lab: local setup (macOS first)

The lab runs on your own machine: no cloud service, no API key, no telemetry. It is a research and
decision-support tool, never an avalanche forecast. Design: ADR-055 to ADR-057 in `docs/decisions.md`.

What it needs: Python 3.11 or newer, git, and about 1 GB of disk for the restored station files and the lab's
tables. The SNOWPACK engine is not needed for milestone 1.

## 1. Clone and create an environment

```bash
git clone <repo-url> banff-snowpack
cd banff-snowpack
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,lab]'
```

`[lab]` adds streamlit, plotly, pyarrow and scikit-learn (all free, from PyPI). The daily update never needs them.
With uv instead: `uv venv .venv -p 3.11 && uv pip install -p .venv/bin/python -e '.[dev,lab]'`.

## 2. Build the inputs from the files in git

The lab reads two things a fresh clone does not have yet (both are rebuilt from tracked files, nothing is edited):

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

## 4. Tests and lint

```bash
pytest tests/unit            # the lab's tests are tests/unit/test_lab_*.py
ruff check src tests
```

The UI smoke test (`test_lab_ui.py`) is skipped with a reason when the lab extra is not installed.

## 5. Launch the app

```bash
streamlit run lab_app/Home.py
```

It opens at http://localhost:8501. Pages: **Home** (disclaimer, coverage per site, warnings, scoring weights,
splits, latest runs) and **Data Explorer** (profiles with the vertical profile plot and raw vs normalized fields;
station weather). Times are shown in America/Edmonton; everything is stored in UTC. To look at another lab data
directory: `SNOWAGENT_LAB_DATA_ROOT=/path/to/lab streamlit run lab_app/Home.py`.

## Troubleshooting

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
- **No weather for a site**: the station files are missing; run `snowagent update bootstrap` (step 2). If its ERA5
  download fails (offline), the station files are already restored by then; the lab does not use ERA5 yet.
- **Reset only the lab's outputs** (never the raw data): `rm -rf data/lab` and run `snowagent lab init` and
  `snowagent lab import` again. Everything under `data/lab` is derived. Do not delete `data/raw`, `data/interim`,
  `archive/`, `profiles/` or `observations/`: those are the inputs (and `archive/`, `profiles/`, `observations/` are
  the tracked originals).
