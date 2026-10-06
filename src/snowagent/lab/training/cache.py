"""Prediction, score and engine cache of the training loop (ADR-066).

Training re-scores the same genomes many times: the survivors of every round, the gene-less SNOWPACK incumbent, and
every fold of the leave-one-season-out check. Two caches under ``<data root>/outputs/cache/`` make that free:

- **Predictions and scores**, one file per (genome hash, case hash, context hash). The case hash is the sha256 of
  the case's ``manifest.json`` (which holds the hashes of its visible and hidden files); the context hash covers
  what a prediction depends on: the prediction code (``code_hash``: every module of ``snowagent`` except the loop,
  UI, CLI and the scoring module), the lab configuration hash, the runner version, the run seed and, per family,
  what else the agent reads: the SNOWPACK binary version and engine settings files (SNOWPACK, hybrid) and the
  analogue library (analogue). Scoring is not in the key (ADR-074): each entry records the scoring identity its row
  was scored under (``scoring_identity``: scoring version, scoring code, frozen weights), and an entry scored under
  another one is re-scored from its stored prediction, never re-predicted and never mixed in as it is.
- **Engine profiles**, one file per engine-input hash: everything ``VisiblePackageEngine.simulate`` reads from a
  visible case (site, day of year, horizon, measured and forecast hours, forecast runs, the season's pits without
  their anonymous keys), the binary version, the engine settings files, the prediction-code hash (so a scoring
  change keeps every profile) and the physics key (ADR-070:
  the normalised physics genes of the case's plot, ``default`` for the incumbent). Output and uncertainty genes are
  not in it, so output-only mutants reuse the profile of their physics and cost milliseconds per case; the same pit
  in a leave-one-season-out case set (other case key, same inputs) hits too. Deterministic engine failures are
  cached as failures; a missing binary is never cached.
- **Restart segments** (``segments/``, ADR-071): restart states shared by cases whose visible inputs agree up to a
  pit update (``lab.agents.segments``).

Entries are written atomically (temporary file, then rename) by the worker that computed them, so a killed run
keeps every finished (genome, case) pair. Nothing here reads hidden truth: a cached score was computed by the
scorer behind the truth gate, for a case the loop selected.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path

from snowagent.lab.agents.physics import EnginePhysics
from snowagent.lab.agents.snowpack import EngineResult, FakeEngine, VisiblePackageEngine
from snowagent.lab.schemas.benchmark import VisibleBenchmarkCase
from snowagent.lab.schemas.genome import AgentFamily

CACHE_VERSION = "lab-train-cache-2"  # 2: engine profiles keyed by physics (ADR-070)
SRC = Path(__file__).resolve().parents[2]  # src/snowagent
REPO = SRC.parents[1]
# Modules that cannot change a prediction or a score: the loop itself, the UI, the CLIs, the lab services, the
# event feed, and the daily update (ADR-080: no prediction module imports it, which a test checks; the site build
# `web/` stays in, as `learn.steer`, which the SNOWPACK agent uses, reads its forcing and profiles).
CODE_EXCLUDE = ("lab/training/", "lab/ui/", "lab/services/", "lab/cli.py", "cli.py",
                "lab/events.py",  # the Arena's event feed (ADR-078): a side channel
                "ops/")
# The scorer: it changes scores, never a prediction or an engine profile, so it has its own hash (ADR-074).
SCORING_FILES = ("lab/competition/scoring.py",)
ENGINE_FAMILIES = frozenset({AgentFamily.snowpack, AgentFamily.hybrid})


def sha(obj) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


@lru_cache(maxsize=1)
def code_hash() -> str:
    """sha256 over the source of every ``snowagent`` module that can change a prediction (or an engine profile).
    The scoring module is not in it (``scoring_hash``): a scoring change re-scores, it never re-predicts."""
    h = hashlib.sha256()
    for f in sorted(SRC.rglob("*.py")):
        rel = f.relative_to(SRC).as_posix()
        if rel.startswith(CODE_EXCLUDE) or rel in CODE_EXCLUDE or rel in SCORING_FILES or "__pycache__" in rel:
            continue
        h.update(rel.encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


@lru_cache(maxsize=1)
def scoring_hash() -> str:
    """sha256 over the scoring module's source (``SCORING_FILES``)."""
    h = hashlib.sha256()
    for rel in SCORING_FILES:
        h.update(rel.encode() + b"\0" + (SRC / rel).read_bytes() + b"\0")
    return h.hexdigest()


def scoring_identity(weights: dict) -> str:
    """What a cached score row was scored under: the scoring version, the scoring code and the frozen weights."""
    from snowagent.lab.competition.scoring import SCORING_VERSION

    return sha({"version": SCORING_VERSION, "code": scoring_hash(), "weights": weights})


@lru_cache(maxsize=1)
def engine_files_hash() -> str:
    """The engine's settings files: the plot precipitation factors and the SNOWPACK templates."""
    h = hashlib.sha256()
    for f in [REPO / "config" / "plot_forcing.yaml", *sorted((REPO / "config" / "snowpack").glob("*.ini"))]:
        if f.is_file():
            h.update(f.name.encode() + b"\0" + f.read_bytes())
    return h.hexdigest()


@lru_cache(maxsize=8)
def engine_identity(kind: str, binary: str | None = None) -> str:
    """What produces engine profiles: the SNOWPACK version string (``auto``), ``fake-0`` or ``none``."""
    if kind == "fake":
        return f"fake:{FakeEngine.version}"
    if kind == "none":
        return "none"
    from snowagent.engine import snowpack as sp
    from snowagent.errors import EngineUnavailable

    try:
        return f"snowpack:{sp.find_engine(binary).version_string}"
    except EngineUnavailable:
        return "unavailable"


