"""Lab data services: initialise the data directory, import the project's data, coverage and loaders.

The import reads the observed-profile set and the QC'd station records of a checkout (``source_root``, read only),
writes the canonical tables under the lab's data root, records a run manifest in the registry and checks that no
input file changed while it ran.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import yaml

from snowagent.lab.ingest.era5 import era5_files, era5_site_series, source_label
from snowagent.lab.ingest.profiles import import_profiles
from snowagent.lab.ingest.weather import site_weather, station_files, station_loader, validate_frame
from snowagent.lab.schemas.observation import Observation
from snowagent.lab.schemas.profile import SnowLayer, SnowProfile
from snowagent.lab.schemas.run import RunKind, RunManifest
from snowagent.lab.settings import LabConfig, season_bounds, season_keys
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.storage.provenance import data_hash, git_commit, input_files, new_run_id, software_version
from snowagent.lab.storage.registry import RunRegistry
from snowagent.lab.storage.tables import read_table, write_table

OBSERVED_PROFILES = Path("data/interim/obs/observed_profiles.jsonl")
READ_ONLY_INPUTS = ("data/raw", "data/interim", "archive", "profiles", "observations")
PROFILE_JSON = ("review_reasons", "flags", "validation_warnings", "temperatures", "raw")
LAYER_JSON = ("concern_basis", "uncertain_fields", "raw")


class LabImportError(RuntimeError):
    """The import cannot run (missing input, output inside an input) or an input changed while it ran."""


# --------------------------------------------------------------------------------------------- init


def init_lab(paths: LabPaths) -> dict:
    for d in paths.directories():
        d.mkdir(parents=True, exist_ok=True)
    RunRegistry(paths.registry).init()
    return {"data_root": str(paths.root), "registry": str(paths.registry),
            "directories": [str(d) for d in paths.directories()]}


def _check_output_location(paths: LabPaths, source_root: Path) -> None:
    root = paths.root.resolve()
    for name in READ_ONLY_INPUTS:
        protected = (Path(source_root) / name).resolve()
        if root == protected or root.is_relative_to(protected):
            raise LabImportError(f"data root {root} is inside the read-only input {protected}; choose another")


# --------------------------------------------------------------------------------------------- frames


def profiles_frame(profiles: list[SnowProfile], season_start: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Profiles and layers as flat tables (lists and dicts as ``<name>_json`` text columns)."""
    prows, lrows = [], []
    for p in profiles:
        d = p.model_dump(mode="json", exclude={"layers"})
        for k in PROFILE_JSON:
            d[f"{k}_json"] = json.dumps(d.pop(k), default=str)
        d["n_layers"] = len(p.layers)
        d["n_layers_of_concern"] = sum(ly.is_layer_of_concern for ly in p.layers)
        d["unique_usable"] = p.usable and p.duplicate_of is None
        prows.append(d)
        for ly in p.layers:
            ld = ly.model_dump(mode="json")
            for k in LAYER_JSON:
                ld[f"{k}_json"] = json.dumps(ld.pop(k), default=str)
            lrows.append(ld)
    pdf, ldf = pd.DataFrame(prows), pd.DataFrame(lrows)
    if len(pdf):
        for c in ("observed_at", "source_recorded_at"):
            pdf[c] = pd.to_datetime(pdf[c], utc=True)
        pdf.insert(4, "season", season_keys(pdf["observed_at"], season_start))
    return pdf, ldf


def observations_frame(observations: list[Observation]) -> pd.DataFrame:
    rows = []
    for o in observations:
        d = o.model_dump(mode="json")
        d["payload_json"] = json.dumps(d.pop("payload"), default=str)
        rows.append(d)
    df = pd.DataFrame(rows)
    if len(df):
        for c in ("observed_at", "source_recorded_at"):
            df[c] = pd.to_datetime(df[c], utc=True)
    return df


def _clean(v):
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, float) and v != v:
        return None
    return v


