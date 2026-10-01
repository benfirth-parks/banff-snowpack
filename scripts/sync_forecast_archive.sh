#!/usr/bin/env bash
# Copy finished GFS point extracts from data/interim (gitignored) into the tracked archive/ folder.
# CSVs are copied as-is; provenance JSON is gzipped. Existing archive files are kept unless the new extract is larger (superseding an early partial extract).
set -euo pipefail
src=data/interim/forecasts/gfs; dst=archive/forecasts/gfs
mkdir -p "$dst"
for csv in "$src"/gfs_*.csv; do
  stem=$(basename "$csv" .csv)
  [ -f "$src/$stem.provenance.json" ] || continue   # run not finished
  # copy new runs; replace an archived copy only if the new extract is larger (a superseded partial/test extract)
  if [ ! -f "$dst/$stem.csv" ] || [ "$(wc -l < "$csv")" -gt "$(wc -l < "$dst/$stem.csv")" ]; then
    cp "$csv" "$dst/$stem.csv"; gzip -c "$src/$stem.provenance.json" > "$dst/$stem.provenance.json.gz"
  fi
  [ -f "$dst/$stem.provenance.json.gz" ] || gzip -c "$src/$stem.provenance.json" > "$dst/$stem.provenance.json.gz"
done
ls "$dst"/*.csv | wc -l
