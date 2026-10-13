#!/usr/bin/env python3
"""Read-only guard for canonical genre naming across active rule files.

Existing mismatches are tracked in an explicit review baseline. New mismatches
fail CI. This tool never normalizes tags, writes media, or edits the rules.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import argparse
import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = ROOT / "references"


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected a JSON object")
    return data


def noncanonical_outputs(references: Path) -> list[dict[str, str]]:
    learner = read_json(references / "genre-learner-rules.json")
    legacy = read_json(references / "genre-rules.json")
    version = read_json(references / "version-genre-rules.json")
    names = learner["canonical_genres"]
    canonical = set(names)
    if len(names) != len(canonical):
        raise ValueError("duplicate names in learner canonical_genres")
    if any(not isinstance(name, str) or not name.strip() or name != name.strip()
           or unicodedata.normalize("NFC", name) != name for name in names):
        raise ValueError("invalid whitespace or Unicode in canonical_genres")
    folded = [unicodedata.normalize("NFC", name).casefold() for name in names]
    if len(folded) != len(set(folded)):
        raise ValueError("canonical names differ only by case/Unicode")
    for alias, target in learner.get("aliases", {}).items():
        if target not in canonical:
            raise ValueError(f"learner alias {alias!r} has noncanonical target {target!r}")
    for token in learner.get("context_tokens_not_genre", []):
        if token in canonical:
            raise ValueError(f"context token {token!r} is also canonical Genre")
    rows = []

    def add(file: str, field: str, key: str, raw: str) -> None:
        if not isinstance(raw, str):
            raise ValueError(f"{file} {field} {key!r}: non-string target")
        # Semicolon means a multi-style mapping, not one new compound Genre.
        for target in raw.split(";"):
            target = target.strip()
            if target and target not in canonical:
                rows.append({
                    "file": file,
                    "field": field,
                    "key": key,
                    "target": target,
                })

    for name in legacy.get("canonical_genres", []):
        add("genre-rules.json", "canonical_genres", name, name)
    for rule in legacy.get("keyword_rules", []):
        add("genre-rules.json", "keyword_rules", "|".join(rule["match"]), rule["genre"])
    for name in legacy.get("bpm_ranges", {}):
        if not name.startswith("_"):
            add("genre-rules.json", "bpm_ranges", name, name)
    for key, value in version.get("tag_aliases", {}).items():
        add("version-genre-rules.json", "tag_aliases", key, value)
    for rule in version.get("filename_keywords", []):
        add("version-genre-rules.json", "filename_keywords", rule["contains"], rule["genre"])
    return sorted(rows, key=lambda row: (row["file"], row["field"], row["key"], row["target"]))


def new_deviations(current: list[dict], baseline: list[dict]) -> list[dict]:
    approved = {json.dumps(row, sort_keys=True, ensure_ascii=False) for row in baseline}
    return [row for row in current if json.dumps(row, sort_keys=True, ensure_ascii=False) not in approved]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", type=Path, default=REFERENCES)
    parser.add_argument("--baseline", type=Path, default=REFERENCES / "genre-naming-baseline.json")
    args = parser.parse_args(argv)
    try:
        deviations = noncanonical_outputs(args.references)
        baseline = read_json(args.baseline)
        if baseline.get("schema_version") != 1 or not isinstance(baseline.get("existing_deviations"), list):
            raise ValueError("invalid genre naming baseline")
        extra = new_deviations(deviations, baseline["existing_deviations"])
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"Genre naming check failed: {exc}", file=sys.stderr)
        return 1
    print(f"Genre naming: {len(deviations)} known noncanonical output references; {len(extra)} new.")
    if extra:
        for row in extra:
            print(json.dumps(row, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
