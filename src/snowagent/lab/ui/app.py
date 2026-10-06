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


def default_workers() -> int:
    """The Workers default of every form: the performance cores on a Mac (``hw.perflevel0.physicalcpu``; the
    efficiency cores would slow SNOWPACK runs), else all but one core, at most 8 either way."""
    import platform
    import subprocess

    n = os.cpu_count() or 1
    if platform.system() == "Darwin":
        try:
            n = int(subprocess.run(["sysctl", "-n", "hw.perflevel0.physicalcpu"], capture_output=True, text=True,
                                   timeout=5).stdout.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    else:
        n = n - 1
    return max(1, min(8, n))


def repo_root(start: str | Path) -> Path:
    """The checkout holding config/lab.yaml, searched upward from a page's file (any working directory works)."""
    p = Path(start).resolve()
    for d in [p, *p.parents]:
        if (d / "config" / "lab.yaml").exists():
            return d
    return Path.cwd()


def config_path(page_file: str | Path) -> Path:
    """The lab configuration the app uses (``SNOWAGENT_LAB_CONFIG``, else the checkout's config/lab.yaml)."""
    return Path(os.environ.get(CONFIG_ENV) or repo_root(page_file) / "config" / "lab.yaml")


def lab_context(page_file: str | Path) -> tuple[LabConfig, LabPaths]:
    root = repo_root(page_file)
    cfg = load_lab_config(config_path(page_file))
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
        f"No lab data in `{paths.root}` yet. Use **Set up data** on the Home page, or from the repository root run:"
        "\n\n"
        "```\nsnowagent lab prepare\nsnowagent lab init\nsnowagent lab import\n```\n"
        "`lab import` reads `data/interim/obs/observed_profiles.jsonl` (built by `snowagent obs profiles`) and the "
        "station files under `data/raw/fts360` and `data/interim` (restored by `snowagent update bootstrap` in a "
        "fresh checkout; `lab prepare` runs it). See docs/lab/web_interface.md.")


def default_run_index(root: Path, runs: list[str], scoring_version: str) -> int:
    """Index of the run a page opens on: the most informative one, not merely the newest.

    Preference, in order: running now (its process alive), scored under the current scoring version, finished, most
    cases, most rounds; ties go to the earlier entry of ``runs`` (the pages list newest first). A run whose files
    cannot be read ranks last.
    """
    from snowagent.lab.services.jobs import pid_alive

    def rank(i_run: tuple[int, str]) -> tuple:
        i, run_id = i_run
        try:
            meta = json.loads((root / run_id / "run.json").read_text())
            plan = meta.get("plan", meta)
            status_file = root / run_id / "status.json"
            status = json.loads(status_file.read_text()) if status_file.is_file() else {"state": "finished"}
            state = status.get("state")
            live = state == "running" and pid_alive(status.get("pid"))
            return (live, plan.get("scoring_version") == scoring_version, state == "finished",
                    len(plan.get("case_ids") or []), int(plan.get("rounds") or 1), -i)
        except (OSError, ValueError, TypeError, AttributeError):
            return (False, False, False, -1, -1, -i)

    return max(enumerate(runs), key=rank)[0] if runs else 0


def fmt_duration(seconds: float) -> str:
    m = int(round(seconds / 60))
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def finish_text(seconds: float | None, now=None) -> str:
    """'about 3 h 10 min, done around 15:40' in the app machine's local time (the Mac the run is on)."""
    from datetime import datetime, timedelta

    if seconds is None:
        return "not known yet"
    now = now or datetime.now().astimezone()
    return f"about {fmt_duration(seconds)}, done around {(now + timedelta(seconds=seconds)):%H:%M}"

