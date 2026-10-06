"""Live event feed of competitions and training runs (ADR-078): small JSON lines appended to ``events.jsonl`` in the
run directory as work happens, read by the Arena page (live and replay).

The feed is a side channel: it never feeds back into a result, a score or a cache key, and a failure to write it is
ignored. Each event is one ``write`` on a file opened with ``O_APPEND`` (whole lines, safe with several worker
processes on a local disk), so it is flushed when the call returns. ``SNOWAGENT_LAB_EVENTS=0`` turns it off.

Events (``ev``), each with ``t`` (UNIX time, UTC):
- ``run_started`` / ``run_finished``: kind (competition or training) and the plan's sizes;
- ``case_started``: one case begins in a worker (training; a competition's workers run the prediction code, which
  stays untouched so that the training cache keys, which hash it, do not change);
- ``case_scored``: one agent's result on one case: status, case composite and its components, plot, case type,
  season, split and the pit time (from the case id) (training: also round, whether it came from the cache, and the
  cache key). A competition's are written by the parent process from each finished case record;
- ``round_started``: the round's agents with role, operator, parents and changed genes (training);
- ``screen``: the screened children with their sample composite and pass/fail (training, ADR-072);
- ``round_committed``: the round's best agent, next survivors and the train-vs-held-out gap (training).
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

EVENTS_FILE = "events.jsonl"
ENV = "SNOWAGENT_LAB_EVENTS"
SCORE_FIELDS = ("status", "composite", "snow_depth", "layer_structure", "critical_layers", "uncertainty",
                "depth_error_m")
CASE_FIELDS = ("site_code", "case_type", "season", "split", "forecast_source")


def enabled() -> bool:
    return os.environ.get(ENV, "1") != "0"


def _clean(o):
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list | tuple):
        return [_clean(v) for v in o]
    return o


def _default(o):
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "item"):
        return o.item()
    return str(o)


def emit(run_dir: str | Path, ev: str, **fields) -> None:
    """Append one event; never raises."""
    if not enabled():
        return
    try:
        line = json.dumps(_clean({"t": round(time.time(), 3), "ev": ev, **fields}), default=_default,
                          separators=(",", ":"), allow_nan=False) + "\n"
        fd = os.open(Path(run_dir) / EVENTS_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line.encode())
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001, S110 - the feed must never change or stop a run
        pass


def score_event(row: dict, **extra) -> dict:
    """The fields of a ``case_scored`` event from a score row."""
    out = {k: row.get(k) for k in ("case_id", "agent_id", "label", "family", "genome_hash", *CASE_FIELDS,
                                   *SCORE_FIELDS)}
    return out | extra


def pit_time(case_id: str) -> str | None:
    """The pit (valid) time in a case id such as ``BOW_20151126T1915Z_H72``, ISO formatted."""
    m = re.search(r"_(\d{8})T(\d{4})Z", case_id or "")
    if not m:
        return None
    d, h = m.groups()
    return f"{d[:4]}-{d[4:6]}-{d[6:]}T{h[:2]}:{h[2:]}Z"


class CompetitionFeed:
    """The feed of one competition, written by the parent process: ``progress`` (the runner's progress callback)
    emits a ``case_scored`` event per agent for every case record written since the last call."""

    def __init__(self, run_dir: str | Path, agents: list[dict] | None = None,
                 then: Callable[[int, int], None] | None = None) -> None:
        self.run_dir = Path(run_dir)
        self.agents = agents or []
        self.then = then
        self.seen = {f.name for f in (self.run_dir / "cases").glob("*.json")}
        self.resumed = len(self.seen)
        self.started = False

    def start(self) -> None:
        """``run_started`` before any work (the run directory is created for it)."""
        if enabled() and not self.started:
            self.started = True
            try:
                self.run_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                return
            emit(self.run_dir, "run_started", kind="competition", run_id=self.run_dir.name, resumed=self.resumed,
                 agents=self.agents)

    def progress(self, done: int, total: int) -> None:
        if not self.started:
            self.started = True
            emit(self.run_dir, "run_started", kind="competition", run_id=self.run_dir.name,
                 cases=total + self.resumed, resumed=self.resumed, agents=self.agents)
        if enabled():
            try:
                new = sorted((f for f in (self.run_dir / "cases").glob("*.json") if f.name not in self.seen),
                             key=lambda f: f.stat().st_mtime_ns)
                for f in new:
                    self.seen.add(f.name)
                    rec = json.loads(f.read_text())
                    for row in rec.get("rows", []):
                        emit(self.run_dir, "case_scored", **score_event(row, pit_time=pit_time(rec["case_id"])))
            except Exception:  # noqa: BLE001, S110 - the feed must never change or stop a run
                pass
        if self.then:
            self.then(done, total)

    def finish(self) -> None:
        emit(self.run_dir, "run_finished", kind="competition", run_id=self.run_dir.name)


def read_events(run_dir: str | Path, offset: int = 0) -> tuple[list[dict], int]:
    """Events from byte ``offset`` on, complete lines only (a line being written is left for the next read), and
    the offset to continue from. Unreadable lines are skipped."""
    f = Path(run_dir) / EVENTS_FILE
    if not f.is_file():
        return [], offset
    with open(f, "rb") as fh:
        fh.seek(offset)
        data = fh.read()
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    out = []
    for line in data[: end + 1].splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out, offset + end + 1