def profile_from_rows(prow: dict, layer_rows: list[dict]) -> SnowProfile:
    """Rebuild a ``SnowProfile`` from its stored rows (validates the stored form)."""
    p = {k: _clean(v) for k, v in prow.items()
         if k not in {"season", "n_layers", "n_layers_of_concern", "unique_usable"} and not k.endswith("_json")}
    for k in PROFILE_JSON:
        p[k] = json.loads(prow[f"{k}_json"])
    layers = []
    for r in sorted(layer_rows, key=lambda r: (r["top_depth_m"], r["bottom_depth_m"])):
        ld = {k: _clean(v) for k, v in r.items() if not k.endswith("_json")}
        for k in LAYER_JSON:
            ld[k] = json.loads(r[f"{k}_json"])
        layers.append(SnowLayer(**ld))
    return SnowProfile(**p, layers=layers)


# --------------------------------------------------------------------------------------------- import


def _plots(config: LabConfig) -> dict:
    return yaml.safe_load(Path(config.plot_forcing_config).read_text())["plots"]


def era5_start(config: LabConfig, efiles: list[Path]) -> pd.Timestamp | None:
    """First hour of the weather table when ERA5 fills the reanalysis seasons before the plot stations (ADR-076):
    the first reanalysis season's start, or the first ERA5 month in the cache if that is later (no rows for years
    the cache does not reach). None with the switch off, without ERA5 files, or when the cache starts after the
    last reanalysis season (the table then starts at the stations' first hour, as before)."""
    s = config.splits
    old = sorted(s.reanalysis_seasons) if s.include_reanalysis_seasons else []
    months = sorted(f.stem[len("era5_box_"):] for f in efiles if f.stem[len("era5_box_"):].isdigit())
    if not old or not months:
        return None
    first = pd.Timestamp(f"{months[0][:4]}-{months[0][4:]}-01", tz="UTC")
    start = max(season_bounds(old[0], config.season_start)[0], first)
    return start if start < season_bounds(old[-1], config.season_start)[1] else None


