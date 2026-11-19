"""Spring melt-out bracket at each plot from Sentinel-2 L2A scene classification (20 m), 2017-2026."""
from pathlib import Path
import json, sys, warnings, urllib.request, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import numpy as np, rasterio
from rasterio.warp import transform
from rasterio.windows import Window
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import site_terrain as st
API = "https://earth-search.aws.element84.com/v1/search"
def items(lon, lat, year):
    out, body = [], {"collections": ["sentinel-2-l2a"], "intersects": {"type": "Point", "coordinates": [lon, lat]},
                     "datetime": f"{year}-04-01T00:00:00Z/{year}-08-10T23:59:59Z", "limit": 200}
    d = json.load(urllib.request.urlopen(urllib.request.Request(API, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}), timeout=60))
    for f in d["features"]:
        out.append((f["properties"]["datetime"][:10], f["assets"]["scl"]["href"], f["id"]))
    return out
def read(args):
    date, href, lon, lat = args
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="2"):
            with rasterio.open(href) as src:
                x, y = transform("EPSG:4326", src.crs, [lon], [lat]); r, c = src.index(x[0], y[0])
                a = src.read(1, window=Window(c - 1, r - 1, 3, 3))
        return date, a.ravel().tolist()
    except Exception as e:
        return date, None
if __name__ == "__main__":
    res = {}
    for name, (lon, lat, _) in st.PLOTS.items():
        jobs = []
        for y in range(2017, 2027):
            try: jobs += [(d, h, lon, lat) for d, h, _ in items(lon, lat, y)]
            except Exception as e: print(name, y, "search failed", e)
        with ThreadPoolExecutor(24) as ex: got = list(ex.map(read, jobs))
        obs = {}
        for date, px in got:
            if not px or len(px) != 9: continue
            px = np.array(px); clear = np.isin(px, [4, 5, 6, 7, 11, 2])
            if clear.sum() < 7 or px[4] not in (4, 5, 6, 7, 11, 2): continue
            obs[date] = {"snow": float((px == 11).mean()), "centre": int(px[4])}
        yrs = {}
        for y in range(2017, 2027):
            ds = sorted(d for d in obs if d.startswith(str(y)))
            snow = [d for d in ds if obs[d]["snow"] >= 0.5]
            if not ds or not snow: yrs[y] = {"clear_views": len(ds)}; continue
            # melt-out: the first of two bare clear views in a row (a lone "snow" view later in summer is cloud or a dusting)
            first = next((d for i, d in enumerate(ds[:-1]) if obs[d]["snow"] == 0.0 and obs[ds[i + 1]]["snow"] == 0.0
                          and any(s_ < d for s_ in snow)), None)
            last = max((s_ for s_ in snow if first is None or s_ < first), default=None)
            yrs[y] = {"clear_views": len(ds), "last_snow": last, "first_bare": first,
                      "later_snow_views": sum(1 for s_ in snow if first and s_ > first)}
        res[name] = {"scenes": len(jobs), "read": sum(1 for _, p in got if p), "years": yrs}
        print(name, json.dumps(res[name]))
    (st.OUT / "s2_meltout.json").write_text(json.dumps(res, indent=1))
    with open(st.OUT / "manifest.jsonl", "a") as f:
        f.write(json.dumps({"url": API, "collection": "sentinel-2-l2a", "asset": "scl 3x3 pixels at each plot, Apr 1 to Aug 10, 2017-2026", "window": "s2_meltout.json", "retrieved_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}) + "\n")
