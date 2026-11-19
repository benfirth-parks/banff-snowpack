"""The app's pages (ADR-081, ADR-119): one list for the menu (lab_app/Home.py) and for the links between pages, in
the order a person works: set up, start a run, watch it, see how it did, then what to do with its best agent."""

from __future__ import annotations

# key: (file under lab_app/pages, title in the menu, icon)
PAGES: dict[str, tuple[str, str, str]] = {
    "overview": ("0_Overview.py", "Overview", "🏔️"),
    "start": ("4_Training.py", "Start a run", "▶️"),
    "watch": ("9_Run_progress.py", "Watch the run", "🧬"),
    "results": ("10_Run_results.py", "Run results", "🏅"),
    "arena": ("5_Arena.py", "Arena", "🏁"),
    "reports": ("7_Reports.py", "Training reports", "📄"),
    "blind": ("11_Blind_test_and_site.py", "Blind test and site", "🧊"),
    "check": ("12_Promotion_check.py", "Promotion check", "⚖️"),
    "group": ("8_Group_check.py", "Group check", "👥"),
    "leaderboard": ("3_Leaderboard.py", "Banff trials (leaderboard)", "🏆"),
    "explorer": ("1_Data_Explorer.py", "Pits and weather", "📈"),
    "cases": ("2_Benchmark_Cases.py", "Benchmark cases", "🗂️"),
    "jobs": ("6_Jobs.py", "Jobs", "⚙️"),
}
MENU: dict[str, list[str]] = {
    "Lab": ["overview"],
    "Evolutions (training)": ["start", "watch", "results", "reports", "arena"],
    "Competitions and checks": ["leaderboard", "blind", "check", "group"],
    "Data": ["explorer", "cases"],
    "Background": ["jobs"],
}
DEFAULT = "overview"


def where(key: str) -> str:
    """'Train agents › Watch the run': a page as the menu names it."""
    group = next(g for g, keys in MENU.items() if key in keys)
    return f"{group} › {PAGES[key][1]}"


def link(st, key: str, label: str | None = None) -> None:
    """A link to another page of the app. A page run on its own (tests, `streamlit run` of one page) has no menu to
    link into: it names the page instead."""
    file, title, icon = PAGES[key]
    try:
        st.page_link(f"pages/{file}", label=label or title, icon=icon)
    except Exception:  # noqa: BLE001 - StreamlitPageNotFoundError lives in a private module; any failure: plain text
        st.caption(f"{label or title}: {where(key)}")
