"""CAAML v5 (niViz SnowProfileIACS) parser: top-down depths converted to heights above ground."""

from __future__ import annotations

from snowagent.obs.caaml import intermediate_hardness_index, parse_caaml_v5

DOC = """<?xml version="1.0" encoding="UTF-8"?>
<caaml:SnowProfile xmlns:caaml="http://caaml.org/Schemas/V5.0/Profiles/SnowProfileIACS"
  xmlns:gml="http://www.opengis.net/gml" gml:id="p">
<caaml:validTime><caaml:TimeInstant gml:id="t">
  <caaml:timePosition>2019-01-28T14:25:00.000-07:00</caaml:timePosition>
</caaml:TimeInstant></caaml:validTime>
<caaml:snowProfileResultsOf><caaml:SnowProfileMeasurements dir="top down">
  <caaml:profileDepth uom="cm">100</caaml:profileDepth>
  <caaml:hS><caaml:Components><caaml:snowHeight uom="cm">100</caaml:snowHeight></caaml:Components></caaml:hS>
  <caaml:hN24><caaml:Components><caaml:snowHeight uom="cm">10</caaml:snowHeight></caaml:Components></caaml:hN24>
  <caaml:stratProfile>
    <caaml:Layer><caaml:depthTop uom="cm">0</caaml:depthTop><caaml:thickness uom="cm">30</caaml:thickness>
      <caaml:grainFormPrimary>PP</caaml:grainFormPrimary><caaml:grainFormSecondary>DF</caaml:grainFormSecondary>
      <caaml:grainSize uom="mm"><caaml:Components><caaml:avg>2</caaml:avg><caaml:avgMax>2</caaml:avgMax>
      </caaml:Components></caaml:grainSize><caaml:hardness uom="N">F</caaml:hardness>
      <caaml:lwc uom="">D</caaml:lwc></caaml:Layer>
    <caaml:Layer><caaml:comment>Jan 17</caaml:comment><caaml:depthTop uom="cm">30</caaml:depthTop>
      <caaml:thickness uom="cm">1</caaml:thickness><caaml:grainFormPrimary>SH</caaml:grainFormPrimary>
      <caaml:grainSize uom="mm"><caaml:Components><caaml:avg>5</caaml:avg><caaml:avgMax>10</caaml:avgMax>
      </caaml:Components></caaml:grainSize><caaml:hardness uom="N">F</caaml:hardness>
      <caaml:lwc uom="">D-M</caaml:lwc></caaml:Layer>
    <caaml:Layer><caaml:depthTop uom="cm">31</caaml:depthTop><caaml:thickness uom="cm">69</caaml:thickness>
      <caaml:grainFormPrimary>MFcr</caaml:grainFormPrimary><caaml:hardness uom="N">P-K</caaml:hardness>
    </caaml:Layer>
  </caaml:stratProfile>
  <caaml:tempProfile uomDepth="cm" uomTemp="degC">
    <caaml:Obs><caaml:depth>0</caaml:depth><caaml:snowTemp>-20.3</caaml:snowTemp></caaml:Obs>
    <caaml:Obs><caaml:depth>40</caaml:depth><caaml:snowTemp>-5</caaml:snowTemp></caaml:Obs>
  </caaml:tempProfile>
  <caaml:stbTests>
    <caaml:ComprTest><caaml:failedOn><caaml:Layer><caaml:depthTop uom="cm">30</caaml:depthTop></caaml:Layer>
      <caaml:Results><caaml:fractureCharacter>SP</caaml:fractureCharacter><caaml:testScore>10</caaml:testScore>
      </caaml:Results></caaml:failedOn></caaml:ComprTest>
    <caaml:ComprTest><caaml:noFailure/></caaml:ComprTest>
  </caaml:stbTests>
</caaml:SnowProfileMeasurements></caaml:snowProfileResultsOf>
<caaml:locRef><caaml:ObsPoint gml:id="s"><caaml:name>Test Study</caaml:name>
  <caaml:validElevation><caaml:ElevationPosition uom="m"><caaml:position>2025</caaml:position>
  </caaml:ElevationPosition></caaml:validElevation>
  <gml:Point gml:id="pt" srsName="urn:ogc:def:crs:OGC:1.3:CRS84"><gml:pos>-116.4784 51.709</gml:pos></gml:Point>
</caaml:ObsPoint></caaml:locRef>
</caaml:SnowProfile>
"""


def test_caaml_v5_heights_time_tests(tmp_path):
    f = tmp_path / "x.caaml"
    f.write_text(DOC)
    r = parse_caaml_v5(f, "Etc/GMT+7")
    assert r["obs_time_utc"] == "2019-01-28T21:25:00+00:00"
    assert r["hs_cm"] == 100 and r["profile_depth_cm"] is None
    assert (r["lat"], r["lon"], r["elevation_m"]) == (51.709, -116.4784, 2025)
    tops = [(ly["top_cm"], ly["bottom_cm"]) for ly in r["layers"]]
    assert tops == [(100, 70), (70, 69), (69, 0)]
    pp, sh, cr = r["layers"]
    assert pp["grain_size_mm"] == [2.0] and pp["grain_form_2"] == "DF"
    assert sh["date_tag"] == "Jan 17" and sh["moisture"] is None and "D-M" in sh["comment"]
    assert cr["hardness"] == "P-K" and cr["hardness_index"] == 4.5
    assert r["temperatures"] == [{"height_cm": 100, "t_c": -20.3}, {"height_cm": 60, "t_c": -5.0}]
    ct, ctn = r["tests"]
    assert (ct["result"], ct["score"], ct["height_cm"], ct["fracture_character"]) == ("CT10", 10, 70, "SP")
    assert ctn["result"] == "CTN" and ctn["height_cm"] is None
    assert r["provenance"]["method"] == "structured:caaml_v5"


