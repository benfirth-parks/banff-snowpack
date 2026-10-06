#!/usr/bin/env bash
# Set up the project from a fresh clone or container: macOS (Apple silicon or Intel) and Linux (ADR-053). Idempotent:
# the .venv, the engine build and the data restores are skipped when their result exists, and pip install -e only adds
# what pyproject.toml gained, so it is safe to rerun (it is also meant as the cloud environment's setup command).
#   bash scripts/setup_env.sh [python]          # .venv with the dev and lab extras, the SNOWPACK engine built if not
#                                               # found (scripts/build_snowpack.sh), then `snowagent doctor`
#   bash scripts/setup_env.sh --data            # ... then `snowagent update bootstrap` (station raw files and interim
#                                               # conversions from archive/) and, when web/data/sites.json is missing,
#                                               # `snowagent update restore-web` (past seasons from the deployed site)
#   bash scripts/setup_env.sh --no-lab          # without the lab extra (Streamlit and friends, about 0.5 GB)
# Needs Python 3.11+ (macOS: `brew install python@3.12`); [python] or PYTHON picks the interpreter for a new .venv.
# PREFIX / SNOWPACK_SRC / JOBS pass through to scripts/build_snowpack.sh. About 3-6 minutes and 1.5 GB, plus about
# 5 minutes for a first engine build.
set -euo pipefail
cd "$(dirname "$0")/.."
DATA=0
EXTRAS=dev,lab
PY=${PYTHON:-}
for arg in "$@"; do
  case "$arg" in
    --data) DATA=1 ;;
    --no-lab) EXTRAS=dev ;;
    -*) echo "usage: $0 [python] [--data] [--no-lab]" >&2; exit 2 ;;
    *) PY=$arg ;;
  esac
done
if [ -z "$PY" ]; then
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      PY=$c; break
    fi
  done
fi
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo "error: Python 3.11 or newer not found (macOS: brew install python@3.12; no brew? see docs/lab/run_locally.md section 0; then rerun)" >&2
  exit 1
fi
# On Apple silicon an x86_64 (Rosetta) Python installs slower, emulated wheels: warn.
if [ "$(uname -s)" = Darwin ] && [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = 1 ] \
   && [ "$("$PY" -c 'import platform; print(platform.machine())')" != arm64 ]; then
  echo "warning: $PY is not an arm64 build on this Apple-silicon Mac; prefer Homebrew's /opt/homebrew/bin/python3.12" >&2
fi
echo "using $("$PY" -c 'import sys; print(sys.executable, sys.version.split()[0])')"
# A .venv left by an earlier attempt with an older Python (e.g. Apple's 3.9) is rebuilt.
if [ -x .venv/bin/python ] && ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo "rebuilding .venv: it was made with $(.venv/bin/python -c 'import sys; print(sys.version.split()[0])')"
  rm -rf .venv
fi
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q -U pip
.venv/bin/python -m pip install -q -e ".[$EXTRAS]"
.venv/bin/snowagent --help >/dev/null
if [ "$EXTRAS" = dev,lab ]; then
  .venv/bin/python -c "import streamlit, pyarrow, plotly, sklearn; print('lab extra ok (streamlit', streamlit.__version__ + ')')"
fi
# A non-default build prefix is only found through SNOWPACK_BIN (engine.snowpack.find_engine); export it for
# this run and remind the caller to do the same in their shell.
if [ -n "${PREFIX:-}" ] && [ -z "${SNOWPACK_BIN:-}" ]; then
  export SNOWPACK_BIN="$PREFIX/bin/snowpack"
  echo "Engine prefix $PREFIX: export SNOWPACK_BIN=$SNOWPACK_BIN in your shell as well."
fi
# Same lookup as `snowagent doctor` ($SNOWPACK_BIN, the default prefixes, $PATH); build only when it fails.
if ! .venv/bin/python -c 'from snowagent.engine.snowpack import find_engine; find_engine()' 2>/dev/null; then
  echo "SNOWPACK not found; building the pinned engine (about 5 min)."
  bash scripts/build_snowpack.sh
fi
.venv/bin/snowagent doctor >/dev/null && echo "snowagent doctor: ok (engine found and a smoke run passed)" \
  || { .venv/bin/snowagent doctor; exit 1; }
if [ "$DATA" = 1 ]; then
  .venv/bin/snowagent update bootstrap
  if [ ! -f web/data/sites.json ]; then
    .venv/bin/snowagent update restore-web
  fi
fi
echo "Done. In every new terminal: source .venv/bin/activate"
