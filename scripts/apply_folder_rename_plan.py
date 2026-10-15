#!/usr/bin/env python3
"""Preview, apply, undo and crash-recover whole-folder renames of the library.

Renaming a top-level library folder (``R&b`` -> ``R&B``, ``Afrobeat`` ->
``Afrobeats``) moves thousands of tracks at once while every Serato path that
points into it goes stale.  This tool performs only the folder rename, as
journaled ``mutation_engine`` transactions, and derives the exact rename map
that ``rewrite_serato_paths.py`` and ``rewrite_serato_sqlite_paths.py`` need
afterwards.  No file inside a folder changes name, bytes or tags.

Why this is not just ``os.rename``:

* On a case-insensitive APFS volume ``R&B`` "exists" whenever ``R&b`` does, so
  an existence check cannot tell a collision from the folder itself, and a
  plain rename to a different-case name is a no-op.  Every check here compares
  the literal directory entries, and a case-only change always goes through a
  unique temporary name (two journaled steps, restored if the second fails).
* A rename is only safe if Serato can be repointed afterwards, so ``apply``
  refuses unless every Serato database and crate has a verified backup that
  still matches the live bytes (and, optionally, the SQLite libraries too).
* The folder's contents are fingerprinted (every entry's path, size and mtime;
  SHA-256 with ``--hash-contents``).  The preview binds the plan to that
  fingerprint, the engine refuses on any drift, and it must be identical after
  the rename or the folder is renamed back.

``preview`` is read-only.  ``apply``, ``undo`` and ``recover`` need the
coordination locks (one per top-level folder named in the plan, source AND
destination spelling, plus ``Serato``), a heartbeat before every step, Serato
closed, and write the write-ahead journal.  The recovery journal of an apply
run is its undo log: ``undo --journal <journal>`` renames every completed
folder back, newest first, refusing while Serato still references the new
names.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "engine-user"

import argparse
import json
import os
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import backup_manifest
import folder_lock
import lock_guard
import mutation_engine
import rewrite_serato_paths as serato_rewrite
import rewrite_serato_sqlite_paths as sqlite_rewrite

PLAN_TYPE = "folder-rename"
PLAN_VERSION = 1
SERATO_LOCK_FOLDER = "Serato"


class FolderRenameError(RuntimeError):
    """The preview or apply could not prove that a folder rename is safe."""


# ------------------------------------------------------------------ rename map

def _key(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _relative_parts(value: object, label: str) -> list[str]:
    if not isinstance(value, str) or not value:
        raise FolderRenameError(f"{label} must be a non-empty string")
    if "\x00" in value or "\\" in value:
        raise FolderRenameError(f"{label} contains a NUL byte or backslash: {value!r}")
    if value.startswith("/"):
        raise FolderRenameError(f"{label} must be relative to the library root: {value!r}")
    parts = value.rstrip("/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise FolderRenameError(f"{label} is not a safe relative path: {value!r}")
    return parts


def normalize_folder_map(payload: object) -> list[dict]:
    """Validate a reviewed ``{old: new}`` folder map (paths relative to the library)."""
    try:
        raw_entries = serato_rewrite._raw_rename_entries(payload)
    except serato_rewrite.SeratoRewriteError as exc:
        raise FolderRenameError(str(exc)) from exc
    entries = []
    for index, raw in enumerate(raw_entries, start=1):
        if not isinstance(raw, dict):
            raise FolderRenameError(f"rename entry {index} must be an object")
        old_parts = _relative_parts(raw.get("from"), f"rename {index} 'from'")
        new_parts = _relative_parts(raw.get("to"), f"rename {index} 'to'")
        if old_parts[:-1] != new_parts[:-1]:
            raise FolderRenameError(
                f"rename {index} must keep the same parent folder (only the last component "
                f"may change): {raw.get('from')!r} -> {raw.get('to')!r}"
            )
        if old_parts == new_parts:
            raise FolderRenameError(f"rename {index} does not change the name: {raw.get('from')!r}")
        entries.append({
            "source_index": index,
            "from": "/".join(old_parts),
            "to": "/".join(new_parts),
            "case_only": _key(old_parts[-1]) == _key(new_parts[-1]),
        })
    if not entries:
        raise FolderRenameError("rename map contains no entries")

    sources = {}
    destinations = {}
    for entry in entries:
        source_key, destination_key = _key(entry["from"]), _key(entry["to"])
        if source_key in sources:
            raise FolderRenameError(f"duplicate rename source {entry['from']!r}")
        if destination_key in destinations:
            raise FolderRenameError(f"two renames share the destination {entry['to']!r}")
        sources[source_key] = entry
        destinations[destination_key] = entry
    for entry in entries:
        destination_key = _key(entry["to"])
        other = sources.get(destination_key)
        if other is not None and other is not entry:
            raise FolderRenameError(
                f"rename destination {entry['to']!r} is another rename's source; "
                "split chained or swapped renames into separate reviewed plans"
            )
        for other in entries:
            if other is not entry and _key(entry["from"]).startswith(_key(other["from"]) + "/"):
                raise FolderRenameError(
                    f"rename source {entry['from']!r} is inside rename source {other['from']!r}"
                )
    return entries


def load_folder_map(path: Path) -> list[dict]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FolderRenameError(f"folder map not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FolderRenameError(f"folder map is not valid JSON: {path}: {exc}") from exc
    return normalize_folder_map(payload)


def serato_path(absolute: Path) -> str:
    """Serato's volume-relative spelling (no leading slash)."""
    return os.path.abspath(os.fspath(absolute)).lstrip("/")


