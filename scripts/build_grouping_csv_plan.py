#!/usr/bin/env python3
"""Build a Grouping (TIT1) plan from a human-reviewed, per-track CSV.

build_grouping_plan.py can only derive one label per folder from move
history. Some Grouping values are decided track by track instead, such as the
DJ energy crates (`Energy: Warmup`, `Energy: Bashment`, ...), which Serato
Smart Crates filter on. This builder turns an approved per-track decision CSV
into a plan that the existing scripts/apply_grouping_plan.py applies through
the mutation engine (lock, backup manifest, write-ahead journal, verification).

Input CSV columns (header required):
  full_path        absolute path of the track as it exists NOW
  grouping_value   the single value to append, e.g. "Energy: Peak"

Rules, matching the rest of this project:
  - Preview-only. Reads tags and writes plan.json + a short preview; never
    touches library media.
  - Existing Grouping is preserved and appended to, deduplicated
    case-insensitively on the "; " delimiter. Nothing is overwritten.
  - Only mp3 (the Grouping writer is mp3-only). ID3v2.2 is skipped, not converted.
  - Values may not contain ";" (it is the delimiter).
  - --require-prefix (e.g. "Energy: ") rejects any value without that prefix.
  - If a track already carries a DIFFERENT value with the same prefix
    (e.g. it is already "Energy: Warmup" and the CSV says "Energy: Peak"),
    it is skipped and reported as a conflict, never silently stacked.
  - Blank grouping_value rows are skipped (undecided), never guessed.
  - Every path must sit under --root, and each path may appear only once.
"""

from __future__ import annotations

# Preview-only: builds a plan for human review. Never mutates library media.
SAFETY_CLASSIFICATION = "preview-only"

import argparse
import collections
import csv
import json
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError


def read_grouping(path: Path) -> tuple[list[str], int]:
    """Same shape apply_grouping_plan.load_and_validate compares against."""
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return [], 4
    values = []
    for frame in tags.getall("TIT1"):
        text = getattr(frame, "text", [])
        values.extend(str(value).strip() for value in text if str(value).strip())
    return values, tags.version[1]


def split_parts(raw_values: list[str]) -> list[str]:
    parts = []
    for raw in raw_values:
        parts.extend(p.strip() for p in raw.split(";") if p.strip())
    return parts


def combined_grouping(existing_raw: list[str], proposed: str) -> str:
    values, seen = [], set()
    for value in [*split_parts(existing_raw), proposed]:
        normalized = value.strip().casefold()
        if normalized and normalized not in seen:
            values.append(value.strip())
            seen.add(normalized)
    return "; ".join(values)


def build(root: Path, decisions: Path, require_prefix: str | None) -> dict:
    root = root.resolve()
    items = []
    skipped: collections.Counter = collections.Counter()
    skipped_detail = []
    errors = []
    seen_paths: set[str] = set()
    value_counts: collections.Counter = collections.Counter()

    with decisions.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = {"full_path", "grouping_value"} - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"decisions CSV is missing column(s): {', '.join(sorted(missing))}")
        rows = list(reader)

    def skip(reason: str, path: str) -> None:
        skipped[reason] += 1
        skipped_detail.append({"path": path, "reason": reason})

    for row in rows:
        raw_path = (row.get("full_path") or "").strip()
        value = (row.get("grouping_value") or "").strip()
        if not raw_path:
            continue
        if not value:
            skip("no grouping_value (undecided)", raw_path)
            continue
        if ";" in value:
            errors.append(f"value contains ';' delimiter: {raw_path}: {value!r}")
            continue
        if require_prefix and not value.startswith(require_prefix):
            errors.append(f"value lacks required prefix {require_prefix!r}: {raw_path}: {value!r}")
            continue

        path = Path(raw_path).expanduser()
        resolved = path.resolve()
        if root not in resolved.parents:
            errors.append(f"outside --root: {raw_path}")
            continue
        key = str(resolved).casefold()
        if key in seen_paths:
            errors.append(f"path listed more than once: {raw_path}")
            continue
        seen_paths.add(key)

        if not path.is_file():
            errors.append(f"missing file: {raw_path}")
            continue
        if path.suffix.casefold() != ".mp3":
            skip("non-mp3 (Grouping writer only supports mp3)", raw_path)
            continue

        current, id3_version = read_grouping(path)
        if id3_version == 2:
            skip("ID3v2.2 preserved", raw_path)
            continue
        parts = split_parts(current)
        if any(p.casefold() == value.casefold() for p in parts):
            skip("already has this exact Grouping value", raw_path)
            continue
        if require_prefix:
            conflicting = [p for p in parts if p.startswith(require_prefix)]
            if conflicting:
                skip(f"already has a different {require_prefix.strip()} value: {conflicting[0]}", raw_path)
                continue

        items.append({
            "path": str(path),
            "current_grouping": current,
            "proposed_grouping": value,
            "combined_grouping": combined_grouping(current, value),
            "id3_version": id3_version,
            "evidence": "human-reviewed per-track decision CSV",
        })
        value_counts[value] += 1

    return {
        "mode": "preview-only",
        "source_mutation": False,
        "rules": {"approval_required": True, "no_moves": True, "append_only": True},
        "root": str(root),
        "decisions_csv": str(decisions),
        "require_prefix": require_prefix,
        "counts": {
            "rows": len(rows),
            "items": len(items),
            "by_value": dict(sorted(value_counts.items())),
            "skipped": dict(skipped),
        },
        "validation": {"errors": errors},
        "skipped": skipped_detail,
        "items": items,
    }


def write_preview(plan: dict, out: Path) -> None:
    lines = [
        "# Grouping plan preview (per-track CSV)",
        "",
        f"- Root: `{plan['root']}`",
        f"- Tracks to tag: {plan['counts']['items']}",
        f"- Errors: {len(plan['validation']['errors'])}",
        "",
        "## By value",
        "",
    ]
    lines += [f"- {v}: {n}" for v, n in plan["counts"]["by_value"].items()] or ["- (none)"]
    lines += ["", "## Skipped", ""]
    lines += [f"- {r}: {n}" for r, n in plan["counts"]["skipped"].items()] or ["- (none)"]
    if plan["validation"]["errors"]:
        lines += ["", "## Errors (plan must not be applied until these are fixed)", ""]
        lines += [f"- {e}" for e in plan["validation"]["errors"]]
    lines += ["", "## Changes", "", "| File | Grouping now | Grouping after |", "|---|---|---|"]
    for item in plan["items"]:
        now = "; ".join(item["current_grouping"])
        lines.append(f"| {Path(item['path']).name} | {now} | {item['combined_grouping']} |")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", required=True, type=Path, help="One top-level library folder, e.g. .../Music/Reggae")
    parser.add_argument("--decisions", required=True, type=Path, help="CSV with full_path,grouping_value")
    parser.add_argument("--require-prefix", help='Reject values without this prefix, e.g. "Energy: "')
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    plan = build(args.root, args.decisions, args.require_prefix)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    plan_path = args.out_dir / "grouping-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_preview(plan, args.out_dir / "GROUPING-PREVIEW.md")
    print(json.dumps({"plan": str(plan_path), **plan["counts"], "errors": plan["validation"]["errors"]}, indent=2))
    if plan["validation"]["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
