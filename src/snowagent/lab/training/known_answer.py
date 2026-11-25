"""Known-answer test of the training loop (ADR-157; research report 2026-11-24, idea 11).

A copy of the lab's cases whose withheld pits are replaced by fake pits drawn by one agent with known genes (the
"true" agent; Carver Daniels by default), with observer-like noise added. A training run on that copy, started where
real runs start, shows which genes the pits can pin down (the run moves them toward the true values), which they
cannot (the run leaves them or wanders), and how far the run's best score falls short of the true agent's own score,
which is the noise floor: no agent can beat the true agent on average except by luck.

The copy keeps every visible file of every case unchanged (weather, the earlier pits a model restarts from), so a
training agent sees exactly what the true agent saw; only ``hidden/truth_profile.json`` and
``hidden/truth_layers.parquet`` are replaced, and the manifest's hidden hashes follow. Each case's fake pit is the
true agent's prediction for that case (all its layers, whatever their presence probability), so the two cases of
one pit (72-hour forecast and next pit) get two fake pits. Sealed-test cases are left out of the copy.

Noise (``NoiseModel``), drawn per case from a seed (same seed, same fake pits):

- snow depth scaled by 1 + N(0, ``depth_frac``), all layers with it;
- every boundary between layers moved by N(0, ``boundary_m``), keeping the order and 5 mm per layer;
- each layer's grain form swapped with probability ``grain_swap`` for one an observer could mistake it for
  (``CONFUSABLE``; crusts and ice are kept);
- hand hardness + N(0, ``hardness_sd``) index steps, rounded to thirds, 1 (F) to 6 (I);
- layers thinner than ``thin_m`` (not crusts, ice or surface hoar) joined to the layer above with probability
  ``thin_merge``; then neighbours of one grain form within a third of a hardness step are drawn as one layer.

``wobble`` (optional) varies the true agent's genes per case (relative sd per gene, e.g. snowfall factors 10 %):
storm-to-storm weather error and processes no gene describes, which real pits always carry. Without it the test is
the easiest one: the pits differ from the true agent only by observer noise.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from snowagent.lab.benchmark import package as pkg
from snowagent.lab.benchmark.loader import load_visible_case, read_manifest
from snowagent.lab.competition.scoring import hardness_index
from snowagent.lab.genome import make_genome
from snowagent.lab.ingest.mapping import layer_of_concern
from snowagent.lab.schemas.benchmark import Split
from snowagent.lab.schemas.genome import AgentGenome, GenomeSpec
from snowagent.lab.schemas.profile import SnowLayer, SnowProfile
from snowagent.lab.storage.paths import LabPaths

SOURCE_ID = "synthetic:known_answer"
KEEP_THIN = frozenset({"MF", "IF", "SH"})  # crusts, ice and surface hoar: observers draw them however thin
CONFUSABLE: dict[str, tuple[str, ...]] = {  # major class -> classes an observer may write instead
    "PP": ("DF",), "DF": ("PP", "RG"), "RG": ("DF", "FC"), "FC": ("RG", "DH"), "DH": ("FC",),
    "SH": ("FC",),
}  # crusts and ice are never written as something else: observers do not miss a hard layer
CLASS_CODE = {"PP": "PP", "DF": "DF", "RG": "RG", "FC": "FC", "DH": "DH", "MF": "MF", "SH": "SH", "IF": "IF"}
HARD_CODES = {1: "F", 2: "4F", 3: "1F", 4: "P", 5: "K", 6: "I"}


@dataclass(frozen=True)
class NoiseModel:
    depth_frac: float = 0.03
    boundary_m: float = 0.015
    grain_swap: float = 0.15
    hardness_sd: float = 0.4
    thin_m: float = 0.01
    thin_merge: float = 0.5

    @classmethod
    def none(cls) -> NoiseModel:
        return cls(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    def scaled(self, k: float) -> NoiseModel:
        return NoiseModel(self.depth_frac * k, self.boundary_m * k, min(1.0, self.grain_swap * k),
                          self.hardness_sd * k, self.thin_m, min(1.0, self.thin_merge * k))


@dataclass
class _L:
    top: float
    bottom: float
    grain: str
    hard: float | None


def hardness_text(h: float | None) -> str | None:
    if h is None:
        return None
    third = int(round(float(np.clip(h, 1.0, 6.0)) * 3))
    base, rest = divmod(third, 3)
    if rest == 2:
        base, rest = base + 1, -1
    base = int(np.clip(base, 1, 6))
    return HARD_CODES[base] + {0: "", 1: "+", -1: "-"}[rest]


def layers_of_prediction(pred) -> tuple[float, list[_L]]:
    """The true agent's column as a pit: snow depth and every layer (p50 depths, first grain form, hardness)."""
    hs = float(pred.bulk_state.snow_depth_m.p50) if pred.bulk_state else 0.0
    out = []
    for ly in sorted(pred.layers, key=lambda x: x.top_depth_m.p50):
        top, bot = float(ly.top_depth_m.p50), min(float(ly.bottom_depth_m.p50), hs)
        if bot - top <= 1e-4:
            continue
        out.append(_L(top, bot, ly.grain_form[0] if ly.grain_form else "UNKNOWN", hardness_index(ly.hardness)))
    return hs, out


