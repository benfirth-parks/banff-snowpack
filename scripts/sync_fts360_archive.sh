#!/usr/bin/env bash
# Copy finished FTS360 monthly CSVs (raw, unchanged) into tracked archive/fts360/ as gzip, plus the manifest.
# A month is copied once it is complete (older than the current month); the current month is refreshed.
set -euo pipefail
src=data/raw/fts360; dst=archive/fts360; cur=$(date -u +%Y-%m)
for f in "$src"/*/*.csv; do
  st=$(basename "$(dirname "$f")"); name=$(basename "$f")
  mkdir -p "$dst/$st"
  if [ ! -f "$dst/$st/$name.gz" ] || [[ "$name" == *"_$cur.csv" ]]; then gzip -9 -n -c "$f" > "$dst/$st/$name.gz"; fi
done
cp "$src/manifest.jsonl" "$dst/manifest.jsonl"
find "$dst" -name "*.gz" | wc -l