def engine_inputs(case: VisibleBenchmarkCase) -> dict:
    """Everything the engine run reads from a visible case (``VisiblePackageEngine.simulate``): not the case key,
    other seasons' pits, observations or availability notes."""
    from snowagent.lab.agents.common import season_pits

    d = case.model_dump(mode="json", include={"site_code", "as_of_day_of_year", "horizon_hours", "site",
                                              "forecast_source", "weather_observed", "weather_forecasts",
                                              "forecast_runs"})
    d["season_pits"] = [p.model_dump(mode="json", exclude={"pit_key"}) for p in season_pits(case)]
    return d


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


class TrainingCache:
    """The cache directory (``<data root>/outputs/cache``)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- predictions and scores
    def prediction_path(self, key: str) -> Path:
        return self.root / "predictions" / key[:2] / f"{key}.json"

    def has(self, key: str) -> bool:
        return self.prediction_path(key).is_file()

    def get(self, key: str) -> dict | None:
        p = self.prediction_path(key)
        try:
            return json.loads(p.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def put(self, key: str, entry: dict) -> None:
        _write_atomic(self.prediction_path(key), json.dumps(entry, default=_json_default))

    # -- engine profiles
    def engine_path(self, key: str) -> Path:
        return self.root / "engine" / key[:2] / f"{key}.json"

    def engine_index_path(self, case_hash: str, physics_key: str = "default") -> Path:
        name = case_hash if physics_key == "default" else f"{case_hash}-{physics_key}"
        return self.root / "engine_index" / case_hash[:2] / f"{name}.txt"

    def engine_cached_for(self, case_hash: str, physics_key: str = "default") -> bool | None:
        """Whether the engine profile of a case with this physics is cached (None: never seen, so unknown)."""
        idx = self.engine_index_path(case_hash, physics_key)
        if not idx.is_file():
            return None
        return self.engine_path(idx.read_text().strip()).is_file()

    @property
    def segments_root(self) -> Path:
        return self.root / "segments"

    @property
    def timings(self) -> Path:
        return self.root / "timings.json"


def _json_default(o):
    import numpy as np

    if isinstance(o, np.generic):
        return o.item()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    raise TypeError(f"not JSON serialisable: {type(o)}")


def prediction_key(genome_hash: str, case_hash: str, context_hash: str) -> str:
    return sha({"v": CACHE_VERSION, "genome": genome_hash, "case": case_hash, "context": context_hash})


def context_hashes(base: dict, engine: dict, library_hash: str | None) -> dict[AgentFamily, str]:
    """The context hash of each family: the base (prediction code, config, runner, seed) plus what that family
    reads."""
    out = {}
    for fam in AgentFamily:
        ctx = dict(base)
        if fam in ENGINE_FAMILIES:
            ctx["engine"] = engine
        if fam == AgentFamily.analogue:
            ctx["library"] = library_hash
        out[fam] = sha(ctx)
    return out


class DiskEngineCache:
    """An engine backend that serves profiles from the cache and stores new ones (see the module doc)."""

    def __init__(self, inner, cache: TrainingCache, identity: str, case_hash: str | None = None) -> None:
        self.inner = inner
        self.cache = cache
        self.identity = identity
        self.case_hash = case_hash
        self.hit: bool | None = None  # of the last call
        self.key: str | None = None
        self.hits = 0
        self.misses: list[float] = []  # engine seconds of each run this backend made

    def key_for(self, case: VisibleBenchmarkCase, physics: EnginePhysics | None = None) -> str:
        pk = physics.key if physics is not None else "default"
        ident = {"v": CACHE_VERSION, "engine": self.identity, "code": code_hash(), "files": engine_files_hash(),
                 "steer": getattr(self.inner, "steer", None), "inputs": sha(engine_inputs(case))}
        if pk != "default":  # the incumbent's key is unchanged by the physics genes' arrival
            ident["physics"] = physics.as_dict()
        return sha(ident)

    def simulate(self, case: VisibleBenchmarkCase, physics: EnginePhysics | None = None) -> EngineResult:
        import time

        from snowagent.errors import EngineRunFailed

        self.key = key = self.key_for(case, physics)
        if self.case_hash:
            idx = self.cache.engine_index_path(self.case_hash, physics.key if physics is not None else "default")
            if not idx.is_file():
                _write_atomic(idx, key)
        p = self.cache.engine_path(key)
        if p.is_file():
            self.hit = True
            self.hits += 1
            d = json.loads(p.read_text())
            if "failure" in d:
                f = d["failure"]
                raise (EngineRunFailed(f["message"]) if f["type"] == "EngineRunFailed" else ValueError(f["message"]))
            return EngineResult.model_validate(d)
        self.hit = False
        t0 = time.perf_counter()
        try:
            res = self.inner.simulate(case, physics)
        except EngineRunFailed as exc:
            self.misses.append(time.perf_counter() - t0)
            _write_atomic(p, json.dumps({"failure": {"type": "EngineRunFailed", "message": exc.message}}))
            raise
        except ValueError as exc:  # no usable forcing: deterministic for these inputs
            self.misses.append(time.perf_counter() - t0)
            _write_atomic(p, json.dumps({"failure": {"type": "ValueError", "message": str(exc)}}))
            raise
        self.misses.append(time.perf_counter() - t0)
        _write_atomic(p, res.model_dump_json())
        return res


def cacheable_engine(inner) -> bool:
    """Engine runs from the visible package (and the test engine) are cached; reused site runs are not."""
    return isinstance(inner, VisiblePackageEngine | FakeEngine)
