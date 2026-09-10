#!/usr/bin/env python3
"""Preview and apply explicit path rewrites in Serato's database and crates.

The input is a reviewed rename map.  No path is inferred from filenames or
from the library tree: a stored Serato path changes only when it equals an
old path or starts with an old directory prefix at a path-component boundary.

``preview`` is read-only and produces a hash-bound JSON diff.  ``apply``
requires that exact plan, an owned coordination lock, a verified backup
manifest covering every Serato file it will change, and an explicit
confirmation that Serato is closed.  Every changed binary is written through
``mutation_engine`` as its own recoverable transaction.  The final post-check
uses ``check_serato_crate_paths`` and fails if any rewritten target is missing
or any mapped old reference remains.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "engine-user"

# The helper owns only a uniquely named temporary file beside the target.  It
# is called only from a mutation_engine item transaction; review it alongside
# the call site whenever this module changes.
ENGINE_MUTATION_HELPERS = ("_atomic_replace",)

import argparse
import hashlib
import json
import os
import stat
import struct
import sys
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

import backup_manifest
import check_serato_crate_paths
import lock_guard
import mutation_engine

PLAN_TYPE = "serato-path-rewrite"
PLAN_VERSION = 1
DATABASE_NAME = "database V2"
CRATE_GLOB = "Subcrates/*.crate"
PATH_FIELDS = {DATABASE_NAME: "pfil", ".crate": "ptrk"}


class SeratoRewriteError(RuntimeError):
    """The preview or apply could not prove that a rewrite is safe."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _literal_stored_path_is_file(serato_path: str) -> bool:
    """Resolve a Serato path, then require its exact on-disk spelling."""
    return mutation_engine.literal_path_is_file(
        check_serato_crate_paths.resolve(serato_path)
    )


def _normalize_map_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SeratoRewriteError(f"{label} must be a non-empty string")
    if "\x00" in value:
        raise SeratoRewriteError(f"{label} contains a NUL byte")
    if "\\" in value:
        raise SeratoRewriteError(
            f"{label} uses backslashes; Serato paths on this library must use '/'"
        )
    # Serato stores paths relative to the volume root.  Accept an absolute
    # spelling in a human-authored map, but canonicalize the plan to Serato's
    # no-leading-slash representation.
    normalized = value.lstrip("/").rstrip("/")
    if not normalized:
        raise SeratoRewriteError(f"{label} resolves to the volume root")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SeratoRewriteError(f"{label} is not a safe normalized path: {value!r}")
    return normalized


def _raw_rename_entries(payload: object) -> list[object]:
    if isinstance(payload, dict) and "renames" in payload:
        entries = payload["renames"]
        if not isinstance(entries, list):
            raise SeratoRewriteError("rename-map 'renames' must be a list")
        return entries
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [{"from": old, "to": new} for old, new in payload.items()]
    raise SeratoRewriteError(
        "rename map must be an object mapping old->new, a list of {from,to}, "
        "or an object with a 'renames' list"
    )


def normalize_rename_map(payload: object) -> list[dict]:
    """Validate a human rename map and return deterministic longest-first rules."""
    rules = []
    seen_old: dict[str, int] = {}
    seen_indexes: set[int] = set()
    raw_entries = _raw_rename_entries(payload)
    has_explicit_indexes = [
        isinstance(raw, dict) and "source_index" in raw for raw in raw_entries
    ]
    if any(has_explicit_indexes) and not all(has_explicit_indexes):
        raise SeratoRewriteError("rename entries must either all carry source_index or none may")
    for default_index, raw in enumerate(raw_entries, start=1):
        if not isinstance(raw, dict):
            raise SeratoRewriteError(f"rename entry {default_index} must be an object")
        source_index = raw.get("source_index", default_index)
        if not isinstance(source_index, int) or isinstance(source_index, bool) or source_index < 1:
            raise SeratoRewriteError(
                f"rename entry {default_index} has invalid source_index {source_index!r}"
            )
        if source_index in seen_indexes:
            raise SeratoRewriteError(f"duplicate rename source_index {source_index}")
        seen_indexes.add(source_index)
        old = _normalize_map_path(raw.get("from"), f"rename {source_index} 'from'")
        new = _normalize_map_path(raw.get("to"), f"rename {source_index} 'to'")
        if old == new:
            raise SeratoRewriteError(f"rename {source_index} does not change the path: {old}")
        if old in seen_old:
            raise SeratoRewriteError(
                f"duplicate rename source {old!r} in entries {seen_old[old]} and {source_index}"
            )
        seen_old[old] = source_index
        rules.append({"from": old, "to": new, "source_index": source_index})

    if not rules:
        raise SeratoRewriteError("rename map contains no entries")

    # Chained maps are unsafe for an idempotent apply.  Folder merges may
    # share a destination, and nested source prefixes are deterministic via
    # longest-match precedence, but no destination may itself be another
    # source path/prefix.
    for rule in rules:
        for candidate in rules:
            old = candidate["from"]
            new = rule["to"]
            if new == old or new.startswith(old + "/"):
                raise SeratoRewriteError(
                    f"rename destination {new!r} is inside mapped source {old!r}; "
                    "split chained/nested moves into separate reviewed plans"
                )

    rules.sort(key=lambda rule: (-len(rule["from"]), rule["source_index"]))
    return rules


