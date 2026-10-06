"""Analogue agent: the k nearest past cases by weather features (ADR-062).

Each case is summarised by an anonymous ``CaseDigest`` (daily precipitation, air temperature and wind for the 21 days
before as-of, totals and means over the horizon, the snow depth at as-of). The agent compares the case's digest with
the digests of a library of past cases, each with its withheld pit (snow depth and layers), and predicts:

- depth: the case's snow depth at as-of plus the neighbours' weighted depth change (``depth_mode: change``), or the
  neighbours' weighted pit depth (``absolute``, also when the depth at as-of is unknown);
- structure: the nearest neighbour's pit layers, scaled to that depth; a layer's presence probability is
  ``presence_confidence`` x (0.3 + 0.7 x the weighted share of neighbours with a layer of the same class at a
  similar relative depth).

Features (``window_days`` before as-of and over the horizon): precipitation (per day before, total over the
horizon), mean air temperature, mean wind, snow depth, each standardised over the library and weighted by its gene.
No date, day of year, case or pit id is a feature. The library holds no season, date or id either: the harness
builds it and passes only cases of OTHER seasons than the scored case (owner, 2026-10-05: agents must not
memorise the snowpacks), and never a sealed-test pit.
"""

from __future__ import annotations

import warnings

import numpy as np

from snowagent.lab.agents.common import (
    Lyr,
    build_prediction,
    insufficient,
    last_value,
    latest_season_pit,
    pit_snow_depth,
    weather_frame,
)
from snowagent.lab.ingest.mapping import critical_class
from snowagent.lab.schemas.benchmark import CaseType, VisibleBenchmarkCase
from snowagent.lab.schemas.common import LabModel, SiteCode
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome
from snowagent.lab.schemas.prediction import SnowpackPrediction

AGENT_VERSION = "analogue-1"
DIGEST_DAYS = 21
REL_DEPTH_TOL = 0.15


class CaseDigest(LabModel):
    """Anonymous weather and state summary of a visible case (no time, date or id)."""

    site_code: SiteCode
    case_type: CaseType
    horizon_h: float
    hs_now_m: float | None = None  # plot sensor at as-of, else the latest pit of the season
    precip_before_mm: list[float | None]  # per day; [0] = the 24 h before as-of
    temp_before_c: list[float | None]
    wind_before_ms: list[float | None]
    precip_horizon_mm: float | None = None
    temp_horizon_c: float | None = None
    wind_horizon_ms: float | None = None


class AnalogueLayer(LabModel):
    top_depth_m: float
    bottom_depth_m: float
    grain: str
    hardness_index: float | None = None


class AnalogueEntry(LabModel):
    """A past case: its digest and its withheld pit."""

    digest: CaseDigest
    truth_hs_m: float | None = None
    truth_layers: list[AnalogueLayer] = []


class AnalogueLibrary(LabModel):
    """What the analogue agent may draw on: past cases of other seasons, anonymous (no season, date or id)."""

    entries: list[AnalogueEntry] = []


def _f(x) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), 4)


def digest(case: VisibleBenchmarkCase) -> CaseDigest:
    """The case's digest, computed from its visible package only (agents and the library use the same function)."""
    wx = weather_frame(case)
    obs = wx[~wx["fc"].astype(bool)] if len(wx) else wx
    fc = wx[wx["fc"].astype(bool)] if len(wx) else wx
    pb, tb, wb = [], [], []
    for d in range(DIGEST_DAYS):
        w = obs[(obs.index > -24.0 * (d + 1)) & (obs.index <= -24.0 * d)] if len(obs) else obs
        pb.append(_f(w["precip_mm"].sum()) if len(w) and w["precip_mm"].notna().any() else None)
        tb.append(_f(w["ta_k"].mean() - 273.15) if len(w) and w["ta_k"].notna().any() else None)
        wb.append(_f(w["ws"].mean()) if len(w) and w["ws"].notna().any() else None)
    hs = last_value(wx["hs_m"], 0.0, 24.0) if len(wx) else None
    if hs is None:
        pit = latest_season_pit(case, with_layers=False)
        hs = pit_snow_depth(pit) if pit is not None else None
    return CaseDigest(
        site_code=case.site_code, case_type=case.case_type, horizon_h=round(case.horizon_hours, 3), hs_now_m=_f(hs),
        precip_before_mm=pb, temp_before_c=tb, wind_before_ms=wb,
        precip_horizon_mm=_f(fc["precip_mm"].sum()) if len(fc) and fc["precip_mm"].notna().any() else None,
        temp_horizon_c=_f(fc["ta_k"].mean() - 273.15) if len(fc) and fc["ta_k"].notna().any() else None,
        wind_horizon_ms=_f(fc["ws"].mean()) if len(fc) and fc["ws"].notna().any() else None)


def _mean(xs: list[float | None]) -> float:
    v = [x for x in xs if x is not None]
    return float(np.mean(v)) if v else np.nan