def test_intermediate_hardness():
    assert intermediate_hardness_index("K-I") == 5.5
    assert intermediate_hardness_index("1F+") == 3 + 1 / 3
    assert intermediate_hardness_index("nonsense") is None


def test_caaml_v6_from_the_snowpack_translator():
    """The Snowpack Translator's own export (voice page, the Nakiska pit): its test results sit directly under the
    test and its locRef has no ObsPoint, which the v6 reader accepts (ADR-110). Heights are HS - depth."""
    from pathlib import Path

    from snowagent.obs.caaml import parse_caaml_v6, xml_kind

    f = Path(__file__).resolve().parents[1] / "fixtures" / "caaml_v6_translator.xml"
    assert xml_kind(f.read_bytes()) == "caaml_v6"
    r = parse_caaml_v6(f, "America/Edmonton")
    assert r["provenance"]["source_format"] == "caaml_v6" and r["hs_cm"] == 100
    assert r["obs_time_utc"] == "2026-11-12T14:40:00+00:00"
    assert (r["site_name_as_written"], r["elevation_m"], r["aspect"], r["slope_deg"]) == ("Nakiska", 2234, "NE", 34)
    assert [(ly["top_cm"], ly["bottom_cm"], ly["grain_form"], ly["hardness"]) for ly in r["layers"]] == [
        (100, 70, "FC", "F"), (70, 35, "RG", "4F+"), (35, 0, "RG", "F+")]
    assert r["layers"][0]["grain_form_2"] == "DH" and r["layers"][0]["grain_size_mm"] == [1.5, 2]
    assert r["layers"][0]["comment"] == "Layer of concern."
    assert r["temperatures"][0] == {"height_cm": 40, "t_c": -2.9}
    ct, ect = r["tests"]
    assert (ct["type"], ct["result"], ct["score"], ct["height_cm"], ct["comment"]) == ("CT", "CT21", 21, 88, "Q2")
    assert (ect["type"], ect["result"], ect["score"], ect["height_cm"]) == ("ECT", "ECTP12", 12, 88)
    assert not [x for x in r["flags"] if x.startswith("gap_or_overlap")]


def test_caaml_v6_schema_shape_with_obspoint_and_failed_on(tmp_path):
    from snowagent.obs.caaml import parse_caaml_v6

    doc = """<?xml version="1.0"?><caaml:SnowProfile xmlns:caaml="http://caaml.org/Schemas/SnowProfileIACS/v6.0.3"
      xmlns:gml="http://www.opengis.net/gml" gml:id="p">
      <caaml:timeRef><caaml:recordTime><caaml:TimeInstant>
        <caaml:timePosition>2026-01-09T11:30:00-07:00</caaml:timePosition></caaml:TimeInstant></caaml:recordTime>
      </caaml:timeRef>
      <caaml:locRef><caaml:ObsPoint gml:id="l"><caaml:name>Simpson</caaml:name>
        <caaml:pointLocation><gml:Point gml:id="pt" srsName="urn:ogc:def:crs:OGC:1.3:CRS84">
          <gml:pos>-115.9 51.0</gml:pos></gml:Point></caaml:pointLocation></caaml:ObsPoint></caaml:locRef>
      <caaml:snowProfileResultsOf><caaml:SnowProfileMeasurements dir="top down">
        <caaml:hS><caaml:Components><caaml:height uom="cm">80</caaml:height></caaml:Components></caaml:hS>
        <caaml:stratProfile><caaml:Layer><caaml:depthTop uom="cm">0</caaml:depthTop>
          <caaml:thickness uom="cm">80</caaml:thickness><caaml:grainFormPrimary>FCso</caaml:grainFormPrimary>
          <caaml:hardness>P-K</caaml:hardness><caaml:wetness>D-M</caaml:wetness></caaml:Layer></caaml:stratProfile>
        <caaml:stbTests>
          <caaml:ComprTest><caaml:failedOn><caaml:Layer><caaml:depthTop uom="cm">30</caaml:depthTop></caaml:Layer>
            <caaml:Results><caaml:fractureCharacter>SP</caaml:fractureCharacter>
            <caaml:testScore>13</caaml:testScore></caaml:Results></caaml:failedOn></caaml:ComprTest>
          <caaml:ExtColumnTest><caaml:noFailure/></caaml:ExtColumnTest>
          <caaml:PropSawTest><caaml:failedOn><caaml:Layer><caaml:depthTop uom="cm">30</caaml:depthTop></caaml:Layer>
            <caaml:Results><caaml:fracturePropagation>End</caaml:fracturePropagation>
            <caaml:cutLength uom="cm">40</caaml:cutLength><caaml:columnLength uom="cm">100</caaml:columnLength>
            </caaml:Results></caaml:failedOn></caaml:PropSawTest>
        </caaml:stbTests>
      </caaml:SnowProfileMeasurements></caaml:snowProfileResultsOf></caaml:SnowProfile>"""
    f = tmp_path / "p.xml"
    f.write_text(doc)
    r = parse_caaml_v6(f, "America/Edmonton")
    assert r["obs_time_utc"] == "2026-01-09T18:30:00+00:00" and (r["lat"], r["lon"]) == (51.0, -115.9)
    (ly,) = r["layers"]
    assert ly["hardness_index"] == intermediate_hardness_index("P-K") and ly["moisture"] is None
    assert "wetness 'D-M'" in ly["comment"]
    ct, ect, pst = r["tests"]
    assert (ct["result"], ct["score"], ct["fracture_character"], ct["height_cm"]) == ("CT13", 13, "SP", 50)
    assert ect["result"] == "ECTX"
    assert (pst["type"], pst["result"], pst["height_cm"]) == ("PST", "PST40/100 End", 50)