def top_level(relative: str) -> str:
    return relative.split("/", 1)[0]


def required_lock_folders(entries: list[dict]) -> list[str]:
    """Top-level folder names (source and destination spelling) plus Serato."""
    names: dict[str, str] = {}
    for entry in entries:
        for relative in (entry["from"], entry["to"]):
            name = top_level(relative)
            names.setdefault(folder_lock.slugify(name), name)
    names.setdefault(folder_lock.slugify(SERATO_LOCK_FOLDER), SERATO_LOCK_FOLDER)
    return sorted(names.values(), key=str.casefold)


def lock_names_for(intents: list[dict]) -> list[str]:
    """Lock folders needed to undo/recover the renames these journal intents record."""
    return required_lock_folders([
        {"from": intent["from"], "to": intent["to"]} for intent in intents
        if intent.get("from") and intent.get("to")
    ])


# --------------------------------------------------------------------- preview

def _checked_library_root(library_root: Path) -> Path:
    library_root = Path(os.path.abspath(os.fspath(library_root)))
    if not library_root.is_dir():
        raise FolderRenameError(f"library root is not a directory: {library_root}")
    if not mutation_engine.literal_path_components_exist(library_root):
        raise FolderRenameError(
            f"library root is not spelled exactly as on disk (case or Unicode form differs): {library_root}"
        )
    return library_root


def build_preview(
    library_root: Path,
    entries: list[dict],
    *,
    hash_contents: bool = False,
    serato_dir: Path | None = None,
    sqlite_dbs: list[Path] | None = None,
) -> dict:
    """Read-only plan: literal-entry checks, inventory digests, derived Serato map."""
    library_root = _checked_library_root(library_root)
    planned = []
    for entry in entries:
        source = library_root / entry["from"]
        destination = library_root / entry["to"]
        if not mutation_engine.literal_path_components_exist(source):
            raise FolderRenameError(
                f"{entry['from']!r} is not a folder spelled exactly like this under {library_root}"
            )
        try:
            case_only = mutation_engine.check_folder_rename(source, destination)
        except mutation_engine.MutationRefused as exc:
            raise FolderRenameError(str(exc)) from exc
        planned.append({
            "source_index": entry["source_index"],
            "from": entry["from"],
            "to": entry["to"],
            "case_only": case_only,
            "inventory": mutation_engine.directory_inventory(source, hash_contents=hash_contents),
            "serato_from": serato_path(source),
            "serato_to": serato_path(destination),
        })

    try:
        serato_rules = serato_rewrite.normalize_rename_map([
            {"from": item["serato_from"], "to": item["serato_to"], "source_index": item["source_index"]}
            for item in planned
        ])
    except serato_rewrite.SeratoRewriteError as exc:
        raise FolderRenameError(f"derived Serato rename map is unsafe: {exc}") from exc

    plan = {
        "plan_type": PLAN_TYPE,
        "plan_version": PLAN_VERSION,
        "mode": "preview-only",
        "approval_required": True,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "library_root": str(library_root),
        "hash_contents": bool(hash_contents),
        "renames": planned,
        "serato_rename_map": serato_rules,
        "required_lock_folders": required_lock_folders(entries),
        "summary": {
            "folders": len(planned),
            "files": sum(item["inventory"]["file_count"] for item in planned),
            "bytes": sum(item["inventory"]["total_bytes"] for item in planned),
            "case_only_renames": sum(1 for item in planned if item["case_only"]),
        },
    }
    impact = {}
    if serato_dir is not None:
        impact["serato_binaries"] = serato_rewrite.build_preview(Path(serato_dir), serato_rules)["summary"]
    if sqlite_dbs:
        impact["serato_sqlite"] = sqlite_rewrite.build_preview(
            [Path(path) for path in sqlite_dbs], serato_rules
        )["summary"]
    if impact:
        # Informational only; apply re-derives nothing from it.
        plan["serato_impact"] = impact
    return plan


