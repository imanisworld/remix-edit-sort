#!/usr/bin/env python3
"""Survey Grouping values, then build a PREVIEW-ONLY plan that removes an
owner-approved list of exact junk values (e.g. "November", a record-pool
watermark), for apply_grouping_plan.py to execute.

Grouping is append-only everywhere else in this project. This is the one,
narrow exception the owner approved on 2026-10-15: remove values the owner
has seen and listed by hand, nothing else.

Two steps:

  survey  Read-only. Lists every distinct Grouping value under --root with a
          count and one example file (grouping-values.csv + GROUPING-VALUES.md),
          so the owner can see exactly what is there before picking junk.

  plan    Read-only. Takes --remove, a text file with one exact value per line
          (blank lines and lines starting with "#" are ignored), and writes
          grouping-plan.json + GROUPING-REMOVAL-PREVIEW.md.

Rules:
  - Grouping is split on ";" into values. A value is removed only when the
    WHOLE value equals a listed value (case-insensitive, surrounding spaces
    ignored). "November" never removes "November Sets" or "Event: November".
  - Every other value is kept, in its original order and spelling.
  - Removing the last value leaves Grouping empty; the writer then deletes the
    field instead of writing an empty one.
  - Files with nothing to remove are not in the plan. mp3, FLAC and M4A only
    (the formats apply_grouping_plan.py writes); ID3v2.2 is skipped, never
    converted. Listed values that matched nothing are reported, so a typo in
    the list shows up instead of silently doing nothing.
  - Never touches library media. Apply with apply_grouping_plan.py (lock,
    PASS backup manifest, write-ahead journal, verification, undo).
"""

from __future__ import annotations

# Preview-only: builds a plan for human review. Never mutates library media.
SAFETY_CLASSIFICATION = "preview-only"

import argparse
import collections
import csv
import json
from pathlib import Path

from build_grouping_csv_plan import SUPPORTED_SUFFIXES, read_grouping, split_parts

EXCLUDED_FOLDERS = {"imani drops"}


def audio_files(root: Path):
    for path in sorted(root.rglob("*")):
        if any(part.casefold() in EXCLUDED_FOLDERS for part in path.relative_to(root).parts):
            continue
        if path.is_file() and not path.name.startswith("._"):
            yield path


def survey(root: Path) -> dict:
    root = root.resolve()
    counts: collections.Counter = collections.Counter()
    examples: dict[str, str] = {}
    skipped: collections.Counter = collections.Counter()
    files = 0
    for path in audio_files(root):
        if path.suffix.casefold() not in SUPPORTED_SUFFIXES:
            skipped[f"unsupported format {path.suffix.casefold() or '(none)'}"] += 1
            continue
        current, id3_version = read_grouping(path)
        files += 1
        if id3_version == 2:
            skipped["ID3v2.2 (not read for removal)"] += 1
            continue
        for value in split_parts(current):
            counts[value] += 1
            examples.setdefault(value, str(path.relative_to(root)))
    values = [{"value": v, "count": n, "example": examples[v]}
              for v, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].casefold()))]
    return {"mode": "grouping-survey", "root": str(root), "files_read": files,
            "skipped": dict(skipped), "values": values}


def load_remove_list(path: Path) -> tuple[list[str], list[str]]:
    values, errors, seen = [], [], set()
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if ";" in value:
            errors.append(f"line {number}: value contains ';' delimiter: {value!r}")
            continue
        if value.casefold() not in seen:
            seen.add(value.casefold())
            values.append(value)
    if not values and not errors:
        errors.append("remove list is empty")
    return values, errors


