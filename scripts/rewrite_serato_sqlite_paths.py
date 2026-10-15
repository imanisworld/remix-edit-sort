#!/usr/bin/env python3
"""Preview and apply explicit path rewrites in Serato's SQLite libraries.

``rewrite_serato_paths.py`` covers the legacy ``database V2`` and ``*.crate``
binaries.  Current Serato also keeps every track path in SQLite --
``~/Library/Application Support/Serato/Library/master.sqlite`` and
``root.sqlite`` each carry an ``asset`` table whose ``portable_id`` column is
the same volume-relative path string (``Users/<name>/Music/...``).  Renaming a
library folder without rewriting those leaves Serato with rows that point at a
folder that no longer exists.

The input is the SAME reviewed rename map the binary rewriter takes
(old path or folder prefix -> new, longest source prefix wins, matched exactly
and case-sensitively at a path-component boundary).  Nothing is inferred.

``preview`` is read-only: it opens each database ``immutable`` (so SQLite
cannot create ``-wal``/``-shm`` sidecars next to Serato's files), refuses a
database with an un-checkpointed WAL or hot journal, and writes a plan that
lists every affected ``rowid`` with its before/after path, the file's SHA-256,
and a digest of every other cell in the database.

``apply`` requires that plan, Serato closed (both an acknowledgement flag and a
process check that fails closed), an owned coordination lock, and a PASS backup
manifest covering each database.  Each database is one ``mutation_engine``
transaction: the engine re-hashes the file and its concrete backup copy, writes
the intent record, and the helper then performs a single SQLite transaction that
updates only ``asset.portable_id``.  The result is verified by SQLite's own
``integrity_check``, by a digest of every other cell being unchanged, by every
``portable_id`` equalling the plan, and by every moved file existing under its
literal on-disk spelling.  Any failure restores the verified backup.

Only ``asset.portable_id`` is rewritten.  ``history_entry.portable_id`` (play
history) is counted in the preview and left untouched, like the binary
rewriter leaves Serato history alone.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "engine-user"

# The helper holds the only SQL ``UPDATE``.  It is called only from inside a
# ``with engine.item(...)`` transaction; review it with its call site whenever
# this module changes.
ENGINE_MUTATION_HELPERS = ("_apply_sqlite_update",)

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import backup_manifest
import check_serato_crate_paths
import lock_guard
import mutation_engine
import rewrite_serato_paths as serato_rewrite

PLAN_TYPE = "serato-sqlite-path-rewrite"
PLAN_VERSION = 1
TABLE = "asset"
COLUMN = "portable_id"
HISTORY_TABLE = "history_entry"

_ASCII_LOWER = {code: code + 32 for code in range(ord("A"), ord("Z") + 1)}


class SeratoSqliteError(serato_rewrite.SeratoRewriteError):
    """The preview or apply could not prove that a SQLite rewrite is safe."""


# --------------------------------------------------------------------- helpers

def sidecar_paths(database: Path) -> dict[str, Path]:
    database = Path(database)
    return {suffix: Path(f"{database}{suffix}") for suffix in ("-wal", "-shm", "-journal")}


def require_quiescent(database: Path) -> None:
    """Refuse a database that another connection has left mid-flight."""
    sidecars = sidecar_paths(database)
    wal, journal = sidecars["-wal"], sidecars["-journal"]
    if wal.is_file() and wal.stat().st_size > 0:
        raise SeratoSqliteError(
            f"{database.name} has an un-checkpointed WAL ({wal.stat().st_size} bytes); "
            "open and cleanly quit Serato first so SQLite folds it into the database file"
        )
    if journal.exists():
        raise SeratoSqliteError(
            f"{database.name} has a rollback journal beside it; Serato did not close it cleanly"
        )


def journal_mode_header(database: Path) -> str:
    """Bytes 18-19 of the SQLite header (02 02 == WAL); persistent journal mode."""
    with Path(database).open("rb") as handle:
        return handle.read(20)[18:20].hex()


def open_immutable(database: Path) -> sqlite3.Connection:
    """Read-only handle that cannot create sidecar files or take locks."""
    uri = f"file:{quote(str(Path(database).resolve()))}?immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def _collate(value: str, collation: str) -> str:
    name = collation.upper()
    if name == "BINARY":
        return value
    if name == "NOCASE":
        # SQLite's NOCASE folds ASCII only; Unicode letters stay distinct.
        return value.translate(_ASCII_LOWER)
    if name == "RTRIM":
        return value.rstrip(" ")
    raise SeratoSqliteError(f"unsupported collation {collation!r} on a unique portable_id index")


def inspect_schema(connection: sqlite3.Connection) -> dict:
    """Confirm ``asset.portable_id`` exists; describe the unique indexes over it."""
    columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{TABLE}")')]
    if COLUMN not in columns:
        raise SeratoSqliteError(f"table {TABLE!r} has no {COLUMN!r} column")
    unique_indexes = []
    for _seq, name, unique, _origin, partial in connection.execute(f'PRAGMA index_list("{TABLE}")'):
        if not unique:
            continue
        keys = []
        for _seqno, cid, column, _desc, collation, is_key in connection.execute(
            f'PRAGMA index_xinfo("{name}")'
        ):
            if not is_key:
                continue
            if cid < 0:
                keys.append(("<expression>", collation))
            else:
                keys.append((column, collation))
        if COLUMN not in {column for column, _ in keys}:
            continue
        if partial or any(column == "<expression>" for column, _ in keys):
            raise SeratoSqliteError(
                f"unique index {name!r} on {TABLE}.{COLUMN} is partial or expression-based; "
                "collision checking is not supported"
            )
        unique_indexes.append({"name": name, "keys": [[column, collation] for column, collation in keys]})
    return {"columns": columns, "unique_indexes": unique_indexes}


def logical_digest(connection: sqlite3.Connection) -> str:
    """Digest of the schema and every cell EXCEPT ``asset.portable_id``.

    A correct rewrite changes only that column, so this value must be identical
    before and after.  It deliberately covers ``history_entry`` and every other
    table, so any collateral write (a trigger, a cascade, a stray UPDATE) shows.
    """
    digest = hashlib.sha256()
    for row in connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
    ):
        digest.update(repr(row).encode("utf-8"))
    tables = [
        name for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    for table in tables:
        columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
        if table == TABLE:
            columns = [column for column in columns if column != COLUMN]
        digest.update(f"\x00table:{table}:{columns!r}\x00".encode("utf-8"))
        selection = ", ".join(f'"{column}"' for column in columns) or "1"
        try:
            cursor = connection.execute(f'SELECT {selection} FROM "{table}" ORDER BY rowid')
        except sqlite3.OperationalError:
            # WITHOUT ROWID table: no rowid to order by.
            cursor = connection.execute(f'SELECT {selection} FROM "{table}" ORDER BY {selection}')
        for row in cursor:
            digest.update(repr(row).encode("utf-8"))
    return digest.hexdigest()


class LiteralFiles:
    """Cached exact-spelling file existence checks (one scandir per directory)."""

    def __init__(self):
        self._listings: dict[str, set[str]] = {}

    def is_file(self, serato_path: str) -> bool:
        target = check_serato_crate_paths.resolve(serato_path)
        parent = str(target.parent)
        if parent not in self._listings:
            names = set()
            try:
                with os.scandir(parent) as entries:
                    names = {entry.name for entry in entries if entry.is_file(follow_symlinks=False)}
            except OSError:
                pass
            self._listings[parent] = names
        return target.name in self._listings[parent]


# --------------------------------------------------------------------- preview

def _asset_rows(connection: sqlite3.Connection, schema: dict) -> tuple[list[tuple], list[str]]:
    extra = sorted({
        column for index in schema["unique_indexes"] for column, _ in index["keys"] if column != COLUMN
    })
    selection = ", ".join(['rowid', f'"{COLUMN}"', *[f'"{column}"' for column in extra]])
    return connection.execute(f'SELECT {selection} FROM "{TABLE}" ORDER BY rowid').fetchall(), extra


def _index_key(index: dict, extra: list[str], row: tuple, portable_id: str) -> tuple:
    values = {column: row[2 + position] for position, column in enumerate(extra)}
    key = []
    for column, collation in index["keys"]:
        value = portable_id if column == COLUMN else values[column]
        key.append(_collate(value, collation) if isinstance(value, str) else value)
    return tuple(key)


def _preview_one(database: Path, rules: list[dict], *, require_live_rules: bool) -> dict:
    database = Path(database)
    if database.is_symlink() or not database.is_file():
        raise SeratoSqliteError(f"SQLite database is missing, not a file, or a symlink: {database}")
    require_quiescent(database)
    file_sha = serato_rewrite.sha256_file(database)
    connection = open_immutable(database)
    try:
        schema = inspect_schema(connection)
        rows, extra = _asset_rows(connection, schema)
        digest = logical_digest(connection)
        history_untouched = 0
        has_history = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (HISTORY_TABLE,)
        ).fetchone()
        if has_history:
            for (stored,) in connection.execute(f'SELECT "{COLUMN}" FROM "{HISTORY_TABLE}"'):
                if isinstance(stored, str) and serato_rewrite.rewrite_path(stored, rules)[1] is not None:
                    history_untouched += 1
    finally:
        connection.close()
    if serato_rewrite.sha256_file(database) != file_sha:
        raise SeratoSqliteError(f"{database.name} changed while it was being read")

    literal = LiteralFiles()
    changed = []
    rule_counts: Counter[int] = Counter()
    for row in rows:
        rowid, stored = row[0], row[1]
        if not isinstance(stored, str) or not stored:
            continue
        new_path, rule = serato_rewrite.rewrite_path(stored, rules)
        if rule is None:
            continue
        rule_counts[rule["source_index"]] += 1
        changed.append({
            "rowid": rowid,
            "from": stored,
            "to": new_path,
            "rule_source_index": rule["source_index"],
            "source_present": literal.is_file(stored),
        })

    # SQLite checks unique indexes row by row, so a destination that equals an
    # existing, unmoved row's key would fail mid-statement.  Prove up front
    # that no final key collides.
    final = {row[0]: row[1] for row in rows}
    for item in changed:
        final[item["rowid"]] = item["to"]
    collisions = []
    for index in schema["unique_indexes"]:
        seen: dict[tuple, int] = {}
        moved = {item["rowid"] for item in changed}
        for row in rows:
            stored = final[row[0]]
            if not isinstance(stored, str):
                continue
            key = _index_key(index, extra, row, stored)
            if key in seen and (row[0] in moved or seen[key] in moved):
                collisions.append({"index": index["name"], "rowids": [seen[key], row[0]], "path": stored})
            seen.setdefault(key, row[0])
    if collisions:
        raise SeratoSqliteError(
            f"{database.name}: rewriting would violate unique index(es): {collisions[:3]}"
        )

    if require_live_rules:
        dead = sorted(
            index for index in rule_counts
            if not any(item["source_present"] for item in changed if item["rule_source_index"] == index)
        )
        if dead:
            raise SeratoSqliteError(
                f"{database.name}: none of the files for rename rule(s) {dead} exist at their old "
                "location. Build the SQLite preview BEFORE renaming folders; a preview built "
                "afterwards cannot tell stale rows from healthy ones."
            )

    return {
        "name": database.name,
        "sha256": file_sha,
        "bytes": database.stat().st_size,
        "journal_mode_header": journal_mode_header(database),
        "schema": schema,
        "logical_digest": digest,
        "rows": changed,
        "rule_counts": {str(index): rule_counts.get(index, 0) for index in sorted(r["source_index"] for r in rules)},
        "rows_changed": len(changed),
        "rows_total": len(rows),
        "rows_already_missing": sum(1 for item in changed if not item["source_present"]),
        "history_entry_references_left_unchanged": history_untouched,
    }


def build_preview(databases: list[Path], rules: list[dict], *, require_live_rules: bool = True) -> dict:
    """Read-only plan bound to each database's bytes and every unrelated cell."""
    databases = [Path(path) for path in databases]
    if not databases:
        raise SeratoSqliteError("no SQLite databases supplied")
    parents = {path.resolve().parent for path in databases}
    if len(parents) != 1:
        raise SeratoSqliteError("all SQLite databases must live in one directory (one backup root)")
    names = [path.name for path in databases]
    if len(set(names)) != len(names):
        raise SeratoSqliteError("duplicate database names")
    planned = [_preview_one(path, rules, require_live_rules=require_live_rules) for path in databases]
    normalized_rules = [dict(rule) for rule in rules]
    used = {int(index) for entry in planned for index, count in entry["rule_counts"].items() if count}
    return {
        "plan_type": PLAN_TYPE,
        "plan_version": PLAN_VERSION,
        "mode": "preview-only",
        "approval_required": True,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "library_dir": str(parents.pop()),
        "rename_map_sha256": serato_rewrite.sha256_bytes(serato_rewrite._canonical_json(normalized_rules)),
        "renames": normalized_rules,
        "databases": planned,
        "summary": {
            "databases": len(planned),
            "rows_changed": sum(entry["rows_changed"] for entry in planned),
            "rows_already_missing": sum(entry["rows_already_missing"] for entry in planned),
            "history_entry_references_left_unchanged": sum(
                entry["history_entry_references_left_unchanged"] for entry in planned
            ),
            "per_database": {entry["name"]: entry["rows_changed"] for entry in planned},
            "unused_rule_source_indexes": sorted(
                rule["source_index"] for rule in normalized_rules if rule["source_index"] not in used
            ),
        },
    }