def _merge(layers: list[_L]) -> list[_L]:
    out: list[_L] = []
    for ly in layers:
        p = out[-1] if out else None
        if p and p.grain[:2] == ly.grain[:2] and (p.hard is None or ly.hard is None or abs(p.hard - ly.hard) < 0.34):
            w1, w2 = p.bottom - p.top, ly.bottom - ly.top
            if p.hard is not None and ly.hard is not None:
                p.hard = (p.hard * w1 + ly.hard * w2) / (w1 + w2)
            p.bottom = ly.bottom
            if w2 > w1:
                p.grain = ly.grain
        else:
            out.append(_L(ly.top, ly.bottom, ly.grain, ly.hard))
    return out


def add_noise(hs: float, layers: list[_L], noise: NoiseModel, rng: np.random.Generator) -> tuple[float, list[_L]]:
    """Observer noise on one fake pit (module doc). Layers surface to ground, depths from the surface."""
    if hs <= 0 or not layers:
        return hs, layers
    f = max(0.5, 1.0 + rng.normal(0.0, noise.depth_frac)) if noise.depth_frac else 1.0
    hs2 = hs * f
    ls = [_L(a.top * f, a.bottom * f, a.grain, a.hard) for a in layers]
    ls[0].top = 0.0
    ls[-1].bottom = hs2
    if noise.boundary_m:
        for i in range(1, len(ls)):
            z = ls[i].top + rng.normal(0.0, noise.boundary_m)
            lo = ls[i - 1].top + 0.005
            hi = ls[i].bottom - 0.005
            if lo < hi:
                z = float(np.clip(z, lo, hi))
                ls[i - 1].bottom = ls[i].top = z
    for a in ls:
        major = a.grain[:2]
        if noise.grain_swap and major in CONFUSABLE and rng.random() < noise.grain_swap:
            a.grain = CLASS_CODE[str(rng.choice(CONFUSABLE[major]))]
        if a.hard is not None and noise.hardness_sd:
            a.hard = float(np.clip(round((a.hard + rng.normal(0.0, noise.hardness_sd)) * 3) / 3, 1.0, 6.0))
    if noise.thin_merge:
        kept: list[_L] = []
        for a in ls:
            if (kept and a.bottom - a.top < noise.thin_m and a.grain[:2] not in KEEP_THIN
                    and rng.random() < noise.thin_merge):
                kept[-1].bottom = a.bottom
            else:
                kept.append(a)
        ls = kept
    return hs2, _merge(ls)


def fake_profile(real: SnowProfile, hs: float, layers: list[_L], note: str) -> SnowProfile:
    """The real pit's header (site, time, ids) with the fake snow depth and layers."""
    out = []
    for i, a in enumerate(layers):
        cls, concern, basis = layer_of_concern(a.grain)
        out.append(SnowLayer(layer_id=f"{real.profile_id}-k{i + 1:02d}", profile_id=real.profile_id,
                             top_depth_m=round(a.top, 4), bottom_depth_m=round(max(a.bottom, a.top + 0.001), 4),
                             grain_primary=a.grain, hardness=hardness_text(a.hard),
                             hardness_index=None if a.hard is None else round(float(np.clip(a.hard, 1, 6)), 3),
                             critical_class=cls, is_layer_of_concern=concern, concern_basis=basis))
    return real.model_copy(update={
        "source_id": SOURCE_ID, "snow_depth_m": round(hs, 4) if hs > 0 else None, "profile_depth_m": None,
        "temperatures": [], "layers": out, "raw": {}, "flags": [], "validation_warnings": [],
        "notes": note, "observer_id": None})


