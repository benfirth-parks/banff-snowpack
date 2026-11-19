"""The top of every page (ADR-119): the bar of what is running now and what ended lately, and the notice that a
newer version of the app is ready (or that its files changed and it needs a restart). Drawn by ``page_header``."""

from __future__ import annotations

import time
from pathlib import Path

from snowagent.lab.services import activity as act
from snowagent.lab.services import update
from snowagent.lab.services.jobs import JobBusy
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.ui.pages import PAGES, link

RESTART_PAUSE_S = 1.5  # lets the browser show the line before the app goes away


def restart(st, text: str = "Restarting: this page comes back by itself in a few seconds.") -> None:
    st.info(text, icon="🔄")
    time.sleep(RESTART_PAUSE_S)
    update.restart()


def restart_after_update(st, paths: LabPaths, root: Path) -> None:
    """Right after the app's own update job, restart without asking (the person pressed Update and restart)."""
    if update.can_restart() and update.stale(root) and update.just_updated(paths):
        restart(st, "The new version is in. The app is restarting: this page comes back by itself in a few "
                    "seconds.")


def restart_notice(st, root: Path, key: str = "restart-app") -> bool:
    """The files on disk are newer than the running app (an update, or a `git pull` in Terminal): say so and offer
    the restart. True when the notice was drawn."""
    if not update.stale(root):
        return False
    if not update.can_restart():  # started with a plain `streamlit run`: nothing is waiting to start it again
        st.info("**The app's files have changed** (a newer version was fetched). Stop the app (Ctrl-C in its "
                "Terminal window) and start it again to use it.", icon="🔄")
        return True
    c1, c2 = st.columns([5, 1])
    c1.info("**The app's files have changed** (a newer version was fetched). Restart the app to use it. Anything "
            "running in the background keeps going.", icon="🔄")
    if c2.button("Restart the app", type="primary", key=key):
        restart(st)
    return True


def update_notice(st, paths: LabPaths, root: Path) -> None:
    """Restart needed (the files on disk are newer than the running app), or update available (GitHub is newer
    than the files). The app restarts by itself only after an update it ran; otherwise it offers a button."""
    restart_after_update(st, paths, root)
    if update.updating(paths):
        return  # the bar shows its steps, and the restart follows by itself: nothing to offer until then
    if restart_notice(st, root):
        return
    info = update.latest(root)
    if not info or info["state"] != "behind":
        return
    busy = update.busy_jobs(paths)
    news = "".join(f"\n- {n}" for n in info.get("news", []))
    if info.get("more_news"):
        news += f"\n- and {info['more_news']} more"
    extra = (" It also rebuilds the SNOWPACK engine, so it takes about 5 minutes." if info.get("rebuild")
             else " It takes under a minute.")
    c1, c2 = st.columns([5, 1])
    day = str(info.get("version", "")).split(" (")[0]  # '19 Nov 2026 (67c1e60)': the day says enough here
    c1.info("**A newer version of the app is ready**" + (f" (of {day})." if day else ".") + extra
            + (f"\n\nWhat is new:{news}" if news else ""), icon="⬇️")
    if c2.button("Update and restart", type="primary", disabled=bool(busy), key="update-app",
                 help="fetches the new version from GitHub, then restarts the app by itself"):
        try:
            update.start_update(paths, root, info)
            update.forget()
            st.rerun()
        except JobBusy as exc:
            st.error(str(exc))
    if busy:
        c1.caption("Something is running, so the update waits: it is offered here again when that has ended, so "
                   "that no run gets new code half way.")


def sidebar_version(st, root: Path) -> None:
    """The version in use and whether GitHub has a newer one, small, under the menu."""
    line = f"Version of {update.version_text(root)}"
    info = update.latest(root)
    if info:
        line += {"current": " · up to date", "behind": " · **newer version ready**",
                 "diverged": " · this copy has changes of its own: update it from Terminal",
                 }.get(info["state"], f" · could not check for a newer one ({info.get('reason', 'unknown')})")
    st.sidebar.caption(line)
    if update.enabled() and st.sidebar.button("Check for a newer version", key="check-update"):
        update.forget()
        update.latest(root, wait=True)
        st.rerun()


def _line(item: dict) -> str:
    sep = ": " if item["active"] else " "
    return f"**{item['what']}**{sep}{item['detail']}" if item["detail"] else f"**{item['what']}**"


def status_bar(st, paths: LabPaths, root: Path | None = None, here: str | None = None) -> None:
    """What is running (with its step, or the round and the time left), then what ended lately, on every page. It
    re-reads the jobs every few seconds while one runs and redraws the whole page when one starts or ends, so
    nothing needs a Refresh button."""
    drawn = act.signature(act.activity(paths))
    live = any(state in ("starting", "running") for _, state in drawn)

    @st.fragment(run_every=act.POLL_S if live else None)
    def bar() -> None:
        items = act.activity(paths)
        if root is not None:
            restart_after_update(st, paths, root)  # here too: this code is in memory, whatever the new files hold
        if act.signature(items) != drawn:
            st.rerun()  # something started or ended: every part of the page may read differently now
        for item in items:
            tag = item["job_id"] or item["run_id"]
            c1, c2, c3 = st.columns([5.5, 2, 0.8])
            if item["active"]:
                c1.info(_line(item), icon="⏳")
                if item["share"] is not None:
                    c1.progress(item["share"])
            elif item["state"] == "finished":
                c1.success(_line(item) + ".", icon="✅")
            elif item["state"] == "failed":
                c1.error(_line(item) + (". The details are below." if here == "jobs"
                                        else ". The details are on the Jobs page."), icon="❌")
            else:
                c1.warning(_line(item) + ". Resume continues it where it stopped.", icon="⏸️")
            page = "jobs" if item["state"] == "failed" else item["page"]
            if page and page != here:
                with c2:
                    link(st, page, "Open " + PAGES[page][1].split(" (")[0])  # 'Banff trials (leaderboard)': short
            if not item["active"] and item["job_id"] and c3.button("Hide", key=f"bar-hide-{tag}"):
                act.dismiss(paths, item["job_id"])
                st.rerun()

    bar()