def _comparable(plan: dict) -> dict:
    """Plan fields that must still match the databases at apply time.

    ``source_present`` is preview-time evidence (the folders have been renamed
    by apply time, so it cannot be recomputed) and ``created_at`` is not data.
    """
    return {
        "renames": plan.get("renames"),
        "rename_map_sha256": plan.get("rename_map_sha256"),
        "library_dir": plan.get("library_dir"),
        "summary": {
            key: value for key, value in (plan.get("summary") or {}).items()
            if key != "rows_already_missing"
        },
        "databases": [
            {
                **{key: value for key, value in entry.items() if key not in {"rows", "rows_already_missing"}},
                "rows": [
                    {key: value for key, value in row.items() if key != "source_present"}
                    for row in entry.get("rows", [])
                ],
            }
            for entry in plan.get("databases", [])
        ],
    }


def load_and_validate_plan(plan_path: Path, databases: list[Path], *, require_targets: bool = False) -> dict:
    try:
        plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SeratoSqliteError(f"plan not found: {plan_path}") from exc
    except json.JSONDecodeError as exc:
        raise SeratoSqliteError(f"plan is not valid JSON: {plan_path}: {exc}") from exc
    if not isinstance(plan, dict):
        raise SeratoSqliteError("plan must be a JSON object")
    if plan.get("plan_type") != PLAN_TYPE or plan.get("plan_version") != PLAN_VERSION:
        raise SeratoSqliteError("unexpected Serato SQLite rewrite plan type/version")
    if plan.get("mode") != "preview-only" or plan.get("approval_required") is not True:
        raise SeratoSqliteError("plan is not an approval-required preview")

    rules = serato_rewrite.normalize_rename_map(plan.get("renames"))
    if rules != plan.get("renames"):
        raise SeratoSqliteError("plan rename rules are not in canonical longest-first form")
    if plan.get("rename_map_sha256") != serato_rewrite.sha256_bytes(serato_rewrite._canonical_json(rules)):
        raise SeratoSqliteError("plan rename-map digest does not match its rules")

    databases = [Path(path) for path in databases]
    planned_names = [entry.get("name") for entry in plan.get("databases", [])]
    if sorted(planned_names) != sorted(path.name for path in databases):
        raise SeratoSqliteError(
            f"plan covers {planned_names}, not the requested databases {[p.name for p in databases]}"
        )
    ordered = sorted(databases, key=lambda path: planned_names.index(path.name))
    rebuilt = build_preview(ordered, rules, require_live_rules=False)
    if _comparable(rebuilt) != _comparable(plan):
        raise SeratoSqliteError(
            "plan no longer matches the current SQLite databases; rebuild the preview"
        )

    if require_targets:
        literal = LiteralFiles()
        used = set()
        missing = []
        for entry in plan["databases"]:
            for row in entry["rows"]:
                used.add(row["rule_source_index"])
                if row["source_present"] and not literal.is_file(row["to"]):
                    missing.append(row["to"])
        if missing:
            raise SeratoSqliteError(
                "rewritten target path(s) do not exist; rename the library folders first: "
                + ", ".join(repr(path) for path in sorted(set(missing))[:10])
            )
        nonliteral = serato_rewrite.nonliteral_rule_target_directories(rules, used)
        if nonliteral:
            raise SeratoSqliteError(
                "rewritten target folder(s) are not spelled exactly as on disk "
                "(case-insensitive match only); rename the folder first: "
                + ", ".join(repr(path) for path in nonliteral[:10])
            )
    return plan