def load_and_validate_plan(plan_path: Path, library_root: Path) -> dict:
    try:
        plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FolderRenameError(f"plan not found: {plan_path}") from exc
    except json.JSONDecodeError as exc:
        raise FolderRenameError(f"plan is not valid JSON: {plan_path}: {exc}") from exc
    if not isinstance(plan, dict):
        raise FolderRenameError("plan must be a JSON object")
    if plan.get("plan_type") != PLAN_TYPE or plan.get("plan_version") != PLAN_VERSION:
        raise FolderRenameError("unexpected folder-rename plan type/version")
    if plan.get("mode") != "preview-only" or plan.get("approval_required") is not True:
        raise FolderRenameError("plan is not an approval-required preview")
    library_root = _checked_library_root(library_root)
    if Path(plan.get("library_root", "")) != library_root:
        raise FolderRenameError(
            f"plan targets {plan.get('library_root')!r}, not requested library root {str(library_root)!r}"
        )
    entries = normalize_folder_map([
        {"from": item.get("from"), "to": item.get("to")} for item in plan.get("renames", [])
    ])
    rebuilt = build_preview(library_root, entries, hash_contents=bool(plan.get("hash_contents")))
    for field in ("renames", "serato_rename_map", "required_lock_folders", "summary", "hash_contents"):
        if rebuilt[field] != plan.get(field):
            raise FolderRenameError(
                f"plan field {field!r} no longer matches the library; rebuild the preview"
            )
    return plan


# ------------------------------------------------------------- locks / Serato

def open_heartbeat(lock_folders: list[str], session: str, required: list[str]):
    provided = {folder_lock.slugify(name) for name in lock_folders}
    missing = [name for name in required if folder_lock.slugify(name) not in provided]
    if missing:
        raise FolderRenameError(
            "--lock-folder must cover every folder this plan touches; missing: "
            + ", ".join(repr(name) for name in missing)
        )
    locks = {name: lock_guard.require_lock_or_exit(name, session) for name in lock_folders}
    heartbeat = lock_guard.MultiFolderLockHeartbeat(lock_folders, session)
    heartbeat.pin_from(locks)
    return heartbeat


def serato_references(serato_dir: Path | None, sqlite_dbs: list[Path], prefixes: list[str]) -> dict:
    """How many stored Serato paths sit at or under any of ``prefixes``."""
    def hits(stored: str) -> bool:
        return any(stored == prefix or stored.startswith(prefix + "/") for prefix in prefixes)

    counts = {}
    if serato_dir is not None:
        counts["database V2 + crates"] = sum(
            1 for stored in serato_rewrite._all_stored_paths(Path(serato_dir)) if hits(stored)
        )
    for database in sqlite_dbs:
        sqlite_rewrite.require_quiescent(Path(database))
        connection = sqlite_rewrite.open_immutable(Path(database))
        try:
            counts[Path(database).name] = sum(
                1 for (stored,) in connection.execute(
                    f'SELECT "{sqlite_rewrite.COLUMN}" FROM "{sqlite_rewrite.TABLE}"'
                )
                if isinstance(stored, str) and hits(stored)
            )
        finally:
            connection.close()
    return counts


