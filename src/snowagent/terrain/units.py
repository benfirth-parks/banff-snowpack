"""Build terrain units (square blocks of DEM cells) from DEM, land cover and boundary.

Unit resolution is a configuration choice. A fine DEM does not imply fine-scale
predictive skill; the unit resolution is reported with every query.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from pyproj import Transformer

from snowagent.contracts import SUPPORTED_LAND_COVER, LandCover, Provenance, TerrainDomain, TerrainUnit
from snowagent.errors import InvalidInput
from snowagent.terrain import derive
from snowagent.terrain.dem import Raster, points_in_polygon, read_boundary, read_raster


@dataclass(frozen=True)
class UnitConfig:
    unit_size_m: float = 1000.0
    min_coverage: float = 0.5  # fraction of block cells inside boundary with valid DEM
    horizon_bins: int = 36
    horizon_max_dist_m: float = 15000.0
    horizon_samples_per_axis: int = 3  # horizons computed at n x n points, median per azimuth
    max_supported_slope_deg: float = 55.0
    exposure_dmax_m: float = 300.0


EXPOSURE_DIRECTIONS = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}


def _hash_files(paths: list[Path], extra: str) -> str:
    h = hashlib.sha256()
    for p in paths:
        for f in (p, Path(str(p) + ".json")):
            if f.exists():
                h.update(f.read_bytes())
    h.update(extra.encode())
    return h.hexdigest()[:12]


def build_domain(domain_id: str, dem_path: Path, boundary_path: Path, landcover_path: Path | None,
                 cfg: UnitConfig, synthetic: bool) -> TerrainDomain:
    dem = read_raster(dem_path, "elevation")
    ring, bcrs = read_boundary(boundary_path)
    if bcrs != dem.crs:
        raise InvalidInput(f"boundary CRS {bcrs} differs from DEM CRS {dem.crs}; reproject explicitly first")
    lc: Raster | None = None
    legend: dict[str, str] = {}
    if landcover_path is not None:
        lc = read_raster(landcover_path, "categorical")
        if lc.crs != dem.crs or lc.shape != dem.shape or lc.res != dem.res or (lc.x_left, lc.y_top) != (dem.x_left, dem.y_top):
            raise InvalidInput("land-cover raster must share the DEM grid exactly (CRS, extent, resolution)")
        legend = {str(k): v for k, v in lc.meta["legend"].items()}
        unknown = set(legend.values()) - {c.value for c in LandCover}
        if unknown:
            raise InvalidInput(f"land-cover legend has unsupported classes {unknown}")

    block = int(round(cfg.unit_size_m / dem.res))
    if block < 1 or abs(block * dem.res - cfg.unit_size_m) > 1e-6:
        raise InvalidInput(f"unit_size_m {cfg.unit_size_m} must be a multiple of DEM resolution {dem.res}")
    terrain_version = "tv-" + _hash_files([dem_path, boundary_path] + ([landcover_path] if landcover_path else []),
                                          f"{cfg}")
    dzdx, dzdy = derive.gradients(dem)
    slope_c, _ = derive.slope_aspect(dem)
    nr, nc = dem.shape
    rows, cols = np.mgrid[0:nr, 0:nc]
    xc, yc = dem.cell_center(rows, cols)
    inside = points_in_polygon(xc, yc, ring) & np.isfinite(dem.data)
    to_ll = Transformer.from_crs(dem.crs, "EPSG:4326", always_xy=True)
    az = np.arange(cfg.horizon_bins) * 360.0 / cfg.horizon_bins
    exp_dirs = np.array(list(EXPOSURE_DIRECTIONS.values()), dtype=float)

    units: list[TerrainUnit] = []
    grid_ids: dict[tuple[int, int], str] = {}
    pending: list[dict] = []
    for br in range(0, nr - block + 1, block):
        for bc in range(0, nc - block + 1, block):
            sl = (slice(br, br + block), slice(bc, bc + block))
            m = inside[sl]
            if m.sum() < cfg.min_coverage * block * block:
                continue
            gi, gj = br // block, bc // block
            uid = f"u{gi:03d}_{gj:03d}"
            grid_ids[(gi, gj)] = uid
            z = dem.data[sl][m]
            slope, aspect = derive.mean_orientation(dzdx[sl][m], dzdy[sl][m])
            x0 = dem.x_left + bc * dem.res
            y1 = dem.y_top - br * dem.res
            x1, y0 = x0 + block * dem.res, y1 - block * dem.res
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            # horizons at n x n interior sample points, median per azimuth
            k = cfg.horizon_samples_per_axis
            fr = (np.arange(k) + 0.5) / k
            hs = []
            for fy in fr:
                for fx in fr:
                    px, py = x0 + fx * (x1 - x0), y1 - fy * (y1 - y0)
                    rr, cc = dem.rowcol(px, py)
                    pz = dem.data[rr, cc]
                    if np.isfinite(pz):
                        hs.append(derive.horizon(dem, px, py, pz + 1.0, az, cfg.horizon_max_dist_m))
            hz = np.median(np.array(hs), axis=0)
            svf = derive.sky_view_factor(slope, aspect, az, hz)
            rr, cc = dem.rowcol(cx, cy)
            zc = dem.data[rr, cc] if np.isfinite(dem.data[rr, cc]) else float(np.mean(z))
            sx = derive.winstral_sx(dem, cx, cy, zc + 1.0, exp_dirs, cfg.exposure_dmax_m)
            cover = LandCover.unknown
            if lc is not None:
                codes = lc.data[sl][m]
                codes = codes[np.isfinite(codes)].astype(int)
                if codes.size:
                    vals, counts = np.unique(codes, return_counts=True)
                    cover = LandCover(legend.get(str(int(vals[np.argmax(counts)])), "unknown"))
            reasons: list[str] = []
            if cover not in SUPPORTED_LAND_COVER:
                if cover == LandCover.forest:
                    reasons.append("forest canopy: SNOWPACK canopy module not enabled/validated (CANOPY=FALSE)")
                elif cover == LandCover.unknown:
                    reasons.append("land cover unknown: supply a land-cover raster")
                else:
                    reasons.append(f"land cover '{cover.value}' not supported in this milestone")
            if slope > cfg.max_supported_slope_deg:
                reasons.append(f"mean slope {slope:.0f} deg exceeds supported {cfg.max_supported_slope_deg:.0f} deg")
            lon, lat = to_ll.transform(cx, cy)
            area_plan = float(m.sum() * dem.res**2)
            cell_slopes = slope_c[sl][m]
            cell_slopes = np.where(np.isfinite(cell_slopes), cell_slopes, slope)
            area_surf = float(np.sum(dem.res**2 / np.cos(np.radians(cell_slopes))))
            pending.append(dict(
                unit_id=uid, gi=gi, gj=gj, domain_id=domain_id, terrain_version=terrain_version, crs=dem.crs,
                polygon_xy=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)], centroid_xy=(cx, cy),
                centroid_lonlat=(float(lon), float(lat)), resolution_m=cfg.unit_size_m,
                n_dem_cells=int(m.sum()), area_planimetric_m2=area_plan, area_surface_m2=area_surf,
                elevation_m=float(np.mean(z)), elevation_min_m=float(np.min(z)), elevation_max_m=float(np.max(z)),
                slope_deg=slope, aspect_deg=aspect if slope >= 1.0 else None,
                horizon_azimuths_deg=[float(a) for a in az], horizon_elevation_deg=[float(h) for h in hz],
                sky_view_factor=svf, land_cover=cover,
                exposure_sx_deg={k2: float(v) for k2, v in zip(EXPOSURE_DIRECTIONS, sx, strict=True)},
                supported=not reasons, unsupported_reasons=reasons,
            ))
    if not pending:
        raise InvalidInput("no terrain units could be built inside the boundary")
    for p in pending:
        gi, gj = p.pop("gi"), p.pop("gj")
        nb = [grid_ids[(gi + a, gj + b)] for a in (-1, 0, 1) for b in (-1, 0, 1)
              if (a or b) and (gi + a, gj + b) in grid_ids]
        units.append(TerrainUnit(neighbour_ids=nb, **p))
    assumptions = [
        "grid north treated as true north (UTM convergence ignored)",
        "horizons computed only within the DEM extent; terrain beyond it is not represented",
        f"unit horizons are the per-azimuth median of {cfg.horizon_samples_per_axis}x"
        f"{cfg.horizon_samples_per_axis} interior sample points",
        "unit slope/aspect from the mean surface normal of the unit's DEM cells",
        "directional exposure (Winstral Sx, 300 m) is a proxy only; no deposition is inferred from it",
    ]
    prov = Provenance(source=str(dem_path), description=f"DEM {dem.res} m, units {cfg.unit_size_m} m",
                      sha256=terrain_version, created_utc=datetime.now(UTC), synthetic=synthetic)
    return TerrainDomain(domain_id=domain_id, terrain_version=terrain_version, crs=dem.crs,
                         dem_resolution_m=dem.res, unit_resolution_m=cfg.unit_size_m, boundary_xy=ring,
                         units=units, provenance=prov, assumptions=assumptions)


def site_unit(unit_id: str, domain_id: str, terrain_version: str, lat: float, lon: float, elevation_m: float,
              crs: str = "EPSG:32611") -> TerrainUnit:
    """A flat, open, unshaded 30 m point column (study plots are level clearings; ADR-026).

    A 30 m DSM cannot resolve a forest clearing, so no slope, horizon or canopy shelter is derived from it.
    """
    x, y = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(lon, lat)
    ring = [(x - 15, y - 15), (x + 15, y - 15), (x + 15, y + 15), (x - 15, y + 15)]
    return TerrainUnit(
        unit_id=unit_id, domain_id=domain_id, terrain_version=terrain_version, crs=crs, polygon_xy=ring,
        centroid_xy=(x, y), centroid_lonlat=(lon, lat), resolution_m=30.0, n_dem_cells=1,
        area_planimetric_m2=900.0, area_surface_m2=900.0, elevation_m=elevation_m, elevation_min_m=elevation_m,
        elevation_max_m=elevation_m, slope_deg=0.0, aspect_deg=None, horizon_azimuths_deg=[0.0, 90.0, 180.0, 270.0],
        horizon_elevation_deg=[0.0, 0.0, 0.0, 0.0], sky_view_factor=1.0, land_cover=LandCover.open, supported=True)


def add_site_units(domain: TerrainDomain, sites: dict[str, tuple[float, float, float]]) -> TerrainDomain:
    """Add named point columns ``{site_id: (lat, lon, elevation_m)}`` (e.g. study plots) to a domain.

    Site units are listed first, so a point query inside a site footprint returns the site column; elsewhere
    the block unit answers. They change the terrain version. Sites must lie inside the boundary.
    """
    if not sites:
        return domain
    tv = "tv-" + hashlib.sha256((domain.terrain_version + repr(sorted(sites.items()))).encode()).hexdigest()[:12]
    to_xy = Transformer.from_crs("EPSG:4326", domain.crs, always_xy=True)
    new, notes = [], []
    for sid, (lat, lon, z) in sorted(sites.items()):
        x, y = to_xy.transform(lon, lat)
        if not bool(points_in_polygon(np.array([x]), np.array([y]), domain.boundary_xy)[0]):
            raise InvalidInput(f"site {sid} ({lat}, {lon}) is outside the domain boundary")
        new.append(site_unit(f"site_{sid}", domain.domain_id, tv, lat, lon, z, domain.crs))
        notes.append(f"site unit site_{sid}: flat, open, unshaded 30 m column at {lat:.5f}, {lon:.5f}, {z:.0f} m "
                     "(study-plot representation of ADR-026; overrides the DEM/land cover at that point)")
    units = new + [u.model_copy(update={"terrain_version": tv}) for u in domain.units]
    return domain.model_copy(update={"terrain_version": tv, "units": units,
                                     "assumptions": domain.assumptions + notes})


def save_domain(domain: TerrainDomain, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(domain.model_dump_json(indent=1))


def load_domain(path: Path) -> TerrainDomain:
    path = Path(path)
    if path.is_dir():
        path = path / "domain.json"
    if not path.exists():
        raise InvalidInput(f"domain file {path} not found")
    return TerrainDomain.model_validate_json(path.read_text())
