"""Avalanche Canada Mountain Information Network (MIN): public field reports near the study plots (ADR-037, ADR-164).

Open API, no key (ADR-164: the feed moved for 2026-27; the old ``api.avalanche.ca/min/en/submissions`` list stopped
receiving reports in October 2026): ``GET https://avcan-services-api.prod.avalanche.ca/min/submissions?fromdate=
&todate=&bbox=`` lists every report in a window of at most 31 days, newest first, with no paging (dates are local,
so windows overlap by a day and ids are de-duplicated), and ``GET .../submissions/<id>`` returns one report as JSON.
Avalanche Canada's API page calls the endpoint undocumented and subject to change. Reports within ``RADIUS_KM`` of
any study plot are kept (the Banff plots of config/plot_forcing.yaml and the site plots of config/new_sites.yaml).

Two report formats come back: the old one (``observations`` with structured snowpack, weather and avalanche parts)
for reports filed before the move, and the 2026-27 trip report (``schemaVersions.dataSchemaId`` ``data_3...``: a
local ``date``, free-text ``comments``, an ``instability`` list and ``avalanches``). Both parse to the same record.

Raw data: each report's JSON is archived unchanged (gzipped) as ``archive/min/<year>/<id>_<updated>.json.gz``. MIN
reports can be edited, so a report is re-read while it is recent and a new version is archived only when its bytes
change; older versions stay. Every file has a manifest line (URL, retrieval time, sha256, bytes). ``state.json``
records which date windows have been scanned and from which feed. Parsed records (``obs.public.PublicObservation``)
drop usernames and account ids, which remain in the raw files only.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import yaml

from snowagent.obs.public import PublicAvalanche, PublicObservation, PublicTest

API = "https://avcan-services-api.prod.avalanche.ca/min/submissions"
OLD_API = "https://api.avalanche.ca/min/en/submissions"  # until 2026-10 (ADR-037); kept for the manifest's old URLs
PAGE_URL = "https://avalanche.ca/mountain-information-network/submissions/{id}"
ARCHIVE = Path("archive/min")
RADIUS_KM = 15.0
REFETCH_DAYS = 14  # re-read reports this recent: late submissions and edits
PAUSE_S = 0.2  # between requests (open API; be polite)
HEADERS = {"User-Agent": "banff-snowpack (snowpack-structure research prototype; contact via repository owner)"}


NEW_SITES = Path("config/new_sites.yaml")
BBOX_PAD_DEG = 0.25  # the listing's bounding box: the plots' box plus this (> RADIUS_KM at these latitudes)


def _plots(cfg: Path, new_sites: Path | None) -> dict[str, dict]:
    plots = dict(yaml.safe_load(Path(cfg).read_text())["plots"])
    if new_sites is not None and Path(new_sites).exists():  # the site's plots outside Banff (ADR-139)
        ns = yaml.safe_load(Path(new_sites).read_text())
        for k in (ns.get("site") or {}).get("plots") or {}:
            plots.setdefault(k, ns["plots"][k])
    return plots


def plot_points(cfg: Path = Path("config/plot_forcing.yaml"), new_sites: Path | None = NEW_SITES
                ) -> dict[str, tuple[float, float]]:
    return {k: (float(v["lat"]), float(v["lon"])) for k, v in _plots(cfg, new_sites).items()}


def plot_elevations(cfg: Path = Path("config/plot_forcing.yaml"), new_sites: Path | None = NEW_SITES
                    ) -> dict[str, float]:
    return {k: float(v["elevation_m"]) for k, v in _plots(cfg, new_sites).items() if v.get("elevation_m")}


def bbox(points: dict[str, tuple[float, float]], pad_deg: float = BBOX_PAD_DEG) -> str:
    """``minLon,minLat,maxLon,maxLat`` around the plots."""
    lats, lons = [p[0] for p in points.values()], [p[1] for p in points.values()]
    return f"{min(lons) - pad_deg:.3f},{min(lats) - pad_deg:.3f},{max(lons) + pad_deg:.3f},{max(lats) + pad_deg:.3f}"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def distances(lat: float, lon: float, points: dict[str, tuple[float, float]], radius_km: float = RADIUS_KM
              ) -> dict[str, float]:
    """Plots within ``radius_km`` of a location (km, 0.1 resolution)."""
    out = {}
    for k, (plat, plon) in points.items():
        d = haversine_km(lat, lon, plat, plon)
        if d <= radius_km:
            out[k] = round(d, 1)
    return out


def list_window(start: date, end: date, session: requests.Session, box: str | None = None) -> Iterator[dict]:
    """All list items with observation date in [start, end] (API dates, inclusive; at most 31 days)."""
    params = {"fromdate": start.isoformat(), "todate": end.isoformat()}
    if box:
        params["bbox"] = box
    r = session.get(API, params=params, timeout=60)
    r.raise_for_status()
    d = r.json()
    items = d["items"]
    yield from (items["data"] if isinstance(items, dict) else items)  # old feed: {"data": [...]}; new: [...]


def item_id(it: dict) -> str | None:
    return it.get("submissionID") or it.get("id")


def item_day(it: dict) -> date | None:
    """A report's observation day: the old format's ``datetime``, the trip report's local ``date``."""
    v = it.get("datetime") or it.get("date")
    return pd.Timestamp(v).date() if v else None


