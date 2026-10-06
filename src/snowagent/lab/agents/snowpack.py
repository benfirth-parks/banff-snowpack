"""SNOWPACK agent: the incumbent, the real SNOWPACK engine with the project's adopted settings (ADR-063).

The agent turns an engine profile at the valid time into the prediction contract: engine elements of one grain
form within ``hardness_merge_tol`` hand-hardness are one layer (as the site draws them), presence probability
``presence_confidence``, depth intervals from the uncertainty genes. Where the profile comes from is a backend:

- ``VisiblePackageEngine`` runs the binary from the visible package only: the case's measured weather from the season
  start (15 Sep, snow-free) to as-of, then its forecast (archived GFS run, or the measured stand-in) to the valid
  time, on an anonymous reference calendar (the case's day of year; the engine never sees the real year). The
  adopted settings: the site's engine template, the plot's precipitation factor on measured hours (ADR-024/038; raw
  GFS as the site's forecasts use), and the pit restart of ADR-039 (at the first 00 UTC after each pit of the
  season visible at as-of, the layering is re-initialised from the pit holding the depth-updated mass). Gaps the
  engine cannot take are filled and counted in the metadata (interpolation, clear-sky shortwave scaled by the case's
  mean clearness, Brutsaert-type longwave, no precipitation).
- ``FixedEngineResult``: a profile the harness found in the site's existing season runs when they used only
  information available at the case's as-of time (``lab.competition.incumbent``).
- ``FakeEngine`` (tests).

Without the binary (and no reusable run) the agent raises ``AgentUnavailable``: the case is skipped with the reason,
not scored as a miss.
"""

from __future__ import annotations

import dataclasses
import math
import re
import shutil
import tempfile
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal, Protocol

import numpy as np
import pandas as pd
import yaml

from snowagent.engine import snowpack as sp
from snowagent.lab.agents.common import (
    SIGMA,
    AgentUnavailable,
    Lyr,
    build_prediction,
    insufficient,
    pit_snow_depth,
    season_pits,
)
from snowagent.lab.agents.physics import EnginePhysics, engine_physics
from snowagent.lab.agents.segments import SEGMENT_VERSION, SegmentStore, pit_content, pit_hash, sha
from snowagent.lab.schemas.benchmark import ForecastSource, VisibleBenchmarkCase
from snowagent.lab.schemas.common import LabModel
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome
from snowagent.lab.schemas.prediction import SnowpackPrediction

AGENT_VERSION = "snowpack-1"
REPO = Path(__file__).resolve().parents[4]
PLOT_FORCING = REPO / "config" / "plot_forcing.yaml"
LAPSE_K_PER_M = -0.0065
ERA5_SOURCE = re.compile(r"^era5_cell_(\d+(?:\.\d+)?)m$")
STEP_MIN = 15.0


class EngineLayer(LabModel):
    top_depth_m: float
    bottom_depth_m: float
    grain: str | None = None
    hardness_index: float | None = None
    density_kg_m3: float | None = None
    temperature_c: float | None = None


class EngineResult(LabModel):
    """An engine profile at (or just before) the valid time, anonymous: no date, run id or pit id."""

    source: Literal["engine_run", "site_run_reuse", "fake"]
    snow_depth_m: float
    layers: list[EngineLayer]  # surface first
    profile_lag_h: float = 0.0  # valid time minus the profile's time
    snowpack_version: str
    config_hash: str | None = None
    forcing_hash: str | None = None
    steer_updates: int = 0
    physics_key: str = "default"  # EnginePhysics.key of the run (ADR-070); "default" = the incumbent
    notes: dict = {}


class EngineBackend(Protocol):
    def simulate(self, case: VisibleBenchmarkCase, physics: EnginePhysics | None = None) -> EngineResult: ...


class FixedEngineResult:
    """A precomputed profile (the harness's reuse of a site run for this case). It holds the incumbent's physics
    only: another physics goes to ``fallback`` (the engine run), or is unavailable without one."""

    def __init__(self, result: EngineResult, fallback: EngineBackend | None = None) -> None:
        self.result = result
        self.fallback = fallback

    def simulate(self, case: VisibleBenchmarkCase, physics: EnginePhysics | None = None) -> EngineResult:
        if physics is None or physics.is_default:
            return self.result
        if self.fallback is None:
            raise AgentUnavailable("a reused site run holds the incumbent's physics only")
        return self.fallback.simulate(case, physics)


