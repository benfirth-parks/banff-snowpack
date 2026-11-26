"""ADR-164: MIN trip reports (2026-27 feed), the snowpack statements read from them, and the check against the main
model's profile."""

import json

from snowagent.ingest.min import bbox, is_trip_report, item_day, item_id, parse_report
from snowagent.obs.min_layers import check_report, read_report, read_text

PTS = {"goats_eye": (51.08953, -115.75462), "bow_summit": (51.70946, -116.4795)}
TRIP = {"submissionID": "638a6520-dd3f-400e-b4ed-f0d49f79e2ab", "userID": "apple|x", "username": "someone",
        "title": "Sunshine - Boundary Bowl", "date": "2026-11-24", "submissionDatetime": "2026-11-25T08:20:53Z",
        "schemaVersions": {"dataSchemaId": "data_3.0.0"}, "activities": ["skiing"],
        "location": {"latitude": 51.0901, "longitude": -115.8070}, "region": "Banff Yoho Kootenay",
        "instability": ["whumpfing"], "avalanches": [{"when": "today", "date": "2026-11-24", "startedBy": ["natural"],
                                                     "aspect": ["E"], "elevation": "alpine", "size": "1",
                                                     "problem": "dryLoose"}],
        "comments": "Snowpack: HS ~35 cm at 2350 m. Buried Surface hoar was present and felt very unstable. "
                    "No distinct crust or layering was visible in the snow wall."}
# Carver-style display rows: [top_cm, bottom_cm, grain, hardness, density, temp, lwc, flags]
PROFILE = {"t": "2026-11-24T18", "hs": 34.0, "L": [[34.0, 24.2, "DF", 1.8, 200, -6, 0, 0],
                                                    [24.2, 20.6, "FC", 1.4, 214, -6, 0, 2],
                                                    [20.6, 20.3, "MFcr", 4.0, 187, -5, 0, 1],
                                                    [20.3, 0.0, "FC", 2.5, 262, -3, 0, 2]]}


def test_trip_report_parses_without_usernames():
    assert is_trip_report(TRIP) and item_id(TRIP) == TRIP["submissionID"] and str(item_day(TRIP)) == "2026-11-24"
    r = parse_report(TRIP, PTS, "2026/x.json.gz", "0" * 64)
    assert r.obs_time_utc.isoformat() == "2026-11-24T19:00:00+00:00"  # local noon (UTC-7)
    assert r.whumpfing is True and r.types == ["trip", "avalanche", "instability"]
    assert r.avalanches[0].character == ["dryLoose"] and r.avalanches[0].trigger == "natural"
    assert set(r.distance_km) == {"goats_eye"} and "someone" not in r.model_dump_json()
    lon0, lat0, lon1, lat1 = map(float, bbox(PTS).split(","))
    assert lon0 < -116.4795 and lon1 > -115.75462 and lat0 < 51.08953 and lat1 > 51.70946


def test_reading_and_check_against_profile():
    rd = read_report(parse_report(TRIP, PTS, "x", "0" * 64))
    got = {(s.kind, s.present, s.where) for s in rd.statements}
    assert got == {("hs", True, "unknown"), ("surface_hoar", True, "buried"), ("crust", False, "unknown")}
    assert rd.elevation_m == 2350
    c = check_report(rd, 2280.0, PROFILE, -1.0)
    v = {x["kind"]: x["verdict"] for x in c["checks"]}
    assert v == {"hs": "agrees", "surface_hoar": "differs", "crust": "differs"}  # the model has a crust; no SH
    assert c["agrees"] == 1 and c["differs"] == 2 and c["profile_t"] == "2026-11-24T18"
    far = check_report(rd, 1900.0, PROFILE, -1.0)  # 450 m below the report: listed, not compared
    assert far["checks"] == [] and "2350 m" in far["not_compared"]
    assert check_report(rd, 2280.0, PROFILE, 30.0)["not_compared"].startswith("no simulated profile")


def test_text_rules_on_typical_notes():
    st, elev = read_text("HS 35 cm in the meadows, from the surface down: 3-7 mm surface hoar on the surface. "
                         "4 cm breakable temperature crust. 19 cm of sugary facets. Whumpfing at 2300 m.")
    k = {(s.kind, s.where, s.subtype) for s in st}
    assert ("surface_hoar", "surface", None) in k and ("crust", "unknown", "temperature") in k
    assert ("facets", "unknown", None) in k and elev == 2300
    st, _ = read_text("Did a CT at the base of Mount Habel: CTM13 30cm down, RP. CTH 23(RP) dn 24cm.")
    assert sorted((s.subtype, s.depth_cm) for s in st if s.kind == "test") == [("CTH23", 24.0), ("CTM13", 30.0)]
    st, _ = read_text("We noticed a 3-4cm crust at some places with facets under it. Wind crust on ridges.")
    assert ("facets", "under_crust") in {(s.kind, s.where) for s in st}
    assert {s.subtype for s in st if s.kind == "crust"} == {"unknown", "wind"}
    st, _ = read_text("Snowpack was probing 160-170cm deep at 2400m, with a noticeable crust 110cm below surface.")
    assert [s.depth_cm for s in st if s.kind == "crust"] == [110.0]
    st, _ = read_text("Found the Jan 24 surface hoar down 50 cm. No facets.")
    assert {(s.kind, s.present, s.where, s.depth_cm) for s in st} == {("surface_hoar", True, "buried", 50.0),
                                                                    ("facets", False, "unknown", None)}


def test_check_facets_under_crust_and_test_depth():
    st, _ = read_text("Thin melt-freeze crust with sugar below. CTM13 15cm down.")
    from snowagent.obs.min_layers import ReportReading

    c = check_report(ReportReading(statements=st), None, PROFILE, 0.0)
    assert {x["kind"]: x["verdict"] for x in c["checks"]} == {"crust": "agrees", "facets": "agrees", "test": "agrees"}
    assert json.dumps(c)  # the site file stores it as JSON


def test_season_check_file_and_site_list_show_a_copied_report_once(tmp_path, monkeypatch):
    from snowagent.ingest.min import archive_report
    from snowagent.obs.min_layers import check_season, season_summary
    from snowagent.web import build

    monkeypatch.setattr(build, "SITES", {"goats_eye": "Goat's Eye"})
    arc = tmp_path / "min"
    archive_report(json.dumps(TRIP).encode(), "u", arc)
    copy = dict(TRIP, submissionID="copy-in-new-format")  # the same report under a second id: counted once
    archive_report(json.dumps(copy).encode(), "u2", arc)
    web = tmp_path / "web"
    (web / "goats_eye").mkdir(parents=True)
    (web / "goats_eye" / "2026-2027_main.json").write_text(json.dumps(
        {"main": {"name": "Carver Daniels", "id": "process-x", "run_id": "r", "genome_hash": "g", "forcing_hash": "f",
                  "nowcast": [dict(PROFILE, t="2026-11-24T12"), PROFILE]}}))
    res = check_season(2026, web, arc, ["goats_eye"])
    d = res["plots"]["goats_eye"]
    assert d["agreement"] == {"reports": 1, "reports_checked": 1, "agrees": 1, "differs": 2, "not_checked": 0}
    assert d["reports"][0]["check"]["profile_t"] == "2026-11-24T18" and d["main_model"]["name"] == "Carver Daniels"
    assert "differs: report no crust" in season_summary(res)
    assert build.write_public(web, arc) == {"goats_eye": 1}
    site = json.loads((web / "goats_eye" / "2026-2027_public.json").read_text())
    assert "check" not in site["reports"][0] and "read" not in site["reports"][0]  # off the site (owner)
