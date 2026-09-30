#!/usr/bin/env bash
# Copy finished GFS point extracts from data/interim (gitignored) into the tracked archive/ folder.
# CSVs are copied as-is; provenance JSON is gzipped. Existing archive files are never overwritten.
set -euo pipefail
src=data/interim/forecasts/gfs; dst=archive/forecasts/gfs
mkdir -p "$dst"
for csv in "$src"/gfs_*.csv; do
  stem=$(basename "$csv" .csv)
  [ -f "$src/$stem.provenance.json" ] || continue   # run not finished
  [ -f "$dst/$stem.csv" ] || cp "$csv" "$dst/$stem.csv"
  [ -f "$dst/$stem.provenance.json.gz" ] || gzip -c "$src/$stem.provenance.json" > "$dst/$stem.provenance.json.gz"
done
ls "$dst"/*.csv | wc -l
