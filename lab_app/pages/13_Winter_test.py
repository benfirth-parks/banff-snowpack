"""Winter-by-winter test (ADR-125): start, follow and resume `snowagent lab winter-test` (estimate first), and read
how a kind of training does on winters it never saw, winter by winter, with the average and the spread."""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from snowagent.lab.services import labels
from snowagent.lab.services.jobs import ACTIVE, JobBusy, latest_job, pid_alive
from snowagent.lab.services.training import running_training
from snowagent.lab.services.winter_test import (
    SLOW,
    WINTER_EVOLUTIONS,
    WINTER_PRESETS,
    estimate_words,
    plain_summary,
    pts,
    resume_winter_args,
    signed,
    start_winter_estimate,
    start_winter_test,
    took,
    winter_args,
    winter_estimate_for,
)
from snowagent.lab.training.loso import WINTER_TEST, list_checks, load_check
from snowagent.lab.ui.app import config_path, default_workers, lab_context, page_header, repo_root
from snowagent.lab.ui.jobs import job_block

page_header(st, "Winter-by-winter test",
            "How well does a kind of training do on a winter it has never seen? The winters are split into "
            "groups. The training is repeated with one group held out, the held-out winters are forecast, and "
            "this is done until every winter has been held out once. You get a score for every winter, the "
            "average, and how much it varies.",
            page="winter")
cfg, paths = lab_context(__file__)
st.caption("Three agents are scored on each held-out winter: the agent the training produced, the same model with "
           "its starting settings (what training added), and standard SNOWPACK (the bar to beat). The locked test "
           "winters are kept out of every part of it, so they stay a clean final test. Parker Ridge is a separate "
           "check.")

tests = list_checks(paths, WINTER_TEST)
job = latest_job(paths, "winter-test")
running = bool(job and job["state"] in ACTIVE)
other_run = None if running else running_training(paths)  # a training run, or a promotion check's re-training
with st.expander("Start or resume a test", expanded=not tests or running):
    root = repo_root(__file__)
    k1, k2 = st.columns(2)
    evo = k1.selectbox("What to test", list(WINTER_EVOLUTIONS), format_func=lambda k: labels.titled(k, paths),
                       help="the evolution whose training is tested")
    preset = k2.selectbox("How thorough", list(WINTER_PRESETS),
                          help="Quick tests a short training. Full training trains as long as a real training run, "
                               "so it shows what a real run does on unseen winters. Every winter on its own holds "
                               "each winter out alone. The times are for the column model on the Mac; the "
                               "estimate below is measured on this computer.")
    p = WINTER_PRESETS[preset]
    k3, k4 = st.columns(2)
    workers = k3.number_input("Workers", 1, max(1, os.cpu_count() or 1), default_workers(), key="winter-workers",
                              help="how many cases run at once: the Mac's performance cores by default")
    engine = k4.selectbox("Engine", ["auto", "none"], key="winter-engine",
                          help="auto: with SNOWPACK (needed for the SNOWPACK bar); none: SNOWPACK skipped")
    shape = (f"{p['folds']} groups of winters (dealt out in turn, so every group has old and recent winters)"
             if p["folds"] else "Every winter held out on its own")
    st.caption(f"{shape}; for each, a training of {p['rounds']} rounds of {p['population']} agents.")
    if evo in SLOW:
        st.caption("SNOWPACK runs in every round of this training (an hour or more a round), so this test takes "
                   "days, not hours. The estimate says how long.")
    args = winter_args(initial=WINTER_EVOLUTIONS[evo], workers=int(workers), engine=engine, **p)
    cid = st.text_input("Test name (the same name resumes the test)",
                        f"winter-{evo.replace('_', '-')}-{'all' if not p['folds'] else str(p['folds']) + 'f'}"
                        f"-r{p['rounds']}-p{p['population']}")
    est = winter_estimate_for(paths, args)
    b1, b2, _ = st.columns([1, 1, 2])
    if b1.button("1. Estimate the time"):
        try:
            est = start_winter_estimate(paths, config_path(__file__), root, args)
            st.info("Estimating (seconds to a minute): the estimate appears below by itself.")
        except JobBusy as exc:
            st.error(str(exc))
    if other_run:
        st.warning("A training run is going now (see the top of the page). Start this test when it has ended: the "
                   "two would share the computer's cores and both would take much longer.")
    if b2.button("2. Start the test", type="primary",
                 disabled=not (est and est["state"] == "finished") or running or bool(other_run)):
        try:
            job = start_winter_test(paths, config_path(__file__), root, args, cid.strip())
            st.success("The test has started. It runs on its own and shows at the top of every page.")
        except JobBusy as exc:
            st.error(str(exc))
    if est:
        st.markdown("**Estimate**")
        words = estimate_words(est)
        if words:
            st.info(words, icon="⏱️")
        job_block(st, paths, est, key="winter-estimate", tail_lines=12, controls=False)
    else:
        st.caption("Estimate first: the test says how long it expects to take before you commit to it.")
    going = (job.get("refs") or {}).get("check_id") if running else None  # the test running now needs no resume
    unfinished = [c for c in tests if c != going and load_check(paths, c)["result"] is None]
    if unfinished:
        st.markdown("**Resume a test**")
        r1, r2 = st.columns([2, 1])
        rcid = r1.selectbox("Unfinished test", unfinished)
        rworkers = r2.number_input("Workers", 1, max(1, os.cpu_count() or 1), default_workers(), key="winter-rw")
        if st.button("Resume the test", disabled=running or bool(other_run)):
            try:
                plan = load_check(paths, rcid)["check"]["plan"]
                job = start_winter_test(paths, config_path(__file__), root,
                                        resume_winter_args(plan, int(rworkers), engine), rcid)
                st.success("Resuming: the groups that were finished are kept.")
            except (JobBusy, FileNotFoundError, KeyError) as exc:
                st.error(str(exc))
    if job:
        st.markdown("**Latest test job**")
        job_block(st, paths, job, key="winter-test")
    with st.expander("The same from Terminal"):
        st.caption("`snowagent lab winter-test --initial column --workers 8` (5 groups of winters, 6 rounds of 10 "
                   "agents; it prints an estimate first). `--every-winter` holds each winter out on its own.")

