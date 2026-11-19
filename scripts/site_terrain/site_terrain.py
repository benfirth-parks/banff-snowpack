"""Map-derived terrain details of the four study plots (exploratory, 2026-11-19). Writes JSON + raw windows."""
import json, math, sys, warnings, hashlib, datetime as dt
from pathlib import Path
import numpy as np
import rasterio
from rasterio.warp import transform, reproject, Resampling, calculate_default_transform
from rasterio.windows import from_bounds
from rasterio.transform import from_origin
warnings.filterwarnings("ignore")
from snowagent.terrain.dem import Raster
from snowagent.terrain import derive

OUT = Path("data/raw/site_terrain")
PLOTS = {"bow": (-116.47950, 51.70946, 2040), "goat": (-115.754620, 51.089530, 2280),
         "simpson": (-115.98430, 50.98516, 2115), "parker": (-117.10408, 52.19587, 2010)}
MRDEM = "https://canelevation-dem.s3.ca-central-1.amazonaws.com/mrdem-30/mrdem-30-{k}.tif"
UTM = "EPSG:32611"
log = []

def utm_xy(lon, lat):
    x, y = transform("EPSG:4326", UTM, [lon], [lat]); return x[0], y[0]

def read_utm(href, x0, y0, half, res, resampling=Resampling.bilinear, name=None):
    """A window of a remote raster, reprojected to UTM 11N on a grid centred on the plot."""
    n = int(2 * half / res)
    dst_t = from_origin(x0 - half, y0 + half, res, res)
    dst = np.full((n, n), np.nan, dtype="float32")
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        with rasterio.open(href) as src:
            reproject(rasterio.band(src, 1), dst, dst_transform=dst_t, dst_crs=UTM, resampling=resampling,
                      src_nodata=src.nodata, dst_nodata=np.nan)
    if name:
        np.save(OUT / f"{name}.npy", dst)
        log.append({"url": href, "window": f"{name}.npy", "centre_utm11": [round(x0, 1), round(y0, 1)], "half_m": half, "res_m": res,
                    "retrieved_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "sha256": hashlib.sha256(dst.tobytes()).hexdigest()})
    return Raster(dst.astype("float64"), x0 - half, y0 + half, res, UTM, {})

def sun_pos(doy, hour_solar, lat):
    """Solar elevation and azimuth (deg) from day of year and local solar time."""
    dec = math.radians(23.44) * math.sin(2 * math.pi * (284 + doy) / 365)
    ha = math.radians(15 * (hour_solar - 12)); la = math.radians(lat)
    el = math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha))
    az = math.atan2(-math.sin(ha) * math.cos(dec), math.cos(la) * math.sin(dec) - math.sin(la) * math.cos(dec) * math.cos(ha))
    return math.degrees(el), math.degrees(az) % 360

def sun_hours(lat, az, hor, doy):
    lit = pot = 0
    for m in range(0, 24 * 60, 5):
        el, a = sun_pos(doy, m / 60, lat)
        if el > 0:
            pot += 5
            if el > np.interp(a, np.append(az, 360), np.append(hor, hor[0])): lit += 5
    return round(lit / 60, 1), round(pot / 60, 1)

def terrain(name, lon, lat, elev):
    x0, y0 = utm_xy(lon, lat)
    out = {}
    dtm = read_utm(MRDEM.format(k="dtm"), x0, y0, 25000, 30, name=f"{name}_mrdem30_dtm")
    dsm = read_utm(MRDEM.format(k="dsm"), x0, y0, 3000, 30, name=f"{name}_mrdem30_dsm")
    z0 = float(derive._sample(dtm, np.array([x0]), np.array([y0]))[0])
    out["map_elevation_m"], out["stated_elevation_m"] = round(z0), elev
    r, c = dtm.rowcol(x0, y0)
    for rad, tag in ((1, "90m"), (3, "210m")):
        sub = Raster(dtm.data[r - rad - 1:r + rad + 2, c - rad - 1:c + rad + 2], 0, 0, 30, UTM, {})
        dzdx, dzdy = derive.gradients(sub)
        s, a = derive.mean_orientation(dzdx[1:-1, 1:-1], dzdy[1:-1, 1:-1])
        out[f"slope_deg_{tag}"], out[f"faces_deg_{tag}"] = round(s, 1), round(a)
    az = np.arange(0, 360, 5.0)
    hor = derive.horizon(dtm, x0, y0, z0 + 2.0, az, 24000, 30)
    out["horizon_deg_by_10deg"] = [round(float(h), 1) for h in hor[::2]]
    out["terrain_sky_view"] = round(derive.sky_view_factor(0.0, 0.0, az, hor), 3)
    for label, doy in (("dec21", 355), ("feb01", 32), ("mar21", 80), ("apr15", 105)):
        lit, pot = sun_hours(lat, az, hor, doy)
        out[f"sun_hours_{label}"], out[f"sun_hours_flat_horizon_{label}"] = lit, pot
    dirs = np.arange(0, 360, 45.0)
    for dmax in (300, 2000):
        sx = derive.winstral_sx(dtm, x0, y0, z0, dirs, dmax)
        out[f"shelter_deg_{dmax}m"] = {d: round(float(v), 1) for d, v in zip(["N", "NE", "E", "SE", "S", "SW", "W", "NW"], sx)}
    yy, xx = np.mgrid[0:dtm.shape[0], 0:dtm.shape[1]]
    dist = np.hypot(yy - r, xx - c) * 30
    for rad in (150, 500, 2000):
        ring = dtm.data[dist <= rad]
        out[f"height_vs_surroundings_m_{rad}m"] = round(z0 - float(np.nanmean(ring)), 1)
    out["relief_within_2km_m"] = [round(float(np.nanmin(dtm.data[dist <= 2000]))), round(float(np.nanmax(dtm.data[dist <= 2000])))]
    out["surface_minus_ground_m_30m_at_plot"] = round(float(derive._sample(dsm, np.array([x0]), np.array([y0]))[0]) - z0, 1)
    return out

if __name__ == "__main__":
    res = {}
    for name, (lon, lat, elev) in PLOTS.items():
        res[name] = terrain(name, lon, lat, elev)
        print(name, json.dumps(res[name]))
    (OUT / "terrain_mrdem30.json").write_text(json.dumps(res, indent=1))
    with open(OUT / "manifest.jsonl", "a") as f:
        for rec in log: f.write(json.dumps(rec) + "\n")
