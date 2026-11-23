"""The site's local time (ADR-145): MST (UTC-7) before 1 November 2026, Alberta Time (UTC-6) from then on.

Runs the local-time block of web/app.js in node.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "web" / "app.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def _run(expr: str):
    m = re.search(r"// -+ local time \(ADR-145\)\n(.*?)// -+ end local time", APP.read_text(), re.S)
    assert m, "local time block not found in web/app.js"
    code = 'const pad = (n) => String(n).padStart(2, "0");\n' + m.group(1)
    out = subprocess.run(
        [NODE, "-e", code + f"\nconsole.log(JSON.stringify({expr}));"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def test_times_from_november_2026_are_alberta_time_and_earlier_ones_mst():
    assert _run('fmtLocal(new Date("2026-11-23T13:15:00Z"))') == "23 Nov 2026, 07:15 AT"
    assert _run('fmtLocal(new Date("2026-01-10T18:00:00Z"))') == "10 Jan 2026, 11:00 MST"
    assert _run('fmtLocal(new Date("2025-07-01T18:00:00Z"), false)') == "1 Jul, 11:00 MST"


def test_the_switch_is_at_midnight_mst_on_1_november_2026():
    assert _run('fmtLocal(new Date("2026-11-01T06:59:00Z"))') == "31 Oct 2026, 23:59 MST"
    assert _run('fmtLocal(new Date("2026-11-01T07:00:00Z"))') == "1 Nov 2026, 01:00 AT"


def test_local_dates_follow_the_offset_of_their_time():
    assert _run('localDateStr(new Date("2026-11-23T05:00:00Z"))') == "2026-11-22"
    assert _run('localDateStr(new Date("2026-11-23T06:00:00Z"))') == "2026-11-23"
    assert _run('localDateStr(new Date("2026-01-10T06:00:00Z"))') == "2026-01-09"


def test_a_local_date_and_hour_go_back_to_utc():
    assert _run("localToUtc(2026, 10, 23, 12).toISOString()") == "2026-11-23T18:00:00.000Z"
    assert _run("localToUtc(2026, 0, 10, 11).toISOString()") == "2026-01-10T18:00:00.000Z"
    assert _run("localToUtc(2026, 10, 23, 0).toISOString()") == "2026-11-23T06:00:00.000Z"
    assert _run("localToUtc(2026, 9, 1, 0).toISOString()") == "2026-10-01T07:00:00.000Z"
    assert _run("localToUtc(2026, 9, 31, 23).toISOString()") == "2026-11-01T06:00:00.000Z"
    # 00:xx on 1 November 2026 does not exist (23:59 MST is followed by 01:00 AT): it is the switch itself
    assert _run("localToUtc(2026, 9 + 1, 1, 0).toISOString()") == "2026-11-01T07:00:00.000Z"


def test_month_starts_step_across_the_switch():
    expr = (
        "(() => { const out = []; let d = localToUtc(2026, 8, 1, 0);"
        " while (d.getTime() < Date.UTC(2027, 1, 2) && out.length < 10) {"
        " out.push(localDateStr(d)); d = localToUtc(toLocal(d).getUTCFullYear(), toLocal(d).getUTCMonth() + 1, 1, 0); }"
        " return out; })()"
    )
    assert _run(expr) == [
        "2026-09-01", "2026-10-01", "2026-11-01", "2026-12-01", "2027-01-01", "2027-02-01",
    ]


def test_the_hours_offered_are_the_outputs_local_hours():
    assert _run("outputHours(7, true)") == [5, 11, 17, 23]
    assert _run("outputHours(6, true)") == [0, 6, 12, 18]
    assert _run("[outputHours(7, false), outputHours(6, false)]") == [[11], [12]]