def load_rename_map(path: Path) -> list[dict]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SeratoRewriteError(f"rename map not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SeratoRewriteError(f"rename map is not valid JSON: {path}: {exc}") from exc
    return normalize_rename_map(payload)


def rewrite_path(path: str, rules: list[dict]) -> tuple[str, dict | None]:
    """Apply exactly one longest matching rule at a component boundary."""
    for rule in rules:
        old = rule["from"]
        if path == old or path.startswith(old + "/"):
            return rule["to"] + path[len(old):], rule
    return path, None


def parse_tlv_strict(data: bytes, context: str) -> list[tuple[str, bytes]]:
    """Parse a complete TLV container; reject truncation and trailing bytes."""
    records: list[tuple[str, bytes]] = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < 8:
            raise SeratoRewriteError(
                f"{context}: {len(data) - offset} trailing byte(s) at offset {offset}"
            )
        raw_tag = data[offset:offset + 4]
        try:
            tag = raw_tag.decode("ascii")
        except UnicodeDecodeError as exc:
            raise SeratoRewriteError(f"{context}: non-ASCII TLV tag at offset {offset}") from exc
        length = struct.unpack(">I", data[offset + 4:offset + 8])[0]
        end = offset + 8 + length
        if end > len(data):
            raise SeratoRewriteError(
                f"{context}: truncated {tag!r} value at offset {offset}; "
                f"declares {length} byte(s), only {len(data) - offset - 8} remain"
            )
        records.append((tag, data[offset + 8:end]))
        offset = end
    return records


def encode_tlv(tag: str, value: bytes) -> bytes:
    if len(tag) != 4 or not tag.isascii():
        raise SeratoRewriteError(f"invalid TLV tag: {tag!r}")
    if len(value) > 0xFFFFFFFF:
        raise SeratoRewriteError(f"TLV value is too large for tag {tag!r}")
    return tag.encode("ascii") + struct.pack(">I", len(value)) + value


def _decode_path_value(value: bytes, context: str) -> tuple[str, str]:
    if len(value) % 2:
        raise SeratoRewriteError(f"{context}: UTF-16BE path has an odd byte length")
    try:
        decoded = value.decode("utf-16-be")
    except UnicodeDecodeError as exc:
        raise SeratoRewriteError(f"{context}: invalid UTF-16BE path") from exc
    terminator_length = len(decoded) - len(decoded.rstrip("\x00"))
    path = decoded[:-terminator_length] if terminator_length else decoded
    terminator = "\x00" * terminator_length
    if not path:
        raise SeratoRewriteError(f"{context}: empty Serato path")
    if path.startswith("/"):
        raise SeratoRewriteError(
            f"{context}: stored path unexpectedly has a leading slash: {path!r}"
        )
    return path, terminator


def _field_for(relative_path: str) -> str:
    if relative_path == DATABASE_NAME:
        return PATH_FIELDS[DATABASE_NAME]
    if relative_path.endswith(".crate"):
        return PATH_FIELDS[".crate"]
    raise SeratoRewriteError(f"unsupported Serato source file: {relative_path}")


def rewrite_serato_bytes(
    data: bytes, relative_path: str, rules: list[dict]
) -> tuple[bytes, list[dict]]:
    """Return rewritten bytes plus an occurrence-level dry-run diff."""
    target_field = _field_for(relative_path)
    outer = parse_tlv_strict(data, relative_path)
    rebuilt_outer: list[bytes] = []
    changes: list[dict] = []
    track_number = 0

    for outer_index, (tag, value) in enumerate(outer):
        if tag != "otrk":
            rebuilt_outer.append(encode_tlv(tag, value))
            continue
        track_number += 1
        inner = parse_tlv_strict(value, f"{relative_path}:otrk[{track_number}]")
        rebuilt_inner: list[bytes] = []
        for field_index, (inner_tag, inner_value) in enumerate(inner):
            if inner_tag != target_field:
                rebuilt_inner.append(encode_tlv(inner_tag, inner_value))
                continue
            context = f"{relative_path}:otrk[{track_number}].{target_field}"
            old_path, terminator = _decode_path_value(inner_value, context)
            new_path, rule = rewrite_path(old_path, rules)
            if rule is None:
                rebuilt_inner.append(encode_tlv(inner_tag, inner_value))
                continue
            new_value = (new_path + terminator).encode("utf-16-be")
            rebuilt_inner.append(encode_tlv(inner_tag, new_value))
            changes.append({
                "field": target_field,
                "track_number": track_number,
                "outer_record_index": outer_index,
                "field_index": field_index,
                "from": old_path,
                "to": new_path,
                "rule_source_index": rule["source_index"],
            })
        rebuilt_outer.append(encode_tlv(tag, b"".join(rebuilt_inner)))

    rewritten = b"".join(rebuilt_outer)
    if not changes and rewritten != data:
        raise SeratoRewriteError(
            f"{relative_path}: strict round-trip changed bytes without a path rewrite"
        )
    return rewritten, changes


def serato_source_files(serato_dir: Path) -> list[Path]:
    serato_dir = Path(serato_dir)
    if serato_dir.is_symlink() or not serato_dir.is_dir():
        raise SeratoRewriteError(f"Serato root is missing, not a directory, or a symlink: {serato_dir}")
    database = serato_dir / DATABASE_NAME
    if database.is_symlink() or not database.is_file():
        raise SeratoRewriteError(f"missing or unsafe {DATABASE_NAME}: {database}")
    crates_dir = serato_dir / "Subcrates"
    crates = []
    if crates_dir.is_dir():
        crates = sorted(crates_dir.glob("*.crate"))
        unsafe = [path for path in crates if path.is_symlink() or not path.is_file()]
        if unsafe:
            raise SeratoRewriteError(f"unsafe crate path(s): {unsafe[:3]}")
    return [database, *crates]


def build_preview(serato_dir: Path, rules: list[dict]) -> dict:
    """Build a read-only plan bound to every database/crate source hash."""
    serato_dir = Path(serato_dir).resolve()
    inventory = []
    changed_files = []
    rule_counts: Counter[int] = Counter()
    field_counts: Counter[str] = Counter()

    for source in serato_source_files(serato_dir):
        relative = str(source.relative_to(serato_dir))
        original = source.read_bytes()
        rewritten, changes = rewrite_serato_bytes(original, relative, rules)
        inventory.append({
            "path": relative,
            "bytes": len(original),
            "sha256": sha256_bytes(original),
        })
        if not changes:
            continue
        for change in changes:
            rule_counts[change["rule_source_index"]] += 1
            field_counts[change["field"]] += 1
        changed_files.append({
            "path": relative,
            "source_sha256": sha256_bytes(original),
            "proposed_sha256": sha256_bytes(rewritten),
            "source_bytes": len(original),
            "proposed_bytes": len(rewritten),
            "rewrites": changes,
        })

    normalized_rules = [dict(rule) for rule in rules]
    used = set(rule_counts)
    summary = {
        "source_files": len(inventory),
        "files_changed": len(changed_files),
        "references_changed": sum(rule_counts.values()),
        "database_references_changed": sum(
            len(item["rewrites"]) for item in changed_files if item["path"] == DATABASE_NAME
        ),
        "crate_references_changed": sum(
            len(item["rewrites"]) for item in changed_files if item["path"] != DATABASE_NAME
        ),
        "field_counts": dict(sorted(field_counts.items())),
        "rule_counts": {
            str(index): rule_counts.get(index, 0)
            for index in sorted(rule["source_index"] for rule in normalized_rules)
        },
        "unused_rule_source_indexes": sorted(
            rule["source_index"] for rule in normalized_rules
            if rule["source_index"] not in used
        ),
    }
    return {
        "plan_type": PLAN_TYPE,
        "plan_version": PLAN_VERSION,
        "mode": "preview-only",
        "approval_required": True,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "serato_dir": str(serato_dir),
        "rename_map_sha256": sha256_bytes(_canonical_json(normalized_rules)),
        "renames": normalized_rules,
        "source_inventory": inventory,
        "files": changed_files,
        "summary": summary,
    }


def write_json(path: Path, payload: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _relative_file_set(plan: dict) -> set[str]:
    return {str(item.get("path")) for item in plan.get("source_inventory", [])}


def load_and_validate_plan(
    plan_path: Path,
    serato_dir: Path,
    *,
    require_targets: bool = False,
) -> dict:
    try:
        plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SeratoRewriteError(f"plan not found: {plan_path}") from exc
    except json.JSONDecodeError as exc:
        raise SeratoRewriteError(f"plan is not valid JSON: {plan_path}: {exc}") from exc
    if not isinstance(plan, dict):
        raise SeratoRewriteError("plan must be a JSON object")
    if plan.get("plan_type") != PLAN_TYPE or plan.get("plan_version") != PLAN_VERSION:
        raise SeratoRewriteError("unexpected Serato rewrite plan type/version")
    if plan.get("mode") != "preview-only" or plan.get("approval_required") is not True:
        raise SeratoRewriteError("plan is not an approval-required preview")

    serato_dir = Path(serato_dir).resolve()
    if Path(plan.get("serato_dir", "")).resolve() != serato_dir:
        raise SeratoRewriteError(
            f"plan targets {plan.get('serato_dir')!r}, not requested Serato root {str(serato_dir)!r}"
        )
    rules = normalize_rename_map(plan.get("renames"))
    if rules != plan.get("renames"):
        raise SeratoRewriteError("plan rename rules are not in canonical longest-first form")
    if plan.get("rename_map_sha256") != sha256_bytes(_canonical_json(rules)):
        raise SeratoRewriteError("plan rename-map digest does not match its rules")

    current_paths = serato_source_files(serato_dir)
    current_relatives = {str(path.relative_to(serato_dir)) for path in current_paths}
    if current_relatives != _relative_file_set(plan):
        added = sorted(current_relatives - _relative_file_set(plan))
        removed = sorted(_relative_file_set(plan) - current_relatives)
        raise SeratoRewriteError(
            f"Serato source inventory changed since preview; added={added}, removed={removed}"
        )

    # Rebuild the complete preview from current bytes.  This checks hashes for
    # changed AND unchanged crates, exact occurrence diffs, proposed hashes,
    # and summary counts rather than trusting editable plan fields.
    rebuilt = build_preview(serato_dir, rules)
    for field in ("renames", "rename_map_sha256", "source_inventory", "files", "summary"):
        if rebuilt[field] != plan.get(field):
            raise SeratoRewriteError(
                f"plan field {field!r} no longer matches current Serato sources; rebuild preview"
            )

    if require_targets:
        missing_targets = sorted({
            change["to"]
            for item in rebuilt["files"]
            for change in item["rewrites"]
            if not _literal_stored_path_is_file(change["to"])
        })
        if missing_targets:
            raise SeratoRewriteError(
                "rewritten target path(s) do not exist; move/rename the library files first: "
                + ", ".join(repr(path) for path in missing_targets[:10])
            )
    return plan


def _atomic_replace(txn, path: Path, content: bytes, expected_sha256: str) -> None:
    """Durably replace one Serato binary, cleaning only our unique temp file."""
    path = Path(path)
    temporary = path.with_name(f".{path.name}.serato-rewrite-{uuid.uuid4().hex}.tmp")
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(str(temporary), flags, stat.S_IMODE(path.stat().st_mode))
        try:
            os.fchmod(descriptor, stat.S_IMODE(path.stat().st_mode))
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            # fdopen owns the descriptor after construction.
            raise
        if sha256_file(path) != expected_sha256:
            raise mutation_engine.MutationRefused(
                f"Serato source changed between transaction precondition and replace: {path}"
            )
        os.replace(temporary, path)
        txn.mark_mutated()
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _all_stored_paths(serato_dir: Path) -> list[str]:
    paths: list[str] = []
    for source in serato_source_files(serato_dir):
        relative = str(source.relative_to(serato_dir))
        target_field = _field_for(relative)
        for tag, value in parse_tlv_strict(source.read_bytes(), relative):
            if tag != "otrk":
                continue
            for inner_tag, inner_value in parse_tlv_strict(value, f"{relative}:otrk"):
                if inner_tag == target_field:
                    stored, _ = _decode_path_value(inner_value, f"{relative}:{target_field}")
                    paths.append(stored)
    return paths


def run_post_check(
    serato_dir: Path,
    library_root: Path,
    plan: dict,
) -> dict:
    """Run the standard checker plus rewrite-specific completeness checks."""
    serato_dir = Path(serato_dir).resolve()
    library_root = Path(library_root).resolve()
    audit = check_serato_crate_paths.audit(serato_dir, library_root)
    expected_targets = sorted({
        change["to"] for item in plan["files"] for change in item["rewrites"]
    })
    unresolved = {
        entry["path"]
        for category in ("moved_likely", "moved_ambiguous", "missing")
        for entry in audit[category]
    }
    stored_paths = _all_stored_paths(serato_dir)
    remaining_mapped_sources = sorted({
        path for path in stored_paths if rewrite_path(path, plan["renames"])[1] is not None
    })
    unresolved_targets = sorted(set(expected_targets) & unresolved)
    absent_targets = sorted(
        path for path in expected_targets
        if not _literal_stored_path_is_file(path)
    )
    expected_hashes = {
        item["path"]: item["sha256"] for item in plan["source_inventory"]
    }
    expected_hashes.update({
        item["path"]: item["proposed_sha256"] for item in plan["files"]
    })
    current_files = serato_source_files(serato_dir)
    current_relatives = {str(path.relative_to(serato_dir)): path for path in current_files}
    added_sources = sorted(set(current_relatives) - set(expected_hashes))
    removed_sources = sorted(set(expected_hashes) - set(current_relatives))
    changed_sources = sorted(
        relative for relative in set(expected_hashes) & set(current_relatives)
        if sha256_file(current_relatives[relative]) != expected_hashes[relative]
    )
    source_integrity = {
        "added": added_sources,
        "removed": removed_sources,
        "hash_mismatch": changed_sources,
        "status": (
            "PASS" if not added_sources and not removed_sources and not changed_sources else "FAIL"
        ),
    }
    rewrite_verification = {
        "expected_target_count": len(expected_targets),
        "unresolved_rewritten_targets": unresolved_targets,
        "absent_rewritten_targets": absent_targets,
        "remaining_mapped_source_references": remaining_mapped_sources,
        "status": (
            "PASS"
            if (
                not unresolved_targets
                and not absent_targets
                and not remaining_mapped_sources
                and source_integrity["status"] == "PASS"
            )
            else "FAIL"
        ),
    }
    return {
        "checker": audit,
        "source_integrity": source_integrity,
        "rewrite_verification": rewrite_verification,
    }


def apply_rewrite_plan(
    *,
    plan: dict,
    serato_dir: Path,
    library_root: Path,
    manifest: dict,
    heartbeat,
    journal_path: Path,
    post_check_path: Path,
    result_path: Path,
) -> dict:
    """Apply an already validated plan through one engine transaction per file."""
    serato_dir = Path(serato_dir).resolve()
    library_root = Path(library_root).resolve()
    if not library_root.is_dir():
        raise SeratoRewriteError(f"library root is not a directory: {library_root}")
    if not plan["files"]:
        raise SeratoRewriteError("plan contains no Serato references to rewrite")

    engine = mutation_engine.MutationEngine(
        Path(journal_path), heartbeat, manifest, serato_dir, PLAN_TYPE
    )
    engine.start({
        "plan_type": PLAN_TYPE,
        "files": len(plan["files"]),
        "references": plan["summary"]["references_changed"],
        "rename_map_sha256": plan["rename_map_sha256"],
    })
    completed = []
    for index, item in enumerate(plan["files"], start=1):
        source = serato_dir / item["path"]
        original = source.read_bytes()
        rewritten, changes = rewrite_serato_bytes(original, item["path"], plan["renames"])
        if changes != item["rewrites"] or sha256_bytes(rewritten) != item["proposed_sha256"]:
            raise SeratoRewriteError(f"rewrite drift immediately before apply: {source}")
        backup_path = Path(manifest["backup_root"]) / item["path"]
        with engine.item(
            index,
            kind=mutation_engine.SERATO_PATHS,
            source=source,
            expected_sha256=item["source_sha256"],
            extra_intent={
                "relative_path": item["path"],
                "proposed_sha256": item["proposed_sha256"],
                "reference_rewrites": len(changes),
            },
        ) as txn:
            txn.on_rollback(
                lambda source=source, backup_path=backup_path,
                expected=item["source_sha256"]: mutation_engine.restore_from_backup(
                    backup_path, source, expected
                )
            )
            _atomic_replace(txn, source, rewritten, item["source_sha256"])
            actual = source.read_bytes()
            if sha256_bytes(actual) != item["proposed_sha256"]:
                raise mutation_engine.MutationVerificationError(
                    f"post-write hash mismatch: {source}"
                )
            # A second rewrite must be a byte-identical no-op.  This proves
            # both that every planned old reference disappeared and that no
            # destination accidentally chains back into the map.
            second_pass, remaining = rewrite_serato_bytes(actual, item["path"], plan["renames"])
            if remaining or second_pass != actual:
                raise mutation_engine.MutationVerificationError(
                    f"post-write Serato rewrite is not idempotent: {source}"
                )
            missing = sorted({
                change["to"] for change in changes
                if not _literal_stored_path_is_file(change["to"])
            })
            if missing:
                raise mutation_engine.MutationVerificationError(
                    f"post-write target path(s) disappeared: {missing[:5]}"
                )
            txn.record(
                relative_path=item["path"],
                after_sha256=item["proposed_sha256"],
                reference_rewrites=len(changes),
            )
        completed.append({
            "path": item["path"],
            "before_sha256": item["source_sha256"],
            "after_sha256": item["proposed_sha256"],
            "reference_rewrites": len(changes),
        })

    post_check = run_post_check(serato_dir, library_root, plan)
    write_json(post_check_path, post_check)
    if post_check["rewrite_verification"]["status"] != "PASS":
        raise SeratoRewriteError(
            f"Serato post-check failed; inspect {post_check_path}. "
            "Completed items remain journaled and recoverable from the verified backup."
        )
    result = {
        "status": "PASS",
        "plan_type": PLAN_TYPE,
        "serato_dir": str(serato_dir),
        "library_root": str(library_root),
        "files_changed": len(completed),
        "references_changed": sum(item["reference_rewrites"] for item in completed),
        "completed": completed,
        "post_check": str(post_check_path),
        "journal": str(journal_path),
    }
    engine.finish(result)
    write_json(result_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview_parser = subparsers.add_parser("preview", help="build a read-only, hash-bound rewrite diff")
    preview_parser.add_argument("--serato-dir", required=True, type=Path)
    preview_parser.add_argument("--rename-map", required=True, type=Path)
    preview_parser.add_argument("--out", required=True, type=Path)

    apply_parser = subparsers.add_parser("apply", help="apply a reviewed preview under lock+backup")
    apply_parser.add_argument("--serato-dir", required=True, type=Path)
    apply_parser.add_argument("--library-root", required=True, type=Path)
    apply_parser.add_argument("--plan", required=True, type=Path)
    apply_parser.add_argument("--backup-manifest", required=True, type=Path)
    apply_parser.add_argument("--lock-folder", default="Serato")
    apply_parser.add_argument("--session", required=True)
    apply_parser.add_argument("--journal", required=True, type=Path)
    apply_parser.add_argument("--post-check-out", required=True, type=Path)
    apply_parser.add_argument("--out", required=True, type=Path)
    apply_parser.add_argument(
        "--confirm-serato-closed",
        action="store_true",
        help="required acknowledgement that Serato is not running during the rewrite",
    )
    args = parser.parse_args()

    try:
        if args.command == "preview":
            rules = load_rename_map(args.rename_map)
            plan = build_preview(args.serato_dir, rules)
            write_json(args.out, plan)
            print(json.dumps({"status": "PREVIEW", **plan["summary"], "plan": str(args.out)}, indent=2))
            return 0

        if not args.confirm_serato_closed:
            raise SeratoRewriteError(
                "apply requires --confirm-serato-closed; close Serato before changing its binary files"
            )
        plan = load_and_validate_plan(args.plan, args.serato_dir, require_targets=True)
        heartbeat, manifest = mutation_engine.run_preflight(
            lock_folder=args.lock_folder,
            session=args.session,
            manifest_path=args.backup_manifest,
            source_root=args.serato_dir,
        )
        result = apply_rewrite_plan(
            plan=plan,
            serato_dir=args.serato_dir,
            library_root=args.library_root,
            manifest=manifest,
            heartbeat=heartbeat,
            journal_path=args.journal,
            post_check_path=args.post_check_out,
            result_path=args.out,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (SeratoRewriteError, backup_manifest.BackupGateError, mutation_engine.MutationRefused) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
