"""Snowpack Agent Lab: the app's entry point (`snowagent lab app`, `streamlit run lab_app/Home.py`). It only builds
the page menu, grouped by what each page is for (ADR-081); every page is its own script under `pages/`."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

PAGES = Path(__file__).resolve().parent / "pages"

menu = st.navigation({
    "Lab": [st.Page(PAGES / "0_Overview.py", title="Overview", icon="🏔️", default=True)],
    "Evolve agents": [st.Page(PAGES / "4_Training.py", title="Training", icon="🧬"),
                      st.Page(PAGES / "5_Arena.py", title="Arena", icon="🏁")],
    "Results": [st.Page(PAGES / "3_Leaderboard.py", title="Leaderboard and pits", icon="🏆")],
    "Data": [st.Page(PAGES / "1_Data_Explorer.py", title="Pits and weather", icon="📈"),
             st.Page(PAGES / "2_Benchmark_Cases.py", title="Benchmark cases", icon="🗂️")],
    "Background": [st.Page(PAGES / "6_Jobs.py", title="Jobs", icon="⚙️")],
})
menu.run()