# ----------------------------------------------------------------------- apply

def _apply_sqlite_update(txn, database: Path, entry: dict) -> None:
    """Update ``asset.portable_id`` for the planned rows in ONE SQLite transaction.

    Opens the real database for writing, so it must run inside the engine item
    for ``database``.  ``mark_mutated`` is called just before COMMIT: if the
    process dies between COMMIT and the completion record the journal already
    says the file may have changed, which is the safe direction.
    """
    connection = sqlite3.connect(str(database), timeout=0, isolation_level=None)
    in_transaction = False
    try:
        connection.execute("PRAGMA busy_timeout = 0")
        connection.execute("BEGIN IMMEDIATE")
        in_transaction = True
        for row in entry["rows"]:
            cursor = connection.execute(
                f'UPDATE "{TABLE}" SET "{COLUMN}" = ? WHERE rowid = ? AND "{COLUMN}" = ?',
                (row["to"], row["rowid"], row["from"]),
            )
            if cursor.rowcount != 1:
                raise mutation_engine.MutationVerificationError(
                    f"{database.name}: row {row['rowid']} no longer holds the planned path"
                )
        txn.mark_mutated()
        connection.execute("COMMIT")
        in_transaction = False
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except BaseException:
        if in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def verify_database(database: Path, entry: dict, rules: list[dict]) -> dict:
    """Prove the rewritten database matches the plan; raise on any difference."""
    sidecars = sidecar_paths(database)
    if sidecars["-journal"].exists():
        raise mutation_engine.MutationVerificationError(f"{database.name}: rollback journal left behind")
    wal = sidecars["-wal"]
    if wal.is_file() and wal.stat().st_size > 0:
        raise mutation_engine.MutationVerificationError(f"{database.name}: WAL not checkpointed")
    if journal_mode_header(database) != entry["journal_mode_header"]:
        raise mutation_engine.MutationVerificationError(
            f"{database.name}: journal mode changed (WAL/rollback header bytes differ)"
        )
    connection = open_immutable(database)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        if integrity != [("ok",)]:
            raise mutation_engine.MutationVerificationError(
                f"{database.name}: integrity_check failed: {integrity[:3]}"
            )
        if logical_digest(connection) != entry["logical_digest"]:
            raise mutation_engine.MutationVerificationError(
                f"{database.name}: cells other than asset.portable_id changed"
            )
        expected = {row["rowid"]: row["to"] for row in entry["rows"]}
        mapped_sources = set()
        mismatched = []
        for rowid, stored in connection.execute(f'SELECT rowid, "{COLUMN}" FROM "{TABLE}"'):
            if rowid in expected:
                if stored != expected[rowid]:
                    mismatched.append(rowid)
            elif isinstance(stored, str) and serato_rewrite.rewrite_path(stored, rules)[1] is not None:
                mapped_sources.add(stored)
        if mismatched:
            raise mutation_engine.MutationVerificationError(
                f"{database.name}: {len(mismatched)} row(s) do not hold the planned path"
            )
        if mapped_sources:
            raise mutation_engine.MutationVerificationError(
                f"{database.name}: {len(mapped_sources)} mapped old path(s) remain"
            )
    finally:
        connection.close()
    literal = LiteralFiles()
    absent = sorted({
        row["to"] for row in entry["rows"] if row["source_present"] and not literal.is_file(row["to"])
    })
    if absent:
        raise mutation_engine.MutationVerificationError(
            f"{database.name}: moved file(s) not found under their new literal path: {absent[:5]}"
        )
    return {"rows_verified": len(entry["rows"])}


