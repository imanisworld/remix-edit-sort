#!/usr/bin/env python3
"""Build a move+Grouping-tag plan for resolving _NEEDS CHECK tracks into
CLEAN/DIRTY, scoped to ONE top-level folder at a time (matching this repo's
lock/backup-manifest granularity).

Consumes check_version_evidence.py's version-evidence.csv plus an OPTIONAL
--reviewed-decisions csv (columns: full_path, decision) for tracks a human
has now confirmed Clean or Dirty after reviewing LIKELY_DIRTY/NEEDS_LISTEN
findings from the transcript-evidence pipeline.

A track is only eligible if EITHER:
  - check_version_evidence.py already resolved it via an explicit filename
    label (clean_dirty_status is CONFIRMED_CLEAN or CONFIRMED_DIRTY -- no
    further human review needed, the label is already in the filename), OR
  - its full_path appears in --reviewed-decisions with a CLEAN/DIRTY decision.

LIKELY_DIRTY and NEEDS_LISTEN rows are NEVER auto-included -- per this
project's safety rule, absence of a detected term is not proof of Clean, and
a detected term still requires human review before becoming an actionable
DIRTY label. This script does not decide anything; it only turns already-made
decisions into a move+tag plan for apply_clean_dirty_move_plan.py.

Destination is the source path with its "_NEEDS CHECK" segment replaced by
"CLEAN" or "DIRTY" -- everything else in the path (top folder, any genre
subfolder beneath the version folder) is preserved. The Grouping tag is
APPENDED (never overwritten), reusing apply_clean_dirty_grouping.py's
combined_grouping()/dedupe convention.
"""

from __future__ import annotations

# Read-only: inspects the library and writes a plan.json only. No files or
# tags are touched by this script.
SAFETY_CLASSIFICATION = "preview-only"

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

import mutation_engine

from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.mp4 import MP4

SUPPORTED_EXTS = {".mp3", ".flac", ".m4a"}
DECIDED_STATUSES = {"CONFIRMED_CLEAN": "CLEAN", "CONFIRMED_DIRTY": "DIRTY"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _split_grouping_parts(raw_values: list[str]) -> list[str]:
    parts = []
    for raw in raw_values:
        parts.extend(p.strip() for p in raw.split(";") if p.strip())
    return parts


def read_grouping(path: Path) -> list[str]:
    suffix = path.suffix.casefold()
    if suffix == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            return []
        raw = []
        for frame in tags.getall("TIT1"):
            text = getattr(frame, "text", [])
            raw.extend(str(value).strip() for value in text if str(value).strip())
        return _split_grouping_parts(raw)
    if suffix == ".m4a":
        raw = [str(v).strip() for v in MP4(path).get("\xa9grp", []) if str(v).strip()]
        return _split_grouping_parts(raw)
    if suffix == ".flac":
        raw = [str(v).strip() for v in FLAC(path).get("grouping", []) if str(v).strip()]
        return _split_grouping_parts(raw)
    return []


def combined_grouping(existing: list[str], proposed: str) -> str:
    values, seen = [], set()
    for value in [*existing, proposed]:
        normalized = value.strip().casefold()
        if normalized and normalized not in seen:
            values.append(value.strip())
            seen.add(normalized)
    return "; ".join(values)


def swap_needs_check(path: Path, decision: str) -> Path | None:
    parts = list(path.parts)
    for i, part in enumerate(parts):
        if part.casefold() == "_needs check":
            parts[i] = decision  # "CLEAN" or "DIRTY"
            return Path(*parts)
    return None


def load_reviewed_decisions(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    decisions = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            full_path = (row.get("full_path") or "").strip()
            decision = (row.get("decision") or "").strip().upper()
            if not full_path or decision not in {"CLEAN", "DIRTY"}:
                continue
            decisions[str(Path(full_path).expanduser())] = decision
    return decisions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", required=True, type=Path, help="One top-level library folder (e.g. 'Juke')")
    parser.add_argument("--version-evidence", required=True, type=Path, help="version-evidence.csv from check_version_evidence.py")
    parser.add_argument("--reviewed-decisions", type=Path, help="Optional CSV (full_path, decision) for human-confirmed LIKELY_DIRTY/NEEDS_LISTEN tracks")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    reviewed = load_reviewed_decisions(args.reviewed_decisions)

    with args.version_evidence.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    items = []
    skipped_undecided = 0
    skipped_out_of_scope = 0
    errors = []
    status_counts: Counter[str] = Counter()

    for row in rows:
        full_path = (row.get("full_path") or "").strip()
        if not full_path:
            continue
        source = Path(full_path)
        try:
            source.resolve().relative_to(root)
        except ValueError:
            skipped_out_of_scope += 1
            continue

        status = (row.get("clean_dirty_status") or "").strip()
        decision = DECIDED_STATUSES.get(status)
        if not decision:
            key = str(source.expanduser())
            decision = reviewed.get(key)
        if not decision:
            skipped_undecided += 1
            continue

        if source.suffix.casefold() not in SUPPORTED_EXTS:
            errors.append(f"unsupported format, skipped: {source}")
            continue
        if not source.is_file():
            errors.append(f"missing file: {source}")
            continue

        destination = swap_needs_check(source, decision)
        if destination is None:
            errors.append(f"no _NEEDS CHECK segment in path, cannot place: {source}")
            continue
        if mutation_engine.destination_conflicts(source, destination):
            errors.append(f"destination already exists: {destination}")
            continue

        existing_grouping = read_grouping(source)
        proposed_grouping = combined_grouping(existing_grouping, decision.capitalize())
        already_has_status = any(v.casefold() == decision.casefold() for v in existing_grouping)

        items.append({
            "source": str(source),
            "destination": str(destination),
            "sha256": sha256_file(source),
            "decision": decision,
            "existing_grouping": existing_grouping,
            "proposed_grouping": proposed_grouping,
            "tag_change": not already_has_status,
            "move": True,
            "evidence_status": status,
            "reason": row.get("reason", ""),
        })
        status_counts[status or "reviewed-decision"] += 1

    plan = {
        "mode": "preview-only",
        "source_mutation": False,
        "rules": {"approval_required": True, "no_moves": False, "no_tag_writes": False},
        "root": str(root),
        "version_evidence": str(args.version_evidence),
        "reviewed_decisions": str(args.reviewed_decisions) if args.reviewed_decisions else None,
        "items": items,
        "counts": {
            "eligible": len(items),
            "clean": sum(1 for i in items if i["decision"] == "CLEAN"),
            "dirty": sum(1 for i in items if i["decision"] == "DIRTY"),
            "tag_changes": sum(1 for i in items if i["tag_change"]),
            "already_tagged_no_change_needed": sum(1 for i in items if not i["tag_change"]),
            "by_evidence_status": dict(status_counts),
            "skipped_undecided_needs_review": skipped_undecided,
            "skipped_out_of_scope": skipped_out_of_scope,
        },
        "errors": errors,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in plan.items() if k != "items"}, indent=2))


if __name__ == "__main__":
    main()
