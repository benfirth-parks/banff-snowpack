"""ESA WorldCover 2021 v200 land cover (10 m, CC BY 4.0) from the AWS Open Data bucket (ADR-032).

Tiles are 3 x 3 degrees named by their SW corner (N51W117 covers 51-54 N, 117-114 W), GeoTIFF in EPSG:4326,
uint8 class codes, 0 = no data. Classes are mapped to the terrain-unit land-cover classes the snow engine
setup supports (contracts.LandCover); the mapping is part of the land-cover sidecar so it travels with the
raster and changes the terrain version when edited.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from snowagent.ingest.fetch import fetch

BASE = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
SOURCE = "ESA WorldCover 10 m 2021 v200 (CC BY 4.0), AWS Open Data"

# WorldCover code -> contracts.LandCover value. Subalpine shrubland (krummholz/willow, mostly buried in winter)
# and herbaceous classes are treated as open snow surfaces; built-up stays "unknown" (unsupported).
LEGEND = {
    "10": "forest",   # tree cover
    "20": "open",     # shrubland
    "30": "open",     # grassland
    "40": "open",     # cropland
    "50": "unknown",  # built-up
    "60": "rock",     # bare / sparse vegetation
    "70": "glacier",  # snow and ice (permanent)
    "80": "water",    # permanent water bodies
    "90": "open",     # herbaceous wetland
    "95": "forest",   # mangroves
    "100": "open",    # moss and lichen
}
LEGEND_NAMES = {"10": "tree cover", "20": "shrubland", "30": "grassland", "40": "cropland", "50": "built-up",
                "60": "bare / sparse vegetation", "70": "snow and ice", "80": "permanent water bodies",
                "90": "herbaceous wetland", "95": "mangroves", "100": "moss and lichen"}


def tile_name(lat: float, lon: float) -> str:
    """Name of the 3-degree tile containing (lat, lon)."""
    la, lo = 3 * math.floor(lat / 3), 3 * math.floor(lon / 3)
    ns, ew = ("N" if la >= 0 else "S"), ("E" if lo >= 0 else "W")
    return f"ESA_WorldCover_10m_2021_v200_{ns}{abs(la):02d}{ew}{abs(lo):03d}_Map"


def download(bbox_lonlat: tuple[float, float, float, float], raw_dir: Path) -> list[dict]:
    """All tiles intersecting (west, south, east, north); raw files unchanged, manifest with sha256."""
    w, s, e, n = bbox_lonlat
    lats = range(3 * math.floor(s / 3), 3 * math.floor(n / 3) + 1, 3)
    lons = range(3 * math.floor(w / 3), 3 * math.floor(e / 3) + 1, 3)
    names = sorted({tile_name(la, lo) for la in lats for lo in lons})
    return [fetch(f"{BASE}/{t}.tif", raw_dir / f"{t}.tif", raw_dir / "manifest.jsonl", timeout=900) for t in names]


def on_grid(tiles: list[Path], x_left: float, y_top: float, res: float, shape: tuple[int, int], crs: str
            ) -> np.ndarray:
    """Class codes resampled (mode) onto a projected grid; NaN where no tile covers a cell."""
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject

    out = np.zeros(shape, dtype="uint8")
    dst_tr = from_origin(x_left, y_top, res, res)
    for t in tiles:
        part = np.zeros(shape, dtype="uint8")
        with rasterio.open(t) as src:
            reproject(rasterio.band(src, 1), part, dst_transform=dst_tr, dst_crs=crs, resampling=Resampling.mode,
                      src_nodata=0, dst_nodata=0)
        out = np.where(out == 0, part, out)
    codes = out.astype(float)
    codes[out == 0] = np.nan
    return codes
