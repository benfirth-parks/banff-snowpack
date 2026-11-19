"""Start a run (ADR-119; the top of the Training page until then): set up the Parker Ridge check (ADR-117) and start
a local training run (`snowagent lab train`, a background job, never inside this app; ADR-066 to ADR-068, ADR-077).
The run is followed on Watch the run, its agents are on Run results, and the promotion check has its own page."""

from __future__ import annotations

import os

import streamlit as st

from snowagent.lab.genome import default_genome
from snowagent.lab.schemas.genome import STANDARD_FAMILIES, AgentFamily
from snowagent.lab.services import labels
from snowagent.lab.services.data import data_status
from snowagent.lab.services.jobs import ACTIVE, JobBusy, latest_job
from snowagent.lab.services.training import (
    PRESETS,
    list_training_runs,
    running_training,
    seen_locked,
    start_training,
    stop_training,
)
from snowagent.lab.services.workflow import start_area_setup
from snowagent.lab.training import area_check
from snowagent.lab.training.loop import (
    DRIFT_K,
    DRIFT_K_MAX,
    LOCKED_SEASONS,
    STOP_WHEN_FLAT,
    committed_rounds,
    slot_families,
    training_root,
)
from snowagent.lab.ui.app import (
    config_path,
    default_workers,
    empty_state,
    lab_context,
    page_header,
    repo_root,
)
from snowagent.lab.ui.jobs import job_block
from snowagent.lab.ui.pages import link, where

SELECTIONS = {"Even across winters and plots": "consistent", "Highest average": "composite"}  # ADR-087

page_header(st, "Start a training run",
            "The agents are tried on the past pits round after round: each round the best ones are kept and "
            "varied to make the next round. A run takes hours and goes on in the background.",
            page="start")
cfg, paths = lab_context(__file__)

built = (paths.benchmark / "all").is_dir() and any((paths.benchmark / "all").glob("*/*/manifest.json"))
runs = list_training_runs(paths)
flash = st.session_state.pop("train-flash", None)  # a message from the click that started a run or a set-up
if flash:
    st.success(flash)
    if flash.startswith("Started training run"):
        link(st, "watch", "Watch the run")


def train_job_active() -> bool:
    job = latest_job(paths, "train")
    return bool(job and job["state"] in ACTIVE)


