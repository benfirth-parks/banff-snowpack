"""Shared restart states for SNOWPACK runs from visible packages (milestone 5, ADR-071).

A case's engine run (``VisiblePackageEngine``) is a chain of segments: snow-free from the season start (15 Sep) to
the first pit update, restart from the pit-updated state to the next update, ..., and a last segment to the valid
time that writes the profile. Cases at one plot in one season share the measured weather and the pits, so their
chains often start with the very same segments. A segment's outcome (the restart state at its end and the modelled
snow depth there) is a deterministic function of its inputs, so it is stored under a key that hashes all of them:

- the engine identity (SNOWPACK version), the store context (code hash), the rendered ``io.ini`` (physics genes
  included), the exact SMET text of the segment's forcing slice, its end time, the terrain unit;
- the state it starts from: snow-free at a named time, or the key of the previous segment plus the full content of
  the pit whose update made the restart state (layers, depth, time; never its anonymous key).

Leakage rule (owner, 2026-10-05: agents must not memorise the snowpacks): a stored state may only contain
information visible to every case that uses it. Because the key hashes every input, a case finds a state only when
its own visible forcing and its own visible pits reproduce that key exactly; a forcing hour that differs between two
cases (a value withheld by availability in one, an ERA5 value younger than its latency in the other) or a pit one of
them cannot see gives a different key from that segment on. So a restart never carries data a case could not see,
and pit restarts happen only from pits visible to that case. Each entry records its provenance (the pit content
hashes and forcing-slice hashes of its whole chain) so tests can check this on real chains. The last segment is
never shared.

Concurrency: workers take a per-key file lock while they compute or read a segment, so two cases needing the same
segment run it once. Entries are written atomically; deleting the store only costs time. The training loop empties
it when a round is committed: every engine profile those states lead to is then in the engine cache (keyed by case
inputs and physics), so a later round, another seed or a leave-one-season-out fold never needs them again.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

SEGMENT_VERSION = "lab-segment-1"


def sha(obj) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def pit_content(pit: dict) -> dict:
    """A pit update's content without its anonymous key (the key differs between cases; the pit does not)."""
    return {k: v for k, v in pit.items() if k != "profile_id"}


def pit_hash(pit: dict) -> str:
    return sha(pit_content(pit))[:32]


class SegmentStore:
    """Restart states by segment key under ``root`` (``<key[:2]>/<key>.sno`` and ``.json``). ``context`` is mixed
    into every key (the caller's code identity)."""

    def __init__(self, root: Path, context: str = "") -> None:
        self.root = Path(root)
        self.context = context
        self.hits = 0
        self.misses = 0
        self.loaded: list[dict] = []  # provenance of every state this process read or wrote (tests, stats)

    def _base(self, key: str) -> Path:
        return self.root / key[:2] / key

    def get(self, key: str) -> tuple[bytes, dict] | None:
        b = self._base(key)
        try:
            meta = json.loads(b.with_suffix(".json").read_text())
            state = b.with_suffix(".sno").read_bytes()
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        if hashlib.sha256(state).hexdigest() != meta.get("state_sha256"):
            return None  # torn or foreign file: recompute
        return state, meta

    def put(self, key: str, state: bytes, meta: dict) -> None:
        b = self._base(key)
        b.parent.mkdir(parents=True, exist_ok=True)
        meta = meta | {"key": key, "state_sha256": hashlib.sha256(state).hexdigest()}
        for suffix, data in ((".sno", state), (".json", json.dumps(meta, sort_keys=True).encode())):
            tmp = b.with_name(f".{key}{suffix}.{os.getpid()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, b.with_suffix(suffix))  # the .json last: an entry is complete once it exists

    @contextmanager
    def lock(self, key: str):
        f = self.root / "locks" / key[:2] / f"{key}.lock"
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def clear(self) -> int:
        """Remove every entry (the training loop does so when a round is committed: by then every profile those
        states lead to is in the engine cache). Returns the bytes freed."""
        import shutil

        n = self.size_bytes()
        if self.root.is_dir():
            shutil.rmtree(self.root, ignore_errors=True)
        return n

    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.root.rglob("*.sno")) if self.root.is_dir() else 0


__all__ = ["SEGMENT_VERSION", "SegmentStore", "pit_content", "pit_hash", "sha"]
