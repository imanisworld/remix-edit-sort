#!/usr/bin/env python3
"""Read-only Genre/Grouping evidence checker for unresolved DJ tracks.

Reuses the shared metadata classifiers and version-genre-rules.json. It never
moves files or writes tags.
"""

from __future__ import annotations

# Read-only: inspects the library and writes reports/derived data only.
SAFETY_CLASSIFICATION = "read-only"

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_version_genre_plan import (  # noqa: E402
    analyze_genre,
    classify_grouping,
    load_genre_rules,
)

OUTPUT_COLUMNS = [
    "full_path",
    "filename",
    "sha256",
    "artist",
    "title",
    "current_genre",
    "proposed_genre",
    "genre_status",
    "genre_confidence",
    "genre_evidence",
    "genre_resolution_status",
    "genre_category",
    "recognized_genres",
    "known_contexts",
    "unknown_tokens",
    "current_grouping",
    "proposed_grouping",
    "grouping_resolution_status",
    "grouping_add_terms",
    "grouping_evidence",
    "grouping_review_reasons",
    "needs_check_path",
]


def is_needs_check_path(path: str) -> bool:
    return any(part.casefold() == "_genre needs check" for part in Path(path).parts)


def evaluate_genre_row(row: dict[str, str], rules: dict) -> dict[str, str]:
    analysis = analyze_genre(row, rules, assume_genre=None)
    proposed = analysis["proposed_genre"]
    evidence = analysis["evidence"]
    confidence = analysis["confidence"]
    current = (row.get("genre") or "").strip()
    current_grouping = (row.get("grouping") or "").strip()
    grouping = classify_grouping(
        row.get("filename", ""), row.get("title", ""), current_grouping, rules
    )

    if proposed is None:
        status = "REVIEW"
        proposed_text = ""
    elif proposed == current:
        status = "ALREADY_MATCHES"
        proposed_text = proposed
    else:
        status = "HIGH_CONFIDENCE_PROPOSAL" if confidence in {"high", "tag"} else "REVIEW"
        proposed_text = proposed if status == "HIGH_CONFIDENCE_PROPOSAL" else ""

    return {
        "full_path": row.get("full_path", ""),
        "filename": row.get("filename", ""),
        "sha256": row.get("sha256", ""),
        "artist": row.get("artist", ""),
        "title": row.get("title", ""),
        "current_genre": current,
        "proposed_genre": proposed_text,
        "genre_status": status,
        "genre_confidence": confidence,
        "genre_evidence": evidence,
        "genre_resolution_status": analysis["resolution_status"],
        "genre_category": analysis["category"],
        "recognized_genres": "; ".join(analysis["recognized_genres"]),
        "known_contexts": "; ".join(analysis["known_contexts"]),
        "unknown_tokens": "; ".join(analysis["unknown_tokens"]),
        "current_grouping": current_grouping,
        "proposed_grouping": grouping["proposed_grouping"],
        "grouping_resolution_status": grouping["resolution_status"],
        "grouping_add_terms": "; ".join(grouping["add_terms"]),
        "grouping_evidence": "; ".join(grouping["evidence"]),
        "grouping_review_reasons": "; ".join(grouping["review_reasons"]),
        "needs_check_path": "yes" if is_needs_check_path(row.get("full_path", "")) else "no",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path, help="inventory.csv from inventory_library.py")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--genre-rules", type=Path, help="Override version-genre-rules.json")
    parser.add_argument(
        "--only-needs-check",
        action="store_true",
        help="Only evaluate rows currently inside a _GENRE NEEDS CHECK directory.",
    )
    args = parser.parse_args()

    rules = load_genre_rules(args.genre_rules)
    with args.inventory.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    if args.only_needs_check:
        rows = [row for row in rows if is_needs_check_path(row.get("full_path", ""))]

    results = [evaluate_genre_row(row, rules) for row in rows]
    status_counts = Counter(result["genre_status"] for result in results)
    resolution_counts = Counter(result["genre_resolution_status"] for result in results)
    category_counts = Counter(result["genre_category"] for result in results)
    proposal_counts = Counter(
        result["proposed_genre"] for result in results
        if result["genre_status"] == "HIGH_CONFIDENCE_PROPOSAL" and result["proposed_genre"]
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.out_dir / "genre-evidence.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(results)

    summary = {
        "mode": "preview-only",
        "source_mutation": False,
        "tracks_checked": len(results),
        "status_counts": dict(sorted(status_counts.items())),
        "resolution_counts": dict(sorted(resolution_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "proposal_counts": dict(sorted(proposal_counts.items())),
        "rules": {
            "classifier": "build_version_genre_plan.analyze_genre + classify_grouping",
            "multi_genre_components_supported": True,
            "known_context_contamination_is_partial": True,
            "unknown_compound_tokens_fail_closed": True,
            "no_moves": True,
            "no_tag_writes": True,
        },
        "outputs": {"csv": str(csv_path)},
    }
    (args.out_dir / "genre-evidence-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    lines = [
        "# Genre evidence preview",
        "",
        "**Read-only. No files or tags were changed.**",
        "",
        f"Tracks checked: **{len(results)}**",
        "",
        "## Status",
        "",
    ]
    for name, count in status_counts.most_common():
        lines.append(f"- `{name}`: **{count}**")
    lines.extend(["", "## High-confidence proposed Genres", ""])
    if proposal_counts:
        for name, count in proposal_counts.most_common():
            lines.append(f"- `{name}`: **{count}**")
    else:
        lines.append("None.")
    lines.extend([
        "",
        "Known multi-Genre values resolve component-by-component. Context contamination and mixed unknown tokens fail closed without a writable proposal.",
        "",
    ])
    (args.out_dir / "GENRE-EVIDENCE-PREVIEW.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