# ------------------------------------------------------------------------------------------- start a run
with st.container():  # the body keeps the indentation it had inside the old Training page's expander
    if not built:
        if not any(data_status(paths).values()):
            empty_state(st, paths)
        st.info(f"No benchmark cases yet: build them first on {where('cases')}.")
        link(st, "cases", "Build the benchmark cases")
    busy = running_training(paths) or (train_job_active() and "a training job")
    if busy:
        b1, b2 = st.columns([3, 1])
        b1.info("**A training run is going already.** One run at a time: a new one can start when it has ended, "
                "or after you stop it (Resume continues it later).")
        if b2.button("Stop the run", key="train-stop-busy") and running_training(paths):
            stop_training(paths, running_training(paths))
            st.session_state["train-flash"] = "Stop requested: the run stops once its started cases finish."
            st.rerun()
    # ADR-117: the out-of-area check (Parker Ridge, Jasper): its cases are prepared once, then every new run can
    # score its tested agents on them each round, never training or choosing on them
    st.subheader(f"1. {labels.titled('jasper')} (optional, set up once)")
    area_st = area_check.status("jasper", paths.root)
    area_job = latest_job(paths, "area-setup")
    area_busy = bool(area_job and area_job["state"] in ACTIVE)
    p1, p2 = st.columns([3, 1])
    if area_busy:
        p1.info("The Parker Ridge check is being set up now: its progress is just below. You can start a run "
                "when it says finished.", icon="⏳")
    elif area_st["cases"]:
        p1.success(f"Parker Ridge check ready: {area_st['cases']} cases from {len(area_st['seasons'])} winters "
                   f"({area_st['seasons'][0]} to {area_st['seasons'][-1]}). New runs can score their agents on them "
                   "every round, without ever training on them.", icon="✅")
    else:
        p1.warning("Parker Ridge check not set up yet: press the button to copy the Jasper weather from GitHub, "
                   "load the Parker Ridge pits and build their cases (about 5-10 minutes). A run can start without "
                   "it; it then leaves that check out.")
    if p2.button("Update Parker Ridge check" if area_st["cases"] else "Set up Parker Ridge check",
                 disabled=area_busy, key="area-setup",
                 help="copies the Jasper weather (about 0.15 GB, once), then loads the Parker Ridge pits and builds "
                 "their cases; press it again after new Jasper pits arrive"):
        try:
            area_job = start_area_setup(paths, repo_root(__file__), "jasper")
            st.session_state["train-flash"] = ("Setting up the Parker Ridge check has started. Its progress is "
                                               "just below, and at the top of every page.")
            st.rerun()
        except JobBusy as exc:
            st.error(str(exc))
    if area_job and (area_busy or area_job["state"] != "finished"):
        job_block(st, paths, area_job, key="area-setup")
    st.subheader("2. The run")
    t = cfg.training
    evolutions = {p: labels.preset_evolution(PRESETS[p]) for p in PRESETS}
    preset_name = st.selectbox("Kind of run", list(PRESETS),
                               format_func=lambda p: labels.prefixed(evolutions[p], p, paths),
                               help="the kind of run: it fills in the options below, "
                               "which you can leave as they are; Custom keeps the configuration's defaults")
    st.caption(" · ".join(f"**{labels.name(e, paths)}** = {labels.EVOLUTIONS[e][1]}"
                          for e in dict.fromkeys(v for v in evolutions.values() if v)))
    pre, k = PRESETS[preset_name], list(PRESETS).index(preset_name)
    with st.form("train"):
        c1, c2, c3, c4 = st.columns(4)
        rounds = c1.number_input("Rounds", 1, 1000, pre.get("rounds", t.rounds), key=f"rounds-{k}",
                                 help="how many times the agents are tried, sorted and varied; the run can also "
                                 "stop by itself earlier, when the locked-winter score stops improving")
        population = c2.number_input("Population", 2, 500, pre.get("population", t.population), key=f"pop-{k}",
                                     help="how many agents are tried in each round")
        survivors = c3.number_input("Survivors", 1, 100, pre.get("survivors", t.survivors), key=f"surv-{k}",
                                    help="how many of the best agents carry on unchanged into the next round")
        workers = c4.number_input("Workers", 1, max(1, os.cpu_count() or 1), default_workers(),
                                  help="how many cases run at once: the Mac's performance cores by default")
        use_area = st.checkbox("Also score each round on the Parker Ridge pits (Jasper), never training on them",
                               value=bool(area_st["cases"]), disabled=not area_st["cases"], key=f"area-{k}",
                               help="after each round's choice, the same agents as the locked winters are scored on "
                               "the Parker Ridge cases: an area the agents never learn from (adds about one tenth "
                               "to each round)")
        with st.expander("Advanced (you can leave these as they are)"):
            a1, a2, a3, a4 = st.columns(4)
            strength = a1.number_input("Mutation strength", 0.01, 1.0, t.mutation_strength, 0.05,
                                       help="the chance that each gene changes in a child, and how far")
            cross = a2.number_input("Crossover share", 0.0, 1.0, t.crossover_share, 0.05,
                                    help="the share of children made by crossing the two survivors")
            seed = a3.number_input("Seed", 0, 2**31 - 1, 0, help="another seed gives an independent run")
            engine = a4.selectbox("Engine", ["auto", "none"],
                                  help="auto: the SNOWPACK binary (SNOWPACK_BIN or PATH); none: SNOWPACK skipped, "
                                  "the hybrid predicts from its other members")
            all_plots = [c.value for c in cfg.sites]
            plots = st.multiselect("Plots", all_plots, default=pre.get("plots") or all_plots, key=f"plots-{k}")
            case_types = st.multiselect("Case types", ["forecast_h72", "next_pit"],
                                        default=pre.get("case_types") or ["forecast_h72", "next_pit"],
                                        key=f"types-{k}")
            initial = st.multiselect("Initial population", [f.value for f in AgentFamily],
                                     default=pre.get("initial") or [f.value for f in STANDARD_FAMILIES],
                                     key=f"initial-{k}",
                                     help="the default genome of each chosen family (genome files: use the CLI); "
                                     "fewer than the survivors are filled with mutants of them. column = the lab's "
                                     "own snowpack model, no SNOWPACK")
            a5, a6 = st.columns(2)
            screen = a5.number_input("Screen cases (0 = off)", 0, 10000, pre.get("screen_cases", 30),
                                     key=f"screen-{k}",
                                     help="score a child with new SNOWPACK physics on this many cases first; only "
                                     "one beating the worst survivor there runs on every case")
            family_slots = a6.checkbox("Family slots", False,
                                       help="each round, one mutant of every family's best agent")
            locked_n = st.number_input("Locked test winters (most recent)", 0, 20, LOCKED_SEASONS,
                                       key=f"locked-{k}",
                                       help="these winters are never used to train or choose agents; each round's "
                                       "leaders are tested on them, a true unseen-winter score (0 = off)")
            sources = [x for x in runs if committed_rounds(training_root(paths) / x)]
            s1, s2 = st.columns([3, 1])
            seed_from = s1.selectbox("Start from an earlier run's agents", ["(none)"] + sources, key=f"seedfrom-{k}",
                                     help="adds the best evolved agents of that run's last round to the starting "
                                     "agents. If they trained on the winters this run locks, its locked-winter "
                                     "results are no longer a clean test (the page will say so)")
            seed_top = s2.number_input("How many", 1, 10, 2, key=f"seedtop-{k}",
                                       help="that many of its best agents (family defaults skipped)")
            sel_label = st.radio("Choose survivors by", list(SELECTIONS), index=0, horizontal=True,
                                 key=f"selection-{k}",
                                 help="even: the score less a penalty for doing much better in some winters and "
                                 "plots than others, so agents that are good everywhere survive (recommended); "
                                 "average: the score alone")
            drift_k = st.number_input("Penalty for drifting from standard settings", 0.0, DRIFT_K_MAX, DRIFT_K,
                                      step=0.001, format="%.3f", key=f"drift-{k}",
                                      help="taken off the score for every setting moved across its whole allowed "
                                      "range (a changed choice counts as one), so a change has to earn its place; "
                                      "0 = off")
            stop_flat = st.number_input("Stop when the locked-winter score is flat for (rounds)", 0, 100,
                                        STOP_WHEN_FLAT, key=f"stopflat-{k}",
                                        help="the run finishes on its own once the best score on the locked test "
                                        "winters has not improved for this many rounds in a row, so it does not "
                                        "keep going for nothing; the winner is the last round's best (0 = off; "
                                        "needs locked test winters)")
        start = st.form_submit_button("Start training", type="primary", disabled=not built or bool(busy))
    if start:
        n_slots = len(slot_families([default_genome(AgentFamily(f)) for f in initial]))
        if survivors >= population or not initial:
            st.error("Survivors must be fewer than the population, and choose at least one initial family.")
        elif family_slots and population - survivors < n_slots:
            st.error(f"Family slots need at least {n_slots} children per round (population - survivors).")
        elif not plots or not case_types:
            st.error("Choose at least one plot and one case type (under Advanced).")
        else:
            try:
                info = start_training(
                    paths, config_path(__file__), cwd=repo_root(__file__),
                    rounds=int(rounds), population=int(population), survivors=int(survivors),
                    mutation_strength=float(strength), crossover_share=float(cross), seed=int(seed),
                    workers=int(workers), engine=engine,
                    plots=None if len(plots) == len(cfg.sites) else plots,
                    case_types=None if len(case_types) == 2 else case_types,
                    initial=None if sorted(initial) == sorted(f.value for f in STANDARD_FAMILIES) else initial,
                    screen_cases=int(screen) or None, family_slots=bool(family_slots),
                    locked_seasons=int(locked_n), selection=SELECTIONS[sel_label], drift_penalty=float(drift_k),
                    stop_when_flat=int(stop_flat), seed_from=None if seed_from == "(none)" else seed_from, seed_top=int(seed_top),
                    area_check="jasper" if use_area and area_st["cases"] else None)
                seen = seen_locked(paths, None if seed_from == "(none)" else seed_from, int(locked_n))
                st.session_state["train-flash"] = (
                    "Started training run. It runs on its own: you can close the browser, but keep the computer "
                    "awake and plugged in. It shows at the top of every page once it has loaded its cases (a minute "
                    f"or so). (Run `{info['run_id']}`.)"
                    + (f" Note: the agents from `{seed_from}` already trained on {', '.join(seen)}, which this run "
                       "locks, so its locked-winter results will not be a clean test." if seen else ""))
                st.rerun()
            except JobBusy as exc:
                st.error(str(exc))
    with st.expander("The same from Terminal"):
        st.caption(f"`snowagent lab train --rounds {int(rounds)} --population {int(population)} "
                   f"--survivors {int(survivors)} --seed {int(seed)} --workers {int(workers)}"
                   f"{f' --screen-cases {int(screen)}' if screen else ''}{' --engine none' if engine == 'none' else ''}"
                   f" --locked-seasons {int(locked_n)}{' --area-check jasper' if use_area and area_st['cases'] else ''}` "
                   "(see docs/lab/training.md for times; the first round runs SNOWPACK once per case, and so does "
                   "every child with new SNOWPACK physics genes).")
