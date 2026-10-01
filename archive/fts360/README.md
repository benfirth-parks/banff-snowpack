# FTS360 station records (raw monthly CSV responses, gzip)

Retrieved with `snowagent ingest fts360` (agency 450, Parks Canada stations); each file is the API's CSV for
one station and calendar month, unchanged apart from gzip. `manifest.jsonl` records URL, retrieval time and
sha256 of the uncompressed bytes. Timestamps are UTC. History in the API starts 2021-05.
Parsing to SI with QC flags: `snowagent.ingest.fts360.parse_station`.
