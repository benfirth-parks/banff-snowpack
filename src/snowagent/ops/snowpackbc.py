"""SnowpackBC, an outside SNOWPACK forecast, as a blind-test competitor (ADR-112).

snowpackbc.com runs SNOWPACK through the AWSOME framework (gitlab.com/avalanche-warning) for Banff, Rogers Pass,
Whistler and the Mt St Anne area: station runs driven by measured weather, and runs on a weather-model grid. It keeps
only the season in progress and replaces its forecast part every 6 hours, so what it issued on a given day is lost
unless it is saved that day. The daily update (``update fetch``) saves one snapshot a day of a few points near our
plots: the last ``DAYS_BEFORE`` days of its flat-field profiles and its forecast beyond them, unchanged (gzip, with
the URL, retrieval time and sha256 in ``index.jsonl``), in ``archive/snowpackbc/<season>/``.

The blind test (``ops.blind_test``) then scores them on this winter's pits beside standard SNOWPACK, as issued: a
snapshot counts for a case only if it was saved by the case's as-of time (``next_pit`` cases: by the pit time,
since there our agents also get measured weather up to the pit). Their profile is turned into a prediction by
standard SNOWPACK's own output step (layer merging, presence, depth spread), so the two differ only in the run.
Research only: SnowpackBC's profiles never reach the public site."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

BASE = "https://snowpackbc.com/nivix/xs"
ARCHIVE = Path("archive/snowpackbc")
PARAMS = "grainshape,hardness,density,grainsize"
DAYS_BEFORE = 3  # days of profiles before the last forecast step (the site's own query window)
MIN_GAP_H = 6.0  # a point saved less than this long ago is not fetched again (a second fetch the same day)
MAX_AGE_H = 72.0  # a snapshot older than this at a case's issue time is not used for it
USER_AGENT = "banff-snowpack-research (daily, one request per point)"
SOURCE = "SnowpackBC (snowpackbc.com, AWSOME SNOWPACK)"


@dataclass(frozen=True)
class Point:
    run: str  # "stations" or "grid": one blind-test competitor each
    area: str  # SnowpackBC's run name (its xsnowpack.zarr folder)
    location: str
    site_code: str | None  # the lab plot it is scored at; None: saved only (no lab plot or pits yet)
    note: str


# Chosen 2026-11-18 from the site's map data (dcfg/operational.json and the areas' geojson): the station runs at
# our plots' stations, and the grid point within 3 km of each plot whose elevation is closest to the plot's.
POINTS: tuple[Point, ...] = (
    Point("stations", "banff_stn", "Bow_Summit", "BOW", "Bow Summit station, 2040 m (our plot 2040 m)"),
    Point("stations", "banff_stn", "Lookout", "GOAT", "Lookout station, 2640 m (Goat's Eye plot 2280 m)"),
    Point("stations", "rogers_stn", "Fidelity", None, "Fidelity station, 1905 m (no lab plot yet)"),
    Point("grid", "banff_grid", "B_0035_0003", "BOW", "grid point 1.8 km from Bow Summit, 2059 m"),
    Point("grid", "banff_grid", "B_0007_0023", "GOAT", "grid point 1.1 km from Goat's Eye, 2253 m"),
    Point("grid", "banff_grid", "B_0002_0016", "SIMP", "grid point 1.5 km from Simpson, 1930 m (plot 2115 m)"),
)
AGENTS = {"stations": ("snowpackbc_stations", "SnowpackBC station runs"),
          "grid": ("snowpackbc_grid", "SnowpackBC weather-model grid")}


def url(p: Point) -> str:
    return (f"{BASE}?file=/opt/awsome/{p.area}/xsnowpack.zarr&location={p.location}&inclination=0&azimuth=0"
            f"&params={PARAMS}&profile=1&days_before={DAYS_BEFORE}")


def _utc(s: Any) -> pd.Timestamp:
    t = pd.Timestamp(s)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def check_body(d: Any, p: Point) -> None:
    """Raise ValueError unless ``d`` is a profile answer for ``p`` (times, heights and every parameter, aligned)."""
    if not isinstance(d, dict) or "error" in d:
        raise ValueError(f"not a profile: {str(d.get('error') if isinstance(d, dict) else d)[:200]}")
    t, tops, params = d.get("time"), d.get("tops"), d.get("params")
    if not isinstance(t, list) or not t or not isinstance(tops, list) or not isinstance(params, dict):
        raise ValueError("profile answer without time, tops or params")
    if len(tops) != len(t) or any(len(params.get(k) or []) != len(t) for k in PARAMS.split(",")):
        raise ValueError("profile answer with misaligned times, tops or params")
    if (d.get("meta") or {}).get("station") != p.location:
        raise ValueError(f"profile answer for {(d.get('meta') or {}).get('station')!r}, asked for {p.location!r}")


def _index(archive: Path, season: str) -> list[dict]:
    f = Path(archive) / season / "index.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.is_file() else []


def fetch_snapshots(now: pd.Timestamp | None = None, archive: Path = ARCHIVE, get=None, points=POINTS) -> dict:
    """Save one snapshot per point (skipped when one was saved less than ``MIN_GAP_H`` ago). A failed point is a
    warning, not a failed step: the outside forecast is a comparison, never an input."""
    from snowagent.web.build import current_season_year

    now = now or pd.Timestamp.now(tz="UTC")
    y = current_season_year(now)
    season = f"{y}-{y + 1}"
    if get is None:
        import requests

        def get(u: str):
            return requests.get(u, timeout=90, headers={"User-Agent": USER_AGENT})

    index = _index(archive, season)
    last: dict[tuple[str, str], pd.Timestamp] = {}
    for r in index:
        k = (r["area"], r["location"])
        last[k] = max(last.get(k, _utc(r["fetched_utc"])), _utc(r["fetched_utc"]))
    saved, skipped, errors = [], [], []
    for p in points:
        if (p.area, p.location) in last and (now - last[(p.area, p.location)]) < pd.Timedelta(hours=MIN_GAP_H):
            skipped.append(f"{p.area}/{p.location}")
            continue
        u = url(p)
        try:
            r = get(u)
            r.raise_for_status()
            body = r.content
            d = json.loads(body)
            check_body(d, p)
        except Exception as exc:  # noqa: BLE001  network, HTTP or format: one warning per point
            errors.append(f"{p.area}/{p.location}: {type(exc).__name__}: {str(exc)[:160]}")
            continue
        stamp = now.strftime("%Y%m%dT%H%MZ")
        rel = Path(season) / p.area / p.location / f"{stamp}.json.gz"
        dst = Path(archive) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(gzip.compress(body, mtime=0))
        t = d["time"]
        rec = {"fetched_utc": now.isoformat(timespec="seconds"), "area": p.area, "location": p.location,
               "url": u, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body), "path": rel.as_posix(),
               "first_utc": pd.Timestamp(t[0], unit="s", tz="UTC").isoformat(),
               "last_utc": pd.Timestamp(t[-1], unit="s", tz="UTC").isoformat()}
        with (Path(archive) / season / "index.jsonl").open("a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        saved.append(f"{p.area}/{p.location}")
    warnings = []
    if errors:
        warnings.append({"level": "info", "source": "snowpackbc",
                         "message": (f"SnowpackBC: {len(errors)} of {len(points)} points not saved "
                                     f"(blind-test comparison only): " + "; ".join(errors))[:600]})
    return {"season": season, "saved": saved, "skipped": skipped, "errors": errors, "warnings": warnings}


# ------------------------------------------------------------------------------------------------ reading


def snapshots(archive: Path, season: str, p: Point) -> list[tuple[pd.Timestamp, Path]]:
    """The point's saved snapshots, oldest first (retrieval time, file)."""
    out = [(_utc(r["fetched_utc"]), Path(archive) / r["path"]) for r in _index(archive, season)
           if r["area"] == p.area and r["location"] == p.location]
    return sorted((t, f) for t, f in out if f.is_file())


