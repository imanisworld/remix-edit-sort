#!/usr/bin/env python3
"""Turn store year research into guarded year-evidence registry entries.

Suspicious reissue dates are retained as evidence but never selected as the
release year. This is a report/reference-data writer only: it never mutates
audio tags, music files, folders, or Serato data.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "preview-only"

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse


REISSUE_MARKERS = {
    "remaster": re.compile(r"\bremaster(?:ed)?\b", re.I),
    "deluxe": re.compile(r"\bdeluxe\b", re.I),
    "anniversary": re.compile(r"\banniversary\b", re.I),
    "greatest_hits": re.compile(r"\bgreatest\s+hits\b", re.I),
    "live": re.compile(r"\blive\b", re.I),
}
WEB_GRADES = {"confirmed_primary", "confirmed_secondary"}


class YearGuardError(RuntimeError):
    pass


def _year(value, field: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool) or not 1900 <= value <= 2100:
        raise YearGuardError(f"{field} must be 1900..2100 or null")
    return value


def _url(value: object, field: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise YearGuardError(f"{field} must be an http(s) URL")
    return url


def load_research(path: Path) -> tuple[str, list[dict]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise YearGuardError("research schema_version must be 1")
    checked_on = str(payload.get("checked_on", "")).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", checked_on):
        raise YearGuardError("checked_on must be YYYY-MM-DD")
    items = payload.get("items")
    if not isinstance(items, list):
        raise YearGuardError("items must be a list")
    seen = set()
    validated = []
    for item_index, item in enumerate(items):
        prefix = f"items[{item_index}]"
        if not isinstance(item, dict):
            raise YearGuardError(f"{prefix} must be an object")
        key = str(item.get("key", "")).strip()
        subject = str(item.get("subject", "")).strip()
        if not key or not subject:
            raise YearGuardError(f"{prefix} requires key and subject")
        if key in seen:
            raise YearGuardError(f"duplicate research key: {key}")
        seen.add(key)
        embedded_year = _year(item.get("embedded_year"), f"{prefix}.embedded_year")
        hits = item.get("store_hits")
        if not isinstance(hits, list) or not hits:
            raise YearGuardError(f"{prefix}.store_hits must be a non-empty list")
        clean_hits = []
        for hit_index, hit in enumerate(hits):
            hit_prefix = f"{prefix}.store_hits[{hit_index}]"
            if not isinstance(hit, dict):
                raise YearGuardError(f"{hit_prefix} must be an object")
            store = str(hit.get("store", "")).strip()
            album_title = str(hit.get("album_title", "")).strip()
            if not store or not album_title:
                raise YearGuardError(f"{hit_prefix} requires store and album_title")
            grade = hit.get("evidence_grade", "confirmed_secondary")
            if grade not in WEB_GRADES:
                raise YearGuardError(f"{hit_prefix}.evidence_grade must be a confirmed web grade")
            clean_hits.append({
                "store": store,
                "album_title": album_title,
                "year": _year(hit.get("year"), f"{hit_prefix}.year"),
                "url": _url(hit.get("url"), f"{hit_prefix}.url"),
                "evidence_grade": grade,
            })
        validated.append({
            "key": key, "subject": subject, "embedded_year": embedded_year,
            "store_hits": clean_hits,
        })
    return checked_on, validated


def guard_entry(item: dict, checked_on: str) -> dict:
    hits = item["store_hits"]
    embedded = item["embedded_year"]
    store_years = sorted({hit["year"] for hit in hits if hit["year"] is not None})
    flags = []
    for hit in hits:
        markers = [name for name, pattern in REISSUE_MARKERS.items() if pattern.search(hit["album_title"])]
        if markers:
            flags.append({
                "code": "reissue_title_marker", "store": hit["store"],
                "album_title": hit["album_title"], "markers": markers,
            })
    later = [year for year in store_years if embedded is not None and year > embedded]
    if later:
        flags.append({
            "code": "store_year_after_embedded", "embedded_year": embedded,
            "later_store_years": later,
        })
    if len(store_years) > 1:
        flags.append({"code": "store_year_disagreement", "store_years": store_years})

    sources = [
        {
            "url": hit["url"], "source_type": "store", "store": hit["store"],
            "album_title": hit["album_title"], "reported_year": hit["year"],
            "evidence_grade": hit["evidence_grade"],
        }
        for hit in hits
    ]
    if flags or len(store_years) != 1:
        selected_year = None
        grade = "unknown"
        status = "reissue_review_required" if flags else "year_unresolved"
        note = "Store evidence is retained, but no year is authorized while reissue guards are unresolved."
    else:
        selected_year = store_years[0]
        grade = (
            "confirmed_primary"
            if all(hit["evidence_grade"] == "confirmed_primary" for hit in hits)
            else "confirmed_secondary"
        )
        status = "store_year_consistent"
        note = "Store hits agree and no reissue guard fired; still requires normal plan approval before tagging."
    return {
        "key": item["key"],
        "subject": item["subject"],
        "year": selected_year,
        "evidence_grade": grade,
        "review_status": status,
        "embedded_year": embedded,
        "store_year_candidates": store_years,
        "guard_flags": flags,
        "sources": sources,
        "evidence_note": note,
        "date_checked": checked_on,
    }


def merge_registry(registry: dict, entries: list[dict], replace_existing: bool = False) -> dict:
    if registry.get("schema_version") != 1 or not isinstance(registry.get("entries"), list):
        raise YearGuardError("registry must use year-evidence schema_version 1")
    existing = {str(entry.get("key", "")): index for index, entry in enumerate(registry["entries"])}
    result = {**registry, "entries": [dict(entry) for entry in registry["entries"]]}
    for entry in entries:
        if entry["key"] in existing:
            if not replace_existing:
                raise YearGuardError(f"registry already contains key: {entry['key']}")
            result["entries"][existing[entry["key"]]] = entry
        else:
            existing[entry["key"]] = len(result["entries"])
            result["entries"].append(entry)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="reviewed store research JSON")
    parser.add_argument("--registry", required=True, type=Path, help="existing year-evidence.json")
    parser.add_argument("--out", required=True, type=Path, help="new registry (may equal --registry)")
    parser.add_argument("--replace-existing", action="store_true")
    args = parser.parse_args()
    checked_on, items = load_research(args.input)
    entries = [guard_entry(item, checked_on) for item in items]
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    result = merge_registry(registry, entries, args.replace_existing)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "entries_added_or_replaced": len(entries),
        "reissue_review_required": sum(
            entry["review_status"] == "reissue_review_required" for entry in entries
        ),
        "years_selected": sum(entry["year"] is not None for entry in entries),
        "output": str(args.out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
