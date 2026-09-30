"""NOAA Integrated Surface Database (Global Hourly) for stations near the study plots.

Source: s3://noaa-global-hourly-pds/<year>/<USAF><WBAN>.csv (public AWS Open Data mirror of NCEI Global
Hourly). The three study-plot stations (Simpson Lower/Upper, Bow Summit, Sunshine Village) are provincial
or Parks stations and are NOT in ISD; these are the nearest ISD stations, used as supporting actuals.

Parsing keeps ISD quality codes: 0/1/4/5/9-with-value -> ok, 2/6 -> suspect, 3/7 -> bad; missing sentinels
(+9999, 999) -> NaN. Nothing is gap-filled. Units: SI, times UTC.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from snowagent.ingest.fetch import fetch

BASE = "https://noaa-global-hourly-pds.s3.amazonaws.com"
QC = {"0": "ok", "1": "ok", "4": "ok", "5": "ok", "9": "ok", "A": "ok", "C": "ok", "I": "ok", "M": "ok",
      "P": "ok", "R": "ok", "U": "ok", "2": "suspect", "6": "suspect", "3": "bad", "7": "bad"}


def download(station_id: str, years: range, raw_dir: Path) -> list[dict]:
    manifest = raw_dir / "manifest.jsonl"
    return [fetch(f"{BASE}/{y}/{station_id}.csv", raw_dir / str(y) / f"{station_id}.csv", manifest) for y in years]


def _split(s: pd.Series, n: int) -> pd.DataFrame:
    return s.fillna("").astype(str).str.split(",", expand=True).reindex(columns=range(n))


def _val(v: pd.Series, missing: str, scale: float) -> pd.Series:
    out = pd.to_numeric(v.where(v != missing), errors="coerce") / scale
    return out


def parse(files: list[Path]) -> pd.DataFrame:
    """Hourly SI table from raw ISD CSVs (one station). Prefers FM-15/SAO hourly reports per timestamp."""
    frames = [pd.read_csv(f, low_memory=False, dtype=str) for f in files if Path(f).exists()]
    if not frames:
        return pd.DataFrame()
    d = pd.concat(frames, ignore_index=True)
    d["time_utc"] = pd.to_datetime(d["DATE"], utc=True)
    out = pd.DataFrame({"time_utc": d["time_utc"], "report_type": d["REPORT_TYPE"].str.strip()})
    t = _split(d["TMP"], 2)
    out["ta_k"] = _val(t[0], "+9999", 10) + 273.15
    out["ta_qc"] = t[1].map(QC).fillna("bad").where(out["ta_k"].notna(), "missing")
    td = _split(d["DEW"], 2)
    out["td_k"] = _val(td[0], "+9999", 10) + 273.15
    w = _split(d["WND"], 5)
    out["dw_deg"] = _val(w[0], "999", 1)
    out["vw_ms"] = _val(w[3], "9999", 10)
    out["vw_qc"] = w[4].map(QC).fillna("bad").where(out["vw_ms"].notna(), "missing")
    p = _split(d.get("MA1", pd.Series(index=d.index, dtype=str)), 4)  # station pressure (MA1: altimeter, stn)
    out["p_pa"] = _val(p[2], "99999", 10) * 100
    aa = _split(d.get("AA1", pd.Series(index=d.index, dtype=str)), 4)  # period h, depth mm*10, cond, qc
    out["psum_period_h"] = _val(aa[0], "99", 1)
    out["psum_mm"] = _val(aa[1], "9999", 10)
    out["psum_qc"] = aa[3].map(QC).fillna("bad").where(out["psum_mm"].notna(), "missing")
    # RH over water from Magnus (ta, td in C)
    tc, tdc = out["ta_k"] - 273.15, out["td_k"] - 273.15
    out["rh_frac"] = np.exp(17.625 * tdc / (243.04 + tdc)) / np.exp(17.625 * tc / (243.04 + tc))
    out = out.sort_values(["time_utc", "report_type"])
    # one row per timestamp: hourly SAO/FM-15 first, synoptic FM-12 (carries 6-h precip) merged in
    hourly = out[out["report_type"] != "FM-12"].drop_duplicates("time_utc", keep="first").set_index("time_utc")
    syn = out[out["report_type"] == "FM-12"].drop_duplicates("time_utc", keep="first").set_index("time_utc")
    merged = hourly.combine_first(syn)
    for c in ("psum_period_h", "psum_mm", "psum_qc"):
        merged[c] = syn[c].reindex(merged.index).combine_first(hourly[c])
    return merged.reset_index()
