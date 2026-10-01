"""Inputs for a real terrain domain: DEM window, land cover on the same grid, and a boundary (ADR-032).

The domain is a square in the projected CRS centred on a point, snapped to the DEM pixel grid. The DEM window
extends ``buffer_m`` beyond the boundary on every side so horizons see the surrounding terrain (rays stop at
the DEM edge); the buffer is a multiple of the unit size, so unit blocks (built from the DEM's top-left corner)
align exactly with the boundary. Output is the ESRI ASCII + JSON-sidecar format ``terrain.dem.read_raster``
requires, so ``terrain.units.build_domain`` reads real and synthetic domains the same way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pyproj import Transformer

from snowagent.errors import InvalidInput
from snowagent.terrain.dem import write_ascii_grid


@dataclass(frozen=True)
class DomainSpec:
    domain_id: str
    center_lat: float
    center_lon: float
    size_m: float
    unit_size_m: float
    buffer_m: float = 15000.0
    crs: str = "EPSG:32611"


def prepare_inputs(spec: DomainSpec, dem_tif: Path, landcover_tiles: list[Path], out_dir: Path) -> dict[str, Path]:
    import rasterio
    from rasterio.windows import Window

    if spec.size_m % spec.unit_size_m or spec.buffer_m % spec.unit_size_m:
        raise InvalidInput("domain size and buffer must be multiples of the unit size")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cx, cy = Transformer.from_crs("EPSG:4326", spec.crs, always_xy=True).transform(spec.center_lon, spec.center_lat)
    with rasterio.open(dem_tif) as src:
        if str(src.crs) != spec.crs:
            raise InvalidInput(f"DEM CRS {src.crs} differs from domain CRS {spec.crs}")
        res = float(src.res[0])
        if abs(src.res[0] - src.res[1]) > 1e-9 or spec.unit_size_m % res or spec.buffer_m % res:
            raise InvalidInput(f"DEM resolution {src.res} must be square and divide the unit size and buffer")
        x_left0, y_top0 = src.transform.c, src.transform.f
        # boundary west/north edges snapped to the DEM pixel grid
        bx0 = x_left0 + round((cx - spec.size_m / 2 - x_left0) / res) * res
        by1 = y_top0 - round((y_top0 - (cy + spec.size_m / 2)) / res) * res
        col0 = int(round((bx0 - spec.buffer_m - x_left0) / res))
        row0 = int(round((y_top0 - (by1 + spec.buffer_m)) / res))
        n = int(round((spec.size_m + 2 * spec.buffer_m) / res))
        if col0 < 0 or row0 < 0 or col0 + n > src.width or row0 + n > src.height:
            raise InvalidInput("domain plus horizon buffer extends beyond the DEM; enlarge the DEM or the buffer")
        z = src.read(1, window=Window(col0, row0, n, n)).astype(float)
        nod = src.nodata
        dem_meta = {k: v for k, v in json.loads(Path(dem_tif).with_suffix(".asc.json").read_text()).items()
                    if k not in ("nodata", "bbox_lonlat")} if Path(dem_tif).with_suffix(".asc.json").exists() else {}
    if nod is not None and not np.isnan(nod):
        z[z == nod] = np.nan
    x_left, y_top = x_left0 + col0 * res, y_top0 - row0 * res
    y_bottom = y_top - n * res
    side = {"crs": spec.crs, "horizontal_units": "m", "synthetic": False}
    dem_out = out_dir / "dem.asc"
    write_ascii_grid(dem_out, z, x_left, y_bottom, res,
                     {**side, "vertical_units": "m", "vertical_datum": dem_meta.get("vertical_datum", "EGM2008"),
                      "source": dem_meta.get("source", str(dem_tif)), "window_of": str(dem_tif),
                      "buffer_m": spec.buffer_m})

    from snowagent.ingest.worldcover import LEGEND, LEGEND_NAMES, SOURCE, on_grid

    lc = on_grid(landcover_tiles, x_left, y_top, res, z.shape, spec.crs)
    lc_out = out_dir / "landcover.asc"
    write_ascii_grid(lc_out, lc, x_left, y_bottom, res,
                     {**side, "legend": LEGEND, "legend_names": LEGEND_NAMES, "source": SOURCE,
                      "resampling": "mode from 10 m", "tiles": [Path(t).name for t in landcover_tiles]},
                     nodata=-9999, fmt="%d")

    bx1, by0 = bx0 + spec.size_m, by1 - spec.size_m
    ring = [[bx0, by0], [bx1, by0], [bx1, by1], [bx0, by1], [bx0, by0]]
    gj = {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": spec.crs}},
          "features": [{"type": "Feature", "properties": {"domain_id": spec.domain_id,
                                                          "center_lonlat": [spec.center_lon, spec.center_lat],
                                                          "size_m": spec.size_m},
                        "geometry": {"type": "Polygon", "coordinates": [ring]}}]}
    b_out = out_dir / "boundary.geojson"
    b_out.write_text(json.dumps(gj, indent=1))
    return {"dem": dem_out, "landcover": lc_out, "boundary": b_out}
