#!/usr/bin/env bash
# The cloud training queue (ADR-128): the lab's own models (no SNOWPACK in the agents), one run after another, all
# with the same budget, each through scripts/cloud_train.sh (checkpoint after every round). Rerun the same command on
# any computer to carry on: finished runs (their checkpoint says "finished") are skipped, a cut run resumes.
#   bash scripts/cloud_queue.sh            # QUEUE_TAG (default cloud1) names the runs: <tag>-<family>[-trimmed]
# ROUNDS, POPULATION, SURVIVORS, SEED, WORKERS override the budget; FAMILIES and TRIM_TOO pick the runs.
set -uo pipefail
cd "$(dirname "$0")/.."
TAG=${QUEUE_TAG:-cloud1}
ROUNDS=${ROUNDS:-15}; POP=${POPULATION:-20}; SURV=${SURVIVORS:-3}; SEED=${SEED:-0}; WORKERS=${WORKERS:-$(nproc)}
FAMILIES=${FAMILIES:-"column process recipe story scorecard"}
TRIM_TOO=${TRIM_TOO:-1}
CK=${CHECKPOINT_DIR:-/mnt/project-files/lab/cloud-runs}
ROOT=${DATA_ROOT:-data/lab}

finished() {  # the run's status, from this computer's copy or else the checkpoint
  local id=$1 st="$ROOT/outputs/training/$1/status.json"
  if [ -f "$st" ]; then grep -q '"state": *"finished"' "$st"; return; fi
  [ -f "$CK/$id.tar.gz" ] && tar -xzOf "$CK/$id.tar.gz" "$id/status.json" 2>/dev/null | grep -q '"state": *"finished"'
}

runs=()
for f in $FAMILIES; do runs+=("$f:"); done
if [ "$TRIM_TOO" = 1 ]; then for f in $FAMILIES; do runs+=("$f:trim"); done; fi
for r in "${runs[@]}"; do
  fam=${r%%:*}; trim=${r#*:}
  id="$TAG-$fam${trim:+-trimmed}"
  if finished "$id"; then echo "$id: finished, skipped"; continue; fi
  echo "$(date -u +%FT%TZ) $id: starting or resuming"
  bash scripts/cloud_train.sh "$id" --rounds "$ROUNDS" --population "$POP" --survivors "$SURV" --seed "$SEED" \
    --initial "$fam" --area-check jasper --workers "$WORKERS" ${trim:+--trim}
  code=$?
  echo "$(date -u +%FT%TZ) $id: exit $code"
  [ $code -eq 5 ] && { echo "stop requested; queue paused"; exit 5; }
  [ $code -ne 0 ] && { echo "$id failed; queue paused (rerun to resume it)"; exit $code; }
done
echo "queue $TAG done"
