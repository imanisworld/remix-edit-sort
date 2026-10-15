#!/usr/bin/env python3
"""Validate a proposed folder rename and shape a preview for existing tools.

No audio, tag, Serato database, or Rekordbox database is read or written.
Candidate paths are checked with ``rewrite_serato_paths.normalize_rename_map``
so the emitted map uses that tool's rules. Rekordbox is named, not parsed:
the Mac follow-up is ``rekordbox_relocation_preview.py`` against a trusted index.
Ambiguous case, Unicode, identity, and chained moves stay unresolved.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import argparse
import json
import unicodedata
from pathlib import Path

import rewrite_serato_paths


class FolderRenamePreviewError(rewrite_serato_paths.SeratoRewriteError):
    """The proposed plan is not a reviewable rename list."""


def _sha256(value: object) -> str | None:
    if not isinstance(value, str) or len(value) != 64:
        return None
    if any(character not in "0123456789abcdefABCDEF" for character in value):
        return None
    return value.lower()


def _identity(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def _prepare(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {"file_id": None, "sha256": None, "from": None, "to": None,
                "state": "REVIEW", "reason": "rename item must be an object"}
    file_id = raw.get("file_id")
    digest = _sha256(raw.get("sha256"))
    row = {
        "file_id": file_id if type(file_id) is int else None,
        "sha256": digest,
        "from": raw.get("from"),
        "to": raw.get("to"),
        "state": None,
        "reason": None,
    }
    if type(file_id) is not int or file_id < 1 or digest is None:
        row["state"] = "REVIEW"
        row["reason"] = "whole-file sha256 and a positive file_id are required"
        return row
    try:
        row["from"] = rewrite_serato_paths._normalize_map_path(raw.get("from"), "from")
        row["to"] = rewrite_serato_paths._normalize_map_path(raw.get("to"), "to")
    except rewrite_serato_paths.SeratoRewriteError as exc:
        row["state"] = "REVIEW"
        row["reason"] = str(exc)
        return row
    if row["from"] == row["to"]:
        row["state"] = "NO_CHANGE"
        row["reason"] = "path is already the proposed destination"
        return row
    if _identity(row["from"]) == _identity(row["to"]):
        row["state"] = "REVIEW"
        row["reason"] = "case or unicode equivalent path is ambiguous"
    return row


def _mark_groups(rows: list[dict], field: str, exact_reason: str, ambiguous_reason: str) -> None:
    exact: dict[str, list[dict]] = {}
    folded: dict[str, list[dict]] = {}
    for row in rows:
        if row["state"] is not None:
            continue
        exact.setdefault(row[field], []).append(row)
        folded.setdefault(_identity(row[field]), []).append(row)
    for group in exact.values():
        if len(group) > 1:
            for row in group:
                row["state"] = "BLOCKED"
                row["reason"] = exact_reason
    for group in folded.values():
        if len(group) > 1:
            for row in group:
                if row["state"] is None:
                    row["state"] = "REVIEW"
                    row["reason"] = ambiguous_reason


def _mark_chains(rows: list[dict]) -> None:
    open_rows = [row for row in rows if row["state"] is None]
    chained: set[int] = set()
    for left in open_rows:
        for right in open_rows:
            if left is right:
                continue
            destination = left["to"]
            source = right["from"]
            if destination == source or destination.startswith(source + "/"):
                chained.add(id(left))
                chained.add(id(right))
    for row in open_rows:
        if id(row) in chained:
            row["state"] = "BLOCKED"
            row["reason"] = "chained rename requires a separate reviewed plan"


def build_preview(plan: dict) -> dict:
    if not isinstance(plan, dict) or plan.get("schema_version") != 1 or not isinstance(plan.get("items"), list):
        raise FolderRenamePreviewError("plan must be schema_version 1 with an items array")
    rows = [_prepare(item) for item in plan["items"]]
    seen_ids: dict[int, list[dict]] = {}
    for row in rows:
        if row["state"] is None and type(row["file_id"]) is int:
            seen_ids.setdefault(row["file_id"], []).append(row)
    for group in seen_ids.values():
        if len(group) > 1:
            for row in group:
                row["state"] = "REVIEW"
                row["reason"] = "duplicate file_id in one rename plan"
    _mark_groups(
        rows, "to",
        "destination collision",
        "case or unicode destination collision",
    )
    _mark_groups(
        rows, "from",
        "duplicate source path",
        "case or unicode source collision",
    )
    _mark_chains(rows)
    for row in rows:
        if row["state"] is None:
            row["state"] = "CANDIDATE"
            row["reason"] = "ready for the existing Serato and Rekordbox preview tools"
    blocked = any(row["state"] in {"REVIEW", "BLOCKED"} for row in rows)
    candidates = [row for row in rows if row["state"] == "CANDIDATE"]
    rename_map = None
    ready = False
    if candidates and not blocked:
        rules = rewrite_serato_paths.normalize_rename_map({
            "renames": [
                {"from": row["from"], "to": row["to"], "source_index": row["file_id"]}
                for row in candidates
            ]
        })
        rename_map = {"renames": rules}
        ready = True
    return {
        "schema_version": 1,
        "mode": "preview-only",
        "apply_authorized": False,
        "proof_of_move": False,
        "serato_rename_map_ready": ready,
        "serato_rename_map": rename_map,
        "existing_tools": {
            "serato": (
                "python3 scripts/rewrite_serato_paths.py preview "
                "--serato-dir <verified _Serato_ directory> "
                "--rename-map <serato_rename_map.json> "
                "--out <private serato-path-preview.json>"
            ),
            "rekordbox": (
                "python3 scripts/rekordbox_relocation_preview.py "
                "--db <trusted library index>"
            ),
        },
        "unsupported": [
            "does not parse Serato or Rekordbox exports",
            "does not move audio or rewrite DJ software references",
            "does not embed live genre folder names",
        ],
        "items": [
            {key: row[key] for key in ("file_id", "sha256", "from", "to", "state", "reason")}
            for row in rows
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.plan.read_text(encoding="utf-8"))
        outcome = build_preview(payload)
    except (OSError, json.JSONDecodeError, FolderRenamePreviewError) as exc:
        parser.exit(2, f"REFUSED: {exc}\n")
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