# ----------------------------------------------------------------------- apply

def apply_folder_renames(
    *,
    plan: dict,
    serato_dir: Path,
    manifest: dict,
    heartbeat,
    journal_path: Path,
    result_path: Path,
    rename_map_path: Path | None = None,
    extra_guards: list[dict] | None = None,
    process_lister=None,
) -> dict:
    """Apply a validated plan: one engine ``folder_item`` per folder, in order."""
    serato_rewrite.require_serato_closed(process_lister)
    library_root = Path(plan["library_root"])
    serato_dir = Path(serato_dir).resolve()
    guards = [{"path": path} for path in serato_rewrite.serato_source_files(serato_dir)]
    guards.extend(extra_guards or [])

    engine = mutation_engine.MutationEngine(
        Path(journal_path), heartbeat, manifest, serato_dir, PLAN_TYPE
    )
    engine.start({
        "plan_type": PLAN_TYPE,
        "library_root": str(library_root),
        "folders": plan["summary"]["folders"],
        "files": plan["summary"]["files"],
        "guarded_files": len(guards),
    })
    completed = []
    for index, item in enumerate(plan["renames"], start=1):
        source = library_root / item["from"]
        destination = library_root / item["to"]
        with engine.folder_item(
            index,
            source,
            destination,
            expected_inventory=item["inventory"],
            guard_files=guards,
            extra_intent={
                "plan_type": PLAN_TYPE,
                "from": item["from"],
                "to": item["to"],
                "case_only": item["case_only"],
                "serato_from": item["serato_from"],
                "serato_to": item["serato_to"],
            },
        ) as txn:
            txn.rename_dir_no_overwrite(source, destination)
        completed.append({
            "from": item["from"],
            "to": item["to"],
            "case_only": item["case_only"],
            "files": item["inventory"]["file_count"],
            "inventory_sha256": item["inventory"]["sha256"],
        })

    if rename_map_path is not None:
        serato_rewrite.write_json(rename_map_path, plan["serato_rename_map"])
    result = {
        "status": "PASS",
        "plan_type": PLAN_TYPE,
        "library_root": str(library_root),
        "folders_renamed": len(completed),
        "files_in_renamed_folders": sum(item["files"] for item in completed),
        "completed": completed,
        "serato_rename_map": str(rename_map_path) if rename_map_path else None,
        "journal": str(journal_path),
        "next_steps": [
            "python3 scripts/rewrite_serato_paths.py preview/apply with the serato_rename_map",
            "python3 scripts/rewrite_serato_sqlite_paths.py apply for master.sqlite and root.sqlite",
            "python3 scripts/check_serato_crate_paths.py, then open Serato and spot-check crates",
        ],
    }
    engine.finish(result)
    serato_rewrite.write_json(result_path, result)
    return result


# ------------------------------------------------------------------------ undo

def _journal_folder_items(journal_path: Path) -> tuple[dict, list[dict]]:
    records = mutation_engine.RecoveryJournal(Path(journal_path)).read()
    if any(record.get("type") == "torn_record" for record in records):
        raise FolderRenameError(f"journal has a torn record; run recover first: {journal_path}")
    intents = {
        (record.get("run_id"), record.get("index")): record
        for record in records
        if record.get("type") == "intent" and record.get("kind") == mutation_engine.FOLDER_RENAME
    }
    completed = [
        record for record in records
        if record.get("type") == "completed" and record.get("kind") == mutation_engine.FOLDER_RENAME
    ]
    return intents, completed