if not tests:
    st.info("No winter-by-winter test yet. Start one above.")
    st.stop()

st.subheader("Results")
cid = st.selectbox("Test", tests)
chk = load_check(paths, cid)
res, folds, plan = chk["result"], chk["folds"], chk["check"]["plan"]
groups = plan.get("folds") or []
evolution = labels.run_evolution(plan)
st.caption(f"{labels.name(evolution, paths) if evolution else 'Custom'} · {len(plan['seasons'])} winters in "
           f"{len(groups)} groups · {plan['rounds']} rounds of {plan['population']} agents for each group")
if res is None:
    stt = chk.get("status") or {}
    gone = stt.get("state") == "running" and not pid_alive(stt.get("pid"))
    (st.warning if gone else st.info)(
        ("Interrupted (its process ended): resume it above. " if gone else "In progress: ")
        + f"{len(folds)} of {len(groups)} groups of winters done.")
    rows = sorted((w for f in folds for w in f.get("winters") or []), key=lambda w: w["season"])
else:
    for line in plain_summary(res):
        st.markdown(line)
    rows = res.get("per_winter") or []
    if took(res):
        st.caption(took(res))
if rows:
    st.dataframe(pd.DataFrame([{"winter": w["season"], "pits": w["cases"], "trained": pts(w["evolved"]),
                                "untrained": pts(w["start"]), "SNOWPACK": pts(w["snowpack"]),
                                "trained vs SNOWPACK": signed(w["difference"]),
                                "training added": signed(w["training_gain"])} for w in rows]),
                 width="stretch", hide_index=True)
    st.caption("Scores out of 100 (higher is better). 'trained vs SNOWPACK' and 'training added' are differences in "
               "points: positive means the trained agent did better on that winter.")
    d = pd.DataFrame([{"winter": w["season"], "difference": 100 * w["difference"]} for w in rows
                      if w.get("difference") is not None]).set_index("winter")
    if len(d):
        st.bar_chart(d, y="difference", y_label="trained minus SNOWPACK (points)")
