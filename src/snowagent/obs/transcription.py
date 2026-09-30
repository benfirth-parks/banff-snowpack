"""Transcriptions of rendered snow-profile charts into structured layers.

Transcriptions are made by reading the images (vision model or person) following
docs/transcription/GUIDE.md. They are DERIVED data with transcription error, kept
separate from engine output and from structured field exports:

- every record says who/what transcribed it and whether a person reviewed it;
- symbols are translated with the IACS 2009 / CAA OGRS grain classification;
- unreadable values are null with an entry in ``uncertain_fields``, never guessed;
- ``validate_transcription`` checks schema and physical/structural consistency and
  returns flags; it never edits the content.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEMA_VERSION = "transcription-1"

# IACS 2009 (Fierz et al.) classes and subclasses, as used by CAA OGRS.
GRAIN_FORMS = {
    "PP", "PPco", "PPnd", "PPpl", "PPsd", "PPir", "PPgp", "PPhl", "PPip", "PPrm",
    "MM", "MMrp", "MMci",
    "DF", "DFdc", "DFbk",
    "RG", "RGsr", "RGlr", "RGwp", "RGxf",
    "FC", "FCso", "FCsf", "FCxr",
    "DH", "DHcp", "DHpr", "DHch", "DHla", "DHxr",
    "SH", "SHsu", "SHcv", "SHxr",
    "MF", "MFcl", "MFpc", "MFsl", "MFcr",
    "IF", "IFil", "IFic", "IFbi", "IFrc", "IFsc",
}
HARDNESS = re.compile(r"^(F|4F|1F|P|K|I)[+-]?$")
MOISTURE = {"D", "M", "W", "V", "S"}
FORMATS = ("avanet", "snowpilot", "propagation_labs", "niviz", "other", "unreadable")


class TLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    top_cm: float | None
    bottom_cm: float | None
    grain_form: str | None
    grain_form_2: str | None = None
    grain_symbol_as_seen: str | None = None
    grain_size_mm: list[float] | None = None  # [value] or [min, max]
    grain_size_2_mm: list[float] | None = None  # size of the secondary form, e.g. "1-2(0.5)"
    hardness: str | None = None  # at layer top (or whole layer)
    hardness_bottom: str | None = None  # only if the chart shows a gradient
    moisture: str | None = None
    density_kg_m3: float | None = None
    date_tag: str | None = None  # layer name/date label as written (e.g. "Jan 3")
    comment: str | None = None
    uncertain_fields: list[str] = Field(default_factory=list)


class TTemp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    height_cm: float
    t_c: float


class TTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw: str
    type: str | None = None  # CT, ECT, PST, RB, DT, SB, ...
    result: str | None = None  # e.g. ECTP14, CT13, ECTX
    score: float | None = None
    fracture_character: str | None = None  # SP, SC, RP, PC, BRK, ...
    shear_quality: str | None = None  # Q1, Q2, Q3
    height_cm: float | None = None
    layer_date_tag: str | None = None
    comment: str | None = None


class THeader(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site_name_as_written: str | None = None
    date_local: str | None = None  # YYYY-MM-DD as written on the profile
    time_local: str | None = None  # HH:MM
    lat: float | None = None
    lon: float | None = None
    elevation_m: float | None = None
    aspect: str | None = None
    slope_deg: float | None = None
    hs_cm: float | None = None
    profile_depth_cm: float | None = None  # observed pit depth if not full depth
    air_temp_c: float | None = None
    sky: str | None = None
    precipitation: str | None = None
    wind: str | None = None
    foot_pen_cm: float | None = None
    ski_pen_cm: float | None = None
    notes: str | None = None


class Transcriber(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["vision_model", "human"]
    agent: str
    reviewed: bool = False
    reviewer: str | None = None


class Transcription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["transcription-1"]
    record_id: str
    source_file: str
    source_sha256: str
    source_format: Literal["avanet", "snowpilot", "propagation_labs", "niviz", "other", "unreadable"]
    transcriber: Transcriber
    readable: bool
    unreadable_reason: str | None = None
    is_snow_profile: bool = True
    header: THeader
    height_reference: Literal["height_above_ground", "depth_from_surface"] = "height_above_ground"
    layers: list[TLayer]  # top (surface) to bottom
    temperatures: list[TTemp] = Field(default_factory=list)
    tests: list[TTest] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"]
    transcriber_notes: str | None = None


def validate_transcription(data: dict) -> tuple[Transcription | None, list[str], list[str]]:
    """Return (model, errors, flags). Errors make the record unusable; flags are warnings."""
    try:
        t = Transcription.model_validate(data)
    except ValidationError as exc:
        return None, [f"schema: {e['loc']}: {e['msg']}" for e in exc.errors()], []
    errors: list[str] = []
    flags: list[str] = []
    if not t.readable or not t.is_snow_profile:
        if t.layers:
            errors.append("unreadable/non-profile record must not contain layers")
        return t, errors, flags
    if not t.layers:
        errors.append("readable profile without layers")
    for i, ly in enumerate(t.layers):
        tag = f"layer{i}"
        for code in (ly.grain_form, ly.grain_form_2):
            if code is not None and code not in GRAIN_FORMS:
                errors.append(f"{tag}: grain form {code!r} not in IACS/OGRS vocabulary")
        for h in (ly.hardness, ly.hardness_bottom):
            if h is not None and not HARDNESS.match(h):
                errors.append(f"{tag}: hardness {h!r} not in F/4F/1F/P/K/I[+-]")
        if ly.moisture is not None and ly.moisture not in MOISTURE:
            errors.append(f"{tag}: moisture {ly.moisture!r} not in {sorted(MOISTURE)}")
        if ly.grain_size_mm is not None and (not 1 <= len(ly.grain_size_mm) <= 2 or min(ly.grain_size_mm) < 0
                                             or max(ly.grain_size_mm) > 50):
            errors.append(f"{tag}: grain size {ly.grain_size_mm} implausible")
        if ly.density_kg_m3 is not None and not 20 <= ly.density_kg_m3 <= 917:
            errors.append(f"{tag}: density {ly.density_kg_m3} outside 20..917")
        if ly.top_cm is None or ly.bottom_cm is None:
            flags.append(f"{tag}: boundary missing")
            continue
        if t.height_reference == "height_above_ground" and not ly.top_cm > ly.bottom_cm:
            errors.append(f"{tag}: top {ly.top_cm} not above bottom {ly.bottom_cm}")
        if t.height_reference == "depth_from_surface" and not ly.top_cm < ly.bottom_cm:
            errors.append(f"{tag}: depth top {ly.top_cm} not shallower than bottom {ly.bottom_cm}")
        if min(ly.top_cm, ly.bottom_cm) < 0:
            errors.append(f"{tag}: negative boundary")
        if ly.grain_form is None:
            flags.append(f"{tag}: grain form missing")
    bounded = [ly for ly in t.layers if ly.top_cm is not None and ly.bottom_cm is not None]
    for a, b in zip(bounded, bounded[1:], strict=False):
        if abs(a.bottom_cm - b.top_cm) > 0.5:
            kind = "gap" if (a.bottom_cm > b.top_cm) == (t.height_reference == "height_above_ground") else "overlap"
            flags.append(f"{kind} between {a.bottom_cm} and {b.top_cm} cm")
    hs = t.header.hs_cm
    if bounded and t.height_reference == "height_above_ground":
        if hs is not None and abs(bounded[0].top_cm - hs) > 2:
            flags.append(f"top layer {bounded[0].top_cm} cm differs from HS {hs} cm")
        if bounded[-1].bottom_cm > 2 and t.header.profile_depth_cm is None:
            flags.append(f"profile ends {bounded[-1].bottom_cm} cm above ground (partial pit?)")
    for tp in t.temperatures:
        if not -40 <= tp.t_c <= 0.5:
            errors.append(f"temperature {tp.t_c} C outside -40..0.5")
    if t.header.date_local and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", t.header.date_local):
        errors.append(f"date_local {t.header.date_local!r} not YYYY-MM-DD")
    if not t.transcriber.reviewed:
        flags.append("unreviewed_transcription")
    return t, errors, flags


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def render_record(source: Path, out_dir: Path, stem: str, max_px: int = 1800, dpi: int = 150) -> list[Path]:
    """Render a profile file to PNG page images for reading (PDF: every page up to 3)."""
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    head = Path(source).read_bytes()[:512].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")):  # e.g. a saved niViz page named .png
        return [_render_html(source, out_dir / f"{stem}__p0.png", max_px)]
    if source.suffix.lower() == ".pdf":
        import pymupdf

        doc = pymupdf.open(str(source))
        for i, page in enumerate(doc):
            if i >= 3:
                break
            p = out_dir / f"{stem}__p{i}.png"
            page.get_pixmap(dpi=dpi).save(str(p))
            paths.append(p)
    else:
        im = Image.open(source)
        try:
            from PIL import ImageOps

            im = ImageOps.exif_transpose(im)
        except Exception:  # noqa: BLE001 - orientation metadata is optional
            pass
        p = out_dir / f"{stem}__p0.png"
        paths.append(p)
        im = _flatten_on_white(im)
        im.thumbnail((max_px, max_px))
        im.save(p)
    for p in paths:  # bound size for readers
        im = Image.open(p)
        if max(im.size) > max_px:
            im.thumbnail((max_px, max_px))
            im.save(p)
    return paths


def _flatten_on_white(im):
    """Transparent charts (e.g. niViz PNG exports) have black text on nothing; plain convert("RGB") puts them
    on black and the text disappears. Composite onto white instead."""
    from PIL import Image

    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, rgba).convert("RGB")
    return im.convert("RGB")


CHROMIUM_CANDIDATES = ("/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell",)


def _render_html(source: Path, dest: Path, max_px: int) -> Path:
    import os
    import shutil
    import subprocess
    import tempfile

    exe = os.environ.get("CHROMIUM_BIN") or next((c for c in CHROMIUM_CANDIDATES if Path(c).exists()), None) \
        or shutil.which("chromium")
    if exe is None:
        raise RuntimeError("HTML profile needs a headless Chromium to render (set CHROMIUM_BIN)")
    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "page.html"
        shutil.copyfile(source, page)
        subprocess.run([exe, "--headless", "--no-sandbox", "--disable-gpu", f"--screenshot={dest}",
                        "--window-size=1400,2600", page.as_uri()], capture_output=True, timeout=120, check=True)
    from PIL import Image

    im = Image.open(dest)
    im.thumbnail((max_px, max_px * 2))
    im.save(dest)
    return dest


def transcription_path(root: Path, record_id: str, source_file: str) -> Path:
    season = next((p for p in Path(source_file).parts if re.fullmatch(r"\d{4}-\d{4}", p)), "unknown_season")
    return Path(root) / season / f"{record_id}.json"


def load_all(root: Path) -> list[tuple[Path, dict]]:
    return [(p, json.loads(p.read_text())) for p in sorted(Path(root).rglob("*.json"))]
