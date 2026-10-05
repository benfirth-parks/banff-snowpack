"""Shared pieces of the lab's Streamlit pages: locating config and data, the persistent disclaimer, empty states."""

from __future__ import annotations

import json
import os
from pathlib import Path

from snowagent.lab import LAB_DISCLAIMER
from snowagent.lab.settings import LabConfig, load_lab_config
from snowagent.lab.storage.paths import LabPaths

DATA_ROOT_ENV = "SNOWAGENT_LAB_DATA_ROOT"  # optional: another lab data directory
CONFIG_ENV = "SNOWAGENT_LAB_CONFIG"


def repo_root(start: str | Path) -> Path:
    """The checkout holding config/lab.yaml, searched upward from a page's file (any working directory works)."""
    p = Path(start).resolve()
    for d in [p, *p.parents]:
        if (d / "config" / "lab.yaml").exists():
            return d
    return Path.cwd()


def lab_context(page_file: str | Path) -> tuple[LabConfig, LabPaths]:
    root = repo_root(page_file)
    cfg = load_lab_config(Path(os.environ.get(CONFIG_ENV) or root / "config" / "lab.yaml"))
    paths = LabPaths(Path(os.environ.get(DATA_ROOT_ENV) or root / "data" / "lab"))
    return cfg, paths


def page_header(st, title: str) -> None:
    """Title plus the decision-support disclaimer, on every page (and in the sidebar)."""
    st.set_page_config(page_title=f"{title} · Snowpack Agent Lab", layout="wide")
    st.sidebar.caption("**Snowpack Agent Lab**: research only")
    st.sidebar.warning(LAB_DISCLAIMER, icon="⚠️")
    st.title(title)
    st.warning(LAB_DISCLAIMER, icon="⚠️")


def empty_state(st, paths: LabPaths) -> None:
    st.info(
        f"No lab data in `{paths.root}` yet. From the repository root run:\n\n"
        "```\nsnowagent lab init\nsnowagent lab import\n```\n"
        "`lab import` reads `data/interim/obs/observed_profiles.jsonl` (built by `snowagent obs profiles`) and the "
        "station files under `data/raw/fts360` and `data/interim` (restored by `snowagent update bootstrap` in a "
        "fresh checkout). See docs/lab/local_setup.md.")


def default_run_index(root: Path, runs: list[str], scoring_version: str) -> int:
    """Index of the run a page opens on: the most informative one, not merely the newest.

    Preference, in order: scored under the current scoring version, finished, most cases, most rounds; ties go to
    the earlier entry of ``runs`` (the pages list newest first). A run whose files cannot be read ranks last.
    """
    def rank(i_run: tuple[int, str]) -> tuple:
        i, run_id = i_run
        try:
            meta = json.loads((root / run_id / "run.json").read_text())
            plan = meta.get("plan", meta)
            status_file = root / run_id / "status.json"
            state = json.loads(status_file.read_text()).get("state") if status_file.is_file() else "finished"
            return (plan.get("scoring_version") == scoring_version, state == "finished",
                    len(plan.get("case_ids") or []), int(plan.get("rounds") or 1), -i)
        except (OSError, ValueError, TypeError, AttributeError):
            return (False, False, -1, -1, -i)

    return max(enumerate(runs), key=rank)[0] if runs else 0
