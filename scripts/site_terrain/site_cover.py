"""Land cover (ESA WorldCover 10 m) and tree height (Meta/WRI 1 m canopy height) around the four plots."""
import json, math, sys, warnings
from pathlib import Path
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import site_terrain as st
from rasterio.warp import Resampling
from snowagent.terrain import derive

WC = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{t}_Map.tif"
WC_CLASS = {10: "tree", 20: "shrub", 30: "grass", 40: "crop", 50: "built", 60: "bare", 70: "snow_ice", 80: "water", 90: "wetland", 95: "mangrove", 100: "moss_lichen"}
CHM = "https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/alsgedi_global_v6_float/chm/{t}.tif"

def wc_tile(lat, lon):
    la, lo = int(math.floor(lat / 3) * 3), int(math.floor(lon / 3) * 3)
    return f"{'N' if la >= 0 else 'S'}{abs(la):02d}{'E' if lo >= 0 else 'W'}{abs(lo):03d}"

def chm_tile(lon, lat):
    for f in json.load(open(st.OUT / "meta_chm_tiles.geojson"))["features"]:
        xs = [p[0] for p in f["geometry"]["coordinates"][0]]; ys = [p[1] for p in f["geometry"]["coordinates"][0]]
        if min(xs) <= lon <= max(xs) and min(ys) <= lat <= max(ys):
            return f["properties"]["tile"]

def cover(name, lon, lat, elev):
    x0, y0 = st.utm_xy(lon, lat)
    out = {}
    wc = st.read_utm(WC.format(t=wc_tile(lat, lon)), x0, y0, 500, 10, Resampling.nearest, name=f"{name}_worldcover10")
    n = wc.shape[0]; yy, xx = np.mgrid[0:n, 0:n]; dist = np.hypot(yy - n / 2 + 0.5, xx - n / 2 + 0.5) * 10
    r, c = wc.rowcol(x0, y0)
    out["land_cover_at_plot"] = WC_CLASS.get(int(wc.data[r, c]), "?")
    for rad in (50, 100, 250, 500):
        v = wc.data[dist <= rad]; v = v[np.isfinite(v)]
        out[f"land_cover_share_{rad}m"] = {WC_CLASS.get(int(k), "?"): round(float((v == k).mean()), 2) for k in np.unique(v)}
    t = chm_tile(lon, lat)
    out["tree_height_tile"] = t
    ch = st.read_utm(CHM.format(t=t), x0, y0, 150, 1, Resampling.bilinear, name=f"{name}_meta_chm1m")
    h = np.nan_to_num(ch.data, nan=0.0)
    n = h.shape[0]; yy, xx = np.mgrid[0:n, 0:n]
    dy, dx = (n / 2 - 0.5 - yy), (xx - n / 2 + 0.5)  # north, east offsets in m
    dist = np.hypot(dy, dx)
    tree = h >= 3.0
    out["canopy_height_at_plot_m"] = round(float(h[dist <= 3].mean()), 1)
    out["nearest_tree_3m_plus_m"] = round(float(dist[tree].min()), 0) if tree.any() else None
    for rad in (15, 30, 60, 120):
        m = dist <= rad
        out[f"tree_cover_share_{rad}m"] = round(float(tree[m].mean()), 2)
    near = tree & (dist <= 60)
    out["tree_height_m_within_60m_median_p90"] = [round(float(np.median(h[near])), 1), round(float(np.percentile(h[near], 90)), 1)] if near.any() else None
    # horizon made by the trees, per 5 degrees: steepest angle to any canopy top within 150 m (observer 1.5 m up)
    az = np.arange(0, 360, 5.0)
    bearing = (np.degrees(np.arctan2(dx, dy)) % 360)
    ang = np.degrees(np.arctan2(h - 1.5, np.maximum(dist, 1.0)))
    hor_tree = np.zeros(len(az))
    for i, a in enumerate(az):
        m = (np.abs((bearing - a + 180) % 360 - 180) <= 2.5) & (dist >= 2) & (dist <= 150) & tree
        hor_tree[i] = max(float(ang[m].max()), 0.0) if m.any() else 0.0
    ter = json.load(open(st.OUT / "terrain_mrdem30.json"))[name]
    hor_ter = np.interp(az, np.arange(0, 360, 10.0), ter["horizon_deg_by_10deg"], period=360)
    both = np.maximum(hor_tree, hor_ter)
    out["tree_horizon_deg_by_10deg"] = [round(float(v), 1) for v in hor_tree[::2]]
    out["sky_view_trees_only"] = round(derive.sky_view_factor(0.0, 0.0, az, hor_tree), 3)
    out["sky_view_trees_and_terrain"] = round(derive.sky_view_factor(0.0, 0.0, az, both), 3)
    for label, doy in (("dec21", 355), ("feb01", 32), ("mar21", 80), ("apr15", 105)):
        out[f"sun_hours_with_trees_{label}"] = st.sun_hours(lat, az, both, doy)[0]
    return out

if __name__ == "__main__":
    res = {}
    for name, (lon, lat, elev) in st.PLOTS.items():
        res[name] = cover(name, lon, lat, elev)
        print(name, json.dumps(res[name]))
    (st.OUT / "cover.json").write_text(json.dumps(res, indent=1))
    with open(st.OUT / "manifest.jsonl", "a") as f:
        for rec in st.log: f.write(json.dumps(rec) + "\n")