def features(d: CaseDigest, window: int) -> np.ndarray:
    """[precip/day before, precip over the horizon, temp before, temp horizon, wind before, wind horizon, depth]."""
    pb = [x for x in d.precip_before_mm[:window] if x is not None]
    return np.array([
        float(np.sum(pb)) / window if pb else np.nan, d.precip_horizon_mm if d.precip_horizon_mm is not None else np.nan,
        _mean(d.temp_before_c[:window]), d.temp_horizon_c if d.temp_horizon_c is not None else np.nan,
        _mean(d.wind_before_ms[:window]), d.wind_horizon_ms if d.wind_horizon_ms is not None else np.nan,
        d.hs_now_m if d.hs_now_m is not None else np.nan], dtype=float)


def _class(grain: str) -> str:
    cls = critical_class(grain)[0].value
    return cls if cls not in ("other", "unknown") else (grain[:2] if grain else "unknown")


class AnalogueAgent:
    def __init__(self, genome: AgentGenome, library: AnalogueLibrary | None = None) -> None:
        if genome.family != AgentFamily.analogue:
            raise ValueError(f"AnalogueAgent needs an analogue genome, got {genome.family}")
        self.genome = genome
        self.agent_id = genome.agent_id
        self.library = library or AnalogueLibrary()

    def predict(self, case: VisibleBenchmarkCase, seed: int) -> SnowpackPrediction:
        g = self.genome.genes
        me = digest(case)
        pool = [e for e in self.library.entries if e.truth_hs_m is not None
                and (g["same_site_only"] == "no" or e.digest.site_code == case.site_code)]
        same_type = [e for e in pool if e.digest.case_type == case.case_type]
        pool = same_type if len(same_type) >= g["k"] else pool
        meta = {"agent_version": AGENT_VERSION, "library_size": len(self.library.entries), "candidates": len(pool)}
        if not pool:
            return insufficient(case, self.genome, "no analogue case from another season in the library", meta)
        window = int(g["window_days"])
        x = features(me, window)
        X = np.array([features(e.digest, window) for e in pool])
        w = np.array([g["weight_precip"], g["weight_precip"], g["weight_temp"], g["weight_temp"], g["weight_wind"],
                      g["weight_wind"], g["weight_depth"]])
        with warnings.catch_warnings():  # a feature missing everywhere has no spread: weight 1, difference 0
            warnings.simplefilter("ignore", RuntimeWarning)
            sd = np.nanstd(np.vstack([X, x]), axis=0)
        sd = np.where(np.isfinite(sd) & (sd > 1e-9), sd, 1.0)
        diff = (X - x) / sd
        diff = np.where(np.isfinite(diff), diff, 0.0)  # a feature missing on either side does not count
        dist = np.sqrt((w * diff**2).sum(axis=1))
        order = np.argsort(dist, kind="stable")[: int(g["k"])]
        nb = [pool[i] for i in order]
        if g["kernel"] == "inverse_distance":
            wt = 1.0 / (dist[order] + 0.1)
        else:
            wt = np.ones(len(order))
        wt = wt / wt.sum()
        truth_hs = np.array([e.truth_hs_m for e in nb], dtype=float)
        now = np.array([e.digest.hs_now_m if e.digest.hs_now_m is not None else np.nan for e in nb], dtype=float)
        mode = g["depth_mode"]
        if mode == "change" and me.hs_now_m is not None and np.isfinite(now).any():
            ok = np.isfinite(now)
            hs = me.hs_now_m + float(np.sum(wt[ok] * (truth_hs[ok] - now[ok])) / wt[ok].sum())
        else:
            mode = "absolute"
            hs = float(np.sum(wt * truth_hs))
        hs = max(hs, 0.0)
        meta |= {"neighbours": len(nb), "depth_mode_used": mode, "nearest_distance": round(float(dist[order[0]]), 3)}
        lead = next((e for e in nb if e.truth_layers), None)
        layers: list[Lyr] = []
        if lead is not None and hs > 0:
            f = hs / max(lead.truth_hs_m or max(ly.bottom_depth_m for ly in lead.truth_layers), 1e-3)
            for ly in lead.truth_layers:
                cls = _class(ly.grain)
                mid = (ly.top_depth_m + ly.bottom_depth_m) / 2 / max(lead.truth_hs_m or 1.0, 1e-3)
                agree = 0.0
                for e, we in zip(nb, wt, strict=True):
                    h = e.truth_hs_m or 1.0
                    if any(_class(o.grain) == cls and abs((o.top_depth_m + o.bottom_depth_m) / 2 / h - mid)
                           <= REL_DEPTH_TOL for o in e.truth_layers):
                        agree += we
                layers.append(Lyr(top=ly.top_depth_m * f, bottom=ly.bottom_depth_m * f, grain=ly.grain,
                                  hardness_index=ly.hardness_index,
                                  prob=g["presence_confidence"] * (0.3 + 0.7 * agree), source="analogue"))
        limits = ["structure copied from past pits of other seasons"]
        return build_prediction(case, self.genome, hs, layers, g["presence_confidence"], limits, meta)