def apply_sqlite_plan(
    *,
    plan: dict,
    databases: list[Path],
    manifest: dict,
    heartbeat,
    journal_path: Path,
    result_path: Path,
    process_lister=None,
) -> dict:
    """Apply an already validated plan: one engine transaction per database."""
    serato_rewrite.require_serato_closed(process_lister)
    databases = {Path(path).name: Path(path) for path in databases}
    library_dir = Path(plan["library_dir"]).resolve()
    if not plan["summary"]["rows_changed"]:
        raise SeratoSqliteError("plan contains no SQLite rows to rewrite")

    engine = mutation_engine.MutationEngine(
        Path(journal_path), heartbeat, manifest, library_dir, PLAN_TYPE
    )
    engine.start({
        "plan_type": PLAN_TYPE,
        "databases": [entry["name"] for entry in plan["databases"]],
        "rows": plan["summary"]["rows_changed"],
        "rename_map_sha256": plan["rename_map_sha256"],
    })
    completed = []
    for index, entry in enumerate(plan["databases"], start=1):
        if not entry["rows"]:
            continue
        database = databases[entry["name"]]
        backup_path = Path(manifest["backup_root"]) / entry["name"]
        with engine.item(
            index,
            kind=mutation_engine.SERATO_PATHS,
            source=database,
            expected_sha256=entry["sha256"],
            extra_intent={
                "relative_path": entry["name"],
                "rows": entry["rows_changed"],
                "table": f"{TABLE}.{COLUMN}",
            },
        ) as txn:
            def restore(database=database, backup_path=backup_path, expected=entry["sha256"]):
                # Sidecars belong to the state being discarded: a WAL or journal
                # left beside a restored main file would be replayed over it.
                # They are moved aside (never deleted) so nothing is lost.
                for sidecar in sidecar_paths(database).values():
                    if sidecar.exists():
                        sidecar.rename(sidecar.with_name(f"{sidecar.name}.discarded-{uuid.uuid4().hex[:8]}"))
                mutation_engine.restore_from_backup(backup_path, database, expected)

            txn.on_rollback(restore)
            require_quiescent(database)
            _apply_sqlite_update(txn, database, entry)
            verification = verify_database(database, entry, plan["renames"])
            after_sha = serato_rewrite.sha256_file(database)
            txn.record(
                relative_path=entry["name"],
                after_sha256=after_sha,
                rows_rewritten=entry["rows_changed"],
            )
        completed.append({
            "database": entry["name"],
            "before_sha256": entry["sha256"],
            "after_sha256": after_sha,
            "rows_rewritten": entry["rows_changed"],
            "rows_already_missing_before": entry["rows_already_missing"],
            **verification,
        })

    result = {
        "status": "PASS",
        "plan_type": PLAN_TYPE,
        "library_dir": str(library_dir),
        "databases_changed": len(completed),
        "rows_rewritten": sum(item["rows_rewritten"] for item in completed),
        "completed": completed,
        "journal": str(journal_path),
    }
    engine.finish(result)
    serato_rewrite.write_json(result_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview_parser = subparsers.add_parser("preview", help="build a read-only, hash-bound rewrite diff")
    preview_parser.add_argument("--sqlite-db", required=True, action="append", type=Path)
    preview_parser.add_argument("--rename-map", required=True, type=Path)
    preview_parser.add_argument("--out", required=True, type=Path)

    apply_parser = subparsers.add_parser("apply", help="apply a reviewed preview under lock+backup")
    apply_parser.add_argument("--sqlite-db", required=True, action="append", type=Path)
    apply_parser.add_argument("--plan", required=True, type=Path)
    apply_parser.add_argument("--backup-manifest", required=True, type=Path)
    apply_parser.add_argument("--lock-folder", default="Serato")
    apply_parser.add_argument("--session", required=True)
    apply_parser.add_argument("--journal", required=True, type=Path)
    apply_parser.add_argument("--out", required=True, type=Path)
    apply_parser.add_argument(
        "--confirm-serato-closed",
        action="store_true",
        help="required acknowledgement that Serato is not running during the rewrite",
    )
    args = parser.parse_args()

    try:
        if args.command == "preview":
            rules = serato_rewrite.load_rename_map(args.rename_map)
            plan = build_preview(args.sqlite_db, rules)
            serato_rewrite.write_json(args.out, plan)
            print(json.dumps({"status": "PREVIEW", **plan["summary"], "plan": str(args.out)}, indent=2))
            return 0

        if not args.confirm_serato_closed:
            raise SeratoSqliteError(
                "apply requires --confirm-serato-closed; close Serato before changing its databases"
            )
        serato_rewrite.require_serato_closed()
        plan = load_and_validate_plan(args.plan, args.sqlite_db, require_targets=True)
        heartbeat, manifest = mutation_engine.run_preflight(
            lock_folder=args.lock_folder,
            session=args.session,
            manifest_path=args.backup_manifest,
            source_root=Path(plan["library_dir"]),
        )
        result = apply_sqlite_plan(
            plan=plan,
            databases=args.sqlite_db,
            manifest=manifest,
            heartbeat=heartbeat,
            journal_path=args.journal,
            result_path=args.out,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (
        SeratoSqliteError,
        serato_rewrite.SeratoRewriteError,
        backup_manifest.BackupGateError,
        mutation_engine.MutationRefused,
        sqlite3.Error,
    ) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