class FakeEngine:
    """Test backend: a fixed two-layer profile whose depth follows the visible precipitation."""

    version = "fake-0"

    def __init__(self, base_depth_m: float = 0.8) -> None:
        self.base = base_depth_m
        self.calls = 0

    def simulate(self, case: VisibleBenchmarkCase, physics: EnginePhysics | None = None) -> EngineResult:
        """Depth follows the visible precipitation (times the physics' precipitation factor); a fixed new-snow
        density shifts it (so tests can see physics genes change the profile)."""
        self.calls += 1
        p = sum(h.precipitation_mm or 0.0 for h in (*case.weather_observed, *case.weather_forecasts))
        phys = physics or EnginePhysics()
        hs = self.base + p * phys.precip_mult / 1000.0
        if any(k == "HN_DENSITY" and v == "FIXED" for _s, k, v in phys.ini):
            hs *= 0.9
        return EngineResult(source="fake", snow_depth_m=hs, snowpack_version=self.version, physics_key=phys.key,
                            layers=[
            EngineLayer(top_depth_m=0.0, bottom_depth_m=hs / 2, grain="DF", hardness_index=1.5, density_kg_m3=150),
            EngineLayer(top_depth_m=hs / 2, bottom_depth_m=hs, grain="FC", hardness_index=2.5, density_kg_m3=250)])


class UnavailableEngine:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def simulate(self, case: VisibleBenchmarkCase, physics: EnginePhysics | None = None) -> EngineResult:
        raise AgentUnavailable(self.reason)


# --------------------------------------------------------------------------------------------- the engine run


@lru_cache(maxsize=2)
def _plots(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text())["plots"]


def reference_time(as_of_day_of_year: float) -> datetime:
    """An anonymous calendar for the engine: the case's UTC day of year in 2001 (Sep-Dec) or 2002 (Jan-Aug), both
    non-leap; only solar geometry depends on it."""
    year = 2001 if as_of_day_of_year >= 182 else 2002
    t = pd.Timestamp(f"{year}-01-01", tz="UTC") + pd.Timedelta(days=as_of_day_of_year - 1)
    return t.round("min").to_pydatetime()


def _hours_frame(case: VisibleBenchmarkCase) -> pd.DataFrame:
    """Observed then forecast hours (t_rel_h index) with each variable and its source, the forecast's kind."""
    rows = []
    last = max((h.t_rel_h for h in case.weather_observed), default=-math.inf)
    gfs_elev = case.forecast_runs[0].surface_elevation_m if case.forecast_runs else None
    for h in [*case.weather_observed, *(h for h in case.weather_forecasts if h.t_rel_h > last + 1e-6)]:
        rows.append({"t": h.t_rel_h, "ta": h.air_temperature_k, "rh": h.relative_humidity_frac,
                     "vw": h.wind_speed_ms, "dw": h.wind_direction_deg, "iswr": h.shortwave_radiation_wm2,
                     "ilwr": h.longwave_radiation_wm2, "psum": h.precipitation_mm,
                     "ta_src": h.sources.get("air_temperature_k"), "gfs": h.kind == "forecast",
                     "gfs_elev": gfs_elev if h.kind == "forecast" else None})
    return pd.DataFrame(rows).drop_duplicates("t").set_index("t").sort_index() if rows else pd.DataFrame()