def undo_folder_renames(
    *,
    journal_path: Path,
    serato_dir: Path | None,
    sqlite_dbs: list[Path],
    heartbeat,
    undo_journal_path: Path,
    result_path: Path,
    process_lister=None,
) -> dict:
    """Rename every completed folder back, newest first, from an apply journal."""
    serato_rewrite.require_serato_closed(process_lister)
    intents, completed = _journal_folder_items(journal_path)
    if not completed:
        raise FolderRenameError(f"journal records no completed folder renames: {journal_path}")

    operations = []
    for record in reversed(completed):
        intent = intents[(record.get("run_id"), record.get("index"))]
        operations.append({
            "from_path": Path(record["destination"]),
            "to_path": Path(record["source"]),
            "serato_prefix": intent.get("serato_to"),
            "inventory": {
                "sha256": intent["inventory"]["sha256"],
                "hash_contents": intent["inventory"]["hash_contents"],
            },
            "label": f"{intent.get('to')} -> {intent.get('from')}",
        })

    prefixes = [op["serato_prefix"] for op in operations if op["serato_prefix"]]
    references = serato_references(serato_dir, [Path(p) for p in sqlite_dbs], prefixes)
    if any(references.values()):
        raise FolderRenameError(
            "Serato still references the renamed folder(s); restore database V2, the crates and "
            f"the SQLite libraries from the pre-rename backup first. References: {references}"
        )

    engine = mutation_engine.MutationEngine(
        Path(undo_journal_path), heartbeat, None, None, f"{PLAN_TYPE}-undo", allow_unbacked=True
    )
    engine.start({"plan_type": f"{PLAN_TYPE}-undo", "source_journal": str(journal_path), "folders": len(operations)})
    restored = []
    for index, operation in enumerate(operations, start=1):
        with engine.folder_item(
            index,
            operation["from_path"],
            operation["to_path"],
            expected_inventory=operation["inventory"],
            extra_intent={"plan_type": f"{PLAN_TYPE}-undo", "label": operation["label"]},
        ) as txn:
            txn.rename_dir_no_overwrite(operation["from_path"], operation["to_path"], purpose="folder_rename_undo")
        restored.append(operation["label"])
    result = {
        "status": "PASS",
        "folders_restored": len(restored),
        "restored": restored,
        "serato_references_checked": references,
        "journal": str(undo_journal_path),
    }
    engine.finish(result)
    serato_rewrite.write_json(result_path, result)
    return result


# --------------------------------------------------------------------- recover

def plan_recovery(journal_path: Path) -> list[dict]:
    """Where each in-flight folder rename's folder actually is right now."""
    report = mutation_engine.recover(Path(journal_path))
    intents, _ = _journal_folder_items(journal_path) if report["torn_records"] == 0 else ({}, [])
    actions = []
    for item in report["in_flight"]:
        if item.get("kind") != mutation_engine.FOLDER_RENAME:
            continue
        source, destination = Path(item["source"]), Path(item["destination"])
        temporary = item.get("case_rename_temporary_path")
        names = mutation_engine.literal_entry_names(source.parent)
        located, state = None, None
        if source.name in names:
            state = "AT_SOURCE"
        elif temporary and Path(temporary).name in names:
            located, state = Path(temporary), "AT_TEMPORARY"
        elif destination.name in names:
            located, state = destination, "AT_DESTINATION"
        else:
            state = "MISSING"
        intent = intents.get((item["run_id"], item["index"]))
        actions.append({
            "run_id": item["run_id"],
            "index": item["index"],
            "journal_state": item["state"],
            "state": state,
            "source": str(source),
            "located_at": str(located) if located else None,
            "inventory": (intent or {}).get("inventory"),
        })
    return actions


