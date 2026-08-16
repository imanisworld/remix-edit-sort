#!/usr/bin/env python3
"""Read-only version evidence checker for unresolved DJ tracks.

This tool never moves, renames, deletes, retags, or edits audio. It reuses the
existing `classify_version()` rules from build_version_genre_plan.py and can
optionally ingest transcript evidence generated elsewhere.

Important safety rule: failure to detect profanity is NEVER treated as proof
that a track is clean.
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
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_version_genre_plan import NEEDS_VERSION, classify_version  # noqa: E402

OUTPUT_COLUMNS = [
    "full_path",
    "filename",
    "sha256",
    "artist",
    "title",
    "current_genre",
    "version_words",
    "label_version_category",
    "label_evidence",
    "transcript_present",
    "explicit_terms_detected",
    "clean_dirty_status",
    "candidate_label",
    "proposed_clean_dirty",
    "confidence",
    "reason",
]


def load_transcript_evidence(path: Path | None) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Load optional transcript evidence indexed by SHA-256 and full path.

    Accepted shape is either a JSON list or {"records": [...]}.
    Each record may contain:
      sha256, full_path, explicit_terms_detected (list), source, confidence

    This checker does not create transcripts itself. Transcript production can
    use any local tool/model as long as the resulting evidence is reviewed.
    """
    if path is None:
        return {}, {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records", []) if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise SystemExit("transcript evidence must be a JSON list or {'records': [...]} object")

    by_hash: dict[str, dict[str, Any]] = {}
    by_path: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        digest = str(record.get("sha256") or "").strip().casefold()
        full_path = str(record.get("full_path") or "").strip()
        if digest:
            by_hash[digest] = record
        if full_path:
            by_path[str(Path(full_path).expanduser())] = record
    return by_hash, by_path


def transcript_for_row(
    row: dict[str, str],
    by_hash: dict[str, dict[str, Any]],
    by_path: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    digest = str(row.get("sha256") or "").strip().casefold()
    if digest and digest in by_hash:
        return by_hash[digest]
    full_path = str(row.get("full_path") or "").strip()
    return by_path.get(full_path)


def explicit_terms(record: dict[str, Any] | None) -> list[str]:
    if not record:
        return []
    values = record.get("explicit_terms_detected", [])
    if values is None:
        return []
    if isinstance(values, str):
        return [values] if values.strip() else []
    if isinstance(values, list):
        return [str(value).strip() for value in values if str(value).strip()]
    return []


def evaluate_version_row(row: dict[str, str], transcript: dict[str, Any] | None = None) -> dict[str, str]:
    source = Path(row.get("full_path", ""))
    category, label_evidence = classify_version(source.stem, row.get("title", ""))
    terms = explicit_terms(transcript)
    transcript_present = transcript is not None

    status = "NEEDS_LISTEN"
    candidate = ""
    proposed = ""
    confidence = "review"
    reason = "no explicit Clean/Dirty label; manual listening or stronger evidence required"

    if category == "CLEAN":
        candidate = "CLEAN"
        if terms:
            status = "CONFLICT_CLEAN_LABEL_AUDIO_EXPLICIT"
            confidence = "conflict"
            reason = "explicit Clean label conflicts with transcript evidence of explicit language"
        else:
            status = "CONFIRMED_CLEAN"
            proposed = "CLEAN"
            confidence = "high"
            reason = label_evidence
    elif category == "DIRTY":
        status = "CONFIRMED_DIRTY"
        candidate = proposed = "DIRTY"
        confidence = "high"
        reason = label_evidence
    elif category == NEEDS_VERSION and terms:
        status = "LIKELY_DIRTY"
        candidate = "DIRTY"
        confidence = str((transcript or {}).get("confidence") or "audio-review")
        reason = "transcript evidence contains explicit-language terms; review before applying"
    elif category == NEEDS_VERSION and transcript_present:
        status = "NEEDS_LISTEN"
        confidence = "review"
        reason = "transcript contains no detected explicit terms; absence of detection is not proof of Clean"
    elif category != NEEDS_VERSION:
        status = "OTHER_VERSION_LABEL"
        confidence = "high"
        reason = f"{label_evidence}; this does not determine Clean vs Dirty"

    return {
        "full_path": row.get("full_path", ""),
        "filename": row.get("filename", source.name),
        "sha256": row.get("sha256", ""),
        "artist": row.get("artist", ""),
        "title": row.get("title", ""),
        "current_genre": row.get("genre", ""),
        "version_words": row.get("version_words", ""),
        "label_version_category": category,
        "label_evidence": label_evidence,
        "transcript_present": "yes" if transcript_present else "no",
        "explicit_terms_detected": "; ".join(terms),
        "clean_dirty_status": status,
        "candidate_label": candidate,
        "proposed_clean_dirty": proposed,
        "confidence": confidence,
        "reason": reason,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path, help="inventory.csv from inventory_library.py")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--transcript-evidence",
        type=Path,
        help="Optional reviewed JSON transcript evidence. This checker never generates transcripts itself.",
    )
    parser.add_argument(
        "--only-needs-check",
        action="store_true",
        help="Only report rows whose current path contains a _NEEDS CHECK directory.",
    )
    args = parser.parse_args()

    by_hash, by_path = load_transcript_evidence(args.transcript_evidence)
    with args.inventory.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    if args.only_needs_check:
        rows = [
            row for row in rows
            if any(part.casefold() == "_needs check" for part in Path(row.get("full_path", "")).parts)
        ]

    results = [evaluate_version_row(row, transcript_for_row(row, by_hash, by_path)) for row in rows]
    counts = Counter(result["clean_dirty_status"] for result in results)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.out_dir / "version-evidence.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(results)

    summary = {
        "mode": "preview-only",
        "source_mutation": False,
        "tracks_checked": len(results),
        "counts": dict(sorted(counts.items())),
        "rules": {
            "explicit_clean_dirty_labels_reuse_existing_classifier": True,
            "no_detected_profanity_is_not_clean_proof": True,
            "likely_dirty_requires_review": True,
            "no_moves": True,
            "no_tag_writes": True,
        },
        "outputs": {"csv": str(csv_path)},
    }
    (args.out_dir / "version-evidence-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    lines = [
        "# Version evidence preview",
        "",
        "**Read-only. No files or tags were changed.**",
        "",
        f"Tracks checked: **{len(results)}**",
        "",
        "## Results",
        "",
    ]
    for name, count in counts.most_common():
        lines.append(f"- `{name}`: **{count}**")
    lines.extend([
        "",
        "## Safety rule",
        "",
        "A transcript with no detected profanity is not evidence that a track is Clean. "
        "Those tracks remain `NEEDS_LISTEN` unless an explicit Clean label already exists.",
        "",
    ])
    (args.out_dir / "VERSION-EVIDENCE-PREVIEW.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
