"""Promotion check (ADR-119; before it the end of the Training page): start, follow and resume the
leave-one-season-out check of an evolved agent (`snowagent lab check-loso`, estimate first; ADR-069, ADR-077) and
read its verdict. An agent can reach site output only through this check, and then only by the owner's decision."""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from snowagent.lab.services.jobs import ACTIVE, JobBusy, latest_job, pid_alive
from snowagent.lab.services.training import run_overview
from snowagent.lab.services.workflow import (
    check_args,
    estimate_for,
    resume_check_args,
    start_check,
    start_check_estimate,
)
from snowagent.lab.training.loso import RULE, list_checks, load_check, seeded_saw
from snowagent.lab.ui.app import config_path, default_workers, lab_context, page_header, repo_root
from snowagent.lab.ui.jobs import job_block
from snowagent.lab.ui.training import no_runs, pick_run

page_header(st, "Promotion check",
            "The fair test of an evolved agent: the training is repeated once for every winter with that winter "
            "held out, and the agent has to beat standard SNOWPACK on the winters it never saw. It can take many "
            "hours, so it estimates the time first.",
            page="check")
cfg, paths = lab_context(__file__)
st.caption("An evolved agent can reach site output only if it beats SNOWPACK on seasons it never trained on "
           "(the project's rule), and even then only by the owner's decision: nothing is promoted automatically.")
with st.expander("The exact pass rule"):
    st.caption(f"Rule: {RULE}")

run_id = pick_run(st, paths)
if run_id is None:
    no_runs(st)
    st.stop()
ov = run_overview(paths, run_id)
plan = ov["plan"]
rounds_done = ov["rounds"]["round"].tolist() if len(ov["rounds"]) else []

checks = list_checks(paths, "promotion")
check_job = latest_job(paths, "check-loso")
with st.expander("Start or resume a promotion check",
                 expanded=not checks or bool(check_job and check_job["state"] != "finished")):
    if not rounds_done:
        st.info("This run has no finished round yet: its agents can be checked when round 1 is done.")
    elif plan.get("case_set") != "all":
        st.info("Choose another training run above: this one is itself one part of a promotion check.")
    else:
        root = repo_root(__file__)
        k1, k2, k3, k4 = st.columns(4)
        c_round = k1.number_input("Round", 1, max(rounds_done), max(rounds_done))
        c_rank = k2.number_input("Rank", 1, int(plan.get("population") or 2), 1,
                                 help="1 = the round's best agent")
        c_workers = k3.number_input("Workers", 1, max(1, os.cpu_count() or 1), default_workers(),
                                    key="check-workers",
                                    help="how many cases run at once: the Mac's performance cores by default")
        c_engine = k4.selectbox("Engine", ["auto", "none"], key="check-engine",
                                help="auto: with SNOWPACK (the normal choice); none: SNOWPACK skipped")
        k5, k6 = st.columns(2)
        c_rounds = k5.number_input(f"Rounds per fold (0 = as the run: {plan.get('rounds')})", 0, 1000, 0,
                                   help="fewer rounds: a cheaper check, but a weaker test of this run (it says so)")
        c_pop = k6.number_input(f"Population (0 = as the run: {plan.get('population')})", 0, 500, 0)
        all_seasons = list(plan.get("seasons") or [])
        c_seasons = st.multiselect("Held-out seasons", all_seasons, default=all_seasons,
                                   help="one fold per season; the verdict needs every season, so fewer seasons "
                                   "is a partial check (or one night's share of it)")
        ref = f"{run_id}/{int(c_round)}/{int(c_rank)}"
        args = check_args(ref, seasons=None if len(c_seasons) == len(all_seasons) else c_seasons,
                          rounds=int(c_rounds) or None, population=int(c_pop) or None, workers=int(c_workers),
                          engine=c_engine)
        cid = st.text_input("Check id (the same id resumes the check)",
                            f"{run_id}-r{int(c_round)}-k{int(c_rank)}-loso"
                            + ("-reduced" if int(c_rounds) or int(c_pop) else "")
                            + ("" if len(c_seasons) == len(all_seasons) else f"-{len(c_seasons)}s"))
        est_job = estimate_for(paths, args)
        unfair = seeded_saw(plan, c_seasons)
        if unfair:
            st.error(f"This run started from agents of "
                     f"{', '.join(sorted({x['run_id'] for x in plan['seeded_from']}))}, which had already trained on "
                     f"{', '.join(unfair)}. Every fold would start from them, so holding those winters out is not a "
                     "fair test. Pick that earlier run above and check its agent instead.")
        b1, b2, _ = st.columns([1, 1, 2])
        if b1.button("1. Estimate the time", disabled=not c_seasons or bool(unfair)):
            try:
                est_job = start_check_estimate(paths, config_path(__file__), root, args)
                st.info("Estimating (seconds to a minute): the estimate appears below by itself.")
            except JobBusy as exc:
                st.error(str(exc))
        running_check = bool(check_job and check_job["state"] in ACTIVE)
        if b2.button("2. Start the check", type="primary",
                     disabled=not (est_job and est_job["state"] == "finished") or running_check or not c_seasons
                     or bool(unfair)):
            try:
                check_job = start_check(paths, config_path(__file__), root, args, cid.strip())
                st.success("The promotion check has started. It may take hours; it runs on its own and shows at "
                           "the top of every page.")
            except JobBusy as exc:
                st.error(str(exc))
        if est_job:
            st.markdown("**Estimate**")
            job_block(st, paths, est_job, key="check-estimate", tail_lines=12, show_log=True)
        else:
            st.caption("Estimate first: the check prints how long it expects to take before you commit to it.")
        if cid.strip() in checks:
            st.caption(f"A check `{cid.strip()}` exists: starting it again resumes it (it must have the same options).")
    unfinished = [c for c in checks if load_check(paths, c)["result"] is None]
    if unfinished:
        st.markdown("**Resume a check**")
        r1, r2, r3 = st.columns([2, 1, 1])
        rcid = r1.selectbox("Unfinished check", unfinished)
        rworkers = r2.number_input("Workers", 1, max(1, os.cpu_count() or 1), default_workers(),
                                   key="resume-workers")
        rengine = r3.selectbox("Engine", ["auto", "none"], key="resume-engine")
        if st.button("Resume check", disabled=bool(check_job and check_job["state"] in ACTIVE)):
            try:
                check_job = start_check(paths, config_path(__file__), repo_root(__file__),
                                        resume_check_args(paths, rcid, int(rworkers), rengine), rcid)
                st.success(f"Resuming `{rcid}`: finished folds are kept.")
            except (JobBusy, FileNotFoundError, KeyError) as exc:
                st.error(str(exc))
    if check_job:
        st.markdown("**Latest check job**")
        job_block(st, paths, check_job, key="check-loso")

