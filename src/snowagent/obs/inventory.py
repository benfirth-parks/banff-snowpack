"""Inventory and QC of uploaded field-profile files (read-only on the raw data).

Produces one ProfileHeader per profile file with explicit flags. It never edits,
moves or "corrects" the raw files; suspect values are flagged and kept.
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from snowagent.engine.snowpack import REPO_ROOT
from snowagent.obs.models import LayersStatus, ProfileCategory, ProfileHeader
from snowagent.obs.propagation_labs import parse_header_text

PROFILE_EXT = {".pdf", ".png", ".jpg", ".jpeg"}
DEFAULT_CONFIG = REPO_ROOT / "config" / "observations.yaml"


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _site_key(folder: str, aliases: dict[str, list[str]]) -> str:
    low = folder.lower().strip()
    for key, names in aliases.items():
        if low in names or low.replace("_", " ") in names:
            return key
    return re.sub(r"[^a-z0-9]+", "_", low).strip("_")


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    try:
        return PdfReader(str(path)).pages[0].extract_text() or ""
    except Exception:  # corrupt/unsupported PDF: recorded as a flag by the caller
        return ""


def build_inventory(root: Path, config_path: Path | None = None) -> tuple[list[ProfileHeader], list[dict]]:
    cfg = yaml.safe_load(Path(config_path or DEFAULT_CONFIG).read_text())
    tz_name = cfg.get("time_zone")
    tz_ok = bool(cfg.get("time_zone_confirmed"))
    aliases = cfg.get("site_aliases", {})
    plots = cfg.get("study_plots", {})
    headers: list[ProfileHeader] = []
    skipped: list[dict] = []
    for f in sorted(Path(root).rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(Path(root).parent) if Path(root).parent != Path(".") else f
        if f.suffix.lower() not in PROFILE_EXT:
            skipped.append({"file": str(rel), "reason": f"not a profile document ({f.suffix or 'no extension'})"})
            continue
        parts = f.relative_to(root).parts
        category, site = ProfileCategory.unknown, None
        if any(p.lower().startswith("study plot") for p in parts):
            category = ProfileCategory.study_plot
            site = _site_key(parts[-2], aliases)
        elif any(p.lower().startswith("test profile") for p in parts):
            category = ProfileCategory.test_profile
        flags: list[str] = []
        m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})", f.name)
        fdate = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
        if fdate is None:
            flags.append("no_date_in_filename")
        season = next((p for p in parts if re.fullmatch(r"\d{4}-\d{4}", p)), None)
        if fdate and season:
            y0, y1 = (int(x) for x in season.split("-"))
            d = pd.Timestamp(fdate)
            if not (pd.Timestamp(f"{y0}-08-01") <= d <= pd.Timestamp(f"{y1}-07-31")):
                flags.append(f"filename_date_outside_season_{season}")
        values: dict = {}
        header_source = "filename"
        if f.suffix.lower() == ".pdf":
            text = _pdf_text(f)
            if text.strip():
                ph = parse_header_text(text)
                values, header_source = ph.values, "pdf_text"
                flags += ph.flags
            else:
                flags.append("pdf_without_text_layer")
        else:
            flags.append("image_only_file_no_header_text")
        local = values.get("obs_time_local")
        utc = None
        if local and tz_name:
            utc = pd.Timestamp(local).tz_localize(ZoneInfo(tz_name)).tz_convert("UTC").to_pydatetime()
            if not tz_ok:
                flags.append("time_zone_assumed_unconfirmed")
        if local and fdate and local[:10] != fdate:
            flags.append(f"header_date_{local[:10]}_differs_from_filename_{fdate}")
        if values.get("hs_m") is None:
            flags.append("hs_missing")
        stem = re.sub(r"^\d{4}-?\d{2}-?\d{2}[_ ]*(\d{4}[_ ])?", "", f.stem)
        slug = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")[:40]
        rid = f"{(local or fdate or 'nodate')[:10]}_{site or slug}"
        headers.append(ProfileHeader(
            record_id=rid, source_file=str(rel), sha256=hashlib.sha256(f.read_bytes()).hexdigest(),
            file_type=f.suffix.lower().lstrip("."), header_source=header_source, category=category,
            site_key=site, station_id=(plots.get(site) or {}).get("station_id") if site else None,
            time_zone=tz_name if local else None, time_zone_confirmed=tz_ok, obs_time_utc=utc,
            filename_date=fdate, layers_status=LayersStatus.image_only, qc_flags=flags,
            **{k: values.get(k) for k in ("profile_name", "observer", "obs_time_local", "lat", "lon", "elevation_m",
                                          "elevation_source_unit", "aspect_deg", "aspect_text", "slope_deg",
                                          "hs_m", "air_temp_c", "sky_cover", "precipitation", "wind",
                                          "blowing_snow", "surface_grain", "foot_pen_m", "ski_pen_m", "notes")}))
    _flag_duplicates(headers)
    _flag_shared_coordinates(headers)
    _flag_location_outliers(headers, float(cfg.get("location_outlier_km", 1.0)))
    return headers, skipped


def _flag_duplicates(headers: list[ProfileHeader]) -> None:
    """Same site/name and date in several formats (e.g. PDF + PNG): keep the PDF as primary."""
    groups: dict[tuple, list[ProfileHeader]] = {}
    for h in headers:
        key = (h.site_key or Path(h.source_file).stem.lower()[11:].strip(" _"), h.filename_date)
        groups.setdefault(key, []).append(h)
    for grp in groups.values():
        if len(grp) < 2:
            continue
        primary = sorted(grp, key=lambda h: (h.header_source != "pdf_text", h.file_type != "pdf"))[0]
        for h in grp:
            if h is not primary:
                h.duplicate_of = primary.source_file
                h.qc_flags.append("same_site_date_as_another_file")


def _site_name(h: ProfileHeader) -> str:
    return h.site_key or h.record_id[11:]


def _flag_shared_coordinates(headers: list[ProfileHeader]) -> None:
    """Identical coordinates reported for different sites indicate a device/default GPS fix."""
    by_xy: dict[tuple, set[str]] = {}
    for h in headers:
        if h.lat is not None and h.duplicate_of is None:
            by_xy.setdefault((round(h.lat, 3), round(h.lon, 3)), set()).add(_site_name(h))
    for h in headers:
        if h.lat is not None and len(by_xy.get((round(h.lat, 3), round(h.lon, 3)), ())) > 1:
            h.qc_flags.append("coordinates_shared_by_different_sites_suspect_device_gps")


def _location_suspect(h: ProfileHeader) -> bool:
    return any("suspect_device_gps" in q for q in h.qc_flags)


def _flag_location_outliers(headers: list[ProfileHeader], tol_km: float) -> None:
    """Per study-plot site, compare each header location to the site's median location."""
    by_site: dict[str, list[ProfileHeader]] = {}
    for h in headers:
        if h.site_key and h.lat is not None and not _location_suspect(h):
            by_site.setdefault(h.site_key, []).append(h)
    for grp in by_site.values():
        if len(grp) < 3:
            for h in grp:
                h.qc_flags.append("site_location_unverified_fewer_than_3_fixes")
            continue
        lat = float(pd.Series([h.lat for h in grp]).median())
        lon = float(pd.Series([h.lon for h in grp]).median())
        for h in grp:
            d = _haversine_km(h.lat, h.lon, lat, lon)
            if d > tol_km:
                h.qc_flags.append(f"location_{d:.1f}km_from_site_median_suspect_device_gps")


