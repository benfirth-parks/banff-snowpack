"""Terrain derivatives: slope, aspect, horizon angles, sky-view and exposure.

Conventions: aspect is the downslope direction, degrees clockwise from grid
north (0 = N, 90 = E). Grid north is assumed to coincide with true north; for
UTM the convergence (< ~2 deg in the pilot area) is recorded as an assumption.
"""

from __future__ import annotations

import numpy as np

from snowagent.terrain.dem import Raster


def gradients(dem: Raster) -> tuple[np.ndarray, np.ndarray]:
    """Horn (1981) gradients: dz/dx (east) and dz/dy (north). NaN where any neighbour is NaN."""
    z = np.pad(dem.data, 1, mode="constant", constant_values=np.nan)
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * dem.res)
    dzdy = ((a + 2 * b + c) - (g + 2 * h + i)) / (8 * dem.res)  # row 0 is north
    return dzdx, dzdy


def slope_aspect(dem: Raster) -> tuple[np.ndarray, np.ndarray]:
    dzdx, dzdy = gradients(dem)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    aspect = np.degrees(np.arctan2(-dzdx, -dzdy)) % 360.0
    return slope, aspect


def mean_orientation(dzdx: np.ndarray, dzdy: np.ndarray) -> tuple[float, float]:
    """Slope/aspect of the mean unit surface normal of a set of cells."""
    ok = np.isfinite(dzdx) & np.isfinite(dzdy)
    nx, ny, nz = -dzdx[ok], -dzdy[ok], np.ones(ok.sum())
    norm = np.sqrt(nx**2 + ny**2 + nz**2)
    mx, my, mz = (nx / norm).mean(), (ny / norm).mean(), (nz / norm).mean()
    m = np.sqrt(mx**2 + my**2 + mz**2)
    slope = float(np.degrees(np.arccos(mz / m)))
    aspect = float(np.degrees(np.arctan2(mx, my)) % 360.0)
    return slope, aspect


def _sample(dem: Raster, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Bilinear sample; NaN outside the raster."""
    fc = (x - dem.x_left) / dem.res - 0.5
    fr = (dem.y_top - y) / dem.res - 0.5
    nr, nc = dem.shape
    c0, r0 = np.floor(fc).astype(int), np.floor(fr).astype(int)
    out = np.full(np.shape(x), np.nan)
    ok = (c0 >= 0) & (r0 >= 0) & (c0 + 1 < nc) & (r0 + 1 < nr)
    tc, tr = fc - c0, fr - r0
    z = dem.data
    c0o, r0o, tco, tro = c0[ok], r0[ok], tc[ok], tr[ok]
    out[ok] = (z[r0o, c0o] * (1 - tco) * (1 - tro) + z[r0o, c0o + 1] * tco * (1 - tro)
               + z[r0o + 1, c0o] * (1 - tco) * tro + z[r0o + 1, c0o + 1] * tco * tro)
    return out


def horizon(dem: Raster, x0: float, y0: float, z0: float, azimuths_deg: np.ndarray,
            max_dist_m: float, step_m: float | None = None) -> np.ndarray:
    """Horizon elevation angle (deg) per azimuth by ray marching over the DEM.

    Rays stop at the DEM edge: terrain outside the DEM is unknown and treated as
    not raising the horizon (recorded as an assumption at domain level).
    """
    step = step_m or dem.res
    d = np.arange(step, max_dist_m + step, step)
    az = np.radians(azimuths_deg)[:, None]
    xs = x0 + np.sin(az) * d[None, :]
    ys = y0 + np.cos(az) * d[None, :]
    zs = _sample(dem, xs, ys)
    ang = np.degrees(np.arctan2(zs - z0, d[None, :]))
    ang = np.where(np.isfinite(ang), ang, -90.0)
    return np.maximum(ang.max(axis=1), 0.0)


def sky_view_factor(slope_deg: float, aspect_deg: float, azimuths_deg: np.ndarray,
                    horizon_deg: np.ndarray) -> float:
    """Sky-view factor of a tilted surface (Dozier & Frew 1990, eq. 7 form).

    The per-direction horizon is the larger of the terrain horizon and the
    surface's own self-shading horizon.
    """
    s = np.radians(slope_deg)
    phi = np.radians(azimuths_deg)
    a = np.radians(aspect_deg)
    self_h = np.degrees(np.arctan(-np.tan(s) * np.cos(phi - a)))  # plane rises upslope
    h = np.radians(np.maximum(horizon_deg, self_h))
    hz = np.pi / 2 - h  # horizon zenith angle
    integrand = np.cos(s) * np.sin(hz) ** 2 + np.sin(s) * np.cos(phi - a) * (hz - np.sin(hz) * np.cos(hz))
    return float(np.clip(integrand.mean(), 0.0, 1.0))


def winstral_sx(dem: Raster, x0: float, y0: float, z0: float, directions_deg: np.ndarray,
                dmax_m: float = 300.0) -> np.ndarray:
    """Upwind maximum slope (deg) toward each direction (Winstral et al. 2002 Sx proxy).

    Positive = sheltered from wind from that direction. PROXY ONLY; not a deposition model.
    """
    d = np.arange(dem.res, dmax_m + dem.res, dem.res)
    az = np.radians(directions_deg)[:, None]
    zs = _sample(dem, x0 + np.sin(az) * d, y0 + np.cos(az) * d)
    ang = np.degrees(np.arctan2(zs - z0, d[None, :]))
    ang = np.where(np.isfinite(ang), ang, -90.0)
    return ang.max(axis=1)
