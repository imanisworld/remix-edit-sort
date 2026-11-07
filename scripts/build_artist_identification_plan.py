#!/usr/bin/env python3
"""Build a read-only Artist/Title identification review plan.

Filename parsing creates candidates, never approvals. Independent local
evidence may corroborate a candidate from a fully tagged exact-SHA-256 twin or,
when explicitly requested and available, a Chromaprint match to a fully tagged
inventory row. Every result still requires human review. This module writes
reports only and has no apply path.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "preview-only"

import argparse
import collections
import csv
import json
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

import audio_fingerprint

SUPPORTED_EVENTUAL_APPLY_FORMATS = frozenset({"mp3", "m4a", "wav"})
EXCLUDED_PATH_COMPONENTS = frozenset({"imani drops"})
TRACK_PREFIX_RE = re.compile(r"^\s*\d{1,3}(?:[-_.]\s*)+")
COPY_SUFFIX_RE = re.compile(r"\s\((\d+)\)$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
CHROMAPRINT_THRESHOLD = 0.85
CHROMAPRINT_DURATION_TOLERANCE_SECONDS = 3.0

REVIEW_FIELDS = [
    "path",
    "sha256",
    "current_artist",
    "current_title",
    "proposed_artist",
    "proposed_title",
    "status",
    "confidence",
    "corroboration",
    "reasons",
    "review_decision",
    "review_notes",
]
OUTSIDE_SCOPE_FIELDS = [
    "path",
    "sha256",
    "file_type",
    "artist",
    "title",
    "outside_scope_reason",
]


def normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def identity_key(artist: str, title: str) -> tuple[str, str]:
    return normalized(artist), normalized(title)


def filename_candidate(row: dict[str, str]) -> tuple[str, str, list[str]] | None:
    stem = COPY_SUFFIX_RE.sub("", Path(row["filename"]).stem).strip()
    stem = TRACK_PREFIX_RE.sub("", stem).strip()
    if stem.count(" - ") != 1:
        return None
    filename_artist, filename_title = (part.strip() for part in stem.split(" - ", 1))
    if not filename_artist or not filename_title:
        return None

    current_artist = row.get("artist", "").strip()
    current_title = row.get("title", "").strip()
    reasons = ["strict single-separator filename candidate"]
    if current_artist and normalized(current_artist) != normalized(filename_artist):
        return None
    if current_title and normalized(current_title) != normalized(filename_title):
        return None
    if current_artist:
        reasons.append("existing artist matches filename artist")
    if current_title:
        reasons.append("existing title matches filename title")
    return current_artist or filename_artist, current_title or filename_title, reasons


def outside_scope_reason(row: dict[str, str]) -> str | None:
    path = Path(row["full_path"])
    excluded = next(
        (part for part in path.parts if normalized(part) in EXCLUDED_PATH_COMPONENTS),
        None,
    )
    if excluded:
        return f"excluded_path_component: {excluded}"
    if not SHA256_RE.fullmatch(row.get("sha256", "").strip()):
        return "invalid_sha256"
    if row.get("artist", "").strip() and row.get("title", "").strip():
        return "artist_and_title_already_present"
    file_type = row.get("file_type", "").strip().casefold()
    if file_type not in SUPPORTED_EVENTUAL_APPLY_FORMATS:
        return f"unsupported_eventual_apply_format: {file_type or 'unknown'}"
    return None


def _hash_map(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    by_hash: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    for row in rows:
        by_hash[row["sha256"]].append(row)
    return by_hash


def _unique_identities(rows: list[dict[str, str]]) -> dict[tuple[str, str], tuple[str, str]]:
    identities: dict[tuple[str, str], tuple[str, str]] = {}
    for row in rows:
        artist = row.get("artist", "").strip()
        title = row.get("title", "").strip()
        if artist and title:
            identities.setdefault(identity_key(artist, title), (artist, title))
    return identities


def _chromaprint_matches(
    targets: list[dict[str, str]],
    references: list[dict[str, str]],
) -> tuple[dict[str, list[dict[str, str]]], dict]:
    if shutil.which("fpcalc") is None:
        return {}, {
            "status": "FAILED",
            "reason": "fpcalc is not installed; Chromaprint evidence was not produced",
            "threshold": CHROMAPRINT_THRESHOLD,
            "duration_tolerance_seconds": CHROMAPRINT_DURATION_TOLERANCE_SECONDS,
        }

    fingerprints: dict[str, list[int]] = {}
    failures: list[dict[str, str]] = []

    def get_fingerprint(row: dict[str, str]) -> list[int] | None:
        path = row["full_path"]
        if path in fingerprints:
            return fingerprints[path]
        try:
            fingerprints[path] = audio_fingerprint.fingerprint(Path(path))
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            failures.append({"path": path, "error": str(exc)})
            return None
        return fingerprints[path]

    matches: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    for target in targets:
        target_fingerprint = get_fingerprint(target)
        if target_fingerprint is None:
            continue
        try:
            target_duration = float(target.get("duration_seconds") or 0)
        except ValueError:
            target_duration = 0
        for reference in references:
            try:
                reference_duration = float(reference.get("duration_seconds") or 0)
            except ValueError:
                reference_duration = 0
            if (
                not target_duration
                or not reference_duration
                or abs(target_duration - reference_duration)
                > CHROMAPRINT_DURATION_TOLERANCE_SECONDS
            ):
                continue
            reference_fingerprint = get_fingerprint(reference)
            if reference_fingerprint is None:
                continue
            score = audio_fingerprint.similarity(
                target_fingerprint, reference_fingerprint
            )
            if score >= CHROMAPRINT_THRESHOLD:
                matches[target["full_path"]].append(
                    {**reference, "chromaprint_similarity": score}
                )

    if failures:
        return {}, {
            "status": "FAILED",
            "reason": (
                "one or more fpcalc probes failed; all Chromaprint matches were "
                "discarded because incomplete references could hide a conflict"
            ),
            "threshold": CHROMAPRINT_THRESHOLD,
            "duration_tolerance_seconds": CHROMAPRINT_DURATION_TOLERANCE_SECONDS,
            "probe_failures": failures,
        }
    return dict(matches), {
        "status": "PASS",
        "reason": "",
        "threshold": CHROMAPRINT_THRESHOLD,
        "duration_tolerance_seconds": CHROMAPRINT_DURATION_TOLERANCE_SECONDS,
        "probe_failures": [],
    }


def _review_item(
    row: dict[str, str],
    exact_references: list[dict[str, str]],
    chromaprint_references: list[dict[str, str]],
    chromaprint_status: dict,
) -> dict:
    candidate = filename_candidate(row)
    proposed_artist = candidate[0] if candidate else row.get("artist", "").strip()
    proposed_title = candidate[1] if candidate else row.get("title", "").strip()
    reasons = list(candidate[2]) if candidate else [
        "filename did not provide one unambiguous `Artist - Title` candidate"
    ]
    status = "NEEDS_CORROBORATION" if candidate else "HOLD_NO_CANDIDATE"
    confidence = "LOW" if candidate else "NONE"
    corroboration: list[str] = []

    exact_tag_states = {
        identity_key(reference.get("artist", ""), reference.get("title", ""))
        for reference in [row, *exact_references]
    }
    if len(exact_tag_states) > 1:
        status = "CONFLICT"
        confidence = "NONE"
        reasons.append(
            "exact-hash rows report different Artist/Title tag states; identical "
            "bytes cannot have different embedded tags, so the inventory is inconsistent"
        )
    elif exact_references:
        reasons.append(
            f"{len(exact_references)} exact-hash duplicate row(s) found; identical "
            "blank/partial tags add no independent identity evidence"
        )

    chromaprint_identities = _unique_identities(chromaprint_references)
    if status != "CONFLICT" and len(chromaprint_identities) > 1:
        status = "CONFLICT"
        confidence = "NONE"
        reasons.append("Chromaprint-matched tagged rows contain conflicting identities")
    elif status != "CONFLICT" and len(chromaprint_identities) == 1:
        fingerprint_artist, fingerprint_title = next(
            iter(chromaprint_identities.values())
        )
        if (
            proposed_artist
            and proposed_title
            and identity_key(proposed_artist, proposed_title)
            != identity_key(fingerprint_artist, fingerprint_title)
        ):
            status = "CONFLICT"
            confidence = "NONE"
            reasons.append("candidate conflicts with Chromaprint-matched tagged row")
        else:
            proposed_artist, proposed_title = fingerprint_artist, fingerprint_title
            if confidence != "HIGH":
                status = "CORROBORATED_FOR_REVIEW"
                confidence = "MEDIUM"
            corroboration.append("chromaprint_tagged_inventory_match")
            scores = sorted(
                {
                    str(reference["chromaprint_similarity"])
                    for reference in chromaprint_references
                }
            )
            reasons.append(
                "Chromaprint match to fully tagged inventory row "
                f"(similarity {', '.join(scores)})"
            )

    if chromaprint_status.get("status") == "FAILED" and not chromaprint_references:
        reasons.append(chromaprint_status["reason"])

    if not proposed_artist or not proposed_title:
        status = "HOLD_NO_CANDIDATE"
        confidence = "NONE"
        reasons.append("proposal is incomplete; Artist and Title both remain required")

    return {
        "path": row["full_path"],
        "sha256": row["sha256"],
        "current_artist": row.get("artist", ""),
        "current_title": row.get("title", ""),
        "proposed_artist": proposed_artist,
        "proposed_title": proposed_title,
        "status": status,
        "confidence": confidence,
        "corroboration": corroboration,
        "reasons": reasons,
        "review_decision": "",
        "review_notes": "",
    }


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            flattened = dict(row)
            for key, value in flattened.items():
                if isinstance(value, list):
                    flattened[key] = "; ".join(str(item) for item in value)
            writer.writerow({field: flattened.get(field, "") for field in fieldnames})


def build(
    inventory_path: Path,
    out_dir: Path,
    use_chromaprint: bool = False,
) -> dict:
    with inventory_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    out_dir.mkdir(parents=True, exist_ok=True)
    in_scope: list[dict[str, str]] = []
    outside_scope: list[dict[str, str]] = []
    for row in rows:
        reason = outside_scope_reason(row)
        if reason:
            outside_scope.append(
                {
                    "path": row["full_path"],
                    "sha256": row.get("sha256", ""),
                    "file_type": row.get("file_type", ""),
                    "artist": row.get("artist", ""),
                    "title": row.get("title", ""),
                    "outside_scope_reason": reason,
                }
            )
        else:
            in_scope.append(row)

    references = [
        row
        for row in rows
        if outside_scope_reason(row) == "artist_and_title_already_present"
        and not any(
            normalized(part) in EXCLUDED_PATH_COMPONENTS
            for part in Path(row["full_path"]).parts
        )
        and row.get("file_type", "").casefold()
        in SUPPORTED_EVENTUAL_APPLY_FORMATS
    ]
    rows_by_hash = _hash_map(rows)

    if use_chromaprint:
        chromaprint_matches, chromaprint_status = _chromaprint_matches(
            in_scope, references
        )
    else:
        chromaprint_matches = {}
        chromaprint_status = {
            "status": "NOT_RUN",
            "reason": "optional --chromaprint was not requested",
            "threshold": CHROMAPRINT_THRESHOLD,
            "duration_tolerance_seconds": CHROMAPRINT_DURATION_TOLERANCE_SECONDS,
        }

    items = [
        _review_item(
            row,
            [
                reference
                for reference in rows_by_hash.get(row["sha256"], [])
                if reference is not row
            ],
            chromaprint_matches.get(row["full_path"], []),
            chromaprint_status,
        )
        for row in in_scope
    ]
    counts = collections.Counter(item["status"] for item in items)
    plan = {
        "mode": "artist-identification-review",
        "inventory": str(inventory_path),
        "rules": {
            "read_only": True,
            "approval_required": True,
            "apply_compatible": False,
            "filename_alone_never_approved": True,
            "no_tag_writes": True,
            "no_serato_writes": True,
            "outside_scope_separate": True,
        },
        "chromaprint": chromaprint_status,
        "counts": dict(sorted(counts.items())),
        "items": items,
    }
    (out_dir / "artist-identification-plan.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _write_csv(out_dir / "artist-identification-review.csv", REVIEW_FIELDS, items)
    _write_csv(
        out_dir / "artist-identification-outside-scope.csv",
        OUTSIDE_SCOPE_FIELDS,
        outside_scope,
    )

    summary = {
        "inventory_rows": len(rows),
        "review_rows": len(items),
        "outside_scope_rows": len(outside_scope),
        "status_counts": dict(sorted(counts.items())),
        "chromaprint": chromaprint_status,
        "changes_made": 0,
    }
    (out_dir / "artist-identification-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    lines = [
        "# Artist identification proposal",
        "",
        "**Read-only review output. No tags, files, or Serato data were changed.**",
        "",
        f"- Review rows: **{len(items)}**",
        f"- Outside-scope rows: **{len(outside_scope)}**",
        f"- Chromaprint: **{chromaprint_status['status']}**",
        "",
        "Filename-derived values are candidates only. `CORROBORATED_FOR_REVIEW` "
        "still requires a human decision and is not an approval.",
        "",
        "This JSON is deliberately not accepted by `apply_artist_title_plan.py`. "
        "An approved subset must be promoted into its hash-bound preview format, "
        "then pass the existing backup-manifest and lock gates.",
    ]
    (out_dir / "ARTIST-IDENTIFICATION-PREVIEW.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return {
        "out_dir": out_dir,
        "review_count": len(items),
        "outside_scope_count": len(outside_scope),
        "status_counts": dict(sorted(counts.items())),
        "changes_made": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--chromaprint",
        action="store_true",
        help=(
            "optionally compare blank-tag rows with fully tagged inventory rows "
            "using local fpcalc; provider/tool failures remain explicit holds"
        ),
    )
    args = parser.parse_args()
    result = build(args.inventory, args.out_dir, args.chromaprint)
    print(
        json.dumps(
            {
                **result,
                "out_dir": str(result["out_dir"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