def build(root: Path, remove_file: Path) -> dict:
    root = root.resolve()
    remove, errors = load_remove_list(remove_file)
    remove_fold = {v.casefold() for v in remove}
    items = []
    skipped: collections.Counter = collections.Counter()
    removed_counts: collections.Counter = collections.Counter()
    emptied = 0

    for path in audio_files(root):
        if path.suffix.casefold() not in SUPPORTED_SUFFIXES:
            skipped["unsupported format (not read)"] += 1
            continue
        current, id3_version = read_grouping(path)
        if id3_version == 2:
            skipped["ID3v2.2 preserved (not checked)"] += 1
            continue
        parts = split_parts(current)
        removed = [p for p in parts if p.casefold() in remove_fold]
        if not removed:
            continue
        kept = [p for p in parts if p.casefold() not in remove_fold]
        combined = "; ".join(kept)
        emptied += not combined
        for value in removed:
            removed_counts[value.casefold()] += 1
        items.append({
            "path": str(path.resolve()),
            "current_grouping": current,
            "removed_values": removed,
            "combined_grouping": combined,
            "id3_version": id3_version,
            "evidence": "owner-approved exact removal list",
        })

    by_value = {v: removed_counts.get(v.casefold(), 0) for v in remove}
    return {
        "mode": "preview-only",
        "source_mutation": False,
        "rules": {
            "approval_required": True,
            "no_moves": True,
            "append_only": False,
            "grouping_removal": {
                "approved_values": remove,
                "match": "whole value between ';', case-insensitive; everything else kept",
            },
        },
        "root": str(root),
        "remove_list": str(remove_file),
        "counts": {
            "items": len(items),
            "left_empty": emptied,
            "by_value": by_value,
            "matched_nothing": [v for v, n in by_value.items() if n == 0],
            "skipped": dict(skipped),
        },
        "validation": {"errors": errors},
        "items": items if not errors else [],
    }


def write_survey(result: dict, out_dir: Path) -> None:
    with (out_dir / "grouping-values.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["value", "count", "example"])
        writer.writeheader()
        writer.writerows(result["values"])
    lines = [
        "# Grouping values (read-only survey)",
        "",
        f"- Root: `{result['root']}`",
        f"- Files read: {result['files_read']}",
        f"- Distinct values: {len(result['values'])}",
        "",
        "Copy the exact values you want removed, one per line, into a text file",
        "and pass it to `build_grouping_removal_plan.py plan --remove`.",
        "",
        "| Value | Files | Example |",
        "|---|---|---|",
    ]
    lines += [f"| {v['value']} | {v['count']} | {v['example']} |" for v in result["values"]]
    if result["skipped"]:
        lines += ["", "## Not read", ""] + [f"- {r}: {n}" for r, n in result["skipped"].items()]
    (out_dir / "GROUPING-VALUES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_preview(plan: dict, out: Path) -> None:
    counts = plan["counts"]
    lines = [
        "# Grouping removal preview",
        "",
        f"- Root: `{plan['root']}`",
        f"- Files to change: {counts['items']}",
        f"- Files left with empty Grouping: {counts['left_empty']}",
        f"- Errors: {len(plan['validation']['errors'])}",
        "",
        "## Approved values to remove",
        "",
    ]
    lines += [f"- {v}: {n} files" for v, n in counts["by_value"].items()] or ["- (none)"]
    if counts["matched_nothing"]:
        lines += ["", "## Listed but found nowhere (check spelling)", ""]
        lines += [f"- {v}" for v in counts["matched_nothing"]]
    if counts["skipped"]:
        lines += ["", "## Skipped", ""] + [f"- {r}: {n}" for r, n in counts["skipped"].items()]
    if plan["validation"]["errors"]:
        lines += ["", "## Errors (plan must not be applied until these are fixed)", ""]
        lines += [f"- {e}" for e in plan["validation"]["errors"]]
    lines += ["", "## Changes", "", "| File | Grouping now | Grouping after |", "|---|---|---|"]
    for item in plan["items"]:
        now = "; ".join(item["current_grouping"])
        lines.append(f"| {Path(item['path']).name} | {now} | {item['combined_grouping'] or '(empty)'} |")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("survey", "plan"):
        p = sub.add_parser(name)
        p.add_argument("--root", required=True, type=Path, help="One top-level library folder, e.g. .../Music/Pop")
        p.add_argument("--out-dir", required=True, type=Path)
        if name == "plan":
            p.add_argument("--remove", required=True, type=Path,
                           help="Text file: one owner-approved exact value per line")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.command == "survey":
        result = survey(args.root)
        write_survey(result, args.out_dir)
        print(json.dumps({"files_read": result["files_read"], "distinct_values": len(result["values"]),
                          "csv": str(args.out_dir / "grouping-values.csv")}, indent=2))
        return

    plan = build(args.root, args.remove)
    plan_path = args.out_dir / "grouping-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_preview(plan, args.out_dir / "GROUPING-REMOVAL-PREVIEW.md")
    print(json.dumps({"plan": str(plan_path), **plan["counts"], "errors": plan["validation"]["errors"]}, indent=2))
    if plan["validation"]["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
