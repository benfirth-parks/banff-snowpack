"""Deploying the site tool (ADR-045): refuse a deploy of an incomplete site folder, and restore ``web/data`` from
the deployed site in a fresh container.

``web/data`` is not in git (~93 MB, ADR-035). ``update build`` regenerates only the live season and writes
``sites.json`` from the season files present, and ``snowagent web-build`` needs the ERA5 cache (not in git
either) for past seasons, so a deploy from a container that lacks past seasons would remove them from the site
without any error.

- ``check_deploy``: the folder about to be deployed has the static files, every data file listed in
  ``data/sites.json`` exists and parses, every site has seasons, ``data/status.json`` is fresh, no update run holds
  the lock, and, against the deployed site's ``sites.json`` as a reference, no site, season or season file of the
  deployed site is missing and the index is not older than the deployed one. Without a reference it refuses,
  unless told there is no deployed site to compare with (``no_reference``, a first deploy; ADR-047). Problems are
  listed; nothing is changed.
- ``restore_web``: when ``web/data/sites.json`` is missing, download the deployed site's ``sites.json``, every data
  file it lists and ``status.json``; each must parse as JSON, local files are kept, the index is written last.
  With ``force`` the local index is removed first and local files are replaced.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from snowagent.ops import update

STATIC_FILES = ("index.html", "app.js", "styles.css", "netlify.toml")  # what the deploy needs beside data/
DATA_KEYS = ("file", "forecasts", "public")  # the data files a season entry of sites.json lists (web/app.js)
DEPLOY_STATUS_MAX_AGE_H = 6.0  # the deploy follows the day's build; an older status.json means no build ran since
SITE_URL = "https://banff-snowpack.netlify.app"  # the deployed site (docs/operations.md step 5)
HTTP_TIMEOUT_S = 120.0
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


def index_files(idx: Any) -> list[Any]:
    """Every data file a sites.json lists (``DATA_KEYS`` of each season; unchecked, in order, without repeats)."""
    seen: dict[Any, None] = {}
    for seasons in (season_index(idx) or {}).values():
        for entry in seasons.values():
            for key in DATA_KEYS:
                if entry.get(key) is not None:
                    seen.setdefault(entry[key], None)
    return list(seen)


def _time(v: Any) -> pd.Timestamp | None:
    try:
        t = pd.Timestamp(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(t) or t.tzinfo is None else t.tz_convert("UTC")


def check_deploy(web_dir: Path = Path("web"), reference: Path | None = None, now: pd.Timestamp | None = None,
                 max_status_age_h: float = DEPLOY_STATUS_MAX_AGE_H, lock: Path | None = None,
                 no_reference: bool = False) -> dict:
    """Problems that make ``web_dir`` unfit to deploy (``ok`` only when there are none). ``reference``: the deployed
    site's sites.json, downloaded beforehand; seasons, sites and season files it lists must all be here. Without
    one the local checks cannot tell a folder holding only the live season from a complete one, so a missing
    reference is a problem unless ``no_reference`` (a first deploy, with no deployed site to compare with).
    ``lock``: the update lock (default ``ops.update.LOCK_FILE``); a valid one means a fetch or build is still
    running."""
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

    if reference is None and not no_reference:
        problems.append("no reference: download the deployed site's data/sites.json and pass it as --reference; "
                        "without it a deploy could drop seasons of the deployed site unnoticed. If it cannot be "
                        "downloaded, do not deploy (--no-reference only for a first deploy)")
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
            "no_reference": reference is None and no_reference,
            "seasons": {site: len(ss) for site, ss in local.items()}, "data_files_checked": checked,
            "status_generated_utc": generated.isoformat() if generated is not None else None, "problems": problems}


# ------------------------------------------------------------------------------------------------ restore
def http_get(url: str) -> bytes:
    """The body of a GET; raises on a reply other than 2xx."""
    import requests

    r = requests.get(url, timeout=HTTP_TIMEOUT_S)
    r.raise_for_status()
    return r.content


def _write_whole(dest: Path, body: bytes) -> None:
    """Write through a temporary file beside ``dest``, so an interrupted restore never leaves a partial file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.part")
    try:
        tmp.write_bytes(body)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def restore_web(base_url: str = SITE_URL, out_dir: Path = update.WEB_DATA, force: bool = False,
                get: Callable[[str], bytes] | None = None) -> dict:
    """Restore ``out_dir`` (web/data) from the deployed site at ``base_url``: its ``data/sites.json``, every data
    file that lists (``index_files``) and ``data/status.json``. Runs only when ``out_dir/sites.json`` is missing, or
    with ``force``. Each download must parse as JSON; a local file is kept, never replaced, unless ``force``.
    ``sites.json`` is written last and only when nothing failed, so running it again without ``force`` resumes an
    interrupted or partial restore (it fetches only the files still missing). ``force`` removes the local
    ``sites.json`` (e.g. a build's, listing the live season only) before anything else, so that after a partial
    forced restore the index stays missing and a plain rerun resumes instead of finding it and restoring nothing
    (ADR-047). ``get``: the HTTP getter (``http_get``)."""
    get = get or http_get
    base, out_dir = base_url.rstrip("/"), Path(out_dir)
    if not base.startswith(("https://", "http://")):
        raise ValueError(f"base URL {base_url!r} is not an http(s) URL")
    index = out_dir / "sites.json"
    res: dict = {"base_url": base, "out": str(out_dir), "force": force, "restored": 0, "kept": 0,
                 "local_index_removed": False, "failed_steps": []}
    if index.exists() and not force:
        return {**res, "ok": True, "skipped": f"{index} exists; nothing restored (--force replaces local files)",
                "sites_json": "kept"}
    if index.exists():  # force: the index (derived, rewritten by update build) stays missing until a full restore
        index.unlink()
        res["local_index_removed"] = True
    try:
        body = get(f"{base}/data/sites.json")
        idx = json.loads(body)
        if season_index(idx) is None:
            raise ValueError("no list of sites")
    except Exception as exc:  # noqa: BLE001 - reported; nothing else can be restored without the index
        res["failed_steps"].append(update.failure("restore:data/sites.json", exc))
        return {**res, "ok": False, "sites_json": "not written"}
    for ref in [*index_files(idx), "data/status.json"]:
        path = data_path(ref)
        if path is None:
            res["failed_steps"].append({"step": "restore:index",
                                        "error": f"ValueError: {str(ref)[:100]!r} is not a data/*.json path"})
            continue
        dest = out_dir / path.removeprefix("data/")
        if dest.exists() and not force:
            res["kept"] += 1
            continue
        try:
            b = get(f"{base}/{path}")
            json.loads(b)
            _write_whole(dest, b)
            res["restored"] += 1
        except Exception as exc:  # noqa: BLE001 - one failed file; the others go on, the index is not written
            res["failed_steps"].append(update.failure(f"restore:{path}", exc))
    if res["failed_steps"]:
        return {**res, "ok": False, "sites_json": "not written: run again without --force to fetch only the files "
                "still missing"}
    _write_whole(index, body)
    return {**res, "ok": True, "sites_json": "written",
            "seasons": {site: len(ss) for site, ss in (season_index(idx) or {}).items()}}

