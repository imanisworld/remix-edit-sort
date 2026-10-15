#!/usr/bin/env python3
"""Build a complete, review-only Genre/Grouping alias proposal table.

The input is the unresolved-token JSON emitted by the cleanup census. Case
and punctuation variants that the learner treats identically are aggregated,
but their counts and spellings are retained. Source filenames are deliberately
omitted from the checked-in review artifact. This script never changes tags,
music, folders, or Serato data.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "preview-only"

import argparse
from collections import Counter
import hashlib
import json
import re
from pathlib import Path

import genre_learner


AMBIGUOUS = {
    "garage": "Decide between a canonical Garage label and UK Garage.",
    "nuwave": "Decide whether Nu Wave becomes canonical or maps to another style.",
    "bollywood": "Decide whether Bollywood becomes a canonical Genre or context.",
    "mainstream": "Decide whether Mainstream is dropped or retained as context.",
    "genre": "Generic placeholder: approve drop or a context term; never a Genre.",
    "noirsound": "Project/source label: approve drop or a context term; never infer a Genre.",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(path: Path) -> list[dict]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("unresolved-token input must be a JSON list")
    rows = []
    for index, row in enumerate(value):
        if not isinstance(row, dict) or not str(row.get("token", "")).strip():
            raise ValueError(f"row {index} has no token")
        count = row.get("count")
        if not isinstance(count, int) or count < 1:
            raise ValueError(f"row {index} has invalid count")
        rows.append({
            "token": str(row["token"]).strip(),
            "count": count,
        })
    return rows


def aggregate(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(genre_learner.norm(row["token"]), []).append(row)
    result = []
    for normalized, variants in grouped.items():
        variants = sorted(variants, key=lambda row: (-row["count"], row["token"].casefold()))
        result.append({
            "normalized": normalized,
            "token": variants[0]["token"],
            "variants": [row["token"] for row in variants],
            "count": sum(row["count"] for row in variants),
        })
    return sorted(result, key=lambda row: (-row["count"], row["token"].casefold()))


def _phrase_matches(raw: str, mapping: dict[str, str]) -> list[str]:
    """Return unique mapped values named as complete phrases in raw."""
    matches = []
    for phrase, value in sorted(mapping.items(), key=lambda pair: -len(pair[0])):
        pattern = r"(?<!\w)" + re.escape(phrase).replace(r"\ ", r"\s+") + r"(?!\w)"
        if re.search(pattern, raw, flags=re.I) and value not in matches:
            matches.append(value)
    return matches


def proposal(token: str, rules: dict) -> dict:
    parsed = genre_learner.classify_raw_genre(token, rules)
    if not parsed["unknown"]:
        return {
            "genre": parsed["labels"],
            "grouping": parsed["grouping"],
            "status": "approved_rule",
            "rationale": "Resolved by the synchronized operator rules.",
        }

    key = genre_learner.norm(token)
    if key in AMBIGUOUS:
        return {
            "genre": [], "grouping": [], "status": "decision_required",
            "rationale": AMBIGUOUS[key],
        }
    if "psychedelic" in token.casefold():
        return {
            "genre": ["Rock"], "grouping": ["Psychedelic"],
            "status": "operator_directed_proposal",
            "rationale": "Operator direction: psychedelic* -> Rock + Psychedelic context.",
        }
    if re.search(r"\bindie\s+rock\b", token, flags=re.I):
        return {
            "genre": ["Rock"], "grouping": ["Indie Rock"],
            "status": "operator_directed_proposal",
            "rationale": "Operator direction: indie rock -> Rock + Indie Rock context.",
        }

    lowered = token.casefold()
    hip_hop = bool(re.search(r"\b(?:hip[ -]?hop|rap|gangsta|gangster)\b", lowered))
    if hip_hop:
        regions = []
        for pattern, value in (
            (r"\beast coast\b", "East Coast"),
            (r"\b(?:atl|atlanta)\b", "Atlanta"),
            (r"\b(?:dirty south|southern|south carolina)\b", "Dirty South"),
            (r"\bst louis\b", "St. Louis"),
        ):
            if re.search(pattern, lowered) and value not in regions:
                regions.append(value)
        if regions:
            return {
                "genre": ["Hip-Hop"], "grouping": regions,
                "status": "operator_directed_proposal",
                "rationale": "Operator direction: regional Hip-Hop keeps region in Grouping.",
            }

    aliases = {
        **{genre: genre for genre in rules["canonical_genres"]},
        **rules.get("aliases", {}),
    }
    contexts = {
        **{value: value for value in rules.get("context_tokens_not_genre", [])},
        **rules.get("context_aliases", {}),
    }
    genres = _phrase_matches(token, aliases)
    grouping = _phrase_matches(token, contexts)
    if genres or grouping:
        return {
            "genre": genres, "grouping": grouping,
            "status": "partial_phrase_proposal",
            "rationale": "Known Genre/context phrases were found inside a compound token; review leftovers.",
        }
    return {
        "genre": [], "grouping": [], "status": "decision_required",
        "rationale": "No current rule supports a safe mapping; operator decision required.",
    }


def build(input_path: Path, rules_path: Path) -> dict:
    source_rows = load_rows(input_path)
    rules = genre_learner.load_rules(rules_path)
    proposals = [
        {**row, **proposal(row["token"], rules)} for row in aggregate(source_rows)
    ]
    statuses = Counter(row["status"] for row in proposals)
    occurrences = Counter()
    for row in proposals:
        occurrences[row["status"]] += row["count"]
    return {
        "schema_version": 1,
        "mode": "review-only",
        "source": input_path.name,
        "source_sha256": sha256_file(input_path),
        "rules": rules_path.name,
        "summary": {
            "source_rows": len(source_rows),
            "normalized_tokens": len(proposals),
            "track_occurrences": sum(row["count"] for row in source_rows),
            "tokens_by_status": dict(statuses),
            "occurrences_by_status": dict(occurrences),
        },
        "proposals": proposals,
    }


def markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Genre alias proposals — 2026-09-10",
        "",
        "Review-only: this file does not authorize tag writes. Counts are aggregated across "
        "case/punctuation variants using the learner's normalization.",
        "",
        f"Source: `{report['source']}` (`{report['source_sha256']}`).",
        "",
        f"{summary['source_rows']} source rows -> {summary['normalized_tokens']} normalized tokens; "
        f"{summary['track_occurrences']} total track occurrences.",
        "",
        "| Count | Token (variants) | Genre[] | Grouping[] | Status | Rationale |",
        "| ---: | --- | --- | --- | --- | --- |",
    ]
    for row in report["proposals"]:
        variants = " / ".join(row["variants"])
        cells = [
            str(row["count"]), variants, "; ".join(row["genre"]),
            "; ".join(row["grouping"]), row["status"], row["rationale"],
        ]
        cells = [value.replace("|", "\\|").replace("\n", " ") for value in cells]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--rules", type=Path, default=genre_learner.DEFAULT_RULES)
    parser.add_argument("--json-out", required=True, type=Path)
    parser.add_argument("--markdown-out", required=True, type=Path)
    args = parser.parse_args()
    report = build(args.input, args.rules)
    args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.markdown_out.write_text(markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
