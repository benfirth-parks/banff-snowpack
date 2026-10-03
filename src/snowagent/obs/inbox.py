"""Drop-in profiles (ADR-037): files added through the site's upload form, a chat, or the repository land in
``profiles/inbox/`` and are filed, unchanged, into the season/site folders that the inventory already reads.

Each upload is either a folder ``profiles/inbox/<id>/`` holding the file(s) and ``submission.json`` (form fields:
site, observed_date, observer, notes, received_utc) or a loose file. Filing rules:

- content-identical to a file already under ``profiles/`` -> recorded as a duplicate, the inbox copy removed;
- unsupported type -> left in place and recorded as rejected (never deleted);
- otherwise moved (bytes unchanged) to ``profiles/<season>/Study Plot profiles/<Site>/`` for a study plot, or
  ``profiles/<season>/Test profiles/`` for anywhere else, named ``<YYYY-MM-DD>_<original name>`` unless the name
  already starts with a date. The season comes from the form date, else the date in the file name, else the
  upload date (receipt flag ``observation_date_unknown_upload_date_used_for_filing``; the observed set then does
  not read that name prefix as an observation date, ADR-052).

``observations/inbox/received.jsonl`` keeps one line per file (sha256, original and filed names, form fields,
status); observer names are not collected by the form (they stay inside the profile files, as before). PDFs and photos then go through the existing transcription queue (``transcribe_cli prepare``); CAAML v5
(recognised by content, as .xml or .caaml) and SnowPro files are read exactly by ``obs profiles``; other XML
is listed there as not read. The site's status panel lists these receipts.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from snowagent.obs.caaml import xml_kind
from snowagent.obs.filenames import parse_filename_date

INBOX = Path("profiles/inbox")
RECEIPTS = Path("observations/inbox/received.jsonl")
SUPPORTED = {".pdf": "pdf", ".png": "image", ".jpg": "image", ".jpeg": "image", ".xml": "xml", ".caaml": "xml"}
SITE_FOLDERS = {"goats_eye": "Goat's Eye", "simpson": "Simpson", "bow_summit": "Bow Summit"}
CAAML_V6 = "http://caaml.org/Schemas/SnowProfileIACS/v6"
UPLOAD_DATE_FLAG = "observation_date_unknown_upload_date_used_for_filing"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _season(d: pd.Timestamp) -> str:
    y = d.year if d.month >= 8 else d.year - 1  # profiles folders run Aug-Jul (inventory convention)
    return f"{y}-{y + 1}"


def _safe(name: str) -> str:
    name = re.sub(r"[/\\\x00-\x1f]", "_", name).strip().strip(".")
    return name[:120] or "upload"


def known_hashes(profiles: Path, inbox: Path) -> dict[str, str]:
    out = {}
    for f in Path(profiles).rglob("*"):
        if f.is_file() and inbox not in f.parents and f.suffix.lower() in SUPPORTED | {".pro": "pro"}:
            out.setdefault(_sha(f), str(f))
    return out


def _items(inbox: Path) -> list[tuple[Path, dict]]:
    items = []
    for p in sorted(Path(inbox).iterdir()) if Path(inbox).exists() else []:
        if p.is_dir():
            meta_f = p / "submission.json"
            meta = json.loads(meta_f.read_text()) if meta_f.exists() else {}
            items += [(f, meta) for f in sorted(p.iterdir()) if f.is_file() and f.name != "submission.json"]
        elif p.is_file() and p.name not in (".gitkeep", "README.md"):
            items.append((p, {}))
    return items


def process_inbox(inbox: Path = INBOX, profiles: Path = Path("profiles"), receipts: Path = RECEIPTS,
                  now: datetime | None = None) -> list[dict]:
    """File every inbox item (see module doc). Returns the receipts written in this call."""
    inbox, profiles = Path(inbox), Path(profiles)
    now = now or datetime.now(UTC)
    known = known_hashes(profiles, inbox)
    done = []
    for f, meta in _items(inbox):
        sha = _sha(f)
        rec = {"received_utc": meta.get("received_utc") or now.isoformat(timespec="seconds"),
               "processed_utc": now.isoformat(timespec="seconds"), "submission_id": meta.get("submission_id"),
               "original_name": f.name, "sha256": sha, "bytes": f.stat().st_size,
               "site": meta.get("site"), "observed_date": meta.get("observed_date"), "notes": meta.get("notes"),
               "flags": []}
        kind = SUPPORTED.get(f.suffix.lower())
        if kind is None:
            rec |= {"status": "rejected", "note": f"unsupported file type {f.suffix or '(none)'}; left in the inbox"}
        elif sha in known:
            rec |= {"status": "duplicate", "filed_as": known[sha], "note": "identical file already archived"}
            f.unlink()
        else:
            if kind == "xml":
                xk = xml_kind(f.read_bytes())  # the observed-set reader classifies with the same function
                rec["format"] = xk
                if xk != "caaml_v5":
                    rec["flags"].append(f"{xk}: kept unchanged; not read automatically (needs a parser or a "
                                        "transcription)")
            date = None
            if meta.get("observed_date"):
                try:
                    date = pd.Timestamp(meta["observed_date"])
                except ValueError:
                    rec["flags"].append("form_date_unreadable")
            if date is None:
                fd, _ = parse_filename_date(f.name, None)
                date = pd.Timestamp(fd) if fd else None
            if date is None:
                date = pd.Timestamp(rec["received_utc"]).tz_convert("Etc/GMT+7").tz_localize(None).normalize()
                rec["flags"].append(UPLOAD_DATE_FLAG)
            site = meta.get("site") if meta.get("site") in SITE_FOLDERS else None
            folder = profiles / _season(date) / ("Study Plot profiles" if site else "Test profiles")
            if site:
                folder = folder / SITE_FOLDERS[site]
            name = _safe(f.name)
            if not re.match(r"^\d{4}-\d{2}-\d{2}", name):
                name = f"{date:%Y-%m-%d}_{name}"
            dest = folder / name
            k = 2
            while dest.exists():
                dest = folder / f"{Path(name).stem}_{k}{Path(name).suffix}"
                k += 1
            folder.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), dest)
            known[sha] = str(dest)
            status = "filed_exact" if rec.get("format") == "caaml_v5" else (
                "filed_needs_transcription" if kind in ("pdf", "image") else "filed_not_read")
            rec |= {"status": status, "filed_as": str(dest), "kind": kind}
        receipts.parent.mkdir(parents=True, exist_ok=True)
        with open(receipts, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        done.append(rec)
    for d in sorted(Path(inbox).iterdir()) if Path(inbox).exists() else []:  # folders emptied by filing
        if d.is_dir() and not [x for x in d.iterdir() if x.name != "submission.json"]:
            keep = Path(receipts).parent / "submissions" / f"{d.name}.json"
            if (d / "submission.json").exists():
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(d / "submission.json"), keep)
            d.rmdir()
    return done


def upload_dated_files(receipts: Path = RECEIPTS) -> dict[str, str]:
    """sha256 -> filed name of every file the inbox filed under its upload date (no form date, no date in the
    original name; receipt flag ``UPLOAD_DATE_FLAG``). The date prefix of that name is the upload date, not an
    observation date (ADR-052). Empty when there are no receipts."""
    if not Path(receipts).exists():
        return {}
    out: dict[str, str] = {}
    for line in Path(receipts).read_text().splitlines():
        r = json.loads(line) if line.strip() else {}
        if r.get("filed_as") and UPLOAD_DATE_FLAG in (r.get("flags") or []):
            out[r["sha256"]] = Path(r["filed_as"]).name
    return out


def receipts_summary(receipts: Path = RECEIPTS, transcriptions: Path = Path("observations/transcriptions"),
                     limit: int = 50) -> list[dict]:
    """Newest receipts for the site, with the transcription state of filed PDFs/photos."""
    from snowagent.obs.transcription import load_all

    if not Path(receipts).exists():
        return []
    done = {d.get("source_sha256") for _p, d in load_all(transcriptions)} if Path(transcriptions).exists() else set()
    out = []
    for x in reversed([json.loads(r) for r in Path(receipts).read_text().splitlines() if r.strip()]):
        status = x["status"]
        if status == "filed_needs_transcription" and x["sha256"] in done:
            status = "transcribed"
        out.append({"received_utc": x.get("received_utc"), "name": x["original_name"], "site": x.get("site"),
                    "status": status.replace("_", " "), "note": x.get("note") or "; ".join(x.get("flags", []))})
        if len(out) >= limit:
            break
    return out