def case_rng(seed: int, case_id: str) -> np.random.Generator:
    return np.random.default_rng(int(hashlib.sha256(f"known-answer:{seed}:{case_id}".encode()).hexdigest()[:12], 16))


def wobbled(genome: AgentGenome, wobble: dict[str, float], rng: np.random.Generator, spec: GenomeSpec | None
            ) -> AgentGenome:
    if not wobble:
        return genome
    allowed = (spec or genome_spec()).family_genes(genome.family)
    genes = dict(genome.genes)
    for g, sd in wobble.items():
        lo, hi = allowed[g][1].min, allowed[g][1].max
        genes[g] = float(np.clip(genes[g] * math.exp(rng.normal(0.0, sd)), lo, hi))
    return make_genome(genome.family, genes, spec, label=genome.label, origin="file")


def genome_spec() -> GenomeSpec:
    from snowagent.lab.schemas.genome import default_spec

    return default_spec()


@dataclass
class BuildReport:
    cases: int = 0
    replaced: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    layers_true: int = 0
    layers_fake: int = 0
    by_season: dict[str, int] = field(default_factory=dict)


def _layers_table(profile: SnowProfile, like: pd.DataFrame | None) -> pd.DataFrame:
    rows = [ly.model_dump(mode="json") for ly in profile.layers]
    for r in rows:
        r["concern_basis"] = json.dumps(r["concern_basis"])
        r["uncertain_fields"] = json.dumps(r["uncertain_fields"])
        r["raw"] = "{}"
    df = pd.DataFrame(rows)
    if like is not None and len(like.columns):
        for c in like.columns:
            if c not in df.columns:
                df[c] = None
        df = df[[c for c in like.columns] + [c for c in df.columns if c not in like.columns]]
    return df


def fake_case(case_dir: Path, truth_genome: AgentGenome, noise: NoiseModel, seed: int,
              wobble: dict[str, float] | None = None, spec: GenomeSpec | None = None) -> dict:
    """Replace one copied case's withheld pit by the true agent's fake pit; returns what was done."""
    from snowagent.lab.agents import make_agent
    from snowagent.lab.competition.runner import case_seed

    m = read_manifest(case_dir)
    hid = case_dir / pkg.HIDDEN
    real = SnowProfile.model_validate_json((hid / pkg.TRUTH_PROFILE).read_text())
    rng = case_rng(seed, m.case_id)
    g = wobbled(truth_genome, wobble or {}, rng, spec)
    case = load_visible_case(case_dir)
    pred = make_agent(g).predict(case, case_seed(seed, case.case_key, g.genome_hash))
    if pred.status != "ok" or pred.bulk_state is None:
        return {"status": "dropped", "reason": "true agent: " + (pred.insufficient_data_reason or "no output")[:80]}
    hs, layers = layers_of_prediction(pred)
    if hs <= 0 or not layers:
        return {"status": "dropped", "reason": "true agent: no snow"}
    hs2, noisy = add_noise(hs, [_L(a.top, a.bottom, a.grain, a.hard) for a in layers], noise, rng)
    fake = fake_profile(real, hs2, noisy, f"known-answer fake pit from {truth_genome.label or truth_genome.agent_id}")
    (hid / pkg.TRUTH_PROFILE).write_text(fake.model_dump_json(indent=1))
    tl = hid / pkg.TRUTH_LAYERS
    like = pd.read_parquet(tl) if tl.is_file() else None
    _layers_table(fake, like).to_parquet(tl, index=False)
    hashes = dict(m.hidden_hashes)
    hashes.update(pkg.hash_dir(hid, tuple(n for n in pkg.HIDDEN_FILES if (hid / n).is_file())))
    raw = json.loads((case_dir / pkg.MANIFEST).read_text())
    raw["hidden_hashes"] = hashes
    (case_dir / pkg.MANIFEST).write_text(json.dumps(raw, indent=1))
    return {"status": "ok", "season": m.season, "layers_true": len(layers), "layers_fake": len(noisy)}


def _fake_task(t: dict) -> tuple[str, dict]:
    spec = genome_spec()
    g = AgentGenome.model_validate(t["genome"], context={"spec": spec})
    try:
        r = fake_case(Path(t["case_dir"]), g, NoiseModel(**t["noise"]), t["seed"], t["wobble"], spec)
    except Exception as exc:  # a case the true agent cannot draw is left out, with the reason
        r = {"status": "dropped", "reason": f"{type(exc).__name__}: {exc}"[:80]}
    return t["case_dir"], r