def recover_folder_renames(
    *,
    journal_path: Path,
    heartbeat,
    recovery_journal_path: Path,
    result_path: Path,
    apply: bool,
    process_lister=None,
) -> dict:
    """Put every interrupted folder back under its original name.

    Dry-run unless ``apply``.  Rolling BACK (not forward) restores the one state
    the plan was validated against; re-run the plan afterwards if still wanted.
    """
    actions = plan_recovery(journal_path)
    lost = [action for action in actions if action["state"] == "MISSING"]
    if lost:
        raise FolderRenameError(
            f"folder missing from its source, temporary and destination names: {lost}; needs a human"
        )
    pending = [action for action in actions if action["state"] in {"AT_TEMPORARY", "AT_DESTINATION"}]
    if not apply:
        return {"status": "DRY_RUN", "actions": actions}

    serato_rewrite.require_serato_closed(process_lister)
    engine = mutation_engine.MutationEngine(
        Path(recovery_journal_path), heartbeat, None, None, f"{PLAN_TYPE}-recover", allow_unbacked=True
    )
    engine.start({"plan_type": f"{PLAN_TYPE}-recover", "source_journal": str(journal_path), "folders": len(pending)})
    original = mutation_engine.RecoveryJournal(Path(journal_path))
    restored = []
    for index, action in enumerate(pending, start=1):
        if not action["inventory"]:
            raise FolderRenameError(f"journal has no inventory for run {action['run_id']} item {action['index']}")
        located, source = Path(action["located_at"]), Path(action["source"])
        with engine.folder_item(
            index,
            located,
            source,
            expected_inventory={
                "sha256": action["inventory"]["sha256"],
                "hash_contents": action["inventory"]["hash_contents"],
            },
            extra_intent={"plan_type": f"{PLAN_TYPE}-recover", "was": action["state"]},
        ) as txn:
            txn.rename_dir_no_overwrite(located, source, purpose="folder_rename_recover")
        # Settle the interrupted item in the ORIGINAL journal so recover() is clean.
        original.append({
            "type": "rolled_back",
            "run_id": action["run_id"],
            "index": action["index"],
            "source": action["source"],
            "reason": f"restored by apply_folder_rename_plan recover ({action['state']})",
            "restored": True,
            "rollback_error": None,
        })
        restored.append(action["source"])
    for action in actions:
        if action["state"] == "AT_SOURCE":
            original.append({
                "type": "rolled_back",
                "run_id": action["run_id"],
                "index": action["index"],
                "source": action["source"],
                "reason": "folder still at its original name; nothing to restore",
                "restored": True,
                "rollback_error": None,
            })
    result = {"status": "PASS", "restored": restored, "actions": actions, "journal": str(recovery_journal_path)}
    engine.finish(result)
    serato_rewrite.write_json(result_path, result)
    return result


# ------------------------------------------------------------------------- CLI

def _add_lock_arguments(parser: argparse.ArgumentParser, *, session_required: bool = True) -> None:
    parser.add_argument("--lock-folder", action="append", default=[], dest="lock_folders",
                        help="repeat once per folder in the plan's required_lock_folders")
    parser.add_argument("--session", required=session_required)
    parser.add_argument(
        "--confirm-serato-closed", action="store_true",
        help="required acknowledgement that Serato is not running",
    )


