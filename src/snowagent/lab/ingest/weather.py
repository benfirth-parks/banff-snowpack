"""QC'd station records (``ingest.fts360.load_station``: FTS360 API files, logger exports, dashboard history) ->
the lab's canonical hourly weather per site (ADR-056).

Each variable takes its stations in the order the plot's forcing recipe lists them (config/plot_forcing.yaml:
``ta``, ``rh``, ``psum``, ``hs_check``, ``swe_check``; wind from config/lab.yaml). Per hour the first station whose
value is QC "ok" supplies it; with none ok, the first "suspect" value is kept with its flag; a "bad" value is not
used (null, flagged bad, the station named; the raw file keeps it); otherwise null and "missing". Apart from the
optional ERA5 backfill below no source fills a gap, and no value is moved to the plot elevation: values are as
measured at the named station (or the ERA5 cell). The one exception is the Jasper test set (ADR-117), whose
stations stand 115 to 1000 m above its plots: there temperature and humidity are moved to the plot's height and
sourced ``<station>_moved_from_<height>m`` (``site_weather(station_heights=...)``). Radiation and pressure are not measured at these plots. With an ERA5 backfill (``fill``; config/lab.yaml ``weather``, owner
2026-10-05: "FTS360, else ERA5 backfill") an hour and variable no station supplied (missing, or failed QC) takes the
ERA5 nearest-cell value, flagged ``filled`` and sourced ``era5_cell_<height>m``: named, never silent, and the
station's bad value stays in the raw file. Without a fill those values stay null. The hourly index runs from the
first to the last hour any of the site's stations reported, so gaps are explicit rows; with a fill and ``start``
(the start of the reanalysis seasons, ADR-076) it begins at ``start`` when that is earlier, so the seasons before
the stations get ERA5 hours (filled) and explicit missing rows where the ERA5 cache has no month.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd

from snowagent.lab.schemas.common import AvailabilityAssumption, QualityFlag
from snowagent.lab.schemas.site import Site
from snowagent.lab.schemas.weather import SEVERITY, WEATHER_VARIABLES, WeatherRecord

SOURCE_ID = "plot_stations"
# canonical variable -> (plot_forcing.yaml recipe key, or "wind" for config/lab.yaml; station column; converter)
RECIPES: dict[str, tuple[str, str, Callable[[pd.Series], pd.Series]]] = {
    "air_temperature_k": ("ta", "ta_k", lambda s: s),
    "relative_humidity_frac": ("rh", "rh_frac", lambda s: s),
    "precipitation_mm": ("psum", "psum_1h_mm", lambda s: s),
    "wind_speed_ms": ("wind", "vw_ms", lambda s: s),
    "wind_direction_deg": ("wind", "dw_deg", lambda s: s),
    "snow_depth_m": ("hs_check", "hs_m", lambda s: s),
    "swe_mm": ("swe_check", "swe_mm", lambda s: s),
}
StationLoader = Callable[[str], pd.DataFrame]


FTS_RAW = "data/raw/fts360"


def station_loader(source_root: Path, station_dir: str = FTS_RAW) -> StationLoader:
    """``load_station`` over a checkout's data directories (read only). ``station_dir`` (relative to the checkout)
    holds the stations' files: the FTS360 API files for Banff, the converted tables of the new sites for the Jasper
    test set (``config/new_sites.yaml`` ``station_dir``, ADR-108, ADR-117)."""
    from snowagent.ingest.fts360 import load_station

    root = Path(source_root)

    def load(key: str) -> pd.DataFrame:
        return load_station(key, root / station_dir, root / "data/interim/byk_export",
                            root / "data/interim/fts_dashboard")

    return load


def recipe_stations(site: Site, plot: dict) -> dict[str, list[str]]:
    """Canonical variable -> station keys in priority order."""
    return {var: list(site.wind_stations if key == "wind" else plot.get(key) or [])
            for var, (key, _c, _f) in RECIPES.items()}


def station_files(source_root: Path, stations: set[str], station_dir: str = FTS_RAW) -> list[Path]:
    """The raw and interim files ``load_station`` reads for these stations (for hashing and the immutability check)."""
    root = Path(source_root)
    out: list[Path] = []
    for key in sorted(stations):
        out += sorted((root / station_dir / key).glob("*.csv"))
        out += [f for f in (root / "data/interim/byk_export" / f"{key}.csv",
                            root / "data/interim/fts_dashboard" / f"{key}.csv") if f.exists()]
    return out


MOVED_SOURCE = "{key}_moved_from_{m}m"  # a station value moved to the plot's height (ADR-117)


def to_plot_height(d: pd.DataFrame, dz_m: float) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    """A station's air temperature and humidity moved ``dz_m`` metres (plot minus station height), the way the
    baseline assembler moves them (``baseline.assemble``): temperature by the forcing builder's lapse rate, humidity
    keeping the dew point at the station's own temperature. Humidity is moved only in hours with a station
    temperature that passed QC (ok or suspect); other hours keep the measured humidity. Returns the frame and, per
    column, the hours that were moved."""
    from snowagent.spatial_forcing.builder import ForcingConfig, _dewpoint_c, _es

    lapse = ForcingConfig().lapse_rate_k_per_m
    d = d.copy()
    moved: dict[str, pd.Series] = {}
    if "ta_k" not in d:
        return d, moved
    ta = d["ta_k"].astype(float)
    ta_qc = d["ta_k_qc"] if "ta_k_qc" in d else pd.Series("ok", index=d.index)
    ta_new = ta + lapse * dz_m
    moved["ta_k"] = ta.notna()
    d["ta_k"] = ta_new
    if "rh_frac" in d:
        rh = d["rh_frac"].astype(float)
        good = ta.notna() & ta_qc.isin(["ok", "suspect"]) & rh.notna()
        tc, tn = (ta - 273.15).to_numpy(), (ta_new - 273.15).to_numpy()
        with np.errstate(invalid="ignore", divide="ignore"):
            td = np.minimum(_dewpoint_c(tc, rh.clip(1e-3, 1.0).to_numpy()), tc)
            rh_new = np.clip(_es(td) / _es(tn), 0.05, 1.0)
        d["rh_frac"] = rh.where(~good, pd.Series(rh_new, index=d.index))
        moved["rh_frac"] = good
    return d, moved


def site_weather(site: Site, plot: dict, load: StationLoader, provenance_id: str,
                 fill: Callable[[pd.DatetimeIndex], tuple[pd.DataFrame, str]] | None = None,
                 start: pd.Timestamp | None = None,
                 station_heights: dict[str, float] | None = None) -> tuple[pd.DataFrame, dict]:
    """Canonical hourly table of one site (one row per hour; columns as ``WeatherRecord``, with ``<var>_source`` and
    ``<var>_qc`` flattened) and a summary. ``fill(index)`` returns the backfill series (canonical variable columns)
    and its source label; ``start`` (with a fill only) moves the first hour back to it.

    ``station_heights`` (station key -> metres; the Jasper test set only, ADR-117): temperature and humidity of
    these stations are moved to the plot's height before they are used, each such value sourced
    ``<key>_moved_from_<height>m`` (``to_plot_height``). Banff's plots pass none, so their values stay as measured
    at the named station, as above."""
    recipes = recipe_stations(site, plot)
    data: dict[str, pd.DataFrame] = {}
    moved: dict[tuple[str, str], pd.Series] = {}
    labels: dict[str, str] = {}
    for key in sorted({k for ks in recipes.values() for k in ks}):
        d = load(key)
        if not d.empty:
            d = d.set_index("time_utc")
            if station_heights and key in station_heights:
                h = float(station_heights[key])
                d, m = to_plot_height(d, float(site.elevation_m) - h)
                moved |= {(key, col): s for col, s in m.items()}
                labels[key] = MOVED_SOURCE.format(key=key, m=f"{h:g}")
            data[key] = d
    starts = [d.index.min() for d in data.values()]
    ends = [d.index.max() for d in data.values()]
    if starts and fill is not None and start is not None:
        starts.append(pd.Timestamp(start).tz_convert("UTC").ceil("h"))
    idx = (pd.date_range(min(starts), max(ends), freq="h", name="observed_at") if data
           else pd.DatetimeIndex([], tz="UTC", name="observed_at"))
    out = pd.DataFrame(index=idx)
    summary: dict = {"stations": recipes, "hours": len(idx), "variables": {}}
    filled, label = fill(idx) if fill is not None and len(idx) else (None, None)
    if label:
        summary["backfill"] = label
    for var in WEATHER_VARIABLES:
        value = pd.Series(np.nan, index=idx)
        source = pd.Series(None, index=idx, dtype=object)
        qc = pd.Series(QualityFlag.missing.value, index=idx, dtype=object)
        _key, col, conv = RECIPES.get(var, (None, None, None))
        for flag in ("ok", "suspect", "bad") if var in RECIPES else ():
            for key in recipes[var]:
                d = data.get(key)
                if d is None or col not in d:
                    continue
                v = conv(d[col].reindex(idx).astype(float))
                f = d[f"{col}_qc"].reindex(idx) if f"{col}_qc" in d else pd.Series("ok", index=idx)
                take = (qc == QualityFlag.missing.value) & (f == flag)
                if flag != "bad":  # a bad value is named and flagged, the value itself is not used
                    take &= v.notna()
                    value[take] = v[take]
                source[take] = key
                mv = moved.get((key, col))
                if mv is not None:
                    source[take & mv.reindex(idx, fill_value=False).astype(bool)] = labels[key]
                qc[take] = flag
        if filled is not None and var in filled:
            take = qc.isin([QualityFlag.missing.value, QualityFlag.bad.value]) & filled[var].notna()
            value[take] = filled[var][take]
            source[take] = label
            qc[take] = QualityFlag.filled.value
        out[var], out[f"{var}_source"], out[f"{var}_qc"] = value, source, qc
        if var in RECIPES or filled is not None:
            summary["variables"][var] = {k: int(n) for k, n in qc.value_counts().items()}
    sev = {f.value: i for i, f in enumerate(SEVERITY)}
    qcols = [f"{v}_qc" for v in WEATHER_VARIABLES]
    ranks = out[qcols].apply(lambda c: c.map(sev)).max(axis=1)
    inv = {i: f for f, i in sev.items()}
    out["quality_flag"] = ranks.map(inv).fillna(QualityFlag.missing.value) if len(out) else pd.Series(dtype=object)
    out = out.reset_index()
    out.insert(0, "site_code", site.code.value)
    out.insert(2, "source_id", SOURCE_ID)
    out.insert(3, "kind", "observed")
    out.insert(4, "issued_at", pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns, UTC]"))
    out.insert(5, "source_recorded_at", pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns, UTC]"))
    out.insert(6, "availability_assumption", AvailabilityAssumption.observed_at.value)
    out["provenance_id"] = provenance_id
    if len(out):
        summary["first_hour"], summary["last_hour"] = str(idx.min()), str(idx.max())
    return out, summary


def record_from_row(row: dict) -> WeatherRecord:
    """One canonical table row -> ``WeatherRecord`` (validation of the stored form)."""
    def val(x):
        return None if x is None or (isinstance(x, float) and np.isnan(x)) or x is pd.NaT else x

    sources = {v: row[f"{v}_source"] for v in WEATHER_VARIABLES if val(row.get(f"{v}_source")) is not None}
    qc = {v: row[f"{v}_qc"] for v in WEATHER_VARIABLES if row.get(f"{v}_qc") != QualityFlag.missing.value}
    return WeatherRecord(
        site_code=row["site_code"], observed_at=row["observed_at"], source_id=row["source_id"], kind=row["kind"],
        issued_at=val(row.get("issued_at")), source_recorded_at=val(row.get("source_recorded_at")),
        availability_assumption=row["availability_assumption"], sources=sources, qc=qc,
        quality_flag=row["quality_flag"], provenance_id=row["provenance_id"],
        **{v: val(row.get(v)) for v in WEATHER_VARIABLES})


def validate_frame(df: pd.DataFrame) -> int:
    """Validate every row as a ``WeatherRecord`` (raises on the first invalid one); returns the row count."""
    for row in df.to_dict("records"):
        record_from_row(row)
    return len(df)
