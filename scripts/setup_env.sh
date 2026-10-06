#!/usr/bin/env bash
# Fresh-container setup (docs/operations.md section 0; ADR-053). Idempotent: the .venv, the engine build and the
# data restores are skipped when their result exists, and pip install -e only adds what pyproject.toml gained, so it
# is safe to run at every start (it is meant as the cloud environment's setup command).
#   bash scripts/setup_env.sh          # .venv, pip install -e .[dev], engine build if not found, snowagent doctor
#   bash scripts/setup_env.sh --data   # ... then `snowagent update bootstrap` (station raw files and interim
#                                      # conversions from archive/) and, when web/data/sites.json is missing,
#                                      # `snowagent update restore-web` (past seasons from the deployed site)
# PYTHON selects the interpreter for a new .venv (default python3); PREFIX / SNOWPACK_SRC / JOBS pass through to
# scripts/build_snowpack.sh (default /opt/snowpack, /opt/snowpack-src/snowpack-model, 4).
set -euo pipefail
cd "$(dirname "$0")/.."
DATA=0
for arg in "$@"; do
  case "$arg" in
    --data) DATA=1 ;;
    *) echo "usage: $0 [--data]" >&2; exit 2 ;;
  esac
done
if [ ! -x .venv/bin/python ]; then
  "${PYTHON:-python3}" -m venv .venv
fi
.venv/bin/pip install -q -e '.[dev]'
# A non-default build prefix is only found through SNOWPACK_BIN (engine.snowpack.find_engine); export it for
# this run and remind the caller to do the same in their shell.
if [ -n "${PREFIX:-}" ] && [ "$PREFIX" != /opt/snowpack ] && [ -z "${SNOWPACK_BIN:-}" ]; then
  export SNOWPACK_BIN="$PREFIX/bin/snowpack"
  echo "Non-default engine prefix: export SNOWPACK_BIN=$SNOWPACK_BIN in your shell as well."
fi
# Same lookup as `snowagent doctor` ($SNOWPACK_BIN, /opt/snowpack/bin/snowpack, $PATH); build only when it fails.
if ! .venv/bin/python -c 'from snowagent.engine.snowpack import find_engine; find_engine()' 2>/dev/null; then
  echo "SNOWPACK not found; building the pinned engine (~5 min on 4 cores)."
  bash scripts/build_snowpack.sh
fi
.venv/bin/snowagent doctor
if [ "$DATA" = 1 ]; then
  .venv/bin/snowagent update bootstrap
  if [ ! -f web/data/sites.json ]; then
    .venv/bin/snowagent update restore-web
  fi
fi
