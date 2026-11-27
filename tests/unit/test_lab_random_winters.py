"""ADR-165: promotion checks hold out winters drawn at random, a new draw each check, recorded so it can be repeated;
the locked test winters (and anything later, such as the live blind-test winter) are never drawn."""

from __future__ import annotations

import json

import pytest

from snowagent.lab.services.reports import check_step
from snowagent.lab.services.workflow import check_args, resume_check_args
from snowagent.lab.training import loso
from snowagent.lab.training.loop import run_training
from tests.unit.test_lab_training import SEASONS, built, lab, opts, quiet  # noqa: F401

WINTERS = [f"{y}-{y + 1}" for y in range(1998, 2026)]  # 28 winters, the last three locked
LOCKED = ["2023-2024", "2024-2025", "2025-2026"]


def test_the_draw_never_holds_out_locked_or_later_winters():
    pool = loso.draw_pool([*WINTERS, "2026-2027"], locked=LOCKED, seen=["1998-1999"], excluded=["2000-2001"])
    assert len(pool) == 23
    assert not set(pool) & {*LOCKED, "2026-2027", "1998-1999", "2000-2001"}
    for seed in range(200):
        assert not set(loso.draw_winters(pool, 3, seed)) & set(LOCKED)


def test_a_draw_number_repeats_its_draw_and_new_numbers_give_new_winters():
    pool = loso.draw_pool(WINTERS, locked=LOCKED)
    a = loso.draw_winters(pool, 3, 4242)
    assert a == loso.draw_winters(list(reversed(pool)), 3, 4242) and len(set(a)) == 3 and a == sorted(a)
    draws = {tuple(loso.draw_winters(pool, 3, s)) for s in range(50)}
    assert len(draws) > 40  # not the same three winters every time
    seen = {w for d in draws for w in d}
    assert len(seen) == len(pool)  # every eligible winter gets its turn
    assert 1000 <= loso.new_draw_seed() < 10000
    with pytest.raises(ValueError):
        loso.draw_winters(pool, len(pool) + 1, 1)


def test_check_args_carry_the_draw_and_a_resume_repeats_it(tmp_path):
    args = check_args("run/3/1", seasons=["2001-2002"], random_winters=3, draw_seed=77)
    assert "--season" not in args and args[args.index("--random-winters") + 1] == "3"
    assert args[args.index("--draw-seed") + 1] == "77"
    assert "--random-winters" not in check_args("run/3/1", seasons=["2001-2002"])
    from snowagent.lab.storage.paths import LabPaths

    paths = LabPaths(tmp_path / "lab")
    d = loso.checks_root(paths) / "c1"
    d.mkdir(parents=True)
    plan = {"genome_ref": "run/3/1", "seasons": ["2001-2002", "2010-2011"], "differs_from_training_run": {},
            "draw": {"winters": 2, "seed": 77, "pool": WINTERS[:25], "drawn": ["2001-2002", "2010-2011"]}}
    (d / "check.json").write_text(json.dumps({"plan": plan}))
    r = resume_check_args(paths, "c1")
    assert "--season" not in r and r[r.index("--draw-seed") + 1] == "77" and r[r.index("--random-winters") + 1] == "2"


def test_the_report_asks_for_a_random_draw():
    text = check_step({"survivors": 2}, WINTERS[:25], 19, 1)
    assert "Random draw" in text and "never the locked test winters" in text


def test_check_loso_holds_out_and_records_a_random_draw(lab):  # noqa: F811
    cfg, paths, source = lab
    run_training(paths, cfg, opts(cfg, rounds=1, population=4), run_id="d", log=quiet)
    with pytest.raises(ValueError):
        loso.check_loso(paths, cfg, "d/1/1", None, source=source, log=quiet, estimate_only=True,
                        seasons=[SEASONS[0]], random_winters=1)
    msgs: list[str] = []
    est = loso.check_loso(paths, cfg, "d/1/1", None, source=source, log=msgs.append, estimate_only=True,
                          random_winters=2, draw_seed=5)
    want = loso.draw_winters(list(SEASONS), 2, 5)
    assert sorted(est.result["estimate"]["folds"]) == want
    assert any("drawn at random (draw 5" in m for m in msgs)
    res = loso.check_loso(paths, cfg, "d/1/1", None, source=source, log=quiet, check_id="rd", random_winters=1,
                          draw_seed=5)
    one = loso.draw_winters(list(SEASONS), 1, 5)
    assert [f["season"] for f in res.folds] == one and res.result["held_out"] == one
    plan = json.loads((res.check_dir / "check.json").read_text())["plan"]
    assert plan["draw"] == {"winters": 1, "seed": 5, "pool": list(SEASONS), "drawn": one, "never_drawn": []}
    assert res.result["draw"]["seed"] == 5
    # the same draw resumes the finished check; another draw number is another plan
    again = loso.check_loso(paths, cfg, "d/1/1", None, source=source, log=quiet, check_id="rd", random_winters=1,
                            draw_seed=5)
    assert again.result["held_out"] == one
