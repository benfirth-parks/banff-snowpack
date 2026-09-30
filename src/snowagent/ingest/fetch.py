"""Idempotent downloader: raw bytes written unchanged, one manifest line per file (URL, UTC time, sha256)."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import requests


def fetch(url: str, dest: Path, manifest: Path, retries: int = 3, timeout: int = 120) -> dict:
    """Download ``url`` to ``dest`` unless it is already there. 404 is recorded, not raised."""
    dest = Path(dest)
    if dest.exists():
        return {"url": url, "path": str(dest), "status": "exists"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 404:
                rec = {"url": url, "path": None, "status": "not_found", "retrieved_utc": _now()}
                _log(manifest, rec)
                return rec
            r.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(r.content)
            tmp.replace(dest)
            rec = {"url": url, "path": str(dest), "status": "downloaded", "retrieved_utc": _now(),
                   "bytes": len(r.content), "sha256": hashlib.sha256(r.content).hexdigest()}
            _log(manifest, rec)
            return rec
        except requests.RequestException as exc:  # network hiccup: back off and retry
            last = exc
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"download failed after {retries} attempts: {url}: {last}")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _log(manifest: Path, rec: dict) -> None:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
