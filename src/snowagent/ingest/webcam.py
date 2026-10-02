"""Sunshine Village webcams (ADR-041): one capture per daily update of each camera's current and last-daylight
image. Kept only when the image is new (by Last-Modified) and fresh (younger than ``max_age_h``); stale feeds
(off-season placeholder) and repeats are recorded in the manifest but not stored. Images are resized to at most
1280 px (JPEG q80) to keep the repository small; the original's SHA-256, size and Last-Modified are recorded.
Webcams are context and checks (new snow on the board vs the gauge), never engine input."""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from pathlib import Path

import pandas as pd
import yaml

ARCHIVE = Path("archive/webcams")
MAX_PX = 1280


def _http_get(url: str) -> tuple[bytes, dict[str, str]]:
    import requests

    r = requests.get(url, timeout=30, headers={"User-Agent": "banff-snowpack research (decision support)"})
    r.raise_for_status()
    return r.content, {k.lower(): v for k, v in r.headers.items()}


def _season(t: pd.Timestamp) -> str:
    y = t.year if t.month >= 8 else t.year - 1
    return f"{y}-{y + 1}"


def _manifest(archive: Path) -> list[dict]:
    f = archive / "manifest.jsonl"
    return [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()] if f.exists() else []


def capture(now: pd.Timestamp | None = None, archive: Path = ARCHIVE, cfg: dict | None = None,
            get: Callable[[str], tuple[bytes, dict[str, str]]] = _http_get) -> list[dict]:
    from PIL import Image

    now = now or pd.Timestamp.now(tz="UTC")
    cfg = cfg or yaml.safe_load(Path("config/external_sources.yaml").read_text())["webcams"]
    seen = {(m["cam"], m["kind"], m.get("last_modified")) for m in _manifest(archive) if m.get("status") == "stored"}
    out = []
    for cam, c in cfg["cams"].items():
        for kind in ("current", "daylight"):
            rec = {"cam": cam, "kind": kind, "url": c[kind], "captured_utc": now.isoformat(timespec="seconds")}
            try:
                data, hdr = get(c[kind])
            except Exception as exc:  # noqa: BLE001 - recorded; retried next update
                out.append(rec | {"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
                continue
            lm = hdr.get("last-modified")
            t_img = pd.Timestamp(parsedate_to_datetime(lm)).tz_convert("UTC") if lm else None
            rec |= {"last_modified": t_img.isoformat() if t_img is not None else None,
                    "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            if t_img is None or (now - t_img) > pd.Timedelta(hours=float(cfg.get("max_age_h", 48))):
                out.append(rec | {"status": "stale"})
                continue
            if (cam, kind, rec["last_modified"]) in seen:
                out.append(rec | {"status": "unchanged"})
                continue
            im = Image.open(io.BytesIO(data))
            rec["original_px"] = list(im.size)
            im = im.convert("RGB")
            im.thumbnail((MAX_PX, MAX_PX))
            dest = archive / cam / _season(t_img) / f"{t_img:%Y%m%dT%H%MZ}_{kind}.jpg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            im.save(dest, quality=80)
            out.append(rec | {"status": "stored", "path": str(dest), "stored_px": list(im.size)})
    archive.mkdir(parents=True, exist_ok=True)
    with (archive / "manifest.jsonl").open("a") as f:
        for r in out:
            f.write(json.dumps(r) + "\n")
    return out
