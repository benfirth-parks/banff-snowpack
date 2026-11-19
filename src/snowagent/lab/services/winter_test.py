"""The winter-by-winter test in the app and in plain words (ADR-125): its kinds of test, the command each starts, and
the summary a person reads. The test itself is `snowagent lab winter-test` (``training.loso.check_loso`` with no
checked genome); this module starts nothing heavy and changes no run, score or case."""

from __future__ import annotations

from pathlib import Path

from snowagent.lab.services.jobs import lab_command, latest_job, start_job, step
from snowagent.lab.storage.paths import LabPaths

# The page's kinds of test. Times are for the column-model families (no SNOWPACK runs while training) on the owner's
# Mac with 8 workers, estimated from the cloud smoke run of 2026-11-19, not measured there. On 4 cloud cores an agent
# takes 0.36 s a case, a case 0.24 s to load, a SNOWPACK run for the bar about 4.5 s and a group's cases 7.5 min to
# build: 5.7 h, 12.4 h and 30 h for the three kinds. With 8 workers the Mac did one and a half to two times that
# machine's work on SNOWPACK runs (2026-10-06: about 8 s a run at 8 at once, against 5.9-8.5 s at 4 at once), which
# puts the three at 3-4 h, 6-8 h and 14-20 h there. The page's own estimate uses the computer's measured times.
# Every group of winters needs its own copy of the cases (0.35 GB each).
WINTER_PRESETS: dict[str, dict] = {
    "Quick (about 3 hours)": {"folds": 5, "rounds": 6, "population": 10, "survivors": 2},
    "Overnight (about 7 hours)": {"folds": 5, "rounds": 12, "population": 12, "survivors": 2},
    "Every winter on its own (about 16 hours, 9 GB of disk)": {"folds": 0, "rounds": 6, "population": 10,
                                                              "survivors": 2},
}
# what is tested: an evolution (its key in ``labels.EVOLUTIONS``, which names it) -> what its training starts from
# on the command line (agent families; ``defaults`` is the standard population of a tuned-SNOWPACK run)
WINTER_EVOLUTIONS: dict[str, list[str]] = {
    "column": ["column"],
    "process": ["process"],
    "recipe": ["recipe"],
    "story": ["story"],
    "column_site": ["scorecard"],
    "tuned": ["defaults"],
}
SLOW = ("tuned",)  # SNOWPACK runs in every round of these: hours a round, so days for a test
MEMORISED_MIN = 0.005  # half a point out of 100: a smaller gap between seen and unseen winters is noise
FEW_PITS = 5  # a winter with fewer pits than this has a rough score of its own
CASE_SET_GB = 0.35  # one group's copy of the benchmark cases on disk (945 cases, measured 2026-11-19)


def winter_args(*, initial: list[str], folds: int, rounds: int, population: int, survivors: int, workers: int = 1,
                seed: int = 0, engine: str = "auto", include_locked: bool = False) -> list[str]:
    """The options of a `lab winter-test`, without the check id: an estimate and the test it precedes share them."""
    args = ["--rounds", str(int(rounds)), "--population", str(int(population)), "--survivors", str(int(survivors)),
            "--seed", str(int(seed)), "--workers", str(int(workers)), "--engine", engine]
    if folds:
        args += ["--folds", str(int(folds))]
    else:
        args += ["--every-winter"]
    for g in initial:
        args += ["--initial", g]
    if include_locked:
        args += ["--include-locked"]
    return args


def _common(paths: LabPaths, config: Path) -> list[str]:
    return ["--data-root", str(Path(paths.root).resolve()), "--config", str(Path(config).resolve())]


def start_winter_estimate(paths: LabPaths, config: Path, root: Path, args: list[str]) -> dict:
    cmd = lab_command("winter-test", *args, "--estimate-only", "--source", str(root.resolve()),
                      *_common(paths, config))
    return start_job(paths, "winter-estimate", "winter test estimate", [step("estimate", cmd)], cwd=root,
                     refs={"args": args})


def winter_estimate_for(paths: LabPaths, args: list[str]) -> dict | None:
    """The latest estimate job of exactly these options (None: estimate first)."""
    job = latest_job(paths, "winter-estimate")
    return job if job and (job.get("refs") or {}).get("args") == args else None


def estimate_words(job: dict | None) -> str | None:
    """A finished estimate job's answer as a sentence: how long the test should take on this computer, what part
    of that prepares the cases, how long each group of winters takes and the disk it needs. None while there is
    nothing to read."""
    import json

    from snowagent.lab.services.activity import minutes

    if not job or job.get("state") != "finished" or not job.get("log"):
        return None
    try:
        lines = Path(job["log"]).read_text().splitlines()
        est = json.loads("\n".join(lines[lines.index("{"):len(lines) - lines[::-1].index("}")]))
    except (OSError, ValueError):
        return None
    lo, hi, n = est.get("total_low_s"), est.get("total_high_s"), len(est.get("folds") or {})
    if lo is None or hi is None or not n:
        return None
    total = f"About {minutes(lo)}" if minutes(lo) == minutes(hi) else f"Between {minutes(lo)} and {minutes(hi)}"
    each = [(f["low_s"] + f["high_s"]) / 2 for f in est["folds"].values()]
    text = (f"{total} on this computer with {est.get('workers', 1)} workers: about {minutes(sum(each) / n)} for "
            f"each of the {n} " + ("groups of winters" if any("+" in k for k in est["folds"]) else "winters") + ".")
    if est.get("builds"):
        b = len(est["builds"])
        what = "the cases (each needs its own copy" if b == n else f"the cases of {b} of them (the rest are ready"
        text += (f" That includes {minutes(est.get('build_s') or 0)} at the start to prepare {what}; about "
                 f"{CASE_SET_GB * b:.1f} GB of disk).")
    return text