def engine_forcing(case: VisibleBenchmarkCase, psum_factor: float, wind_mult: float = 1.0
                   ) -> tuple[pd.DataFrame, dict]:
    """Hourly SI forcing (ta rh vw dw iswr ilwr psum) at the plot elevation on the reference calendar, from the
    first visible hour to the hour after the valid time; gaps filled and counted. ``psum_factor`` (the plot's gauge
    factor, times the physics gene) and ``wind_mult`` (physics gene) apply to measured hours, not GFS hours."""
    from snowagent.baseline.run import plot_unit
    from snowagent.spatial_forcing import solar
    from snowagent.spatial_forcing.builder import ForcingConfig, shortwave_geometry

    site = case.site
    if site is None:
        raise ValueError("the case has no site")
    h = _hours_frame(case)
    if h.empty:
        raise ValueError("no visible weather")
    ref = pd.Timestamp(reference_time(case.as_of_day_of_year))
    h.index = pd.DatetimeIndex([ref + pd.Timedelta(hours=float(t)) for t in h.index]).round("h")
    h = h[~h.index.duplicated(keep="first")]
    valid = ref + pd.Timedelta(hours=case.horizon_hours)
    idx = pd.date_range(h.index.min(), valid.ceil("h"), freq="h")
    w = h.reindex(idx)
    fills: dict[str, int] = {"extended_after_last_hour": int((idx > h.index.max()).sum())}
    # temperature to the plot elevation: ERA5 cell and GFS surface heights are named; stations count as at the plot
    elev = np.full(len(w), float(site.elevation_m))
    for i, s in enumerate(w["ta_src"].astype(object)):
        m = ERA5_SOURCE.match(str(s)) if isinstance(s, str) else None
        if m:
            elev[i] = float(m[1])
    gfs = w["gfs"].eq(True).to_numpy()
    ge = pd.to_numeric(w["gfs_elev"], errors="coerce").to_numpy()
    elev = np.where(gfs & np.isfinite(ge), ge, elev)
    ta = pd.to_numeric(w["ta"], errors="coerce") + LAPSE_K_PER_M * (site.elevation_m - elev)
    out = pd.DataFrame(index=idx)
    for col, s, default in (("ta", ta, 268.15), ("rh", w["rh"], 0.8), ("vw", w["vw"], 2.0), ("dw", w["dw"], 0.0)):
        s = pd.to_numeric(s, errors="coerce")
        fills[col] = int(s.isna().sum())
        out[col] = s.interpolate(limit=6, limit_area="inside").ffill().bfill().fillna(default).to_numpy()
    out["rh"] = out["rh"].clip(0.05, 1.0)
    if wind_mult != 1.0:
        out["vw"] = np.where(gfs, out["vw"].to_numpy(), out["vw"].to_numpy() * wind_mult)
    p = pd.to_numeric(w["psum"], errors="coerce")
    fills["psum"] = int(p.isna().sum())
    p = p.fillna(0.0).clip(lower=0.0).to_numpy()
    out["psum"] = np.where(gfs, p, p * psum_factor)  # the plot factor corrects the gauge (raw GFS, as the site)
    unit = plot_unit(site.plot_id, site.latitude, site.longitude, site.elevation_m)
    geo = shortwave_geometry(idx, site.latitude, site.longitude, unit, ForcingConfig())
    ext = solar.SOLAR_CONSTANT * solar.eccentricity(idx) * geo["mean_cosz"].to_numpy()
    sw = pd.to_numeric(w["iswr"], errors="coerce").to_numpy()
    ok = np.isfinite(sw) & (ext > 50)
    kt = float(np.clip(np.nansum(sw[ok]) / np.nansum(ext[ok]), 0.2, 0.8)) if ok.sum() > 24 else 0.5
    fills["iswr"] = int((~np.isfinite(sw)).sum())
    out["iswr"] = np.where(np.isfinite(sw), sw, kt * ext).clip(min=0.0)
    lw = pd.to_numeric(w["ilwr"], errors="coerce").to_numpy()
    fills["ilwr"] = int((~np.isfinite(lw)).sum())
    tk = out["ta"].to_numpy()
    e_hpa = out["rh"].to_numpy() * 6.112 * np.exp(17.62 * (tk - 273.15) / (tk - 30.03))
    eps_clear = 1.24 * (e_hpa / tk) ** (1 / 7)
    cloud = float(np.clip(1.0 - kt / 0.75, 0.0, 1.0))  # cloudiness from the case's mean clearness
    eps = np.clip((1 - 0.84 * cloud) * eps_clear + 0.84 * cloud, 0.5, 1.0)
    out["ilwr"] = np.where(np.isfinite(lw), lw, eps * SIGMA * tk**4)
    return out, {"filled_hours": {k: v for k, v in fills.items() if v}, "clearness": round(kt, 3),
                 "hours": len(out)}


