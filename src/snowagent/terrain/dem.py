"""Local raster and boundary ingest with explicit CRS, units and no-data handling.

Supported now: ESRI ASCII grids (``.asc``) with a mandatory JSON sidecar
(``<name>.asc.json``) that declares CRS, units and (for categorical rasters) the
class legend. Anything ambiguous is rejected rather than guessed. A GeoTIFF
adapter can be added behind :func:`read_raster` without changing callers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pyproj import CRS

from snowagent.errors import InvalidInput, InvalidUnits


@dataclass(frozen=True)
class Raster:
    data: np.ndarray  # float array, NaN = no data; row 0 is the NORTH edge
    x_left: float  # CRS x of the left (west) edge
    y_top: float  # CRS y of the top (north) edge
    res: float  # square cell size in CRS units (metres)
    crs: str
    meta: dict

    @property
    def shape(self) -> tuple[int, int]:
        return self.data.shape  # type: ignore[return-value]

    def cell_center(self, row: int | np.ndarray, col: int | np.ndarray) -> tuple:
        return self.x_left + (np.asarray(col) + 0.5) * self.res, self.y_top - (np.asarray(row) + 0.5) * self.res

    def rowcol(self, x: float, y: float) -> tuple[int, int]:
        return int(np.floor((self.y_top - y) / self.res)), int(np.floor((x - self.x_left) / self.res))


def validate_projected_crs(crs_text: str) -> CRS:
    try:
        crs = CRS.from_user_input(crs_text)
    except Exception as exc:  # pyproj raises CRSError
        raise InvalidInput(f"unrecognised CRS {crs_text!r}") from exc
    if not crs.is_projected:
        raise InvalidInput(f"CRS {crs_text} is not projected; terrain derivatives need metric axes")
    units = {ax.unit_name.lower() for ax in crs.axis_info}
    if units - {"metre", "meter"}:
        raise InvalidUnits(f"CRS {crs_text} axis units {units} are not metres")
    return crs


def read_raster(path: Path, kind: str = "elevation") -> Raster:
    """Read an ESRI ASCII grid plus JSON sidecar.

    Sidecar keys: ``crs`` (required), ``horizontal_units`` = "m" (required),
    for elevation ``vertical_units`` = "m" and ``vertical_datum``; for categorical
    rasters ``legend`` {code: class}. ``nodata`` overrides the header value.
    """
    path = Path(path)
    sidecar = path.with_name(path.name + ".json")
    if not sidecar.exists():
        raise InvalidInput(f"missing sidecar {sidecar.name}: CRS and units must be declared explicitly")
    meta = json.loads(sidecar.read_text())
    if "crs" not in meta:
        raise InvalidInput(f"{sidecar.name} lacks 'crs'")
    validate_projected_crs(meta["crs"])
    if meta.get("horizontal_units") != "m":
        raise InvalidUnits(f"{sidecar.name}: horizontal_units must be 'm' (got {meta.get('horizontal_units')!r})")
    if kind == "elevation" and meta.get("vertical_units") != "m":
        raise InvalidUnits(f"{sidecar.name}: vertical_units must be 'm' (got {meta.get('vertical_units')!r})")
    if kind == "categorical" and not isinstance(meta.get("legend"), dict):
        raise InvalidInput(f"{sidecar.name}: categorical raster needs a 'legend' mapping")

    header: dict[str, float] = {}
    lines = path.read_text().splitlines()
    n_header = 0
    for line in lines[:7]:
        parts = line.split()
        if len(parts) != 2 or not parts[0][0].isalpha():
            break
        header[parts[0].lower()] = float(parts[1])
        n_header += 1
    body = np.loadtxt(lines[n_header:], dtype=float, ndmin=2)
    for k in ("ncols", "nrows", "cellsize"):
        if k not in header:
            raise InvalidInput(f"{path.name}: ASCII grid header missing {k}")
    nrows, ncols, res = int(header["nrows"]), int(header["ncols"]), header["cellsize"]
    if body.shape != (nrows, ncols):
        raise InvalidInput(f"{path.name}: data shape {body.shape} != header ({nrows}, {ncols})")
    if "xllcorner" in header:
        x_left, y_bottom = header["xllcorner"], header["yllcorner"]
    elif "xllcenter" in header:
        x_left, y_bottom = header["xllcenter"] - res / 2, header["yllcenter"] - res / 2
    else:
        raise InvalidInput(f"{path.name}: header needs xllcorner/yllcorner or xllcenter/yllcenter")
    nodata = meta.get("nodata", header.get("nodata_value"))
    data = body.astype(float)
    if nodata is not None:
        data[data == float(nodata)] = np.nan
    if kind == "elevation":
        finite = data[np.isfinite(data)]
        if finite.size == 0:
            raise InvalidInput(f"{path.name}: raster has no valid cells")
        if finite.min() < -500 or finite.max() > 9000:
            raise InvalidUnits(f"{path.name}: elevations outside -500..9000 m; check units")
    return Raster(data=data, x_left=x_left, y_top=y_bottom + nrows * res, res=res, crs=meta["crs"], meta=meta)


def write_ascii_grid(path: Path, data: np.ndarray, x_left: float, y_bottom: float, res: float,
                     sidecar: dict, nodata: float = -9999.0, fmt: str = "%.2f") -> None:
    arr = np.where(np.isfinite(data), data, nodata)
    header = (f"ncols {arr.shape[1]}\nnrows {arr.shape[0]}\nxllcorner {x_left}\nyllcorner {y_bottom}\n"
              f"cellsize {res}\nNODATA_value {nodata}\n")
    with open(path, "w") as fh:
        fh.write(header)
        np.savetxt(fh, arr, fmt=fmt)
    Path(str(path) + ".json").write_text(json.dumps({**sidecar, "nodata": nodata}, indent=2))


def read_boundary(path: Path) -> tuple[list[tuple[float, float]], str]:
    """Read a single-polygon GeoJSON boundary; ``crs`` must be declared in the file."""
    gj = json.loads(Path(path).read_text())
    crs = (gj.get("crs") or {}).get("properties", {}).get("name")
    if not crs:
        raise InvalidInput(f"{Path(path).name}: boundary GeoJSON must declare its CRS")
    validate_projected_crs(crs)
    feats = gj.get("features") or [gj]
    geom = feats[0].get("geometry", feats[0])
    if geom.get("type") != "Polygon":
        raise InvalidInput("boundary must be a single Polygon")
    ring = [(float(x), float(y)) for x, y in geom["coordinates"][0]]
    if len(ring) < 4:
        raise InvalidInput("boundary polygon needs at least 3 vertices")
    return ring, crs


def points_in_polygon(x: np.ndarray, y: np.ndarray, ring: list[tuple[float, float]]) -> np.ndarray:
    """Vectorised even-odd ray casting."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    inside = np.zeros(np.broadcast(x, y).shape, dtype=bool)
    pts = ring if ring[0] != ring[-1] else ring[:-1]
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        cond = (y1 > y) != (y2 > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
        inside ^= cond & (x < xint)
    return inside
