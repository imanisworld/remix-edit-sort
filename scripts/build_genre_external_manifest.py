#!/usr/bin/env python3
"""Build a learner-ready external Genre manifest from reviewed web evidence.

This script never changes music files, tags, folders, Serato data, or models.
It only validates reviewed evidence and writes a manifest/report for
``genre_learner.py --external-manifest``.

Research may come from Apple Music, Bandcamp, SoundCloud, TIDAL, YouTube,
Discogs, MusicBrainz, Mixcloud, or other direct web sources. Spotify evidence
is retained in the review report but is never emitted as ML training data,
because Spotify's developer policy forbids using Spotify Platform/Content to
train or ingest into ML/AI models.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "preview-only"

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import genre_learner


SOURCE_ALIASES = {
    "apple": "apple_music",
    "apple music": "apple_music",
    "itunes": "apple_music",
    "bandcamp": "bandcamp",
    "soundcloud": "soundcloud",
    "tidal": "tidal",
    "youtube": "youtube",
    "youtube music": "youtube",
    "mixcloud": "mixcloud",
    "discogs": "discogs",
    "musicbrainz": "musicbrainz",
    "spotify": "spotify",
    "web": "direct_web",
    "direct web": "direct_web",
    "artist site": "direct_web",
    "label site": "direct_web",
}

TRAINING_ALLOWED_SOURCES = {
    "apple_music",
    "bandcamp",
    "soundcloud",
    "tidal",
    "youtube",
    "mixcloud",
    "discogs",
    "musicbrainz",
    "direct_web",
}

RESEARCH_ONLY_SOURCES = {
    "spotify": "spotify_policy_disallows_ml_training",
}

WEAK_EVIDENCE_KINDS = {
    "platform_category",
    "playlist_tag",
    "show_tag",
    "mix_tag",
    "artist_only_genre",
}


class EvidenceError(RuntimeError):
    pass


def _bool(value) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "yes", "y", "1", "accepted", "accept"}
    return False


def _source(value: str) -> str:
    key = (value or "").strip().casefold()
    return SOURCE_ALIASES.get(key, key.replace(" ", "_"))


def _valid_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except (TypeError, ValueError):
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def load_research(path: Path) -> list[dict]:
    path = Path(path)
    if not path.is_file():
        raise EvidenceError(f"research file does not exist: {path}")
    suffix = path.suffix.casefold()
    if suffix == ".jsonl":
        rows = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise EvidenceError(f"invalid JSONL at line {number}: {exc}") from exc
        return rows
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return value.get("evidence", value.get("rows", value.get("examples", [])))
    raise EvidenceError("research input must be a list or an object containing evidence/rows/examples")


def target_metadata(targets: list[str], serato_rows: list[dict]) -> dict[str, dict]:
    target_set = {str(Path(path).resolve()) for path in targets}
    result = {path: {"path": path, "artist": "", "title": ""} for path in target_set}
    for row in serato_rows:
        raw_path = str(row.get("path") or "")
        if not raw_path:
            continue
        path = Path(raw_path if raw_path.startswith("/") else "/" + raw_path)
        resolved = str(path.resolve())
        if resolved not in target_set:
            continue
        result[resolved] = {
            "path": resolved,
            "artist": str(row.get("artist") or "").strip(),
            "title": str(row.get("title") or "").strip(),
        }
    return result


def _identity(artist: str, title: str) -> str | None:
    artist_norm = genre_learner.norm(artist)
    title_norm = genre_learner.norm(title)
    if not artist_norm or not title_norm:
        return None
    return f"{artist_norm}|{title_norm}"


def match_evidence(row: dict, targets: dict[str, dict]) -> tuple[str | None, str | None]:
    raw_path = str(row.get("path") or row.get("file") or "").strip()
    if raw_path:
        resolved = str(Path(raw_path).resolve())
        if resolved in targets:
            return resolved, None
        return None, "path_not_in_prediction_targets"

    identity = _identity(str(row.get("artist") or ""), str(row.get("title") or ""))
    if not identity:
        return None, "missing_path_or_complete_artist_title"
    matches = [
        path for path, meta in targets.items()
        if _identity(meta.get("artist", ""), meta.get("title", "")) == identity
    ]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, "artist_title_not_found_in_prediction_targets"
    return None, "artist_title_matches_multiple_prediction_targets"


def canonical_research_genre(raw: str, rules: dict) -> tuple[str | None, str | None]:
    parsed = genre_learner.classify_raw_genre(raw, rules)
    if parsed["status"] == "single" and len(parsed["labels"]) == 1:
        return parsed["labels"][0], None
    if parsed["status"] == "multi_label":
        return None, "research_genre_is_multi_label"
    if parsed["status"] == "context_only":
        return None, "research_label_is_context_not_genre"
    return None, "research_genre_is_not_canonical"


def validate_evidence_row(row: dict, rules: dict) -> tuple[dict | None, str | None]:
    source = _source(str(row.get("source") or ""))
    if not source:
        return None, "missing_source"
    if source in RESEARCH_ONLY_SOURCES:
        return None, RESEARCH_ONLY_SOURCES[source]
    if source not in TRAINING_ALLOWED_SOURCES:
        return None, "unsupported_source"

    source_url = str(row.get("source_url") or row.get("url") or "").strip()
    if not _valid_url(source_url):
        return None, "missing_or_invalid_source_url"
    if not _bool(row.get("accepted")):
        return None, "not_explicitly_accepted"

    confidence = str(row.get("label_confidence") or row.get("confidence") or "").strip().casefold()
    if confidence not in {"high", "reviewed"}:
        return None, "label_confidence_is_not_high_or_reviewed"

    evidence_kind = str(row.get("evidence_kind") or "").strip().casefold().replace(" ", "_")
    if evidence_kind in WEAK_EVIDENCE_KINDS:
        return None, f"weak_evidence_kind:{evidence_kind}"

    genre, genre_error = canonical_research_genre(str(row.get("genre") or ""), rules)
    if genre_error:
        return None, genre_error

    return {
        **row,
        "source": source,
        "source_url": source_url,
        "genre": genre,
        "label_confidence": confidence,
        "accepted": True,
        "evidence_kind": evidence_kind or "reviewed_track_or_release_evidence",
    }, None


def build_manifest(
    research_rows: list[dict],
    targets: dict[str, dict],
    rules: dict,
) -> tuple[list[dict], dict]:
    rejected = []
    matched = defaultdict(list)

    for index, raw_row in enumerate(research_rows, start=1):
        row, error = validate_evidence_row(raw_row, rules)
        if error:
            rejected.append({"index": index, "reason": error, "row": raw_row})
            continue
        path, match_error = match_evidence(row, targets)
        if match_error:
            rejected.append({"index": index, "reason": match_error, "row": raw_row})
            continue
        matched[path].append({"index": index, **row})

    examples = []
    conflicts = []
    for path in sorted(matched):
        evidence = matched[path]
        genres = sorted({row["genre"] for row in evidence})
        if len(genres) != 1:
            conflicts.append({
                "path": path,
                "reason": "accepted_sources_disagree_on_genre",
                "genres": genres,
                "evidence": evidence,
            })
            continue

        meta = targets[path]
        sources = [
            {
                "source": row["source"],
                "source_url": row["source_url"],
                "genre": row["genre"],
                "label_confidence": row["label_confidence"],
                "evidence_kind": row["evidence_kind"],
            }
            for row in evidence
        ]
        examples.append({
            "path": path,
            "artist": meta.get("artist", ""),
            "title": meta.get("title", ""),
            "genre": genres[0],
            "accepted": True,
            "label_confidence": "reviewed",
            "source": "+".join(sorted({row["source"] for row in evidence})),
            "source_url": evidence[0]["source_url"],
            "sources": sources,
        })

    report = {
        "research_rows": len(research_rows),
        "matched_paths_with_accepted_evidence": len(matched),
        "manifest_examples": len(examples),
        "rejected_rows": len(rejected),
        "conflicting_paths": len(conflicts),
        "rejected": rejected,
        "conflicts": conflicts,
    }
    return examples, report


def write_json(path: Path, value) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--research", type=Path, required=True)
    root.add_argument("--targets", type=Path, required=True)
    root.add_argument("--output", type=Path, required=True)
    root.add_argument("--review-output", type=Path)
    root.add_argument("--rules", type=Path, default=genre_learner.DEFAULT_RULES)
    root.add_argument("--library", type=Path, default=genre_learner.DEFAULT_LIBRARY)
    root.add_argument("--serato-db", type=Path, default=genre_learner.DEFAULT_SERATO_DB)
    root.add_argument("--serato-parser", type=Path, default=genre_learner.DEFAULT_SERATO_PARSER)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        rules = genre_learner.load_rules(args.rules)
        target_paths = genre_learner.load_targets_file(args.targets)
        raw_serato = genre_learner.parse_serato_database(args.serato_db, args.serato_parser)
        metadata = target_metadata(target_paths, raw_serato)
        research = load_research(args.research)
        examples, report = build_manifest(research, metadata, rules)
        write_json(args.output, {"examples": examples})
        review_output = args.review_output or args.output.with_name(args.output.stem + "-review.json")
        write_json(review_output, report)
        print(json.dumps({
            "targets": len(target_paths),
            "research_rows": len(research),
            "manifest_examples": len(examples),
            "rejected_rows": report["rejected_rows"],
            "conflicting_paths": report["conflicting_paths"],
            "output": str(args.output),
            "review_output": str(review_output),
        }, indent=2))
        return 0
    except (EvidenceError, genre_learner.LearnerError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "REFUSED", "reason": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