def import_data(source_root: Path, paths: LabPaths, config: LabConfig, what: tuple[str, ...] = ("profiles", "weather"),
                profiles_file: Path | None = None) -> dict:
    """Import from a checkout (read only) into ``paths``; returns the report (also the run manifest's counts)."""
    t0 = time.time()
    created = datetime.now(UTC)
    source_root = Path(source_root)
    _check_output_location(paths, source_root)
    init_lab(paths)
    run_id = new_run_id("import", created, str(paths.root))
    warnings: list[str] = []
    report: dict = {"run_id": run_id, "data_root": str(paths.root), "source_root": str(source_root), "sites": {}}
    for code in config.sites:
        report["sites"][code.value] = {}
    inputs: list[Path] = []
    wfiles: list[Path] = []
    pfile = Path(profiles_file) if profiles_file else source_root / OBSERVED_PROFILES
    plots = _plots(config)
    stations = {k for s in config.sites.values() for k in s.wind_stations}
    for s in config.sites.values():
        stations |= {k for key in ("ta", "rh", "psum", "hs_check", "swe_check") for k in plots[s.plot_id].get(key, [])}
    if "profiles" in what:
        if pfile.exists():
            inputs.append(pfile)
        else:
            raise LabImportError(f"observed profiles not found at {pfile}; build them with `snowagent obs profiles`")
    if "weather" in what:
        wfiles = station_files(source_root, stations)
        if not wfiles:
            warnings.append(f"no station files under {source_root}/data (restore them with "
                            "`snowagent update bootstrap` in that checkout); weather not imported")
        inputs += wfiles
        era5_dir = source_root / config.weather.era5_dir
        efiles = era5_files(era5_dir) if config.weather.era5_backfill else []
        if config.weather.era5_backfill and wfiles and not efiles:
            warnings.append(f"ERA5 backfill configured but no ERA5 cache at {era5_dir}; station values only")
        if efiles and wfiles:
            inputs += efiles  # monthly files and the surface-height file (era5_box_z.npz)
    before = input_files(inputs, source_root)
    profile_ids: list[str] = []
    outputs: list[str] = []

    if "profiles" in what:
        records = [json.loads(line) for line in pfile.read_text().splitlines() if line.strip()]
        profiles, observations, prep = import_profiles(records, config, run_id)
        pdf, ldf = profiles_frame(profiles, config.season_start)
        odf = observations_frame(observations)
        outputs += [str(write_table(pdf, paths.profiles)), str(write_table(ldf, paths.layers)),
                    str(write_table(odf, paths.observations))]
        profile_ids = [p.profile_id for p in profiles]
        report["profiles_source_records"] = len(records)
        report["profiles_skipped"] = prep["skipped"]
        if prep["skipped_ids"]:
            report["profiles_skipped_ids"] = prep["skipped_ids"]
        for code in config.sites:
            ps = [p for p in profiles if p.site_code == code]
            report["sites"][code.value] |= {
                "profiles": len(ps), "profiles_unique_usable": sum(p.usable and p.duplicate_of is None for p in ps),
                "profiles_duplicates": sum(p.duplicate_of is not None for p in ps),
                "profiles_on_review_list": sum(bool(p.review_reasons) for p in ps),
                "profiles_with_warnings": sum(bool(p.validation_warnings) for p in ps),
                "layers": sum(len(p.layers) for p in ps),
                "layers_of_concern": sum(ly.is_layer_of_concern for p in ps for ly in p.layers),
                "stability_tests": sum(o.site_code == code for o in observations)}

    if "weather" in what and wfiles:
        load = station_loader(source_root)
        frames = []
        first_season_start = era5_start(config, efiles)
        for code, site in config.sites.items():
            fill = None
            if efiles:
                def fill(idx, site=site):
                    series, elev = era5_site_series(site.latitude, site.longitude, idx, era5_dir)
                    return series, source_label(elev)
            df, summary = site_weather(site, plots[site.plot_id], load, run_id, fill, start=first_season_start)
            validate_frame(df)
            frames.append(df)
            report["sites"][code.value] |= {"weather_hours": summary["hours"],
                                            "weather_first_hour": summary.get("first_hour"),
                                            "weather_last_hour": summary.get("last_hour"),
                                            "weather_qc": summary["variables"],
                                            "weather_backfill": summary.get("backfill"),
                                            "weather_stations": summary["stations"]}
        outputs.append(str(write_table(pd.concat(frames, ignore_index=True), paths.weather)))

    after = input_files(inputs, source_root)
    if [(f.path, f.sha256) for f in after] != [(f.path, f.sha256) for f in before]:
        raise LabImportError("an input file changed while the import ran; outputs are not trustworthy, run it again")
    counts = {f"{site}_{k}": v for site, d in report["sites"].items() for k, v in d.items() if isinstance(v, int)}
    manifest = RunManifest(
        run_id=run_id, kind=RunKind.data_import, status="ok", created_at=created, finished_at=datetime.now(UTC),
        config_hash=config.config_hash(), data_hash=data_hash(before), software_version=software_version(),
        git_commit=git_commit(Path(__file__).parent), profile_ids_used=profile_ids, inputs=before, outputs=outputs,
        counts=counts, warnings=warnings, runtime_s=round(time.time() - t0, 1))
    RunRegistry(paths.registry).record(manifest)
    mpath = paths.manifests / f"{run_id}.json"
    mpath.write_text(manifest.model_dump_json(indent=1))
    report |= {"inputs": len(before), "data_hash": manifest.data_hash, "config_hash": manifest.config_hash,
               "manifest": str(mpath), "warnings": warnings, "runtime_s": manifest.runtime_s}
    return report


# --------------------------------------------------------------------------------------------- read side