def build(src: LabPaths, dst: LabPaths, truth_genome: AgentGenome, noise: NoiseModel, seed: int = 0,
          wobble: dict[str, float] | None = None, workers: int = 1, log=print) -> BuildReport:
    """Copy the lab's data root (no outputs) to ``dst`` and give every case a fake withheld pit."""
    if dst.root.exists() and any(dst.root.iterdir()):
        raise ValueError(f"{dst.root} exists and is not empty; choose a new data root")
    dst.root.mkdir(parents=True, exist_ok=True)
    for item in src.root.iterdir():
        if item.name == "outputs":
            continue
        (shutil.copytree if item.is_dir() else shutil.copy2)(item, dst.root / item.name)
    (dst.root / "outputs").mkdir(exist_ok=True)
    cases = sorted(p.parent for p in dst.benchmark.rglob(pkg.MANIFEST))
    rep = BuildReport(cases=len(cases))
    tasks = []
    for d in cases:
        m = read_manifest(d)
        if m.split == Split.sealed_test:
            shutil.rmtree(d)
            rep.dropped["sealed test (left out)"] = rep.dropped.get("sealed test (left out)", 0) + 1
            continue
        tasks.append({"case_dir": str(d), "genome": truth_genome.model_dump(mode="json"), "noise": asdict(noise),
                      "seed": seed, "wobble": wobble or {}})
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(workers) as ex:
            results = list(ex.map(_fake_task, tasks, chunksize=4))
    else:
        results = [_fake_task(t) for t in tasks]
    for i, (d, r) in enumerate(results, 1):
        if r["status"] == "ok":
            rep.replaced += 1
            rep.layers_true += r["layers_true"]
            rep.layers_fake += r["layers_fake"]
            rep.by_season[r["season"]] = rep.by_season.get(r["season"], 0) + 1
        else:
            shutil.rmtree(d)
            rep.dropped[r["reason"]] = rep.dropped.get(r["reason"], 0) + 1
        if i % 100 == 0:
            log(f"  {i}/{len(results)} cases")
    meta = {"kind": "known_answer", "source": str(src.root), "truth_genome": truth_genome.model_dump(mode="json"),
            "truth_genome_hash": truth_genome.genome_hash, "noise": asdict(noise), "seed": seed,
            "wobble": wobble or {}, "report": asdict(rep)}
    (dst.root / "known_answer.json").write_text(json.dumps(meta, indent=1) + "\n")
    return rep


# --------------------------------------------------------------------------------------------- reading a run


def gene_recovery(start: AgentGenome, truth: AgentGenome, found: AgentGenome, spec: GenomeSpec | None = None,
                  evolvable: set[str] | None = None) -> list[dict]:
    """Per gene: start, true and found values, on the gene's own range (0 = its lower bound, 1 = its upper), and
    how much of the way from start to truth the run went (``closed``: 1 = all the way, 0 = not at all, negative =
    away; None when the truth equals the start). Choice genes: whether the run found the true choice."""
    allowed = (spec or genome_spec()).family_genes(truth.family)
    rows = []
    for gname, (_b, gs) in allowed.items():
        if gs.fixed or (evolvable is not None and gname not in evolvable):
            continue
        s, t, f = start.genes[gname], truth.genes[gname], found.genes[gname]
        if isinstance(t, str):
            rows.append({"gene": gname, "kind": "choice", "start": s, "truth": t, "found": f,
                         "moved_by_truth": s != t, "right": f == t})
            continue
        span = (gs.max - gs.min) or 1.0
        st, tt, ft = (s - gs.min) / span, (t - gs.min) / span, (f - gs.min) / span
        need = tt - st
        rows.append({"gene": gname, "kind": "number", "start": s, "truth": t, "found": f,
                     "start_pos": round(st, 3), "truth_pos": round(tt, 3), "found_pos": round(ft, 3),
                     "moved_by_truth": abs(need) >= 0.05, "error_pos": round(abs(ft - tt), 3),
                     "start_error_pos": round(abs(need), 3),
                     "closed": round((ft - st) / need, 2) if abs(need) >= 0.05 else None})
    return rows


__all__ = ["NoiseModel", "BuildReport", "add_noise", "build", "fake_case", "fake_profile", "gene_recovery",
           "hardness_text", "layers_of_prediction"]