def _manifest(archive_dir: Path) -> list[dict]:
    m = Path(archive_dir) / "manifest.jsonl"
    return [json.loads(x) for x in m.read_text().splitlines() if x.strip()] if m.exists() else []


def latest_versions(archive_dir: Path = ARCHIVE) -> dict[str, dict]:
    """id -> manifest record of the newest archived version."""
    out: dict[str, dict] = {}
    for rec in _manifest(archive_dir):
        if rec["id"] not in out or rec["retrieved_utc"] >= out[rec["id"]]["retrieved_utc"]:
            out[rec["id"]] = rec
    return out


def _compact(ts: str | None) -> str:
    return pd.Timestamp(ts).strftime("%Y%m%dT%H%M%S") if ts else "na"


def archive_report(raw: bytes, url: str, archive_dir: Path = ARCHIVE, known: dict[str, dict] | None = None) -> dict:
    """Archive one report's bytes unless identical to the newest archived version of the same id."""
    archive_dir = Path(archive_dir)
    sha = hashlib.sha256(raw).hexdigest()
    obj = json.loads(raw)
    sid = obj["submissionID"]
    known = known if known is not None else latest_versions(archive_dir)
    prev = known.get(sid)
    if prev and prev["sha256"] == sha:
        return {"id": sid, "status": "unchanged"}
    upd = obj.get("updatedDatetime") or obj.get("submissionDatetime") or obj.get("datetime")
    rel = Path(str(obj.get("datetime") or obj["date"])[:4]) / f"{sid}_{_compact(upd)}.json.gz"
    dest = archive_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(gzip.compress(raw, mtime=0))
    rec = {"id": sid, "path": str(rel), "url": url, "retrieved_utc": datetime.now(UTC).isoformat(timespec="seconds"),
           "sha256": sha, "bytes": len(raw), "datetime": obj.get("datetime") or obj.get("date"), "updated": upd}
    with open(archive_dir / "manifest.jsonl", "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    known[sid] = rec
    return {"id": sid, "status": "new_version" if prev else "archived"}


def update(start: date, end: date, archive_dir: Path = ARCHIVE, radius_km: float = RADIUS_KM,
           refetch_days: int = REFETCH_DAYS, session: requests.Session | None = None) -> dict:
    """Scan [start, end] month by month; archive new reports near the plots, re-read recent ones for edits."""
    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    session = session or requests.Session()
    session.headers.update(HEADERS)
    points = plot_points()
    box = bbox(points)
    known = latest_versions(archive_dir)
    recent = end - timedelta(days=refetch_days)
    seen: set[str] = set()
    counts = {"listed": 0, "near": 0, "archived": 0, "unchanged": 0, "skipped_known": 0, "errors": 0}
    a = start
    while a <= end:
        b = min(end, (pd.Timestamp(a) + pd.offsets.MonthEnd(0)).date())
        for it in list_window(a, b, session, box):
            counts["listed"] += 1
            sid, loc = item_id(it), it.get("location") or {}
            if not sid or sid in seen or loc.get("latitude") is None:
                continue
            seen.add(sid)
            if not distances(float(loc["latitude"]), float(loc["longitude"]), points, radius_km):
                continue
            counts["near"] += 1
            obs_day = item_day(it) or end
            if sid in known and obs_day < recent:
                counts["skipped_known"] += 1
                continue
            url = f"{API}/{sid}"
            try:
                r = session.get(url, timeout=60)
                r.raise_for_status()
                res = archive_report(r.content, url, archive_dir, known)
                counts["unchanged" if res["status"] == "unchanged" else "archived"] += 1
            except (requests.RequestException, ValueError, KeyError) as exc:
                counts["errors"] += 1
                with open(archive_dir / "errors.jsonl", "a") as fh:
                    fh.write(json.dumps({"id": sid, "url": url, "time_utc": datetime.now(UTC).isoformat(
                        timespec="seconds"), "error": f"{type(exc).__name__}: {str(exc)[:200]}"}) + "\n")
            time.sleep(PAUSE_S)
        a = b + timedelta(days=1)
    st = archive_dir / "state.json"
    state = json.loads(st.read_text()) if st.exists() else {"windows": []}
    state["windows"].append({"from": start.isoformat(), "to": end.isoformat(), "radius_km": radius_km, "api": API,
                             "scanned_utc": datetime.now(UTC).isoformat(timespec="seconds"), **counts})
    state["last_scan_utc"] = state["windows"][-1]["scanned_utc"]
    state["api"] = API
    st.write_text(json.dumps(state, indent=1))
    return counts


# ------------------------------------------------------------------------------------------------ parsing
def _num(x) -> float | None:
    try:
        return None if x in (None, "") else float(x)
    except (TypeError, ValueError):
        return None


def _list(x) -> list[str]:
    if x is None:
        return []
    return [str(v) for v in x] if isinstance(x, list) else [str(x)]


def feed_changed(archive_dir: Path = ARCHIVE) -> bool:
    """True until a scan of the current feed (``API``) is recorded in ``state.json`` (the daily fetch then scans the
    whole live season once, ADR-164)."""
    st = Path(archive_dir) / "state.json"
    return not st.exists() or json.loads(st.read_text()).get("api") != API


def is_trip_report(obj: dict) -> bool:
    """The 2026-27 MIN format (``data_3...`` schema: free-text comments, no structured snowpack part)."""
    sid = str((obj.get("schemaVersions") or {}).get("dataSchemaId") or "")
    return sid.startswith("data_3") or ("observations" not in obj and "date" in obj and "comments" in obj)


# the trip report's local date carries no time: the report is placed at local noon (UTC-7, the site's fixed MST)
TRIP_REPORT_HOUR_UTC = 19


def parse_trip_report(obj: dict, points: dict[str, tuple[float, float]], raw_path: str, raw_sha256: str,
                      radius_km: float = RADIUS_KM) -> PublicObservation:
    loc = obj.get("location") or {}
    lat, lon = float(loc["latitude"]), float(loc["longitude"])
    flags: list[str] = []
    t = pd.Timestamp(f"{str(obj['date'])[:10]}T{TRIP_REPORT_HOUR_UTC:02d}:00:00Z")
    inst = _list(obj.get("instability"))
    avs = [a for a in obj.get("avalanches") or [] if isinstance(a, dict)]
    comment = str(obj.get("comments") or "").strip()
    types = ["trip"] + (["avalanche"] if avs else []) + (["instability"] if set(inst) - {"none"} else [])
    return PublicObservation(
        source="min", source_id=obj["submissionID"], url=PAGE_URL.format(id=obj["submissionID"]),
        obs_time_utc=t.isoformat(), submitted_utc=_time(obj.get("submissionDatetime"), flags, "submitted"),
        updated_utc=_time(obj.get("updatedDatetime"), flags, "updated"), lat=lat, lon=lon,
        region=obj.get("region") or None, title=str(obj.get("title") or ""), types=types,
        whumpfing=True if "whumpfing" in inst else (False if "none" in inst else None),
        cracking=True if {"shootingCracks", "cracking"} & set(inst) else (False if "none" in inst else None),
        avalanches=[PublicAvalanche(time_utc=_time(a.get("date"), flags, "avalanche_time"), number=_str(a.get("number")),
                                    size=_str(a.get("size")), character=_list(a.get("problem")),
                                    trigger=_str(a.get("trigger")) or (_list(a.get("startedBy")) or [None])[0],
                                    aspects=_list(a.get("aspect")), elevation_bands=_list(a.get("elevation")))
                    for a in avs],
        comments={"trip": comment} if comment else {},
        image_urls=[i["url"] for i in obj.get("images") or [] if isinstance(i, dict) and i.get("url")],
        distance_km=distances(lat, lon, points, radius_km), flags=flags, raw_path=raw_path, raw_sha256=raw_sha256)


def parse_report(obj: dict, points: dict[str, tuple[float, float]], raw_path: str, raw_sha256: str,
                 radius_km: float = RADIUS_KM) -> PublicObservation:
    if is_trip_report(obj):
        return parse_trip_report(obj, points, raw_path, raw_sha256, radius_km)
    obs = obj.get("observations") or {}
    sp = obs.get("snowpack") or {}
    wx = obs.get("weather") or {}
    av = obs.get("avalanche")
    avs = av if isinstance(av, list) else ([av] if av else [])
    loc = obj.get("location") or {}
    lat, lon = float(loc["latitude"]), float(loc["longitude"])
    flags: list[str] = []
    hs = _num(sp.get("depth"))
    if hs is not None and not 0 <= hs <= HS_MAX_CM:
        flags.append(f"hs_implausible:{hs:g}")
        hs = None
    test = None
    if any(sp.get(k) is not None for k in ("testInitiation", "testFracture", "testFailureDepth")) or \
            sp.get("testFailureLayerCrystalTypes"):
        test = PublicTest(initiation=_str(sp.get("testInitiation")), fracture=_str(sp.get("testFracture")),
                          depth_cm=_num(sp.get("testFailureDepth")),
                          crystal_types=_list(sp.get("testFailureLayerCrystalTypes")))
    comments = {k: str(v["comment"]).strip() for k, v in obs.items()
                if isinstance(v, dict) and v.get("comment") and str(v["comment"]).strip()}
    if isinstance(obs.get("incident"), dict) and obs["incident"].get("description"):
        comments["incident"] = str(obs["incident"]["description"]).strip()
    return PublicObservation(
        source="min", source_id=obj["submissionID"], url=PAGE_URL.format(id=obj["submissionID"]),
        obs_time_utc=obj["datetime"], submitted_utc=_time(obj.get("submissionDatetime"), flags, "submitted"),
        updated_utc=_time(obj.get("updatedDatetime"), flags, "updated"), lat=lat, lon=lon, region=obj.get("region") or None,
        title=str(obj.get("title") or ""), types=sorted(k for k, v in obs.items() if v),
        snowpack_obs_type=_str(sp.get("obsType")), elevation_m=_num(sp.get("siteElevation")),
        elevation_bands=_list(sp.get("siteElevationBand")), aspects=_list(sp.get("siteAspect")),
        hs_cm=hs, foot_pen_cm=_num(sp.get("footPenetration")),
        ski_pen_cm=_num(sp.get("skiPenetration")), test=test, surface=_list(sp.get("surfaceConditions")),
        whumpfing=sp.get("whumpfingObserved"), cracking=sp.get("crackingObserved"),
        new_snow_24h_cm=_num(wx.get("newSnow24Hours")),
        avalanches=[PublicAvalanche(time_utc=_time(a.get("avalancheOccurrenceDatetime"), flags, "avalanche_time"), number=_str(a.get("numberOfAvalanches")),
                                    size=_str(a.get("avalancheSize")), character=_list(a.get("avalancheCharacter")),
                                    trigger=_str(a.get("triggerType")), aspects=_list(a.get("startZoneAspect")),
                                    elevation_bands=_list(a.get("startZoneElevationBand")),
                                    incline_deg=_num(a.get("startZoneIncline")),
                                    weak_layer=_list(a.get("weakLayerCrystalType")),
                                    crust_near_weak_layer=a.get("crustNearWeakLayer"))
                    for a in avs if isinstance(a, dict)],
        incident=bool(obs.get("incident")), comments=comments,
        image_urls=[i["url"] for i in obj.get("images") or [] if isinstance(i, dict) and i.get("url")],
        distance_km=distances(lat, lon, points, radius_km), flags=flags, raw_path=raw_path, raw_sha256=raw_sha256)


def _str(x) -> str | None:
    return None if x in (None, "") else str(x)


def _time(x, flags: list[str], name: str) -> str | None:
    if x in (None, ""):
        return None
    try:
        return pd.Timestamp(x).isoformat()
    except (ValueError, TypeError):
        flags.append(f"{name}_unreadable:{str(x)[:30]}")
        return None


HS_MAX_CM = 1000.0  # a snowpack depth above 10 m at these sites is an entry error (e.g. elevation typed as depth)


def load_reports(archive_dir: Path = ARCHIVE, points: dict[str, tuple[float, float]] | None = None
                 ) -> list[PublicObservation]:
    """Newest archived version of every report, parsed; sorted by observation time."""
    archive_dir = Path(archive_dir)
    points = points or plot_points()
    out = []
    bad = []
    for rec in latest_versions(archive_dir).values():
        raw = gzip.decompress((archive_dir / rec["path"]).read_bytes())
        try:
            out.append(parse_report(json.loads(raw), points, rec["path"], rec["sha256"]))
        except (ValueError, KeyError, TypeError) as exc:  # kept in the archive; listed, not shown
            bad.append({"id": rec["id"], "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
    load_reports.unparsed = bad  # type: ignore[attr-defined]
    return sorted(out, key=lambda r: r.obs_time_utc)