def _gate_serato_closed(args) -> None:
    if not args.confirm_serato_closed:
        raise FolderRenameError("this command requires --confirm-serato-closed; quit Serato first")
    serato_rewrite.require_serato_closed()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview = subparsers.add_parser("preview", help="read-only: check literal entries, fingerprint, derive Serato map")
    preview.add_argument("--library-root", required=True, type=Path)
    preview.add_argument("--folder-map", required=True, type=Path, help='JSON {"R&b": "R&B", ...} relative to the library root')
    preview.add_argument("--out", required=True, type=Path)
    preview.add_argument("--serato-rename-map-out", type=Path)
    preview.add_argument("--hash-contents", action="store_true", help="fingerprint with SHA-256 of every file (slow)")
    preview.add_argument("--serato-dir", type=Path, help="also report database V2 / crate impact (read-only)")
    preview.add_argument("--sqlite-db", action="append", type=Path, default=[], help="also report SQLite impact (read-only)")

    apply_parser = subparsers.add_parser("apply", help="rename the folders under lock + verified Serato backup")
    apply_parser.add_argument("--library-root", required=True, type=Path)
    apply_parser.add_argument("--plan", required=True, type=Path)
    apply_parser.add_argument("--serato-dir", required=True, type=Path)
    apply_parser.add_argument("--backup-manifest", required=True, type=Path, help="PASS manifest whose source root is the Serato dir")
    apply_parser.add_argument("--sqlite-db", action="append", type=Path, default=[])
    apply_parser.add_argument("--sqlite-backup-manifest", type=Path, help="PASS manifest whose source root is the SQLite dir")
    apply_parser.add_argument("--journal", required=True, type=Path)
    apply_parser.add_argument("--out", required=True, type=Path)
    apply_parser.add_argument("--serato-rename-map-out", required=True, type=Path)
    _add_lock_arguments(apply_parser)

    undo = subparsers.add_parser("undo", help="rename completed folders back, from an apply journal")
    undo.add_argument("--journal", required=True, type=Path, help="the apply run's recovery journal (its undo log)")
    undo.add_argument("--serato-dir", required=True, type=Path)
    undo.add_argument("--sqlite-db", action="append", type=Path, default=[])
    undo.add_argument("--undo-journal", required=True, type=Path)
    undo.add_argument("--out", required=True, type=Path)
    _add_lock_arguments(undo)

    recover = subparsers.add_parser("recover", help="restore folders left mid-rename by a crashed apply")
    recover.add_argument("--journal", required=True, type=Path)
    recover.add_argument("--recovery-journal", type=Path)
    recover.add_argument("--out", type=Path)
    recover.add_argument("--apply", action="store_true", help="perform the restore (default: dry run)")
    _add_lock_arguments(recover, session_required=False)

    args = parser.parse_args()
    try:
        if args.command == "preview":
            entries = load_folder_map(args.folder_map)
            plan = build_preview(
                args.library_root, entries, hash_contents=args.hash_contents,
                serato_dir=args.serato_dir, sqlite_dbs=args.sqlite_db,
            )
            serato_rewrite.write_json(args.out, plan)
            if args.serato_rename_map_out:
                serato_rewrite.write_json(args.serato_rename_map_out, plan["serato_rename_map"])
            print(json.dumps({"status": "PREVIEW", **plan["summary"], "plan": str(args.out),
                              "required_lock_folders": plan["required_lock_folders"],
                              "serato_impact": plan.get("serato_impact")}, indent=2, ensure_ascii=False))
            return 0

        if args.command != "recover" or args.apply:
            _gate_serato_closed(args)
            if not args.session:
                raise FolderRenameError("--session is required")
        if args.command == "apply":
            plan = load_and_validate_plan(args.plan, args.library_root)
            heartbeat = open_heartbeat(args.lock_folders, args.session, plan["required_lock_folders"])
            manifest = backup_manifest.require_pass(args.backup_manifest, args.serato_dir)
            extra_guards = []
            if args.sqlite_db:
                if args.sqlite_backup_manifest is None:
                    raise FolderRenameError("--sqlite-db requires --sqlite-backup-manifest")
                sqlite_root = Path(args.sqlite_db[0]).resolve().parent
                sqlite_manifest = backup_manifest.require_pass(args.sqlite_backup_manifest, sqlite_root)
                extra_guards = [
                    {"path": Path(db).resolve(), "manifest": sqlite_manifest, "source_root": sqlite_root}
                    for db in args.sqlite_db
                ]
            result = apply_folder_renames(
                plan=plan, serato_dir=args.serato_dir, manifest=manifest, heartbeat=heartbeat,
                journal_path=args.journal, result_path=args.out,
                rename_map_path=args.serato_rename_map_out, extra_guards=extra_guards,
            )
        elif args.command == "undo":
            intents, completed = _journal_folder_items(args.journal)
            heartbeat = open_heartbeat(
                args.lock_folders, args.session,
                lock_names_for([intents[(r.get("run_id"), r.get("index"))] for r in completed]),
            )
            result = undo_folder_renames(
                journal_path=args.journal, serato_dir=args.serato_dir, sqlite_dbs=args.sqlite_db,
                heartbeat=heartbeat, undo_journal_path=args.undo_journal, result_path=args.out,
            )
        else:
            heartbeat = None
            if args.apply:
                intents, _ = _journal_folder_items(args.journal)
                heartbeat = open_heartbeat(args.lock_folders, args.session, lock_names_for(list(intents.values())))
            result = recover_folder_renames(
                journal_path=args.journal, heartbeat=heartbeat,
                recovery_journal_path=args.recovery_journal or args.journal.with_suffix(".recovery.jsonl"),
                result_path=args.out or args.journal.with_suffix(".recovery-result.json"),
                apply=args.apply,
            )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (
        FolderRenameError,
        serato_rewrite.SeratoRewriteError,
        backup_manifest.BackupGateError,
        mutation_engine.MutationRefused,
        mutation_engine.MutationVerificationError,
    ) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