@dataclasses.dataclass(frozen=True)
class LabEngineSettings(sp.EngineSettings):
    """``EngineSettings`` plus PROF_START (verified in the pinned source, DataClasses.cc: with PROF_START > 0 the
    first profile is written at the restart file's date + PROF_START days), so the final segment writes its profile
    at the valid time. A field, so the engine's numerical retry (``dataclasses.replace``) keeps it."""

    prof_start_days: float = 0.0
    physics: EnginePhysics = EnginePhysics()  # ini overrides of the physics genes (ADR-070); default: none

    @classmethod
    def from_base(cls, base: sp.EngineSettings, prof_start_days: float = 0.0,
                  physics: EnginePhysics | None = None) -> LabEngineSettings:
        return cls(**{f.name: getattr(base, f.name) for f in dataclasses.fields(base)}, prof_start_days=prof_start_days,
                   physics=physics or EnginePhysics())

    def render(self, station_id: str) -> str:
        text = super().render(station_id)
        if self.prof_start_days > 0:
            text, n = re.subn(r"(?m)^PROF_START\s*=.*$", f"PROF_START = {self.prof_start_days:.10f}", text)
            if n != 1:
                raise ValueError("engine template has no single PROF_START key")
        return self.physics.apply_ini(text) if self.physics.ini else text


def _pit_dict(p, ref: pd.Timestamp) -> dict | None:
    """A visible pit in the observed-profile layout ``learn.steer`` uses (heights above ground, cm)."""
    hs = pit_snow_depth(p)
    if not hs:
        return None
    return {"profile_id": p.pit_key, "obs_time_utc": (ref + pd.Timedelta(hours=p.t_rel_h)).isoformat(),
            "hs_cm": hs * 100.0, "layers": [
                {"top_cm": (hs - ly.top_depth_m) * 100.0, "bottom_cm": (hs - ly.bottom_depth_m) * 100.0,
                 "grain_form": None if ly.grain_primary == "UNKNOWN" else ly.grain_primary,
                 "hardness_index": ly.hardness_index, "density_kg_m3": ly.density_kg_m3} for ly in p.layers
                if ly.bottom_depth_m <= hs + 1e-6]}


