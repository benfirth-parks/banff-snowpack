#!/usr/bin/env bash
# A lab training run in a temporary cloud computer (ADR-128): the same `snowagent lab train` as on the Mac, plus a
# checkpoint of the run's folder copied to a lasting folder after every finished round, so that a new computer can
# carry on where the old one stopped (usage limits pause work for hours and a cloud computer is thrown away).
#   bash scripts/cloud_train.sh RUN_ID [lab train options...]
#   CHECKPOINT_DIR=/mnt/project-files/lab/cloud-runs (default) holds RUN_ID.tar.gz (the run's folder: plan, rounds,
#   log) and RUN_ID-cache.tar.gz (the scored (agent, case) pairs, so a resume does not score them again).
# First call: starts the run with the options given. Later calls with the same RUN_ID: restore the checkpoint if this
# computer lacks the run, then `--resume` (the stored plan wins; other options are ignored by the loop).
# Needs the lab's data (scripts/cloud_lab_setup.sh) in data/lab. Exit code is lab train's.
set -euo pipefail
cd "$(dirname "$0")/.."
RUN_ID=${1:?usage: $0 RUN_ID [lab train options...]}
shift
CK=${CHECKPOINT_DIR:-/mnt/project-files/lab/cloud-runs}
ROOT=${DATA_ROOT:-data/lab}
RUN_DIR="$ROOT/outputs/training/$RUN_ID"
CACHE_DIR="$ROOT/outputs/cache"
mkdir -p "$CK" "$ROOT/outputs/training"
[ -f .venv/bin/activate ] && source .venv/bin/activate

if [ ! -d "$RUN_DIR" ] && [ -f "$CK/$RUN_ID.tar.gz" ]; then
  echo "restoring $RUN_ID from $CK"
  tar -xzf "$CK/$RUN_ID.tar.gz" -C "$ROOT/outputs/training"
  if [ -f "$CK/$RUN_ID-cache.tar.gz" ]; then mkdir -p "$CACHE_DIR"; tar -xzf "$CK/$RUN_ID-cache.tar.gz" -C "$CACHE_DIR"; fi
fi

# tar exits 1 when a file changed while it was read (the cache grows while a round runs; its entries are written
# atomically, so the archive is still usable); a failed checkpoint is reported and never stops the run
snap() {  # snap ARCHIVE DIR WHAT: write beside, then rename (a reader never sees half an archive)
  local rc=0
  tar -czf "$CK/.$1.part" --exclude='.*.tmp' -C "$2" "$3" 2>/dev/null || rc=$?
  if [ "$rc" -le 1 ]; then mv -f "$CK/.$1.part" "$CK/$1"; else echo "warning: checkpoint $1 failed (tar $rc)"; fi
}
checkpoint() {
  [ -d "$RUN_DIR" ] || return 0
  snap "$RUN_ID.tar.gz" "$ROOT/outputs/training" "$RUN_ID"
  if [ -d "$CACHE_DIR" ] && [ "${1:-}" = cache ]; then snap "$RUN_ID-cache.tar.gz" "$CACHE_DIR" .; fi
}

rounds_done() { find "$RUN_DIR/rounds" -mindepth 2 -maxdepth 2 -name round.json 2>/dev/null | wc -l || true; }

if [ -d "$RUN_DIR/rounds" ] || [ -f "$RUN_DIR/run.json" ]; then
  set -- "$@" --resume
fi
snowagent lab train --run-id "$RUN_ID" --data-root "$ROOT" "$@" &
PID=$!
trap 'kill $PID 2>/dev/null || true' EXIT
last=$(rounds_done)
while kill -0 "$PID" 2>/dev/null; do
  sleep 30
  now=$(rounds_done)
  if [ "$now" != "$last" ]; then checkpoint cache; echo "checkpoint: $now rounds saved to $CK"; last=$now; fi
done
set +e
wait "$PID"
code=$?
set -e
checkpoint cache
echo "lab train exited $code; checkpoint in $CK/$RUN_ID.tar.gz"
exit $code
