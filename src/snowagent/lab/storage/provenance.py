"""Hashes, versions and run ids recorded in run manifests (CLAUDE.md principle 2)."""

from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from snowagent.lab.schemas.run import InputFile


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def input_files(paths: list[Path], root: Path) -> list[InputFile]:
    """Each input with its sha256 and size; paths relative to ``root`` where possible."""
    out = []
    for p in sorted(set(paths)):
        rel = p.resolve().relative_to(root.resolve()) if p.resolve().is_relative_to(root.resolve()) else p
        out.append(InputFile(path=str(rel), sha256=sha256_file(p), bytes=p.stat().st_size))
    return out


def data_hash(files: list[InputFile]) -> str:
    """sha256 over the sorted (path, sha256) pairs: changes when any input's content or name changes."""
    h = hashlib.sha256()
    for f in sorted(files, key=lambda f: f.path):
        h.update(f"{f.path}\0{f.sha256}\n".encode())
    return h.hexdigest()


def software_version() -> str:
    try:
        return version("snowagent")
    except PackageNotFoundError:  # pragma: no cover - running from a source tree without install
        return "unknown"


def git_commit(cwd: Path | None = None) -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def new_run_id(kind: str, now: datetime | None = None, salt: str = "") -> str:
    """``<kind>-<UTC time>-<6 hex>``; unique per call within a second through the salt and time."""
    now = now or datetime.now(UTC)
    tag = hashlib.sha256(f"{now.isoformat()}{salt}".encode()).hexdigest()[:6]
    return f"{kind}-{now:%Y%m%dT%H%M%SZ}-{tag}"