class VisiblePackageEngine:
    """Runs SNOWPACK from a visible case (see the module doc). ``work_dir``: where run directories go (removed
    after each case unless ``keep``). ``segments``: a ``SegmentStore`` of restart states shared between cases
    (ADR-071; None = every segment runs). ``simulate(case, physics)`` runs the physics of the genes (ADR-070;
    None = the incumbent)."""

    def __init__(self, work_dir: Path | None = None, binary: str | None = None, keep: bool = False,
                 steer: bool = True, segments: SegmentStore | None = None) -> None:
        self.work_dir = Path(work_dir) if work_dir else None
        self.binary = binary
        self.keep = keep
        self.steer = steer
        self.segments = segments
        self._engine = None
        self.segment_stats = {"run": 0, "reused": 0}

    def engine(self):
        if self._engine is None:
            from snowagent.errors import EngineUnavailable

            try:
                self._engine = sp.find_engine(self.binary)
            except EngineUnavailable as exc:
                raise AgentUnavailable(f"SNOWPACK binary not found ({exc}); set SNOWPACK_BIN or build it with "
                                       "scripts/build_snowpack.sh") from exc
        return self._engine

    # the three engine steps, separate so a test can replace them without a binary
    def _run(self, eng, settings, run_dir: Path, unit, f: pd.DataFrame, t: pd.Timestamp, sno: Path | None,
             seg_start: pd.Timestamp):
        from snowagent.engine.column import prepare_and_run

        return prepare_and_run(eng, settings, run_dir, unit, f, t.to_pydatetime(), initial_sno=sno,
                               snowfree_start=None if sno else seg_start.to_pydatetime())

    @staticmethod
    def _model_hs(out) -> float:
        return float(sp.parse_met(out.met)["Modelled snow depth (vertical)"].dropna().iloc[-1])

    @staticmethod
    def _update(state: Path, o: dict, model_hs: float, init: Path, rec: dict) -> None:
        """ADR-038/039 update of the restart state toward the pit ``o`` (writes ``init``)."""
        from snowagent.learn.steer import (
            HARD_RHO_PITS,
            MIN_HS_CM,
            STEER_WEIGHT,
            _pit_hs,
            _read_sno,
            pit_to_sno,
            scale_sno,
            sno_swe,
        )

        if model_hs < MIN_HS_CM:
            shutil.copyfile(state, init)
            rec["method"] = "none (model too shallow)"
            return
        fac = 1.0 + STEER_WEIGHT * (_pit_hs(o) / model_hs - 1.0)
        try:
            pit_to_sno(state, o, init, hard_rho=HARD_RHO_PITS, swe_target=sno_swe(_read_sno(state)[1]) * fac)
            rec["method"] = "layers"
        except ValueError:
            scale_sno(state, init, fac)
            rec["method"] = "depth"

    def _profile(self, out, valid: pd.Timestamp):
        from snowagent.engine.profiles import convert_profile

        _hdr, profiles = sp.parse_pro(out.pro)
        prof = min(profiles, key=lambda p: abs((p.time - valid).total_seconds()))
        lag_h = (valid - prof.time) / pd.Timedelta(hours=1)
        layers, diag, _n, _w = convert_profile(prof, 0.0, "plot")
        hs = diag.hs_vertical_m
        elayers = [EngineLayer(top_depth_m=max(0.0, hs - ly.top_vertical_m), bottom_depth_m=max(0.0, hs - ly.bottom_vertical_m),
                               grain=ly.grain_form_primary, hardness_index=ly.hand_hardness_index,
                               density_kg_m3=ly.density_kg_m3, temperature_c=ly.temperature_c)
                   for ly in sorted(layers, key=lambda x: -x.top_vertical_m)]
        return hs, elayers, lag_h

    def simulate(self, case: VisibleBenchmarkCase, physics: EnginePhysics | None = None) -> EngineResult:
        from snowagent.baseline.run import plot_unit
        from snowagent.engine.column import station_id
        from snowagent.learn.steer import _pit_hs
        from snowagent.spatial_forcing.builder import build_unit_forcing

        phys = physics or EnginePhysics()
        eng = self.engine()
        site = case.site
        plot = _plots(str(PLOT_FORCING)).get(site.plot_id, {})
        forcing, fnotes = engine_forcing(case, float(plot.get("psum_factor", 1.0)) * phys.precip_mult,
                                         wind_mult=phys.wind_mult)
        unit = plot_unit(site.plot_id, site.latitude, site.longitude, site.elevation_m)
        uf = build_unit_forcing(forcing, site.latitude, site.longitude, site.elevation_m, unit, phys.forcing_config())
        smet = uf.smet
        ref = pd.Timestamp(reference_time(case.as_of_day_of_year))
        valid = ref + pd.Timedelta(hours=case.horizon_hours)
        end = valid.ceil(f"{int(STEP_MIN)}min")
        start = smet.index[0] + pd.Timedelta(hours=6)
        # ADR-038/039 updates: the first 00 UTC after each visible pit of the season, the last pit before it wins
        updates: dict[pd.Timestamp, dict] = {}
        if self.steer:
            for p in season_pits(case):
                d = _pit_dict(p, ref)
                if d is None:
                    continue
                t = pd.Timestamp(d["obs_time_utc"]).ceil("D")
                if start < t < end:
                    updates[t] = d
        base = sp.EngineSettings(prof_days_between=1.0, snow_days_between=3650.0, first_backup=400.0)
        work = Path(tempfile.mkdtemp(prefix=f"sp_{case.case_key}_", dir=self.work_dir))
        notes = {**fnotes, "updates": []}
        if not phys.is_default:
            notes["physics"] = phys.as_dict()
        sid = station_id(unit)
        lon, lat = unit.centroid_lonlat
        seg_ctx = None
        if self.segments is not None:
            seg_ctx = {"v": SEGMENT_VERSION, "engine": eng.version_string, "store": self.segments.context,
                       "unit": [unit.unit_id, unit.slope_deg, unit.aspect_deg, unit.elevation_m, lat, lon]}
        smet_texts: list[str] = []
        try:
            seg_start, sno, k = start, None, 0
            init_key: str | None = None
            chain: dict = {"pits": [], "smet": []}
            for t in [*sorted(updates), end]:
                last = t == end
                settings = LabEngineSettings.from_base(base, (valid - seg_start) / pd.Timedelta(days=1) if last else 0.0,
                                                       phys)
                f = smet[(smet.index >= seg_start - pd.Timedelta(hours=6)) & (smet.index <= t.ceil("h"))]
                if last or seg_ctx is None:
                    out = self._run(eng, settings, work / f"seg{k:02d}", unit, f, t, sno, seg_start)
                    smet_texts.append((work / f"seg{k:02d}" / "input" / f"{sid}.smet").read_text())
                    self.segment_stats["run"] += 1
                    if last:
                        break
                    state, model_hs = Path(out.sno), self._model_hs(out)
                else:
                    # shared restart reuse (ADR-071): the key hashes every input of the segment
                    kf = work / "keys" / f"seg{k:02d}.smet"
                    kf.parent.mkdir(parents=True, exist_ok=True)
                    sp.write_smet_forcing(kf, sid, lat, lon, unit.elevation_m, f)
                    text = kf.read_text()
                    smet_texts.append(text)
                    smet_sha = sp.sha256_text(text)
                    seg_key = sha({"ctx": seg_ctx, "ini": settings.render(sid), "smet": smet_sha,
                                   "end": t.isoformat(), "init": init_key or {"snowfree": seg_start.isoformat()}})
                    prov = {"pits": list(chain["pits"]), "smet": [*chain["smet"], smet_sha], "init": init_key,
                            "end": t.isoformat()}
                    with self.segments.lock(seg_key):
                        hit = self.segments.get(seg_key)
                        if hit is None:
                            out = self._run(eng, settings, work / f"seg{k:02d}", unit, f, t, sno, seg_start)
                            state, model_hs = Path(out.sno), self._model_hs(out)
                            self.segments.put(seg_key, state.read_bytes(), prov | {"model_hs": model_hs})
                            self.segments.misses += 1
                            self.segment_stats["run"] += 1
                        else:
                            state_bytes, meta = hit
                            state = work / f"state{k:02d}.sno"
                            state.write_bytes(state_bytes)
                            model_hs = float(meta["model_hs"])
                            self.segments.hits += 1
                            self.segment_stats["reused"] += 1
                    self.segments.loaded.append(prov | {"key": seg_key})
                    chain = {"pits": [*chain["pits"], pit_hash(updates[t])], "smet": prov["smet"]}
                    init_key = sha({"prev": seg_key, "pit": pit_content(updates[t])})
                o = updates[t]
                init = work / f"init{k:02d}.sno"
                rec = {"t_rel_h": round((t - ref) / pd.Timedelta(hours=1), 2), "hs_pit_cm": round(_pit_hs(o), 1),
                       "hs_model_cm": round(model_hs, 1)}
                self._update(state, o, model_hs, init, rec)
                notes["updates"].append(rec)
                sno, seg_start, k = init, t, k + 1
            hs, elayers, lag_h = self._profile(out, valid)
            fhash = sp.sha256_text("".join(smet_texts))[:16]
        finally:
            if not self.keep:
                shutil.rmtree(work, ignore_errors=True)
        return EngineResult(source="engine_run", snow_depth_m=hs, layers=elayers, profile_lag_h=round(lag_h, 3),
                            snowpack_version=eng.version_string,
                            config_hash=LabEngineSettings.from_base(base, 0.0, phys).config_hash(),
                            forcing_hash=fhash, steer_updates=sum(u["method"] in ("layers", "depth") for u in notes["updates"]),
                            physics_key=phys.key, notes=notes)