if not checks:
    st.info("No promotion check yet. Start one above: it estimates the time first, and the check itself may take "
            "hours.")
    if rounds_done:
        with st.expander("The same from Terminal"):
            st.caption(f"`snowagent lab check-loso --genome {run_id}/{rounds_done[-1]}/1 --workers 4` (it prints an "
                       "estimate first).")
else:
    st.subheader("Results")
    cid = st.selectbox("Check", checks)
    chk = load_check(paths, cid)
    res, folds = chk["result"], chk["folds"]
    st.caption(f"Genome `{chk['check']['plan']['genome_ref']}` · seasons {', '.join(chk['check']['plan']['seasons'])}"
               f" · rule: {RULE}")
    if res is None:
        stt = chk.get("status") or {}
        gone = stt.get("state") == "running" and not pid_alive(stt.get("pid"))
        (st.warning if gone else st.info)(
            ("Interrupted (its process ended): resume it above. " if gone else "In progress: ")
            + f"{stt.get('phase', 'starting')} ({len(folds)} of {len(chk['check']['plan']['seasons'])} folds done).")
    else:
        verdict = "PASS" if res["passed"] else "FAIL"
        (st.success if res["passed"] else st.error)(
            f"{verdict}: pooled held-out composite, evolved {res['pooled_evolved_composite']} vs SNOWPACK "
            f"{res['pooled_incumbent_composite']} on {res['pooled_cases']} cases; wins {res['wins']}, losses "
            f"{res['losses']}, ties {res['ties']}."
            + (" A PASS makes the agent a candidate only: promotion to site output is the owner's decision."
               if res["passed"] else " The evolved agent stays a research entry; SNOWPACK remains the site model."))
        if res.get("differs_from_training_run"):
            st.caption("Reduced configuration (" + ", ".join(
                f"{k} {v['check']} instead of {v['training_run']}" for k, v in res["differs_from_training_run"].items())
                + "): a weaker test of the training run.")
    if folds:
        st.dataframe(pd.DataFrame([{"held-out season": f["season"], "cases": f.get("holdout_cases"),
                                    "fold winner": f["winner"]["label"], "family": f["winner"]["family"],
                                    "evolved": f.get("winner_composite"), "SNOWPACK": f.get("incumbent_composite"),
                                    "difference": f.get("difference"), "outcome": f.get("outcome")}
                                   for f in folds]), width="stretch", hide_index=True)
