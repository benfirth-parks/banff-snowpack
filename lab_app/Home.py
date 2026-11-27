"""Snowpack Agent Lab: the app's entry point (`snowagent lab app`, `streamlit run lab_app/Home.py`). It only builds
the page menu, grouped by what each page is for (ADR-081, ADR-119: `snowagent.lab.ui.pages` lists them); every page
is its own script under `pages/`."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from snowagent.lab.ui.app import repo_root
from snowagent.lab.ui.pages import DEFAULT, MENU, PAGES
from snowagent.obs.pits import keep_pits

DIR = Path(__file__).resolve().parent / "pages"

# The pit files left git (ADR-165): an update that deleted them from this computer's disk gets them back here.
keep_pits(repo_root(__file__))

menu = st.navigation({
    group: [st.Page(DIR / PAGES[key][0], title=PAGES[key][1], icon=PAGES[key][2], default=key == DEFAULT)
            for key in keys]
    for group, keys in MENU.items()}, expanded=True)
try:
    menu.run()
except Exception:
    # A page can fail when the files changed under the running app (a `git pull` without a restart): its new
    # script meets the old code in memory. Then say so and offer the restart, instead of the error (ADR-119).
    from snowagent.lab.ui.status import restart_notice

    if not restart_notice(st, repo_root(__file__), key="restart-after-error"):
        raise
