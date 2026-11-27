"""The original pit files live outside the public repository (ADR-165).

Scanned charts, PDFs and photos print the observer's name (some a work e-mail address) and SnowPro files carry the
observer's initials, and none of that can be edited out of an image. So ``profiles/`` is git-ignored: the code still
reads it at the same path, but its files come from a private store instead of from git.

The private store is a folder of tar archives (``SNOWAGENT_PITS_STORE``, default the project's private folder
``/mnt/project-files/private/pits``; on the owner's Mac the files simply stay in ``profiles/``). Archives keep every
file byte for byte, which matters because transcriptions and receipts record each file's sha256, and the project
folder rewrites loose images. ``profiles.tar`` holds every file the repository tracked up to its removal; each later
``save`` adds one ``new-<UTC time>.tar`` holding only the files no archive has yet.

``restore`` fills a missing ``profiles/`` file from the store, else from the last repository commit that still
tracked ``profiles/`` (a clone made before any history rewrite). It never overwrites a file on disk.
"""

from __future__ import annotations

import os
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

PITS = Path("profiles")
STORE_ENV = "SNOWAGENT_PITS_STORE"
DEFAULT_STORE = Path("/mnt/project-files/private/pits")


def store_dir() -> Path:
    return Path(os.environ.get(STORE_ENV) or DEFAULT_STORE)


def archives(store: Path) -> list[Path]:
    """The store's archives, base first, then the add-ons in time order."""
    if not store.is_dir():
        return []
    base = [store / "profiles.tar"] if (store / "profiles.tar").is_file() else []
    return base + sorted(p for p in store.glob("new-*.tar") if p.is_file())


def _members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Regular files under ``profiles/`` only (no links, no absolute or ``..`` paths)."""
    out = []
    for m in tar.getmembers():
        parts = Path(m.name).parts
        if m.isfile() and len(parts) > 1 and parts[0] == "profiles" and ".." not in parts and not m.name.startswith("/"):
            out.append(m)
    return out


def stored_names(store: Path) -> set[str]:
    names: set[str] = set()
    for a in archives(store):
        with tarfile.open(a) as t:
            names |= {m.name for m in _members(t)}
    return names


def local_files(root: Path) -> list[str]:
    """Files under ``root/profiles`` as ``profiles/...`` paths."""
    d = root / PITS
    if not d.is_dir():
        return []
    return sorted(p.relative_to(root).as_posix() for p in d.rglob("*") if p.is_file() and ".git" not in p.parts)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, env=os.environ | {
        "GIT_TERMINAL_PROMPT": "0"}, stdin=subprocess.DEVNULL)


def last_tracked_commit(root: Path) -> str | None:
    """The newest commit on HEAD that still tracked files under ``profiles/`` (None when history has none)."""
    r = _git(root, "log", "-1", "--format=%H", "--diff-filter=D", "--", "profiles/")
    gone = r.stdout.decode().strip() if r.returncode == 0 else ""
    if gone:
        return f"{gone}^"
    r = _git(root, "ls-tree", "-r", "--name-only", "HEAD", "--", "profiles/")
    return "HEAD" if r.returncode == 0 and r.stdout.strip() else None


def restore(root: Path = Path("."), store: Path | None = None, say=print) -> dict:
    """Fill files missing from ``profiles/``: from the store's archives, else from git history."""
    root = Path(root)
    store = store_dir() if store is None else Path(store)
    have = set(local_files(root))
    added, source = 0, "none needed"
    found = archives(store)
    if found:
        source = f"store {store}"
        for a in found:
            with tarfile.open(a) as t:
                for m in _members(t):
                    if m.name in have:
                        continue
                    dest = root / m.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with t.extractfile(m) as src, open(dest, "wb") as out:  # type: ignore[union-attr]
                        out.write(src.read())
                    have.add(m.name)
                    added += 1
    else:
        commit = last_tracked_commit(root)
        if commit is None:
            source = "none found"
            say(f"no pit store at {store} and no commit with profiles/ in this clone's history; "
                f"set {STORE_ENV} to the folder holding profiles.tar")
        else:
            source = f"git history ({commit})"
            names = _git(root, "ls-tree", "-r", "-z", "--name-only", commit, "--", "profiles/").stdout.decode()
            for name in filter(None, names.split("\0")):
                if name in have:
                    continue
                blob = _git(root, "show", f"{commit}:{name}")
                if blob.returncode != 0:
                    continue
                dest = root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(blob.stdout)
                have.add(name)
                added += 1
    rep = {"source": source, "added": added, "files": len(have)}
    say(f"pits: {added} files restored from {source}; {len(have)} in profiles/")
    return rep


