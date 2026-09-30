"""Helper entry point for transcription work: prepare page images and validate outputs.

    python -m snowagent.obs.transcribe_cli prepare --work <dir> [--out observations/transcriptions]
    python -m snowagent.obs.transcribe_cli validate <file.json> [...]
    python -m snowagent.obs.transcribe_cli report [--out observations/transcriptions]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from snowagent.engine.snowpack import REPO_ROOT
from snowagent.obs.inventory import build_inventory
from snowagent.obs.transcription import (
    load_all,
    render_record,
    transcription_path,
    validate_transcription,
)

DEFAULT_OUT = REPO_ROOT / "observations" / "transcriptions"


def prepare(work: Path, out: Path, profiles: Path) -> dict:
    """Render every not-yet-transcribed, non-duplicate profile file; write a task manifest."""
    headers, _ = build_inventory(profiles)
    done = {d.get("source_sha256") for _p, d in load_all(out)} if Path(out).exists() else set()
    tasks = []
    for h in headers:
        if h.duplicate_of is not None or h.sha256 in done:  # never re-queue an already transcribed file
            continue
        dest = transcription_path(out, h.record_id, h.source_file)
        if dest.exists() and json.loads(dest.read_text()).get("source_sha256") == h.sha256:
            continue
        src = REPO_ROOT / h.source_file if not Path(h.source_file).is_absolute() else Path(h.source_file)
        try:
            images = render_record(src, work / "img", h.record_id)
            render_error = None
        except Exception as exc:  # noqa: BLE001 - unreadable/corrupt file becomes an explicit record
            images, render_error = [], f"{type(exc).__name__}: {exc}"
        tasks.append({"render_error": render_error,"record_id": h.record_id, "source_file": h.source_file, "source_sha256": h.sha256,
                      "site_key": h.site_key, "category": h.category.value, "images": [str(p) for p in images],
                      "output": str(dest)})
    (work / "tasks.json").write_text(json.dumps(tasks, indent=1))
    return {"pending": len(tasks), "manifest": str(work / "tasks.json")}


def validate_files(paths: list[Path]) -> int:
    bad = 0
    for p in paths:
        _, errors, flags = validate_transcription(json.loads(Path(p).read_text()))
        status = "INVALID" if errors else "ok"
        bad += bool(errors)
        print(json.dumps({"file": str(p), "status": status, "errors": errors, "flags": flags}))
    return bad


def report(out: Path) -> dict:
    stats: Counter = Counter()
    conf: Counter = Counter()
    fmts: Counter = Counter()
    n_layers = 0
    for _p, d in load_all(out):
        t, errors, flags = validate_transcription(d)
        stats["invalid" if errors else "valid"] += 1
        if t is not None:
            conf[t.confidence] += 1
            fmts[t.source_format] += 1
            n_layers += len(t.layers)
            stats["readable_profiles"] += int(t.readable and t.is_snow_profile)
            stats["with_flags"] += int(any(f != "unreviewed_transcription" for f in flags))
    return {"records": dict(stats), "confidence": dict(conf), "formats": dict(fmts), "layers": n_layers}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("prepare")
    a.add_argument("--work", type=Path, required=True)
    a.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a.add_argument("--profiles", type=Path, default=REPO_ROOT / "profiles")
    v = sub.add_parser("validate")
    v.add_argument("files", nargs="+", type=Path)
    r = sub.add_parser("report")
    r.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    if args.cmd == "prepare":
        print(json.dumps(prepare(args.work, args.out, args.profiles)))
        return 0
    if args.cmd == "validate":
        return 1 if validate_files(args.files) else 0
    print(json.dumps(report(args.out), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