def start_winter_test(paths: LabPaths, config: Path, root: Path, args: list[str], check_id: str) -> dict:
    """`lab winter-test` under ``check_id``; the same command with the same id resumes it (finished folds kept)."""
    cmd = lab_command("winter-test", *args, "--check-id", check_id, "--source", str(root.resolve()),
                      *_common(paths, config))
    return start_job(paths, "winter-test", f"winter test {check_id}", [step("winter-test", cmd)], cwd=root,
                     refs={"check_id": check_id, "args": args})


def resume_winter_args(plan: dict, workers: int = 1, engine: str = "auto") -> list[str]:
    """The options that resume a winter test from its stored plan."""
    initial = [g["family"] for g in plan["initial"]]  # the page starts tests from families' starting settings
    groups = plan.get("folds") or []
    every = all(len(g) == 1 for g in groups)
    args = winter_args(initial=initial, folds=0 if every else len(groups), rounds=plan["rounds"],
                       population=plan["population"], survivors=plan["survivors"], workers=workers,
                       seed=plan["seed"], engine=engine, include_locked=not plan.get("excluded_seasons"))
    # a test started from Terminal may be limited to some plots, case types, weather sources or winters
    for flag, key in (("--plots", "plots"), ("--case-types", "case_types"), ("--weather-sources", "weather_sources"),
                      ("--season", "seasons")):
        for value in plan.get(key) or []:
            args += [flag, str(value)]
    return args


# --------------------------------------------------------------------------------------------- plain words


def pts(x: float | None) -> str:
    """A score on the lab's 0-100 scale ('52.1'); composites are stored 0-1."""
    return "-" if x is None else f"{100 * x:.1f}"


def signed(x: float | None) -> str:
    """A difference in points with its sign ('+1.2', '-0.4'); '0.0' for one that rounds to nothing."""
    if x is None:
        return "-"
    v = round(100 * x, 1)
    return f"{v:+.1f}" if v else "0.0"


def plain_summary(result: dict) -> list[str]:
    """What a winter test found, in sentences for a person who does not read the code (ADR-125)."""
    out = []
    n = (result.get("vs_snowpack") or {}).get("winters", 0)
    ev, sp, st = (result.get("pooled_evolved_composite"), result.get("pooled_incumbent_composite"),
                  result.get("pooled_start_composite"))
    out.append(f"Each of {n} winters was forecast by agents trained only on the other winters. On those unseen "
               f"winters the trained agents scored {pts(ev)} out of 100, standard SNOWPACK {pts(sp)}"
               + (f" and the same model before any training {pts(st)}." if st is not None else "."))
    vs = result.get("vs_snowpack") or {}
    if vs.get("winters"):
        out.append(f"Against SNOWPACK: on average {signed(vs['mean'])} points a winter (a typical winter differs "
                   f"from that average by about {100 * vs['sd']:.1f} points; from {signed(vs['lowest'])} in the "
                   f"worst winter to {signed(vs['highest'])} in the best). Better in {vs['better']} winters, worse "
                   f"in {vs['worse']}. The true average is likely between {signed(vs['range90'][0])} and "
                   f"{signed(vs['range90'][1])} (90% range from resampling the winters).")
    tg, gain = result.get("training_gain") or {}, result.get("pooled_training_gain")
    if tg.get("winters"):
        lo, hi = tg["range90"]
        verdict = ("so training adds something that holds on new winters" if lo > 0 else
                   "so training makes things worse on new winters (it memorises the winters it sees)" if hi < 0 else
                   "so we cannot tell whether training helps on new winters")
        out.append(f"What training added: {signed(gain)} points over all the held-out pits. Winter by winter it "
                   f"helped in {tg['better']} winters and hurt in {tg['worse']} (on average {signed(tg['mean'])} "
                   f"points a winter, likely between {signed(lo)} and {signed(hi)}), {verdict}.")
    gap, seen = result.get("memorising_gap"), result.get("gain_on_training_winters")
    if gap is not None and seen is not None and gain is not None:
        out.append(f"Memorising: on the winters they trained on, training changed the score by {signed(seen)} "
                   f"points; on the pits of winters they had not seen, by {signed(gain)}. "
                   + (f"The {100 * gap:.1f} points between the two were memorised from those winters, not learned "
                      "about snow." if gap > MEMORISED_MIN else
                      "No clear sign of memorising (the two are within half a point)."))
    few = [w for w in result.get("per_winter") or [] if w.get("cases", 0) < FEW_PITS]
    if few:
        out.append(f"{len(few)} of the winters have fewer than {FEW_PITS} pits, so their own numbers are rough; the "
                   "averages count every winter the same.")
    if result.get("excluded_seasons"):
        out.append(f"The locked test winters ({', '.join(result['excluded_seasons'])}) were kept out of every part "
                   "of this test, so they remain a clean final test.")
    return out


def took(result: dict) -> str | None:
    """'Computing time: 1 h 52 min (each training round: 3 min), plus 6 min to prepare the cases.' from the rounds'
    own records, so a test that was resumed counts all of its parts."""
    from snowagent.lab.services.activity import minutes

    if result.get("work_s") is None:
        return None
    per, build = result.get("round_s_median"), result.get("build_s")
    return (f"Computing time: {minutes(result['work_s'])}"
            + (f" (each training round: {minutes(per)})" if per else "")
            + (f", plus {minutes(build)} to prepare the cases" if build else "") + ".")


__all__ = ["SLOW", "WINTER_EVOLUTIONS", "WINTER_PRESETS", "estimate_words", "plain_summary", "pts",
           "resume_winter_args", "signed", "start_winter_estimate", "start_winter_test", "took", "winter_args",
           "winter_estimate_for"]
