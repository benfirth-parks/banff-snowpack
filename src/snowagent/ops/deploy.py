"""Deploying the site tool (ADR-045): refuse a deploy of an incomplete site folder, and restore ``web/data`` from
the deployed site in a fresh container.

``web/data`` is not in git (~93 MB, ADR-035). ``update build`` regenerates only the live season (and the finished
previous season once, ADR-054) and writes ``sites.json`` from the season files present, and ``snowagent web-build``
needs the ERA5 cache (not in git either) for past seasons, so a deploy from a container that lacks past seasons
would remove them from the site without any error.

- ``check_deploy``: the folder about to be deployed has the static files, every data file listed in
  ``data/sites.json`` exists and parses, every site has seasons, ``data/status.json`` is fresh, no update run holds
  the lock, and, against the deployed site's ``sites.json`` as a reference, no site, season or season file of the
  deployed site is missing and the index is not older than the deployed one. Without a reference it refuses,
  unless told there is no deployed site to compare with (``no_reference``, a first deploy; ADR-047). Problems are
  listed; nothing is changed.
- ``restore_web``: when ``web/data/sites.json`` is missing, download the deployed site's ``sites.json``, every data
  file it lists and ``status.json``; each must parse as JSON, local files are kept, the index is written last.
  With ``force`` the local index is removed first and local files are replaced.
- ``publish_site``: check a copy of the site folder as ``check_deploy`` does and, only when it passes, push it as
  one parentless commit to the ``site`` branch, which Netlify deploys (ADR-107).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from snowagent.ops import update

STATIC_FILES = ("index.html", "app.js", "styles.css", "netlify.toml",
                "pit/index.html", "pit/sw.js", "pit/manifest.webmanifest")  # what the deploy needs beside data/
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


# ------------------------------------------------------------------------------------------------ publish
SITE_BRANCH = "site"  # the branch Netlify deploys the site from (ADR-107)
PUSH_RETRY_S = (2.0, 4.0, 8.0, 16.0)  # waits before the next push attempt after a failed one


class PublishError(RuntimeError):
    """A git step of ``publish_site`` that failed."""


def _git(cwd: Path, *args: str, index: Path | None = None, timeout: int = 600) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # a missing sign-in fails at once instead of waiting for input
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, env=env, timeout=timeout)
    if r.returncode != 0:
        step = next(a for a in args if not a.startswith("--"))
        raise PublishError(f"git {step} failed: {r.stderr.decode(errors='replace').strip()[:400]}")
    return r.stdout.decode().strip()


def copy_site(web_dir: Path, dest: Path) -> int:
    """Copy every file of ``web_dir`` to ``dest`` except hidden ones (such as ``.netlify/`` left by a command-line
    deploy, or a ``.part`` file of an interrupted restore); returns the number of files copied."""
    web_dir, n = Path(web_dir), 0
    for src in sorted(web_dir.rglob("*")):
        rel = src.relative_to(web_dir)
        if src.is_dir() or any(part.startswith(".") for part in rel.parts):
            continue
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest / rel)
        n += 1
    return n


def commit_site(site_dir: Path, message: str, repo: Path = Path(".")) -> str:
    """A commit with no parent holding exactly the files of ``site_dir``, written to ``repo``'s object store through
    a temporary index, so the checkout, its index and its branch are untouched; returns its id."""
    git_dir = _git(repo, "rev-parse", "--absolute-git-dir")
    site_dir = Path(site_dir).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        index = Path(tmp) / "index"
        _git(site_dir, f"--git-dir={git_dir}", f"--work-tree={site_dir}", "add", "--all", "--force", ".",
             index=index)
        tree = _git(repo, "write-tree", index=index)
    return _git(repo, "commit-tree", tree, "-m", message)


def push_site(commit: str, branch: str = SITE_BRANCH, remote: str = "origin", repo: Path = Path("."),
              sleep: Callable[[float], None] = time.sleep) -> int:
    """Force-push ``commit`` to ``branch`` of ``remote``, replacing the copy there: the branch holds one commit, so
    the repository does not keep a site a day. Retried after each wait of ``PUSH_RETRY_S``; returns the attempts."""
    for attempt, wait in enumerate((*PUSH_RETRY_S, None), start=1):
        try:
            _git(repo, "push", "--quiet", "--force", remote, f"{commit}:refs/heads/{branch}")
            return attempt
        except (PublishError, subprocess.TimeoutExpired):
            if wait is None:
                raise
            sleep(wait)
    raise AssertionError("unreachable")


def publish_site(web_dir: Path = Path("web"), base_url: str = SITE_URL, branch: str = SITE_BRANCH,
                 remote: str = "origin", repo: Path = Path("."), now: pd.Timestamp | None = None,
                 lock: Path | None = None, get: Callable[[str], bytes] | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> dict:
    """Publish the site (ADR-107): copy ``web_dir`` to a temporary folder (``copy_site``), download the deployed
    site's ``data/sites.json`` from ``base_url`` as the reference, run ``check_deploy`` on the copy and, only when
    it passes, push the copy to ``branch`` (``commit_site``, ``push_site``), which Netlify deploys. Nothing is pushed
    when the reference cannot be downloaded (without it the check refuses, ADR-047) or the check finds a problem.
    ``get``: the HTTP getter (``http_get``)."""
    get = get or http_get
    res: dict = {"branch": branch, "remote": remote, "pushed": False, "commit": None}
    with tempfile.TemporaryDirectory() as tmp:
        site, ref = Path(tmp) / "site", Path(tmp) / "deployed_sites.json"
        res["files"] = copy_site(web_dir, site)
        try:
            ref.write_bytes(get(f"{base_url.rstrip('/')}/data/sites.json"))
        except Exception as exc:  # noqa: BLE001 - reported; nothing is pushed without the reference
            return {**res, "ok": False, "problems": [
                f"the deployed data/sites.json could not be downloaded ({type(exc).__name__}: {str(exc)[:200]})"]}
        check = check_deploy(site, ref, now=now, lock=lock)
        res["check"] = {k: check[k] for k in ("seasons", "data_files_checked", "status_generated_utc")}
        if not check["ok"]:
            return {**res, "ok": False, "problems": check["problems"]}
        try:
            commit = commit_site(site, f"Site built {check['status_generated_utc']}", repo)
            res["push_attempts"] = push_site(commit, branch, remote, repo, sleep=sleep)
        except (PublishError, subprocess.TimeoutExpired) as exc:
            return {**res, "ok": False, "problems": [str(exc)]}
    return {**res, "ok": True, "pushed": True, "commit": commit, "problems": []}
