#!/usr/bin/env python3
"""Validate references/year-evidence.json without performing network access."""

from __future__ import annotations

# Read-only: inspects the library and writes reports/derived data only.
SAFETY_CLASSIFICATION = "read-only"

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

ALLOWED_GRADES = {
    "confirmed_primary",
    "confirmed_secondary",
    "local_evidence",
    "inference",
    "unconfirmable",
    "unknown",
}


def validate(payload: dict) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return errors + ["entries must be a list"]

    seen_keys: set[str] = set()
    for index, entry in enumerate(entries):
        prefix = f"entries[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix} must be an object")
            continue
        key = str(entry.get("key", "")).strip()
        grade = entry.get("evidence_grade")
        year = entry.get("year")
        if not key:
            errors.append(f"{prefix}.key is required")
        elif key in seen_keys:
            errors.append(f"{prefix}.key duplicates {key}")
        seen_keys.add(key)
        if grade not in ALLOWED_GRADES:
            errors.append(f"{prefix}.evidence_grade invalid: {grade!r}")
        if year is not None and (not isinstance(year, int) or year < 1900 or year > 2100):
            errors.append(f"{prefix}.year must be an integer 1900..2100 or null")
        sources = entry.get("sources", [])
        if not isinstance(sources, list):
            errors.append(f"{prefix}.sources must be a list")
            continue
        if grade in {"confirmed_primary", "confirmed_secondary"} and not sources:
            errors.append(f"{prefix} confirmed web evidence requires at least one source")
        for source_index, source in enumerate(sources):
            if not isinstance(source, dict):
                errors.append(f"{prefix}.sources[{source_index}] must be an object")
                continue
            url = str(source.get("url", "")).strip()
            if url:
                parsed = urlparse(url)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    errors.append(f"{prefix}.sources[{source_index}].url is not http(s)")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=Path("references/year-evidence.json"))
    args = parser.parse_args()
    payload = json.loads(args.path.read_text(encoding="utf-8"))
    errors = validate(payload)
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"valid: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