def first_snapshot(archive: Path, season: str) -> pd.Timestamp | None:
    ts = [_utc(r["fetched_utc"]) for r in _index(archive, season)]
    return min(ts) if ts else None


def issued_snapshot(snaps: list[tuple[pd.Timestamp, Path]], issue: pd.Timestamp,
                    max_age_h: float = MAX_AGE_H) -> tuple[pd.Timestamp, Path] | None:
    """The newest snapshot saved at or before ``issue``, if not older than ``max_age_h``."""
    ok = [s for s in snaps if s[0] <= issue]
    if not ok or issue - ok[-1][0] > pd.Timedelta(hours=max_age_h):
        return None
    return ok[-1]


def load_snapshot(path: Path) -> dict:
    return json.loads(gzip.decompress(Path(path).read_bytes()))


def profile_at(d: dict, valid: pd.Timestamp, issued: pd.Timestamp):
    """The snapshot's profile at the last step at or before ``valid`` (its last forecast step when ``valid`` lies
    beyond it), as an engine result, or None when the snapshot starts after ``valid`` or holds no profile then.
    Heights above ground in cm (``tops``: layer tops, ground up) become depths from the surface in m."""
    from snowagent.lab.agents.snowpack import EngineLayer, EngineResult
    from snowagent.lab.schemas.profile import GRAIN_VOCABULARY

    times = [pd.Timestamp(x, unit="s", tz="UTC") for x in d["time"]]
    idx = [i for i, t in enumerate(times) if t <= valid]
    if not idx:
        return None
    i = idx[-1]
    tops = [float(x) for x in (d["tops"][i] or [])]
    hs_cm = d.get("hs", [None] * len(times))[i]
    hs_cm = float(hs_cm) if hs_cm is not None else (tops[-1] if tops else 0.0)
    pr = {k: (d["params"].get(k) or [[]] * len(times))[i] or [] for k in PARAMS.split(",")}
    layers = []
    for j in range(len(tops) - 1, -1, -1):  # surface first
        top, bottom = (hs_cm - tops[j]) / 100.0, (hs_cm - (tops[j - 1] if j else 0.0)) / 100.0
        if bottom - top <= 0:
            continue
        g, h, rho = (pr[k][j] if j < len(pr[k]) else None for k in ("grainshape", "hardness", "density"))
        layers.append(EngineLayer(top_depth_m=max(0.0, top), bottom_depth_m=bottom,
                                  grain=g if g in GRAIN_VOCABULARY else None,
                                  hardness_index=None if h is None else min(6.5, max(0.5, float(h))),
                                  density_kg_m3=None if rho is None else float(rho)))
    lag = (valid - times[i]).total_seconds() / 3600.0
    return EngineResult(source="site_run_reuse", snowpack_version=SOURCE, snow_depth_m=max(0.0, hs_cm / 100.0),
                        layers=layers, profile_lag_h=round(lag, 2),
                        notes={"outside": "snowpackbc", "issued_utc": issued.isoformat(),
                               "profile_utc": times[i].isoformat(), "forecast": bool(times[i] > issued)})
