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


def _through_the_menu(st) -> None:
    """Streamlit lists every file under ``pages/`` as a menu of its own (its older multipage mode) until the entry
    point has run once. A first page view after a start that goes straight to a page (a refreshed tab, the app's
    own restart) would be drawn with that menu and without ``lab_app/Home.py``: turn the older mode off and draw
    again through the entry point. Uses a Streamlit internal, so any surprise there leaves things as they were."""
    try:
        from streamlit.runtime.pages_manager import PagesManager

        if PagesManager.uses_pages_directory:
            PagesManager.uses_pages_directory = False
            st.rerun()
    except (ImportError, AttributeError):
        pass


def page_header(st, title: str, about: str | None = None, page: str | None = None) -> None:
    """The top of every page (ADR-119): the title, the decision-support disclaimer (once), ``about`` (one plain
    sentence on what the page is for), then whether a newer version of the app is ready and what is running now
    (``page``: this page's key in ``ui.pages``, so the bar does not link a page to itself)."""
    from snowagent.lab.ui.status import sidebar_version, status_bar, update_notice

    _through_the_menu(st)
    st.set_page_config(page_title=f"{title} · Snowpack Agent Lab", layout="wide")
    st.sidebar.caption("**Snowpack Agent Lab**: research only")
    st.title(title)
    st.warning(LAB_DISCLAIMER, icon="⚠️")
    if about:
        st.markdown(about)
    root = repo_root(__file__)
    paths = LabPaths(Path(os.environ.get(DATA_ROOT_ENV) or root / "data" / "lab"))
    sidebar_version(st, root)
    update_notice(st, paths, root)
    status_bar(st, paths, root, here=page)


def empty_state(st, paths: LabPaths) -> None:
    """A page that has nothing to show before the data is set up: where to do that, in the app."""
    from snowagent.lab.ui.pages import link

    st.info("**No lab data yet.** Open the Overview page and press **Run set-up**: it fetches the pits and the "
            "weather and loads them, in the background.")
    link(st, "overview", "Open the Overview page")
    with st.expander("The same from Terminal"):
        st.caption(f"Lab data folder: `{paths.root}`. From the repository root: `snowagent lab prepare`, `snowagent "
                   "lab init`, then `snowagent lab import` (it reads `data/interim/obs/observed_profiles.jsonl` and "
                   "the station files under `data/raw/fts360` and `data/interim`, which `lab prepare` restores in a "
                   "fresh checkout). See docs/lab/web_interface.md.")


def default_run_index(root: Path, runs: list[str], scoring_version: str) -> int:
    """Index of the run a page opens on: the most informative one, not merely the newest.

    Preference, in order: running now (its process alive), a training run rather than a promotion check's fold,
    scored under the current scoring version, finished, most
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
            fold = str(plan.get("case_set") or "").startswith("loso_")  # a promotion check's fold, not a run
            return (live, not fold, plan.get("scoring_version") == scoring_version, state == "finished",
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