def site_summary(headers: list[ProfileHeader], tol_km: float = 1.0) -> pd.DataFrame:
    """Consensus study-plot locations from non-outlier header fixes."""
    rows = []
    for site in sorted({h.site_key for h in headers if h.site_key}):
        grp = [h for h in headers if h.site_key == site and h.duplicate_of is None]
        good = [h for h in grp if h.lat is not None and not _location_suspect(h)]
        if len(good) < 3:
            spread = max((_haversine_km(a.lat, a.lon, b.lat, b.lon) for a in good for b in good), default=0.0)
            status = {0: "no_fix", 1: "single_fix"}.get(len(good), "consistent" if spread <= tol_km else "conflicting")
        else:
            status = "consensus"
        if status == "conflicting":
            good = []
        elev = [h.elevation_m for h in good if h.elevation_m is not None]
        rows.append({
            "site_key": site, "station_id": grp[0].station_id if grp else None, "n_profiles": len(grp),
            "n_with_header_text": sum(h.header_source == "pdf_text" for h in grp),
            "n_location_fixes_used": len(good), "location_status": status,
            "lat_median": round(float(pd.Series([h.lat for h in good]).median()), 5) if good else None,
            "lon_median": round(float(pd.Series([h.lon for h in good]).median()), 5) if good else None,
            "elevation_m_median": round(float(pd.Series(elev).median()), 0) if elev else None,
            "elevation_m_range": f"{min(elev):.0f}-{max(elev):.0f}" if elev else None,
            "first_date": min(h.filename_date for h in grp if h.filename_date),
            "last_date": max(h.filename_date for h in grp if h.filename_date),
        })
    return pd.DataFrame(rows)


def write_inventory(headers: list[ProfileHeader], skipped: list[dict], out_dir: Path,
                    include_observer: bool = False) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    recs = [h.model_dump(mode="json") for h in headers]
    if not include_observer:
        for r in recs:
            r["observer"] = "<redacted>" if r["observer"] else None
    df = pd.DataFrame(recs)
    df["qc_flags"] = df["qc_flags"].apply(lambda x: ";".join(x))
    paths = {"csv": out_dir / "profile_inventory.csv", "sites": out_dir / "study_plot_sites.csv",
             "skipped": out_dir / "skipped_files.csv"}
    df.to_csv(paths["csv"], index=False)
    site_summary(headers).to_csv(paths["sites"], index=False)
    pd.DataFrame(skipped, columns=["file", "reason"]).to_csv(paths["skipped"], index=False)
    return paths
