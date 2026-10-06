"""Blind live test (ADR-086): freeze an evolved agent before a winter's pits are dug, and score it on them later.

Owner (2026-10-06) wanted to be "as sure as possible" agents are not just getting good at the seasons and pits they
were trained on; a winter whose pits do not exist yet is the one test nothing can leak into. Freezing writes the
agent's genome, the code it ran with (git commit and the prediction code hash) and the scoring version to
``blind_test/<agent_id>.json`` on the ``site-agents`` branch, pushed with the computer's own GitHub sign-in (as Send
to site, ADR-084), so the freeze time is on GitHub, not only on the Mac. An entry is never edited or removed: a
second freeze of the same agent in the same winter is refused, and at most ``MAX_ENTRIES`` agents enter a winter.
Only pits observed after an entry's freeze time count for it. Standard SNOWPACK is the comparison."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from snowagent.lab.services.site_send import REF, SendError, _commit, _git, agent_parts, fetch
from snowagent.lab.storage.paths import LabPaths
from snowagent.ops.site_agents import NAME, SiteAgentSource, branch_files

FOLDER = "blind_test"
RESULTS_FOLDER = "blind_results"  # the daily update's scores of the frozen agents (ADR-090)
MAX_ENTRIES = 5
SEASON = re.compile(r"^\d{4}-\d{4}$")


class BlindEntry(BaseModel):
    """One frozen agent of the blind test."""

    model_config = ConfigDict(extra="forbid")

    name: str
    genome: dict
    source: SiteAgentSource = Field(default_factory=SiteAgentSource)
    season: str
    frozen_utc: str
    git_commit: str | None = None
    code_hash: str
    scoring_version: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME.match(v):
            raise ValueError("name: 1-48 letters, digits, spaces or _.:+()'-")
        return v

    @field_validator("season")
    @classmethod
    def _season(cls, v: str) -> str:
        if not SEASON.match(v):
            raise ValueError("season: YYYY-YYYY")
        return v

    def checked_genome(self, spec=None):
        from snowagent.lab.schemas.genome import AgentGenome

        return AgentGenome.model_validate(self.genome, context={"spec": spec} if spec else None)


def entry_record(paths: LabPaths, run_id: str, r: int, agent_id: str, repo: Path, season_start: str = "09-15",
                 now: datetime | None = None) -> dict:
    """The frozen entry of one agent of a committed round, for the winter in progress at ``now``."""
    from snowagent.lab.competition.scoring import SCORING_VERSION
    from snowagent.lab.settings import season_key
    from snowagent.lab.storage.provenance import git_commit
    from snowagent.lab.training.cache import code_hash

    now = now or datetime.now(UTC)
    name, genome, source = agent_parts(paths, run_id, r, agent_id)
    rec = {"name": name, "genome": genome, "source": source, "season": season_key(now, season_start),
           "frozen_utc": now.isoformat(timespec="seconds"), "git_commit": git_commit(repo), "code_hash": code_hash(),
           "scoring_version": SCORING_VERSION}
    BlindEntry.model_validate(rec).checked_genome()
    return rec


def parse_entries(files: dict[str, bytes]) -> tuple[list[tuple[str, BlindEntry]], list[str]]:
    """Valid entries, oldest first, and a note for every file skipped (the branch is data, validated strictly)."""
    ok, skipped = [], []
    for fname, blob in sorted(files.items()):
        try:
            e = BlindEntry.model_validate(json.loads(blob))
            ok.append((e.checked_genome().agent_id, e))
        except (ValueError, ValidationError, json.JSONDecodeError) as exc:
            skipped.append(f"{fname}: {str(exc).splitlines()[0][:200]}")
    ok.sort(key=lambda x: x[1].frozen_utc)
    return ok, skipped


def _path(season: str, agent_id: str) -> str:
    return f"{FOLDER}/{season}/{agent_id}.json"


def frozen(repo: Path, season: str | None = None) -> list[dict]:
    """The frozen entries on the branch (one winter, or all), oldest first."""
    fetch(repo)
    files = {}
    for s in ([season] if season else _seasons(repo)):
        files |= {f"{s}/{k}": v for k, v in branch_files(repo, ref=REF, fetch=False, folder=f"{FOLDER}/{s}").items()}
    entries, _ = parse_entries(files)
    return [{"id": aid, "name": e.name, "season": e.season, "frozen_utc": e.frozen_utc, "git_commit": e.git_commit,
             **e.source.model_dump()} for aid, e in entries if season is None or e.season == season]


def _seasons(repo: Path) -> list[str]:
    from subprocess import run

    r = run(["git", "ls-tree", "--name-only", REF, f"{FOLDER}/"], cwd=repo, capture_output=True, text=True)
    return [Path(n).name for n in r.stdout.split("\n") if SEASON.match(Path(n).name)] if r.returncode == 0 else []


def freeze(repo: Path, record: dict) -> str:
    """Add an entry to its winter on the branch. Refused for an agent already frozen that winter, or when the winter
    has ``MAX_ENTRIES`` entries. Never replaces or removes an entry."""
    e = BlindEntry.model_validate(record)
    aid = e.checked_genome().agent_id
    current = frozen(repo, e.season)
    if aid in {x["id"] for x in current}:
        raise SendError(f"{e.name} is already frozen for {e.season}.")
    if len(current) >= MAX_ENTRIES:
        raise SendError(f"The {e.season} blind test already has {MAX_ENTRIES} agents.")
    blob = _git(repo, "hash-object", "-w", "--stdin", stdin=(json.dumps(record, indent=1) + "\n").encode())

    def add(index: Path) -> None:
        _git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{_path(e.season, aid)}", index=index)

    return _commit(repo, add, f"Blind test {e.season}: freeze {e.name} ({aid})")


def results(repo: Path, season: str) -> dict | None:
    """The daily update's latest blind-test results for a winter (``blind_results/<season>.json`` on the branch,
    ADR-090), or None before the first scoring. Data from a branch: read as JSON and only displayed."""
    from subprocess import run

    fetch(repo)
    r = run(["git", "show", f"{REF}:{RESULTS_FOLDER}/{season}.json"], cwd=repo, capture_output=True, timeout=60)
    if r.returncode != 0 or len(r.stdout) > 1_000_000:
        return None
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) and isinstance(d.get("entries"), list) else None


def result_line(e: dict) -> str:
    """One plain sentence for an entry's results (``results``)."""
    n = e.get("cases") or 0
    if not n or e.get("composite") is None or e.get("standard_composite") is None:
        return "no pit dug since its freeze has been scored yet"
    d = e["composite"] - e["standard_composite"]
    return (f"{n} pit case{'s' if n != 1 else ''} so far: {e['composite']:.3f} against {e['standard_composite']:.3f} "
            f"for standard SNOWPACK ({d:+.3f}), better on {e.get('won', 0)}, worse on {e.get('lost', 0)}")