# --------------------------------------------------------------------------------------------- the agent


def _wavg(a: float | None, b: float | None, wa: float, wb: float) -> float | None:
    return None if a is None or b is None else (a * wa + b * wb) / (wa + wb)


def merge_engine_layers(layers: list[EngineLayer], tol: float) -> list[EngineLayer]:
    """Adjacent elements (surface first) with the same grain form and hand hardness within ``tol`` -> one layer
    (thickness-weighted hardness and density), as ``web.build.web_layers`` draws them."""
    out: list[EngineLayer] = []
    for ly in layers:
        if ly.bottom_depth_m - ly.top_depth_m <= 0:
            continue
        m = out[-1] if out else None
        if (m is not None and m.grain == ly.grain and (m.hardness_index is None or ly.hardness_index is None
                                                        or abs(m.hardness_index - ly.hardness_index) <= tol)):
            tm, tl = m.bottom_depth_m - m.top_depth_m, ly.bottom_depth_m - ly.top_depth_m

            out[-1] = m.model_copy(update={"bottom_depth_m": ly.bottom_depth_m,
                                           "hardness_index": _wavg(m.hardness_index, ly.hardness_index, tm, tl),
                                           "density_kg_m3": _wavg(m.density_kg_m3, ly.density_kg_m3, tm, tl)})
        else:
            out.append(ly)
    return out