def save(root: Path = Path("."), store: Path | None = None, now: datetime | None = None, say=print) -> dict:
    """Add the ``profiles/`` files no store archive has yet as one new archive (nothing is written when none)."""
    root = Path(root)
    store = store_dir() if store is None else Path(store)
    if not store.is_dir():
        say(f"no pit store at {store}; new pit files stay only in profiles/ on this machine")
        return {"saved": 0, "archive": None}
    known = stored_names(store)
    new = [n for n in local_files(root) if n not in known]
    if not new:
        say("pits: store already holds every file in profiles/")
        return {"saved": 0, "archive": None}
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    name = "profiles.tar" if not archives(store) else f"new-{stamp}.tar"
    tmp = store / f".{name}.part"
    with tarfile.open(tmp, "w") as t:
        for n in new:
            t.add(root / n, arcname=n, recursive=False)
    tmp.rename(store / name)
    say(f"pits: {len(new)} new files saved to {store / name}")
    return {"saved": len(new), "archive": str(store / name), "files": new}


def keep_after_removal(root: Path, old_head: str, say=print) -> int:
    """After an update moved a clone past the commit that untracked ``profiles/``, put back the pit files that the
    merge just deleted from disk (from ``old_head``, which still tracked them) as untracked, ignored files."""
    r = _git(root, "ls-tree", "-r", "-z", "--name-only", old_head, "--", "profiles/")
    if r.returncode != 0 or not r.stdout.strip():
        return 0
    if _git(root, "ls-files", "--", "profiles/").stdout.strip():
        return 0  # still tracked: nothing was removed
    n = 0
    for name in filter(None, r.stdout.decode().split("\0")):
        dest = root / name
        if dest.exists():
            continue
        blob = _git(root, "show", f"{old_head}:{name}")
        if blob.returncode == 0:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob.stdout)
            n += 1
    if n:
        say(f"kept {n} pit files in profiles/ (no longer in git, ADR-165)")
    return n


_KEPT: set[str] = set()


def keep_pits(root: Path, say=print) -> int:
    """Once per process: when this clone has moved past the commit that untracked ``profiles/`` and the update
    deleted the pit files from disk, put them back from the commit before (the lab app and the Mac queue call this
    as they start, so the owner's Mac keeps its pits however it was updated). Never raises."""
    key = str(Path(root).resolve())
    if key in _KEPT:
        return 0
    _KEPT.add(key)
    try:
        commit = last_tracked_commit(Path(root))
        if commit is None or commit == "HEAD":
            return 0
        return keep_after_removal(Path(root), commit, say=say)
    except (OSError, subprocess.SubprocessError):
        return 0


if __name__ == "__main__":  # python -m snowagent.obs.pits restore|save|keep [ROOT]
    import sys

    if len(sys.argv) not in (2, 3) or sys.argv[1] not in ("restore", "save", "keep"):
        sys.exit("usage: python -m snowagent.obs.pits restore|save|keep [ROOT]")
    where = Path(sys.argv[2]) if len(sys.argv) == 3 else Path(".")
    {"restore": restore, "save": save, "keep": keep_pits}[sys.argv[1]](where)
