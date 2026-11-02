"""Plumbing for study plots outside Banff: Parker Ridge (Jasper) and Fidelity (Rogers Pass) (ADR-108).

Kept apart from the Banff modules on purpose. Nothing the lab predicts with imports this package (a test checks),
so it sits outside the lab's prediction code hash and building it never invalidates the Banff training cache. If a
new site ever joins the lab, this package must come back into the hash (``lab/training/cache.py``).

Every converter here writes station tables with the Banff FTS360 column names into
``data/interim/new_sites/stations/<key>/<key>.csv``, so ``ingest.fts360.load_station`` and the baseline forcing
assembler read them unchanged (``config/new_sites.yaml`` ``station_dir``).
"""

STATION_DIR = "data/interim/new_sites/stations"