def data_status(paths: LabPaths) -> dict[str, bool]:
    return {"profiles": paths.profiles.exists(), "layers": paths.layers.exists(),
            "observations": paths.observations.exists(), "weather": paths.weather.exists(),
            "registry": paths.registry.exists()}


def load_profiles(paths: LabPaths, site: str | None = None, start: pd.Timestamp | None = None,
                  end: pd.Timestamp | None = None) -> pd.DataFrame:
    df = read_table(paths.profiles)
    if df.empty:
        return df
    if site:
        df = df[df["site_code"] == site]
    if start is not None:
        df = df[df["observed_at"] >= start]
    if end is not None:
        df = df[df["observed_at"] <= end]
    return df.sort_values("observed_at").reset_index(drop=True)


def load_layers(paths: LabPaths, profile_id: str) -> pd.DataFrame:
    df = read_table(paths.layers, filters=[("profile_id", "==", profile_id)]) if paths.layers.exists() else (
        pd.DataFrame())
    return df.sort_values("top_depth_m").reset_index(drop=True) if len(df) else df


def load_profile(paths: LabPaths, profile_id: str) -> SnowProfile | None:
    p = read_table(paths.profiles, filters=[("profile_id", "==", profile_id)]) if paths.profiles.exists() else (
        pd.DataFrame())
    if p.empty:
        return None
    return profile_from_rows(p.iloc[0].to_dict(), load_layers(paths, profile_id).to_dict("records"))


def load_weather(paths: LabPaths, site: str, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None,
                 columns: list[str] | None = None) -> pd.DataFrame:
    if not paths.weather.exists():
        return pd.DataFrame(columns=columns or [])
    filters: list = [("site_code", "==", site)]
    if start is not None:
        filters.append(("observed_at", ">=", pd.Timestamp(start)))
    if end is not None:
        filters.append(("observed_at", "<=", pd.Timestamp(end)))
    return read_table(paths.weather, columns=columns, filters=filters)


def coverage(paths: LabPaths, config: LabConfig) -> dict[str, pd.DataFrame]:
    """Profiles and weather per site and season (season from the 15 Sep start)."""
    prof = read_table(paths.profiles, columns=None) if paths.profiles.exists() else pd.DataFrame()
    if len(prof):
        prof = prof.assign(on_review_list=prof["review_reasons_json"] != "[]")
        pc = (prof.groupby(["site_code", "season"])
              .agg(profiles=("profile_id", "size"), unique_usable=("unique_usable", "sum"),
                   on_review_list=("on_review_list", "sum"), layers=("n_layers", "sum"))
              .reset_index())
    else:
        pc = pd.DataFrame(columns=["site_code", "season", "profiles", "unique_usable", "on_review_list", "layers"])
    cols = ["site_code", "observed_at", "air_temperature_k_qc", "precipitation_mm_qc", "snow_depth_m_qc"]
    w = read_table(paths.weather, columns=cols) if paths.weather.exists() else pd.DataFrame(columns=cols)
    if len(w):
        w = w.assign(season=season_keys(w["observed_at"], config.season_start))
        agg = {"hours": ("observed_at", "size")}
        for c, name in (("air_temperature_k_qc", "temperature_ok"), ("precipitation_mm_qc", "precipitation_ok"),
                        ("snow_depth_m_qc", "snow_depth_ok")):
            w[name] = w[c] == "ok"
            agg[name] = (name, "mean")
        wc = w.groupby(["site_code", "season"]).agg(**agg).reset_index()
        for c in ("temperature_ok", "precipitation_ok", "snow_depth_ok"):
            wc[c] = (wc[c] * 100).round(1)
    else:
        wc = pd.DataFrame(columns=["site_code", "season", "hours", "temperature_ok", "precipitation_ok",
                                   "snow_depth_ok"])
    return {"profiles": pc, "weather": wc}


def latest_runs(paths: LabPaths, limit: int = 10) -> list[RunManifest]:
    return RunRegistry(paths.registry).latest(limit)
