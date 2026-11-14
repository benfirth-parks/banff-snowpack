"""ERA5 for the new sites: the same extraction as ``ingest.era5``, over a box around Parker Ridge and Fidelity
(ADR-108), written to its own folder so the Banff ERA5 files never change.

``ingest.era5`` reads its fixed Banff box; rather than edit that module (it is inside the lab's prediction code
hash), the box lookup is swapped for this box in the worker process that extracts a month. The output files have
the Banff layout (``era5_box_YYYYMM.npz``, ``era5_box_z.npz``), so ``baseline.assemble.era5_cell_series`` reads
them with ``era5_dir`` set to this folder (``config/new_sites.yaml`` ``era5_dir``).
"""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path

NORTH_WEST = (50.9, 52.5, -118.1, -116.6)  # lat_min, lat_max, lon_min, lon_max: Jasper's Icefields to Rogers Pass
ERA5_DIR = "data/interim/era5_north_west"


def _use_box(box) -> None:
    from snowagent.ingest import era5

    if not getattr(era5._box_index, "new_sites_box", None) == box:
        f = partial(era5._box_index.func if isinstance(era5._box_index, partial) else era5._box_index, box=box)
        f.new_sites_box = box
        era5._box_index = f


def extract_month(year: int, month: int, out_dir: Path, box=NORTH_WEST) -> Path:
    """One month of the box (run in its own process: the swap lasts for that process)."""
    from snowagent.ingest import era5

    _use_box(box)
    dest = era5.extract_month(year, month, Path(out_dir))
    meta = json.loads(dest.with_suffix(".json").read_text())
    dest.with_suffix(".json").write_text(json.dumps(meta | {"box": list(box), "new_sites": "ADR-108"}))
    return dest


def fetch_box_height(out_dir: Path, box=NORTH_WEST) -> Path:
    from snowagent.ingest import era5

    _use_box(box)
    return era5.fetch_box_height(Path(out_dir))
