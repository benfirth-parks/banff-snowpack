#!/usr/bin/env bash
# Create the project's Python environment (.venv) with the dev and lab extras: macOS (Apple silicon or Intel) and
# Linux. Usage, from the repository root:  bash scripts/setup_env.sh [python-interpreter]
# Needs Python 3.11+ (macOS: `brew install python@3.12`). About 3-6 minutes and 1.5 GB (wheels only, no compiler).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${1:-${PYTHON:-}}
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
.venv/bin/python -m pip install -q -e '.[dev,lab]'
.venv/bin/snowagent --help >/dev/null
.venv/bin/python -c "import streamlit, pyarrow, plotly, sklearn; print('lab extra ok (streamlit', streamlit.__version__ + ')')"
echo "Done. In every new terminal: source .venv/bin/activate"