class SnowpackAgent:
    """The incumbent. ``backend`` gives the engine profile (``VisiblePackageEngine`` by default)."""

    def __init__(self, genome: AgentGenome, backend: EngineBackend | None = None,
                 physics_genes: dict | None = None) -> None:
        if genome.family != AgentFamily.snowpack:
            raise ValueError(f"SnowpackAgent needs a snowpack genome, got {genome.family}")
        self.genome = genome
        self.agent_id = genome.agent_id
        self.backend = backend or VisiblePackageEngine()
        self.physics_genes = genome.genes if physics_genes is None else physics_genes  # hybrid: its own genes

    def physics(self, case: VisibleBenchmarkCase) -> EnginePhysics:
        """The engine physics of this agent's genes at the case's plot (ADR-070)."""
        return engine_physics(self.physics_genes, str(getattr(case.site_code, "value", case.site_code)))

    def engine_result(self, case: VisibleBenchmarkCase) -> EngineResult:
        return self.backend.simulate(case, self.physics(case))

    def layers_from(self, result: EngineResult, prob: float) -> list[Lyr]:
        merged = merge_engine_layers(result.layers, self.genome.genes["hardness_merge_tol"])
        return [Lyr(top=ly.top_depth_m, bottom=ly.bottom_depth_m, grain=ly.grain or "UNKNOWN",
                    hardness_index=ly.hardness_index, density=ly.density_kg_m3, prob=prob, source="snowpack")
                for ly in merged]

    def predict(self, case: VisibleBenchmarkCase, seed: int) -> SnowpackPrediction:
        from snowagent.errors import EngineRunFailed

        g = self.genome.genes
        try:
            r = self.engine_result(case)
        except EngineRunFailed as exc:
            return insufficient(case, self.genome, f"SNOWPACK run failed: {exc.message}"[:300],
                                {"agent_version": AGENT_VERSION})
        except ValueError as exc:
            return insufficient(case, self.genome, f"no engine forcing: {exc}"[:300], {"agent_version": AGENT_VERSION})
        meta = {"agent_version": AGENT_VERSION, "engine_source": r.source, "snowpack_version": r.snowpack_version,
                "engine_config_hash": r.config_hash, "forcing_hash": r.forcing_hash, "profile_lag_h": r.profile_lag_h,
                "steer_updates": r.steer_updates, "forecast_source": case.forecast_source.value}
        if r.notes.get("filled_hours"):
            meta["filled_hours"] = r.notes["filled_hours"]
        if r.physics_key != "default":
            meta["physics_key"] = r.physics_key
            meta["physics"] = r.notes.get("physics")
        limits = ["SNOWPACK flat-plot column; settings as the site" if r.physics_key == "default" else
                  "SNOWPACK flat-plot column; evolved physics settings (not the site's)"]
        if case.forecast_source == ForecastSource.archived_gfs:
            limits.append("forecast hours are raw GFS at the plot (no correction)")
        return build_prediction(case, self.genome, r.snow_depth_m, self.layers_from(r, g["presence_confidence"]),
                                g["presence_confidence"], limits, meta)


__all__ = ["EngineLayer", "EngineResult", "FakeEngine", "FixedEngineResult", "SnowpackAgent", "UnavailableEngine",
           "VisiblePackageEngine", "engine_forcing", "merge_engine_layers", "reference_time"]
