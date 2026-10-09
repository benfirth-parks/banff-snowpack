"""The group check's plain-language report (ADR-095): who was in the group, whether the pooled answer beats one agent
on the locked winters, whether the agents disagree where they are wrong, and whether weak layers most of them agree on
are really in the pits. Every number comes from the check's stored files; the verdicts follow the fixed rules of
``services.group``. Same document format as the training report (HTML and Markdown)."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from snowagent.lab.services.reports import Report, _hours, _plural, _pts, _span
from snowagent.lab.storage.paths import LabPaths

GROUP_NAMES = {"standard": "standard model", "other kind": "another kind of agent", "evolved": "evolved agent",
               "weather nudge": "weather nudge"}


def _ok(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def _cm(v) -> str:
    return f"{100 * v:.0f} cm" if _ok(v) else "n/a"


def _share(v) -> str:
    return f"{100 * v:.0f}%" if _ok(v) else "n/a"


def group_report(res: dict) -> Report:
    """The report of a loaded group check (``services.group.load_group_check``)."""
    seasons = res["seasons"]
    n = res["cases"]
    std, best = res.get("standard"), res.get("best_member")
    rep = Report(
        title="Group check: what the agents say together",
        meta=[("Test winters", f"{_span(seasons)} (locked: no agent in the group trained on them)"),
              ("Test pits", str(n)), ("Agents in the group", str(len(res["members"]))),
              ("Test cases from", f"training run {res['test_run']}"),
              ("Scoring", res.get("scoring_version", "")), ("Check", res["check_id"]),
              ("Written", res.get("created_at", "")),
              ("Took", _hours(res.get("runtime_s") or 0) + f" ({res['evaluation']['cache_hits']} saved predictions "
               f"reused, {res['evaluation']['cache_misses']} new ones computed)")])

    # ---- in short
    rep.h2("In short")
    short = []
    if std:
        short.append(f"**Against standard SNOWPACK the group is {res.get('vs_standard', 'not comparable')}:** "
                     f"{_pts(std['group'])} vs {_pts(std['agent'])} points out of 100, better on "
                     f"{_plural(std['group_wins'], 'pit')} and worse on {std['group_losses']}.")
    if best:
        short.append(f"Against the agent that did best on these winters ({best['name']}, picked after the fact, so a "
                     f"tough bar) the group is {res.get('vs_best', 'not comparable')}: {_pts(best['group'])} vs "
                     f"{_pts(best['agent'])}.")
    du, su = res.get("disagreement_useful") or ("too few cases", "too few cases")
    short.append(f"**Do the agents disagree where they are wrong?** Snow depth: {_said(du)}. Layering: {_said(su)}.")
    short.append(f"**Are weak layers most agents agree on really there?** {_agreement_said(res.get('agreement_useful'))}")
    rep.bullets(short)

    # ---- who
    rep.h2("Who was in the group")
    rep.p("The group is chosen for variety, so that when the agents agree it means more than close cousins agreeing: "
          "standard SNOWPACK, other kinds of agent, the best agents of separate training runs, and standard SNOWPACK "
          "run with nudged weather (more or less snowfall, warmer or colder air), because the weather that fell is "
          "often the biggest unknown. Each agent has one vote.")
    rows = []
    for m in res["members"]:
        b = next((x for x in res["board"] if x["agent_id"] == m["agent_id"]), {})
        rows.append({"agent": m["name"], "kind": GROUP_NAMES.get(m["group"], m["group"]), "about it": m["note"],
                     "its score": _pts(b["agent"]) if _ok(b.get("agent")) else "n/a",
                     "group better on": b.get("group_wins", 0), "group worse on": b.get("group_losses", 0)})
    rep.table(pd.DataFrame(rows), note=f"Scores are points out of 100 on the {n} test pits (the training score). "
              f"The group scored {_pts(res['group_mean'])} on the same pits. 'Better on' and 'worse on' count pits "
              "where the group's score differs from that agent's by more than half a point.")
    if res.get("notes"):
        rep.bullets(res["notes"])

    # ---- 1. better than one agent?
    rep.h2("Is the group better than one agent?")
    rep.p("On every test pit the group's predictions are pooled into one profile: the middle snow depth; through the "
          "pack, the grain type most agents give at each depth; and every weak layer or crust at least a third of "
          "them forecast, with a chance of being there equal to the share that forecast it. That profile is scored "
          "exactly like an agent. " + _better_text(res))

    # ---- 2. disagreement
    rep.h2("Do the agents disagree where they are wrong?")
    rep.p("If disagreement is a useful warning, the pits where the agents disagreed most should be the ones where the "
          "group's answer was furthest off. The test pits are split into three equal groups by how much the agents "
          "disagreed.")
    dt = res.get("depth_thirds") or []
    if dt:
        rep.table(pd.DataFrame([{"agents' disagreement on depth": t["disagreement"],
                                 "spread of their depths": f"{_cm(t['range'][0])} to {_cm(t['range'][1])}",
                                 "pits": t["cases"], "group's depth error": _cm(t["abs_error_m"]),
                                 "pit depth inside the group's range": _share(t["covered"])} for t in dt]),
                  note="Spread = the range covering the middle 80% of the agents' snow depths. Error = how far the "
                       "group's middle depth was from the pit, on average.")
        rep.p(f"Verdict for snow depth: **{_said(du)}**.")
    st = res.get("structure_thirds") or []
    if st:
        rep.table(pd.DataFrame([{"agents' disagreement on layering": t["disagreement"],
                                 "share of the pack they disagree on": f"{_share(t['range'][0])} to "
                                                                       f"{_share(t['range'][1])}",
                                 "pits": t["cases"], "group's layering score": _pts(t["layer_structure"]),
                                 "group's weak-layer score": _pts(t["critical_layers"])
                                 if _ok(t["critical_layers"]) else "n/a"} for t in st]),
                  note="Share disagreed = the part of the snowpack, top to bottom, where agents give different grain "
                       "types. Scores are points out of 100, higher is better.")
        rep.p(f"Verdict for layering: **{_said(su)}**.")
    if not dt and not st:
        rep.p("Too few test pits to tell.")

    # ---- 3. weak layers
    rep.h2("Are the weak layers most agents agree on really in the pits?")
    rep.p("Every weak layer the agents forecast is grouped with the ones other agents forecast of the same kind at "
          "about the same depth. Then we look in the pit: was a layer of that kind there, within the depth tolerance "
          "the scorer uses? If agreement means something, layers most agents forecast should turn up more often than "
          "layers only a few forecast.")
    rel = pd.DataFrame(res.get("reliability") or [])
    if len(rel):
        rel = rel.assign(share=rel["share"].map(_share)).rename(columns={
            "what": "what", "agreement": "forecast by", "layers": "layers forecast", "in_the_pit": "found in the pit",
            "share": "share found"})
        rep.table(rel, note="Weak layers are surface hoar, facets and depth hoar, counted the way the weak-layer "
                            "score counts them (a slab above, a harder bed below and a clear hardness change).")
    rep.p(f"Verdict: **{_agreement_said(res.get('agreement_useful'))}**")
    if _ok(res.get("pit_weak_layers")) and res.get("pit_weak_layers"):
        rep.p(f"The pits held {res['pit_weak_layers']} weak layers; "
              f"{res['pit_weak_missed_by_all']} of them were forecast by no agent at all, so pooling could not have "
              "found them. Those are the gaps a better agent, not a bigger group, has to close.")

    # ---- forecasters
    rep.h2("What this means for forecasters")
    rep.bullets(_advice(res))

    rep.h2("Limits")
    rep.bullets([
        f"{n} test pits from {_plural(len(seasons), 'winter')} and three study plots: a small sample, so differences of "
        "a point or two can be chance.",
        "All agents read the same weather records, and most share SNOWPACK's physics, so they can all be wrong the same "
        "way. Agreement is not proof; the weather nudges only cover part of that doubt.",
        "The test winters were never used to train or choose these agents, so this is a fair test of them; at most, "
        "the training run that supplied the test pits used them to decide when to stop.",
        "Decision support for research only, not an avalanche forecast.",
    ])
    return rep


def _said(v: str) -> str:
    return {"yes": "yes, where they disagreed most the group was clearly further off",
            "weak": "somewhat: the most-disagreed third was further off, but not in a steady pattern",
            "no": "no clear link: disagreement did not mark the pits where the group was wrong",
            "too few cases": "too few test pits to tell"}.get(v, v)


def _agreement_said(v: str | None) -> str:
    return {"yes": "Yes: weak layers most agents forecast were found in the pits clearly more often than those only a "
                   "few forecast.",
            "no": "No: weak layers most agents forecast were not found clearly more often than those only a few "
                  "forecast.",
            "too few": "Too few forecast weak layers in one of the groups to tell."}.get(v or "", "Not measured.")


def _better_text(res: dict) -> str:
    std, best = res.get("standard"), res.get("best_member")
    if not std:
        return "Standard SNOWPACK was not in the group, so there is no baseline to compare with."
    v = res.get("vs_standard")
    out = {"better": "Pooled, the group beats standard SNOWPACK", "worse": "Pooled, the group does worse than standard "
           "SNOWPACK", "about the same": "Pooled, the group scores about the same as standard SNOWPACK"}[v]
    out += f" ({_pts(std['group'])} vs {_pts(std['agent'])}; better on {_plural(std['group_wins'], 'pit')}, " \
           f"worse on {std['group_losses']})."
    if best and best["agent_id"] != std["agent_id"]:
        out += (f" The best single agent on these winters, {best['name']}, scored {_pts(best['agent'])}; but it could "
                "only be named after the test, so the fair comparison is with an agent chosen beforehand.")
    return out


def _advice(res: dict) -> list[str]:
    """Fixed rules from the three verdicts to what a forecaster may take from the group view."""
    du, su = res.get("disagreement_useful") or ("too few cases", "too few cases")
    agree = res.get("agreement_useful")
    out = []
    if agree == "yes":
        out.append("A weak layer most agents forecast is worth looking for in the field: on the test winters such "
                   "layers were usually there.")
    elif agree == "no":
        out.append("Do not read a weak layer as more likely because most agents forecast it: on the test winters that "
                   "did not make it more likely to be there.")
    else:
        out.append("Not enough forecast weak layers to say how far the group's agreement can be trusted yet.")
    if "yes" in (du, su):
        what = " and ".join(w for w, v in (("snow depth", du), ("layering", su)) if v == "yes")
        out.append(f"Where the agents disagree on {what}, treat the model view as uncertain and lean on observations: "
                   "that is where the group was furthest off.")
    elif du == su == "too few cases":
        out.append("Too few test pits to say yet whether the agents' disagreement marks the places where the model is "
                   "wrong.")
    elif "weak" in (du, su):
        out.append("Disagreement among the agents is only a weak warning sign so far: use it as a prompt to check, not "
                   "as a measure of how wrong the model is.")
    else:
        out.append("Agents agreeing is not a sign the model is right here: on the test winters the group was wrong as "
                   "often where they agreed as where they did not.")
    if res.get("vs_standard") == "better":
        out.append("The pooled profile scored better than standard SNOWPACK, so it is a candidate for an experimental "
                   "group view on the site, after the blind test of this winter confirms it.")
    else:
        out.append("The pooled profile did not beat standard SNOWPACK, so standard SNOWPACK stays the main view; the "
                   "group is useful at most for its warnings.")
    return out


def report_filename(check_id: str, ext: str) -> str:
    return f"report-{check_id}.{ext}"


def save_group_report(paths: LabPaths, rep: Report, check_id: str) -> list[Path]:
    out = paths.outputs / "reports"
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for ext, text in (("html", rep.to_html()), ("md", rep.to_markdown())):
        f = out / report_filename(check_id, ext)
        f.write_text(text, encoding="utf-8")
        files.append(f)
    return files
