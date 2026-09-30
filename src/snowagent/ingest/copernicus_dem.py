"""Copernicus GLO-30 DSM (1 arc-second, EGM2008 heights) tiles from the AWS Open Data bucket.

Tiles are named by their SW corner: Copernicus_DSM_COG_10_N51_00_W116_00_DEM covers 51-52 N, 116-115 W.
Note: it is a surface model (includes forest canopy), not a bare-earth terrain model.
"""

from __future__ import annotations

from pathlib import Path

from snowagent.ingest.fetch import fetch

BASE = "https://copernicus-dem-30m.s3.amazonaws.com"


def tile_name(lat: int, lon: int) -> str:
    ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"


def download(lats: list[int], lons: list[int], raw_dir: Path) -> list[dict]:
    manifest = raw_dir / "manifest.jsonl"
    return [fetch(f"{BASE}/{tile_name(la, lo)}/{tile_name(la, lo)}.tif", raw_dir / f"{tile_name(la, lo)}.tif",
                  manifest) for la in lats for lo in lons]


def build_study_dem(tiles: list[Path], out_tif: Path, bbox_lonlat: tuple[float, float, float, float],
                    crs: str = "EPSG:32611", res: float = 30.0) -> Path:
    """Mosaic tiles, reproject (bilinear) to ``crs`` at ``res`` m and clip to the lon/lat bbox.

    Also writes an ESRI ASCII copy with the JSON sidecar that ``terrain.dem.read_raster`` requires.
    """
    import numpy as np
    import rasterio
    from rasterio.merge import merge
    from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds

    from snowagent.terrain.dem import write_ascii_grid

    srcs = [rasterio.open(t) for t in tiles]
    mosaic, tr = merge(srcs, bounds=bbox_lonlat)
    src_crs = srcs[0].crs
    h, w = mosaic.shape[1:]
    left, bottom, right, top = transform_bounds(src_crs, crs, *bbox_lonlat)
    dst_tr, dw, dh = calculate_default_transform(src_crs, crs, w, h, *bbox_lonlat, resolution=res)
    dst = np.full((dh, dw), np.nan, dtype="float32")
    reproject(mosaic[0].astype("float32"), dst, src_transform=tr, src_crs=src_crs, dst_transform=dst_tr,
              dst_crs=crs, resampling=Resampling.bilinear, src_nodata=None, dst_nodata=np.nan)
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_tif, "w", driver="GTiff", height=dh, width=dw, count=1, dtype="float32", crs=crs,
                       transform=dst_tr, nodata=np.nan, compress="deflate") as ds:
        ds.write(dst, 1)
    meta = {"crs": crs, "horizontal_units": "m", "vertical_units": "m", "vertical_datum": "EGM2008",
            "synthetic": False, "source": "Copernicus GLO-30 DSM (surface model incl. canopy), AWS Open Data",
            "tiles": [Path(t).name for t in tiles], "bbox_lonlat": list(bbox_lonlat)}
    write_ascii_grid(out_tif.with_suffix(".asc"), dst, dst_tr.c, dst_tr.f + dst_tr.e * dh, res, meta)
    return out_tif
