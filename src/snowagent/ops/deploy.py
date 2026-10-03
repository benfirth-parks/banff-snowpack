"""Deploying the site tool (ADR-045): refuse a deploy of an incomplete site folder.

``web/data`` is not in git (~93 MB, ADR-035). ``update build`` regenerates only the live season and writes
``sites.json`` from the season files present, so a deploy from a container that lacks past seasons would remove
them from the site without any error.

- ``check_deploy``: the folder about to be deployed has the static files, every data file listed in
  ``data/sites.json`` exists and parses, every site has seasons, ``data/status.json`` is fresh, no update run holds
  the lock, and, given the deployed site's ``sites.json`` as a reference, no site, season or season file of the
  deployed site is missing and the index is not older than the deployed one. Problems are listed; nothing is
  changed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

STATIC_FILES = ("index.html", "app.js", "styles.css", "netlify.toml")  # what the deploy needs beside data/
DATA_KEYS = ("file", "forecasts", "public")  # the data files a season entry of sites.json lists (web/app.js)
DEPLOY_STATUS_MAX_AGE_H = 6.0  # the deploy follows the day's build; an older status.json means no build ran since
_DATA_PATH = re.compile(r"data/[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*\.json")


def data_path(ref: Any) -> str | None:
    """``ref`` if it is a relative ``data/...json`` path (no ``..``, no absolute path), else None."""
    if isinstance(ref, str) and _DATA_PATH.fullmatch(ref) and ".." not in ref.split("/"):
        return ref
    return None


def _load(path: Path) -> tuple[Any, str | None]:
    """The parsed JSON file, or the reason it could not be read."""
    if not Path(path).is_file():
        return None, "missing"
    try:
        return json.loads(Path(path).read_bytes()), None
    except (OSError, ValueError) as exc:
        return None, f"not valid JSON ({type(exc).__name__}: {str(exc)[:120]})"


def season_index(idx: Any) -> dict[str, dict[str, dict]] | None:
    """{site id: {season: season entry}} of a sites.json, or None when it has no list of sites."""
    if not isinstance(idx, dict) or not isinstance(idx.get("sites"), list):
        return None
    out: dict[str, dict[str, dict]] = {}
    for site in idx["sites"]:
        if isinstance(site, dict) and isinstance(site.get("id"), str):
            out[site["id"]] = {s["season"]: s for s in site.get("seasons") or []
                               if isinstance(s, dict) and isinstance(s.get("season"), str)}
    return out


def _time(v: Any) -> pd.Timestamp | None:
    try:
        t = pd.Timestamp(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(t) or t.tzinfo is None else t.tz_convert("UTC")


def check_deploy(web_dir: Path = Path("web"), reference: Path | None = None, now: pd.Timestamp | None = None,
                 max_status_age_h: float = DEPLOY_STATUS_MAX_AGE_H, lock: Path | None = None) -> dict:
    """Problems that make ``web_dir`` unfit to deploy (``ok`` only when there are none). ``reference``: the deployed
    site's sites.json, downloaded beforehand; seasons, sites and season files it lists must all be here. ``lock``:
    the update lock (default ``ops.update.LOCK_FILE``); a valid one means a fetch or build is still running."""
    from snowagent.ops import update
    from snowagent.web.build import SITES

    now = now or pd.Timestamp.now(tz="UTC")
    web_dir = Path(web_dir)
    problems: list[str] = []
    problems += [f"{name}: missing" for name in STATIC_FILES if not (web_dir / name).is_file()]

    idx, err = _load(web_dir / "data" / "sites.json")
    found = season_index(idx) if err is None else None
    if err is not None:
        problems.append(f"data/sites.json: {err}")
    elif found is None:
        problems.append("data/sites.json: no list of sites")
    else:
        problems += [f"{site}: not in data/sites.json" for site in SITES if site not in found]
        problems += [f"{site}: no seasons in data/sites.json" for site, ss in found.items() if not ss]
    local = found or {}
    checked = 0
    for site, seasons in local.items():
        for season, entry in seasons.items():
            for key in DATA_KEYS:
                ref = entry.get(key)
                if ref is None and key != "file":
                    continue
                path = data_path(ref)
                if path is None:
                    problems.append(f"{site} {season} {key}: {ref!r} is not a data/*.json path")
                    continue
                checked += 1
                err = _load(web_dir / path)[1]
                if err is not None:
                    problems.append(f"{path}: {err}")

    st, err = _load(web_dir / "data" / "status.json")
    generated = _time(st.get("generated_utc")) if isinstance(st, dict) else None
    if err is not None:
        problems.append(f"data/status.json: {err}")
    elif generated is None:
        problems.append("data/status.json: no valid generated_utc")
    else:
        age_h = (now - generated).total_seconds() / 3600
        if age_h > max_status_age_h:
            problems.append(f"data/status.json: generated {generated.isoformat()}, {age_h:.1f} h ago (at most "
                            f"{max_status_age_h:g} h): no build ran since; run `snowagent update build` first")
        elif age_h < -0.25:
            problems.append(f"data/status.json: generated {generated.isoformat()}, in the future (clock?)")

    if reference is not None:
        ref_idx, err = _load(Path(reference))
        deployed = season_index(ref_idx) if err is None else None
        if err is not None or deployed is None:
            problems.append(f"reference {reference}: {err or 'no list of sites'}")
        for site, seasons in (deployed or {}).items():
            here = local.get(site)
            if here is None:
                problems.append(f"{site}: {len(seasons)} seasons on the deployed site, none here")
                continue
            missing = sorted(set(seasons) - set(here))
            if missing:
                problems.append(f"{site}: seasons of the deployed site missing here: {', '.join(missing)} "
                                f"({len(here)} here, {len(seasons)} deployed)")
            for season in sorted(set(seasons) & set(here)):
                problems += [f"{site} {season}: {key} file on the deployed site, none here" for key in DATA_KEYS
                             if seasons[season].get(key) and not here[season].get(key)]
        mine = _time(idx.get("generated_utc")) if isinstance(idx, dict) else None
        theirs = _time(ref_idx.get("generated_utc")) if isinstance(ref_idx, dict) else None
        if mine is not None and theirs is not None and mine < theirs:
            problems.append(f"data/sites.json: generated {mine.isoformat()}, older than the deployed one "
                            f"({theirs.isoformat()})")

    lock = Path(lock or update.LOCK_FILE)
    held = update._lock_holder(lock) if lock.exists() else {}
    if held and update.lock_stale(held, now) is None:
        problems.append(f"{lock}: an update run holds the lock ({held.get('command', '?')} started "
                        f"{held['started_utc']}, pid {held.get('pid', '?')} on {held.get('host', '?')}); wait for it")

    return {"ok": not problems, "web": str(web_dir), "reference": str(reference) if reference is not None else None,
            "seasons": {site: len(ss) for site, ss in local.items()}, "data_files_checked": checked,
            "status_generated_utc": generated.isoformat() if generated is not None else None, "problems": problems}
