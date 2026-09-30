"""File-backed, write-once snowpack state checkpoints.

Layout::

    <store>/<domain_id>/checkpoints/<state_id>/
        manifest.json          StateCheckpoint (with manifest_sha256)
        units/<unit_id>.sno    SNOWPACK restart state per terrain unit
        units/<unit_id>.tail.csv  last hours of the forcing that produced the state
        profiles/<unit_id>.json   analysis profile at analysis_time

Checkpoints are never modified after writing (files are made read-only and
hash-verified on load). Forecasts copy the ``.sno`` files into their own run
directories. A later observation creates a new analysis version with a new
state_id; earlier checkpoints and issued forecasts are untouched.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime
from pathlib import Path

import pandas as pd

from snowagent.contracts import StateCheckpoint
from snowagent.engine.snowpack import sha256_file, sha256_text
from snowagent.errors import CheckpointIntegrityError, ImmutableRecord


def _manifest_hash(cp: StateCheckpoint) -> str:
    body = cp.model_copy(update={"manifest_sha256": None}).model_dump_json()
    return sha256_text(body)


def _make_read_only(root: Path) -> None:
    for p in sorted(root.rglob("*"), reverse=True):
        mode = p.stat().st_mode
        if p.is_file():
            os.chmod(p, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    os.chmod(root, root.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


class StateStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def checkpoint_dir(self, domain_id: str, state_id: str) -> Path:
        return self.root / domain_id / "checkpoints" / state_id

    # ------------------------------------------------------------------ write
    def next_analysis_version(self, domain_id: str, analysis_time: datetime) -> int:
        versions = [cp.analysis_version for cp in self.list(domain_id, verify=False)
                    if cp.analysis_time == analysis_time]
        return max(versions, default=0) + 1

    def write(self, cp: StateCheckpoint, staged_dir: Path) -> StateCheckpoint:
        """Move a fully prepared staging directory into place and seal it."""
        dest = self.checkpoint_dir(cp.domain_id, cp.state_id)
        if dest.exists():
            raise ImmutableRecord(f"checkpoint {cp.state_id} already exists; checkpoints are write-once")
        for u in cp.units:
            f = staged_dir / u.sno_file
            if not f.exists() or sha256_file(f) != u.sha256:
                raise CheckpointIntegrityError(f"staged state for {u.unit_id} missing or hash mismatch")
        sealed = cp.model_copy(update={"manifest_sha256": _manifest_hash(cp)})
        (staged_dir / "manifest.json").write_text(sealed.model_dump_json(indent=1))
        dest.parent.mkdir(parents=True, exist_ok=True)
        staged_dir.rename(dest)
        _make_read_only(dest)
        return sealed

    # ------------------------------------------------------------------ read
    def load(self, domain_id: str, state_id: str, verify: bool = True) -> StateCheckpoint:
        d = self.checkpoint_dir(domain_id, state_id)
        mf = d / "manifest.json"
        if not mf.exists():
            raise CheckpointIntegrityError(f"checkpoint {state_id} has no manifest")
        cp = StateCheckpoint.model_validate_json(mf.read_text())
        if verify:
            self.verify(cp)
        return cp

    def verify(self, cp: StateCheckpoint) -> None:
        d = self.checkpoint_dir(cp.domain_id, cp.state_id)
        if cp.manifest_sha256 != _manifest_hash(cp):
            raise CheckpointIntegrityError(f"manifest hash mismatch for {cp.state_id}")
        for u in cp.units:
            f = d / u.sno_file
            if not f.exists() or sha256_file(f) != u.sha256:
                raise CheckpointIntegrityError(f"state file for {u.unit_id} in {cp.state_id} was modified")

    def list(self, domain_id: str, verify: bool = True) -> list[StateCheckpoint]:
        base = self.root / domain_id / "checkpoints"
        if not base.exists():
            return []
        out = []
        for d in sorted(base.iterdir()):
            if (d / "manifest.json").exists():
                out.append(self.load(domain_id, d.name, verify=verify))
        return out

    def latest_valid(self, domain_id: str, issue_time: datetime, terrain_version: str,
                     engine_version: str | None = None) -> StateCheckpoint | None:
        """Newest checkpoint usable for a forecast issued at ``issue_time``.

        Only checkpoints whose analysis time AND assimilation cutoff (latest
        availability of any datum used) are <= issue_time qualify, so an
        archived forecast never sees observations or actuals that arrived later.
        """
        issue = pd.Timestamp(issue_time)
        cands = [cp for cp in self.list(domain_id, verify=False)
                 if pd.Timestamp(cp.analysis_time) <= issue
                 and pd.Timestamp(cp.assimilation_cutoff) <= issue
                 and cp.terrain_version == terrain_version
                 and (engine_version is None or cp.engine_version == engine_version)]
        if not cands:
            return None
        best = max(cands, key=lambda c: (c.analysis_time, c.analysis_version, c.created_utc))
        self.verify(best)
        return best

    def unit_tail(self, cp: StateCheckpoint, unit_id: str) -> pd.DataFrame:
        p = self.checkpoint_dir(cp.domain_id, cp.state_id) / "units" / f"{unit_id}.tail.csv"
        df = pd.read_csv(p, index_col=0)
        df.index = pd.to_datetime(df.index, utc=True)
        return df

    def analysis_profile(self, cp: StateCheckpoint, unit_id: str) -> dict:
        p = self.checkpoint_dir(cp.domain_id, cp.state_id) / "profiles" / f"{unit_id}.json"
        return json.loads(p.read_text())
