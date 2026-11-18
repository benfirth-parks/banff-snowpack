"""Forecaster notes on the modelled snowpack (ADR-115).

Forecasters write notes in the Rockies Weather Data Explorer's paired panes (repo weather-explorer,
``/api/plot-feedback``). This module copies them into a private folder and filters them by time so no note can tell
an agent about a pit it is scored on.

Privacy: notes and names are internal. They are never committed (the repository is public) and never published. The
folder is ``$SNOWAGENT_FEEDBACK_DIR``, else ``data/feedback`` (git-ignored). Names are replaced by a short keyed hash
before any note is sent to the language model.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_URL = "https://rockiesweatherdataexplorer.netlify.app/api/plot-feedback"
NOTES_FILE = "notes.jsonl"
Agreement = Literal["agree", "partly", "disagree"]


class ForecasterNote(BaseModel):
    """One note as the explorer stores it (``netlify/functions/_plot-feedback.mjs``, version 1)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    key: str
    version: int = 1
    received_at: datetime  # server receive time: the only time trusted for leakage
    plot: str
    source: str = "banff-snowpack"
    agreement: Agreement | None = None
    topics: list[str] = Field(default_factory=list)
    note: str = ""
    name: str = ""
    model_run_t: datetime | None = None
    model_hs_cm: float | None = None
    pit_shown_t: datetime | None = None

    def visible_at(self, cutoff: datetime) -> bool:
        """True if the note existed at ``cutoff`` (received strictly before it)."""
        return self.received_at < cutoff


def feedback_dir() -> Path:
    return Path(os.environ.get("SNOWAGENT_FEEDBACK_DIR") or "data/feedback")


def load_notes(folder: Path | None = None) -> list[ForecasterNote]:
    path = (folder or feedback_dir()) / NOTES_FILE
    if not path.is_file():
        return []
    out = [ForecasterNote.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]
    return sorted(out, key=lambda n: (n.received_at, n.key))


def merge_notes(old: list[ForecasterNote], new: list[ForecasterNote]) -> list[ForecasterNote]:
    """Union by key; a key already held is kept as first copied (raw data are immutable)."""
    seen = {n.key: n for n in old}
    for n in new:
        seen.setdefault(n.key, n)
    return sorted(seen.values(), key=lambda n: (n.received_at, n.key))


def save_notes(notes: list[ForecasterNote], folder: Path | None = None) -> Path:
    folder = folder or feedback_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / NOTES_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(n.model_dump_json() + "\n" for n in notes))
    tmp.replace(path)
    return path


def fetch_notes(token: str, since: str | None = None, url: str = DEFAULT_URL, session=None) -> list[ForecasterNote]:
    """Notes from the explorer, oldest first. ``token`` is FEEDBACK_ADMIN_TOKEN (from the environment, never code)."""
    import requests

    http = session or requests
    params = {"since": since} if since else {}
    r = http.get(url, params=params, headers={"x-feedback-admin-token": token, "accept": "application/json"},
                 timeout=60)
    r.raise_for_status()
    body = r.json()
    if body.get("truncated"):
        raise RuntimeError("the explorer returned a truncated list; fetch again with a later --since")
    return [ForecasterNote.model_validate(n) for n in body.get("notes", [])]


def visible_notes(notes: list[ForecasterNote], cutoff: datetime) -> list[ForecasterNote]:
    """Leakage guard: only notes received before ``cutoff`` (a case's cut-off, UTC)."""
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=UTC)
    return [n for n in notes if n.visible_at(cutoff)]


def writer_code(name: str, salt: str) -> str:
    """Stable pseudonym for a forecaster, so the model can weigh repeat writers without seeing names."""
    h = hashlib.sha256(f"{salt}:{name.strip().lower()}".encode()).hexdigest()[:6]
    return f"F-{h}"


def for_model(notes: list[ForecasterNote], salt: str) -> str:
    """Notes as JSON lines for the language model: names replaced by pseudonyms, nothing else changed."""
    rows = []
    for n in notes:
        rows.append({"key": n.key, "received_at": n.received_at.isoformat(), "plot": n.plot,
                     "writer": writer_code(n.name, salt), "agreement": n.agreement, "topics": n.topics,
                     "note": n.note, "model_run_t": n.model_run_t.isoformat() if n.model_run_t else None,
                     "model_hs_cm": n.model_hs_cm,
                     "pit_shown_t": n.pit_shown_t.isoformat() if n.pit_shown_t else None})
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)


__all__ = ["DEFAULT_URL", "ForecasterNote", "feedback_dir", "fetch_notes", "for_model", "load_notes", "merge_notes",
           "save_notes", "visible_notes", "writer_code"]
