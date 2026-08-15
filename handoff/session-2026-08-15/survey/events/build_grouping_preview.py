#!/usr/bin/env python3
import csv
import json
from collections import Counter
from pathlib import Path

from mutagen import File

BASE = Path(__file__).resolve().parent
PLAN = BASE / "events-version-genre-plan.json"
OUT_JSON = BASE / "events-grouping-preview.json"
OUT_CSV = BASE / "EVENTS-GROUPING-PREVIEW.csv"
OUT_MD = BASE / "EVENTS-GROUPING-PREVIEW.md"


def grouping_values(path):
    try:
        audio = File(path, easy=False)
        if audio is None or audio.tags is None:
            return [], None
        tags = audio.tags
        # ID3 (MP3/AIFF): Grouping / Content group description.
        if hasattr(tags, "getall"):
            vals = [str(frame) for frame in tags.getall("TIT1") if str(frame).strip()]
            return vals, "TIT1"
        # MP4/M4A convention.
        if "\xa9grp" in tags:
            vals = [str(v) for v in tags.get("\xa9grp", []) if str(v).strip()]
            return vals, "©grp"
        # Vorbis/FLAC convention.
        for key in ("grouping", "GROUPING"):
            if key in tags:
                raw = tags.get(key, [])
                vals = raw if isinstance(raw, list) else [raw]
                return [str(v) for v in vals if str(v).strip()], key
        return [], None
    except Exception as exc:
        return [], f"ERROR: {exc}"


def main():
    plan = json.loads(PLAN.read_text())
    rows = []
    missing = []
    errors = []
    event_counts = Counter()
    existing_count = 0
    already_present = 0

    for item in plan["items"]:
        source_rel = Path(item["source_relative"])
        event_name = source_rel.parts[0]
        proposed = f"Event: {event_name.strip()}"
        current_path = Path(item["destination"])
        if not current_path.exists():
            missing.append(str(current_path))
            continue
        existing, frame = grouping_values(current_path)
        read_error = bool(frame and frame.startswith("ERROR:"))
        if read_error:
            errors.append({"path": str(current_path), "error": frame})
        current = " ; ".join(existing)
        present = any(v.casefold() == proposed.casefold() for v in existing)
        if existing:
            existing_count += 1
        if present:
            already_present += 1
        action = "skip_tag_read_error" if read_error else ("unchanged" if present else ("append" if existing else "set"))
        event_counts[proposed] += 1
        rows.append({
            "path": str(current_path),
            "original_event_folder": event_name,
            "current_grouping": current,
            "proposed_grouping": proposed,
            "action": action,
            "tag_field": frame or "format default",
            "sha256_before_sort": item["sha256"],
        })

    result = {
        "mode": "preview-only",
        "scope": plan["library_root"],
        "rules": {
            "no_move": True,
            "no_delete": True,
            "preserve_existing_grouping": True,
            "approval_required": True,
        },
        "counts": {
            "plan_tracks": len(plan["items"]),
            "located_tracks": len(rows),
            "missing_tracks": len(missing),
            "tag_read_errors": len(errors),
            "existing_grouping_tracks": existing_count,
            "already_contains_proposed": already_present,
            "proposed_changes": sum(r["action"] in ("set", "append") for r in rows),
            "skipped_tag_read_errors": sum(r["action"] == "skip_tag_read_error" for r in rows),
            "event_groups": len(event_counts),
        },
        "event_group_counts": dict(sorted(event_counts.items())),
        "missing": missing,
        "errors": errors,
        "items": rows,
    }
    OUT_JSON.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["path"])
        writer.writeheader()
        writer.writerows(rows)

    c = result["counts"]
    lines = [
        "# Events Grouping metadata preview",
        "",
        "**Preview only — no music files or tags were changed.**",
        "",
        f"- Tracks in successful Events plan: {c['plan_tracks']:,}",
        f"- Current files located: {c['located_tracks']:,}",
        f"- Proposed Grouping changes: {c['proposed_changes']:,}",
        f"- Excluded because tags could not be read safely: {c['skipped_tag_read_errors']:,}",
        f"- Tracks with an existing Grouping value: {c['existing_grouping_tracks']:,}",
        f"- Already containing the proposed value: {c['already_contains_proposed']:,}",
        f"- Missing current paths: {c['missing_tracks']:,}",
        f"- Tag-read errors: {c['tag_read_errors']:,}",
        f"- Distinct original event folders: {c['event_groups']}",
        "",
        "## Proposed groups",
        "",
    ]
    lines += [f"- `{name}`: {count:,}" for name, count in sorted(event_counts.items())]
    lines += [
        "",
        "## Apply rules",
        "",
        "- Tags only: no moves, renames, or deletions.",
        "- Existing Grouping values are preserved and the proposed value is appended once.",
        "- Musical Genre is not changed.",
        "- A fresh verified backup and explicit approval are required before apply.",
        "",
        f"Detailed rows: `{OUT_CSV.name}`",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n")
    print(json.dumps(result["counts"], indent=2))


if __name__ == "__main__":
    main()
