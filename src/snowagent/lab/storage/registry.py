"""SQLite run registry (stdlib ``sqlite3``): one row per run with its full manifest. Rows are only inserted;
an existing run_id is never overwritten."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from snowagent.lab.schemas.run import RunManifest

SCHEMA = """
CREATE TABLE IF NOT EXISTS run_manifest (
    run_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    config_hash TEXT NOT NULL,
    data_hash TEXT NOT NULL,
    software_version TEXT NOT NULL,
    git_commit TEXT,
    snowpack_version TEXT,
    seed INTEGER,
    scoring_weights_json TEXT,
    manifest_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS run_manifest_kind_created ON run_manifest (kind, created_at);
"""


class RunRegistry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.path)

    def init(self) -> None:
        with closing(self._connect()) as con, con:
            con.executescript(SCHEMA)

    def record(self, m: RunManifest) -> None:
        """Insert a run; raises ``sqlite3.IntegrityError`` if the run_id exists (manifests are write-once)."""
        self.init()
        with closing(self._connect()) as con, con:
            con.execute(
                "INSERT INTO run_manifest VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (m.run_id, m.kind.value, m.status, m.created_at.isoformat(),
                 m.finished_at.isoformat() if m.finished_at else None, m.config_hash, m.data_hash,
                 m.software_version, m.git_commit, m.snowpack_version, m.seed,
                 m.scoring_weights.model_dump_json() if m.scoring_weights else None, m.model_dump_json()))

    def get(self, run_id: str) -> RunManifest | None:
        if not self.path.exists():
            return None
        with closing(self._connect()) as con:
            row = con.execute("SELECT manifest_json FROM run_manifest WHERE run_id = ?", (run_id,)).fetchone()
        return RunManifest.model_validate_json(row[0]) if row else None

    def latest(self, limit: int = 10, kind: str | None = None) -> list[RunManifest]:
        if not self.path.exists():
            return []
        q = "SELECT manifest_json FROM run_manifest"
        args: tuple = ()
        if kind:
            q, args = q + " WHERE kind = ?", (kind,)
        with closing(self._connect()) as con:
            rows = con.execute(q + " ORDER BY created_at DESC LIMIT ?", (*args, limit)).fetchall()
        return [RunManifest.model_validate(json.loads(r[0])) for r in rows]
