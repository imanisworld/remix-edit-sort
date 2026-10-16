#!/usr/bin/env python3
"""Remove Serato records whose audio file no longer exists (dry-run by default).

Stale records block ``rewrite_serato_paths.py`` / ``rewrite_serato_sqlite_paths.py``
(a rename map cannot point a missing file at a new path), and Serato itself can
re-add old paths after a rewrite (observed 2026-10-15: 48 old
``Afrobeat/CLEAN|DIRTY/Afrobeat/...`` paths reappeared next to the rewritten ones).

What it touches
* ``_Serato_/database V2``: whole ``otrk`` records whose ``pfil`` is missing.
* ``asset`` rows in ``master.sqlite`` / ``root.sqlite`` (the same files the rename
  tooling handles) whose ``portable_id`` is missing. Deleted with
  ``PRAGMA foreign_keys = ON`` so SQLite's own ON DELETE CASCADE rows follow.

What it never touches
* Crates (``Subcrates/*.crate``): crate refs to a candidate path are reported and
  the record is HELD, never removed.
* SQLite crate memberships: an asset that is still in a container is HELD, unless
  ``--drop-stale-memberships`` is given (off by default).  With that flag a
  ``live_twin`` asset may be removed together with its ``container_asset`` rows,
  but ONLY when it has exactly one live twin record in the same database and that
  twin is already in every container the stale row is in.  If any container lacks
  the live copy (or containers cannot be resolved) the row is HELD and the
  containers are reported.  Live rows, ``truly_missing`` rows and container rows
  themselves are never touched.  Undo restores the whole database file from its
  verified backup, so memberships come back byte-for-byte.
* History/session files and ``history_entry`` rows (verified unchanged).

A missing record is removed only when it is EITHER
* ``live_twin``: another record in the same store has the same filename and that
  file exists (a stale duplicate of a live track), OR
* ``truly_missing``: no file with that filename exists anywhere under the library.
A missing record whose filename exists elsewhere but has no live record is
``moved_unindexed`` and HELD (relocate it in Serato instead).

Default run writes a hash-bound plan and changes nothing.  ``--apply`` needs that
plan, the lock, PASS backup manifests for the Serato dir and the SQLite dir,
``--confirm-serato-closed`` and a closed Serato.  Each file is its own
mutation_engine transaction (journal = undo log).  ``--undo`` restores every
completed file from its verified backup copy.  A post-check re-scans the stores.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "engine-user"
# Both helpers run only inside a mutation_engine item transaction.
ENGINE_MUTATION_HELPERS = ("_delete_sqlite_rows", "_replace_database_v2")

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import backup_manifest
import check_serato_crate_paths
import lock_guard  # noqa: F401  (engine-user contract: lock via run_preflight)
import mutation_engine
import rewrite_serato_paths as sr
import rewrite_serato_sqlite_paths as sq

PLAN_TYPE = "serato-prune-missing-records"
PLAN_VERSION = 2
REMOVABLE = ("live_twin", "truly_missing")


class PruneError(sr.SeratoRewriteError):
    """The prune could not prove it is safe."""


def _norm_prefix(library_root: Path, prefix: str | None) -> str | None:
    if not prefix:
        return None
    p = prefix.strip().strip("/")
    if not p or ".." in Path(p).parts:
        raise PruneError(f"invalid --prefix: {prefix!r}")
    return f"{str(Path(library_root).resolve()).lstrip('/')}/{p}/"


def _in_scope(path: str, scope: str | None, library_stored: str) -> bool:
    if scope is not None:
        return path.startswith(scope)
    return path.startswith(library_stored + "/")


class Classifier:
    def __init__(self, library_root: Path):
        self.literal = sq.LiteralFiles()
        self.index = check_serato_crate_paths.build_filename_index(Path(library_root))

    def exists(self, path: str) -> bool:
        return self.literal.is_file(path)

    def classify(self, path: str, store_paths: set[str]) -> str:
        if self.exists(path):
            return "present"
        name = path.rsplit("/", 1)[-1]
        if any(o != path and o.rsplit("/", 1)[-1] == name and self.exists(o) for o in store_paths):
            return "live_twin"
        if self.index.get(name):
            return "moved_unindexed"
        return "truly_missing"


# ------------------------------------------------------------ database V2

def _dbv2_records(data: bytes) -> list[tuple[int, str | None]]:
    out = []
    for i, (tag, value) in enumerate(sr.parse_tlv_strict(data, sr.DATABASE_NAME)):
        if tag != "otrk":
            out.append((i, None)); continue
        path = None
        for itag, ival in sr.parse_tlv_strict(value, f"otrk[{i}]"):
            if itag == "pfil":
                path, _ = sr._decode_path_value(ival, f"otrk[{i}].pfil")
        out.append((i, path))
    return out


def _crate_refs(serato_dir: Path) -> Counter:
    refs: Counter = Counter()
    for source in sr.serato_source_files(serato_dir)[1:]:
        rel = str(source.relative_to(serato_dir))
        for tag, value in sr.parse_tlv_strict(source.read_bytes(), rel):
            if tag != "otrk":
                continue
            for itag, ival in sr.parse_tlv_strict(value, rel):
                if itag == "ptrk":
                    refs[sr._decode_path_value(ival, rel)[0]] += 1
    return refs


def prune_dbv2_bytes(data: bytes, remove_indexes: set[int]) -> bytes:
    outer = sr.parse_tlv_strict(data, sr.DATABASE_NAME)
    rebuilt = b"".join(sr.encode_tlv(t, v) for i, (t, v) in enumerate(outer) if i not in remove_indexes)
    if not remove_indexes and rebuilt != data:
        raise PruneError("database V2 strict round-trip changed bytes")
    return rebuilt


# ------------------------------------------------------------ SQLite

def _dependents(conn: sqlite3.Connection) -> dict:
    """Tables that reference asset ids, and whether rows there are crate membership."""
    tables = [n for (n,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    deps = {}
    for t in tables:
        if t in (sq.TABLE, sq.HISTORY_TABLE):
            continue
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')]
        if "asset_id" in cols:
            fk = any(r[2] == sq.TABLE for r in conn.execute(f'PRAGMA foreign_key_list("{t}")'))
            deps[t] = {"fk_cascade": fk, "membership": "container" in t}
    two_level = "space_asset" in deps and "container_asset" in tables and "space_asset_id" in [
        r[1] for r in conn.execute('PRAGMA table_info("container_asset")')]
    return {"tables": deps, "space_container": two_level}


def _asset_memberships(conn, deps, asset_id) -> dict:
    counts = {}
    for t, d in deps["tables"].items():
        n = conn.execute(f'SELECT count(*) FROM "{t}" WHERE asset_id = ?', (asset_id,)).fetchone()[0]
        if n:
            counts[t] = n
    if deps["space_container"]:
        n = conn.execute('SELECT count(*) FROM container_asset WHERE space_asset_id IN '
                         '(SELECT id FROM space_asset WHERE asset_id = ?)', (asset_id,)).fetchone()[0]
        if n:
            counts["container_asset(via space_asset)"] = n
    return counts


_NAME_SUFFIX = re.compile(r"[\U000F0000-\U0010FFFF][0-9a-fA-F]{40}$")


def _clean_name(name) -> str:
    return _NAME_SUFFIX.sub("", name or "")


def _container_label(conn, cid) -> str:
    parts, seen = [], set()
    while cid and cid not in seen:
        seen.add(cid)
        row = conn.execute('SELECT parent_id, name FROM container WHERE id = ?', (cid,)).fetchone()
        if not row:
            break
        nm = _clean_name(row[1])
        if nm:
            parts.append(nm)
        cid = row[0]
    return " \u226b ".join(reversed(parts)) or "(unnamed)"


def _membership_rows(conn, asset_id):
    """[(container_asset_id, container_id)] for an asset, or None if containers can't be resolved."""
    if not (_has_table(conn, "container_asset") and _has_table(conn, "space_asset") and _has_table(conn, "container")):
        return None
    cols = [r[1] for r in conn.execute('PRAGMA table_info("container_asset")')]
    if "space_asset_id" not in cols:
        return None
    via = "space_asset_id IN (SELECT id FROM space_asset WHERE asset_id = ?)"
    if "container_id" in cols:
        q = f"SELECT id, container_id FROM container_asset WHERE {via}"
    elif "location_container_id" in cols and _has_table(conn, "location_container"):
        q = ("SELECT ca.id, lc.container_id FROM container_asset ca JOIN location_container lc "
             f"ON lc.id = ca.location_container_id WHERE ca.{via}")
    else:
        return None
    rows = conn.execute(q, (asset_id,)).fetchall()
    if "asset_id" in cols:  # every row of this asset must be reachable through space_asset
        n = conn.execute("SELECT count(*) FROM container_asset WHERE asset_id = ?", (asset_id,)).fetchone()[0]
        if n and n > len(rows):
            return None
    return sorted(rows)


def _membership_decision(conn, rowid, path, astore_rows, cls) -> dict:
    name = path.rsplit("/", 1)[-1]
    twins = [(r, p) for r, p in astore_rows if r != rowid and p.rsplit("/", 1)[-1] == name and cls.exists(p)]
    out = {"live_twin_rowids": [r for r, _ in twins]}
    if len(twins) != 1:
        out["ok"] = False; out["why"] = f"{len(twins)} live twin records (need exactly 1)"; return out
    stale = _membership_rows(conn, rowid); live = _membership_rows(conn, twins[0][0])
    if stale is None or live is None:
        out["ok"] = False; out["why"] = "containers could not be resolved"; return out
    live_c = {c for _, c in live}
    conts = []
    for ca_id, cid in stale:
        conts.append({"container_asset_id": ca_id, "container_id": cid,
                      "container": _container_label(conn, cid), "live_copy_present": cid in live_c})
    lacking = sorted({c["container"] for c in conts if not c["live_copy_present"]})
    out.update(containers=conts, live_twin_path=twins[0][1], lacking=lacking, ok=not lacking,
               why=None if not lacking else f"live copy missing from {len(lacking)} container(s)")
    return out


def _table_digest(conn, table, exclude_rowids=()) -> str:
    h = hashlib.sha256(); ex = set(exclude_rowids)
    cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
    sel = ", ".join(f'"{c}"' for c in cols)
    for row in conn.execute(f'SELECT rowid, {sel} FROM "{table}" ORDER BY rowid'):
        if row[0] not in ex:
            h.update(repr(row).encode("utf-8"))
    return h.hexdigest()


def _has_table(conn, name) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


# ------------------------------------------------------------ preview

def build_plan(serato_dir: Path, library_root: Path, databases: list[Path], prefix: str | None,
               drop_stale_memberships: bool = False) -> dict:
    serato_dir = Path(serato_dir).resolve(); library_root = Path(library_root).resolve()
    scope = _norm_prefix(library_root, prefix); lib_stored = str(library_root).lstrip("/")
    cls = Classifier(library_root)
    summary: Counter = Counter()
    # database V2
    dbv2 = serato_dir / sr.DATABASE_NAME
    data = dbv2.read_bytes()
    recs = _dbv2_records(data)
    store = {p for _, p in recs if p}
    crates = _crate_refs(serato_dir)
    dbv2_rows = []
    for idx, path in recs:
        if not path or not _in_scope(path, scope, lib_stored):
            continue
        c = cls.classify(path, store)
        if c == "present":
            continue
        action = "remove" if c in REMOVABLE else "hold"
        reason = c
        if action == "remove" and crates.get(path):
            action, reason = "hold", f"{c}; {crates[path]} crate ref(s) (crates are never edited)"
        dbv2_rows.append({"outer_index": idx, "path": path, "class": c, "action": action,
                          "reason": reason, "crate_refs": crates.get(path, 0)})
        summary[f"database_v2_{action}_{c}"] += 1
    remove_idx = {r["outer_index"] for r in dbv2_rows if r["action"] == "remove"}
    dbv2_entry = {"path": sr.DATABASE_NAME, "source_sha256": sr.sha256_bytes(data),
                  "proposed_sha256": sr.sha256_bytes(prune_dbv2_bytes(data, remove_idx)),
                  "records": dbv2_rows}
    # SQLite
    db_entries = []
    for database in databases:
        database = Path(database).resolve()
        sq.require_quiescent(database)
        conn = sq.open_immutable(database)
        try:
            deps = _dependents(conn)
            assets = conn.execute(f'SELECT rowid, "{sq.COLUMN}" FROM "{sq.TABLE}"').fetchall()
            astore = {p for _, p in assets if isinstance(p, str)}
            astore_rows = [(r, p) for r, p in assets if isinstance(p, str)]
            rows = []
            for rowid, path in assets:
                if not isinstance(path, str) or not _in_scope(path, scope, lib_stored):
                    continue
                c = cls.classify(path, astore)
                if c == "present":
                    continue
                mem = _asset_memberships(conn, deps, rowid)
                crate_mem = {k: v for k, v in mem.items() if "container" in k}
                action = "remove" if c in REMOVABLE else "hold"; reason = c
                drop = None; decision = None
                if action == "remove" and crate_mem:
                    action, reason = "hold", f"{c}; still in a Serato container {crate_mem}"
                    if drop_stale_memberships and c == "live_twin":
                        decision = _membership_decision(conn, rowid, path, astore_rows, cls)
                        resolved = len(decision.get("containers", []))
                        total = crate_mem.get("container_asset(via space_asset)", crate_mem.get("container_asset"))
                        if decision["ok"] and set(crate_mem) <= {"container_asset", "container_asset(via space_asset)"} \
                                and total == resolved:
                            action, reason = "remove", f"{c}; drop {resolved} stale membership(s), live copy in every container"
                            drop = [d["container_asset_id"] for d in decision["containers"]]
                        else:
                            reason = f"{c}; held: {decision.get('why') or 'membership count mismatch'}"
                row = {"rowid": rowid, "path": path, "class": c, "action": action,
                       "reason": reason, "dependent_rows": mem}
                if decision is not None:
                    row["membership_check"] = decision
                if drop is not None:
                    row["drop_container_asset_ids"] = drop
                rows.append(row)
                summary[f"{database.name}_{action}_{c}"] += 1
            removed = [r["rowid"] for r in rows if r["action"] == "remove"]
            dropped = [i for r in rows if r["action"] == "remove" for i in r.get("drop_container_asset_ids", [])]
            db_entries.append({
                "container_asset_digest_kept": (_table_digest(conn, "container_asset", dropped)
                                                if _has_table(conn, "container_asset") else None),
                "name": database.name, "path": str(database), "sha256": sr.sha256_file(database),
                "journal_mode_header": sq.journal_mode_header(database),
                "asset_digest_kept": _table_digest(conn, sq.TABLE, removed),
                "history_digest": _table_digest(conn, sq.HISTORY_TABLE) if _has_table(conn, sq.HISTORY_TABLE) else None,
                "dependent_tables": deps, "rows": rows,
            })
        finally:
            conn.close()
    if len({str(Path(e["path"]).parent) for e in db_entries}) > 1:
        raise PruneError("SQLite databases must share one directory")
    summary["database_v2_remove"] = len(remove_idx)
    for e in db_entries:
        summary[f"{e['name']}_remove"] = sum(r["action"] == "remove" for r in e["rows"])
        summary[f"{e['name']}_memberships_dropped"] = sum(len(r.get("drop_container_asset_ids", []))
                                                          for r in e["rows"] if r["action"] == "remove")
    return {
        "plan_type": PLAN_TYPE, "plan_version": PLAN_VERSION, "mode": "dry-run",
        "approval_required": True, "created_at": datetime.now().astimezone().isoformat(),
        "serato_dir": str(serato_dir), "library_root": str(library_root), "prefix": prefix,
        "drop_stale_memberships": bool(drop_stale_memberships),
        "database_v2": dbv2_entry, "sqlite": db_entries, "summary": dict(sorted(summary.items())),
    }


def _comparable(plan: dict) -> dict:
    p = dict(plan); p.pop("created_at", None); p.pop("mode", None)
    return json.loads(json.dumps(p, sort_keys=True))


def load_and_validate_plan(plan_path: Path, serato_dir, library_root, databases) -> dict:
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    if plan.get("plan_type") != PLAN_TYPE or plan.get("plan_version") != PLAN_VERSION:
        raise PruneError("not a prune plan")
    rebuilt = build_plan(serato_dir, library_root, databases, plan.get("prefix"),
                         bool(plan.get("drop_stale_memberships")))
    if _comparable(rebuilt) != _comparable(plan):
        raise PruneError("plan no longer matches current Serato files; rebuild the dry run")
    return plan


# ------------------------------------------------------------ apply helpers

def _replace_database_v2(txn, path: Path, content: bytes, expected_sha: str) -> None:
    sr._atomic_replace(txn, path, content, expected_sha)


def _delete_sqlite_rows(txn, database: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(str(database), timeout=0, isolation_level=None)
    in_tx = False
    try:
        conn.execute("PRAGMA busy_timeout = 0")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE"); in_tx = True
        for r in rows:
            drop = r.get("drop_container_asset_ids") or []
            for ca_id in drop:
                cur = conn.execute("DELETE FROM container_asset WHERE id = ? AND space_asset_id IN "
                                   "(SELECT id FROM space_asset WHERE asset_id = ?)", (ca_id, r["rowid"]))
                if cur.rowcount != 1:
                    raise mutation_engine.MutationVerificationError(
                        f"{database.name}: membership {ca_id} of row {r['rowid']} changed since the plan")
            cur = conn.execute(f'DELETE FROM "{sq.TABLE}" WHERE rowid = ? AND "{sq.COLUMN}" = ?',
                               (r["rowid"], r["path"]))
            if cur.rowcount != 1:
                raise mutation_engine.MutationVerificationError(
                    f"{database.name}: row {r['rowid']} no longer holds the planned path")
        txn.mark_mutated()
        conn.execute("COMMIT"); in_tx = False
        busy = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy is None or busy[0] != 0:
            raise mutation_engine.MutationVerificationError(f"{database.name}: WAL checkpoint incomplete")
    except BaseException:
        if in_tx:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        raise
    finally:
        conn.close()


def _verify_sqlite(database: Path, entry: dict) -> dict:
    removed = [r["rowid"] for r in entry["rows"] if r["action"] == "remove"]
    if sq.journal_mode_header(database) != entry["journal_mode_header"]:
        raise mutation_engine.MutationVerificationError(f"{database.name}: journal mode changed")
    conn = sq.open_immutable(database)
    try:
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise mutation_engine.MutationVerificationError(f"{database.name}: integrity_check failed")
        left = [r for r in removed if conn.execute(f'SELECT 1 FROM "{sq.TABLE}" WHERE rowid=?', (r,)).fetchone()]
        if left:
            raise mutation_engine.MutationVerificationError(f"{database.name}: rows not removed: {left[:5]}")
        if _table_digest(conn, sq.TABLE) != entry["asset_digest_kept"]:
            raise mutation_engine.MutationVerificationError(f"{database.name}: a kept asset row changed")
        ca_digest = entry.get("container_asset_digest_kept")
        if ca_digest is not None and _table_digest(conn, "container_asset") != ca_digest:
            raise mutation_engine.MutationVerificationError(f"{database.name}: a kept container membership changed")
        if entry["history_digest"] is not None and _table_digest(conn, sq.HISTORY_TABLE) != entry["history_digest"]:
            raise mutation_engine.MutationVerificationError(f"{database.name}: history_entry changed")
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()
    return {"rows_removed": len(removed), "foreign_key_check_rows": len(fk)}


def post_check(serato_dir, library_root, databases, prefix, drop_stale_memberships=False) -> dict:
    again = build_plan(serato_dir, library_root, databases, prefix, drop_stale_memberships)
    remaining = again["summary"].get("database_v2_remove", 0) + sum(
        again["summary"].get(f"{Path(d).name}_remove", 0) for d in databases)
    held = sum(1 for r in again["database_v2"]["records"] if r["action"] == "hold") + sum(
        1 for e in again["sqlite"] for r in e["rows"] if r["action"] == "hold")
    return {"status": "PASS" if remaining == 0 else "FAIL", "removable_remaining": remaining,
            "held_remaining": held, "summary": again["summary"]}


def apply_plan(*, plan, serato_dir, library_root, databases, serato_manifest, sqlite_manifest,
               heartbeat, journal_path, result_path, process_lister=None) -> dict:
    sr.require_serato_closed(process_lister)
    serato_dir = Path(serato_dir).resolve(); databases = {Path(d).name: Path(d).resolve() for d in databases}
    completed = []
    entry = plan["database_v2"]
    remove_idx = {r["outer_index"] for r in entry["records"] if r["action"] == "remove"}
    if remove_idx:
        engine = mutation_engine.MutationEngine(Path(journal_path), heartbeat, serato_manifest, serato_dir, PLAN_TYPE)
        engine.start({"plan_type": PLAN_TYPE, "store": "database V2", "records": len(remove_idx)})
        source = serato_dir / sr.DATABASE_NAME
        new = prune_dbv2_bytes(source.read_bytes(), remove_idx)
        if sr.sha256_bytes(new) != entry["proposed_sha256"]:
            raise PruneError("database V2 drifted immediately before apply")
        with engine.item(1, kind=mutation_engine.SERATO_PATHS, source=source,
                         expected_sha256=entry["source_sha256"],
                         extra_intent={"relative_path": sr.DATABASE_NAME, "records_removed": len(remove_idx)}) as txn:
            backup = Path(txn.intent["backup_path"])
            txn.on_rollback(lambda: mutation_engine.restore_from_backup(backup, source, entry["source_sha256"]))
            _replace_database_v2(txn, source, new, entry["source_sha256"])
            if sr.sha256_file(source) != entry["proposed_sha256"]:
                raise mutation_engine.MutationVerificationError("database V2 post-write hash mismatch")
            gone = {r["path"] for r in entry["records"] if r["action"] == "remove"}
            if gone & {p for _, p in _dbv2_records(source.read_bytes()) if p}:
                raise mutation_engine.MutationVerificationError("pruned database V2 path still present")
            txn.record(relative_path=sr.DATABASE_NAME, after_sha256=entry["proposed_sha256"])
        engine.finish({"records_removed": len(remove_idx)})
        completed.append({"store": sr.DATABASE_NAME, "removed": len(remove_idx),
                          "before_sha256": entry["source_sha256"], "after_sha256": entry["proposed_sha256"]})
    sql_entries = [e for e in plan["sqlite"] if any(r["action"] == "remove" for r in e["rows"])]
    if sql_entries:
        library_dir = Path(sql_entries[0]["path"]).parent
        engine = mutation_engine.MutationEngine(Path(f"{journal_path}.sqlite.jsonl"), heartbeat,
                                                sqlite_manifest, library_dir, PLAN_TYPE)
        engine.start({"plan_type": PLAN_TYPE, "store": "sqlite", "databases": [e["name"] for e in sql_entries]})
        for i, e in enumerate(sql_entries, start=1):
            database = databases[e["name"]]
            rows = [r for r in e["rows"] if r["action"] == "remove"]
            with engine.item(i, kind=mutation_engine.SERATO_PATHS, source=database, expected_sha256=e["sha256"],
                             extra_intent={"relative_path": e["name"], "rows_removed": len(rows)}) as txn:
                backup = Path(txn.intent["backup_path"])

                def restore(database=database, backup=backup, expected=e["sha256"]):
                    for side in sq.sidecar_paths(database).values():
                        if side.exists():
                            side.rename(side.with_name(f"{side.name}.discarded-{os.getpid()}"))
                    mutation_engine.restore_from_backup(backup, database, expected)
                txn.on_rollback(restore)
                sq.require_quiescent(database)
                _delete_sqlite_rows(txn, database, rows)
                ver = _verify_sqlite(database, e)
                after = sr.sha256_file(database)
                txn.record(relative_path=e["name"], after_sha256=after, rows_removed=len(rows))
            completed.append({"store": e["name"], "removed": len(rows), "before_sha256": e["sha256"],
                              "after_sha256": after, **ver})
        engine.finish({"databases": len(sql_entries)})
    if not completed:
        raise PruneError("plan has nothing to remove")
    check = post_check(serato_dir, library_root, list(databases.values()), plan.get("prefix"),
                       bool(plan.get("drop_stale_memberships")))
    result = {"status": check["status"], "plan_type": PLAN_TYPE, "completed": completed,
              "post_check": check, "journal": str(journal_path)}
    sr.write_json(result_path, result)
    if check["status"] != "PASS":
        raise PruneError(f"post-check failed; see {result_path}")
    return result


def undo(*, journal_path: Path, serato_dir, databases, serato_manifest, sqlite_manifest, heartbeat,
         undo_journal: Path, process_lister=None) -> dict:
    """Restore every completed file of a prune run from its verified backup copy."""
    sr.require_serato_closed(process_lister)
    restored = []
    for jp, root, manifest, label in (
        (Path(journal_path), Path(serato_dir).resolve(), serato_manifest, "serato"),
        (Path(f"{journal_path}.sqlite.jsonl"), Path(databases[0]).resolve().parent if databases else None, sqlite_manifest, "sqlite"),
    ):
        if not jp.exists() or root is None:
            continue
        done = []
        for line in jp.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("type") == "completed" and rec.get("mutated"):
                done.append(rec)
        engine = mutation_engine.MutationEngine(Path(f"{undo_journal}.{label}.jsonl"), heartbeat, manifest, root, PLAN_TYPE + "-undo")
        engine.start({"undo_of": str(jp)})
        for rec in done:
            target = Path(rec["source"]); backup = Path(rec["backup_path"])
            with engine.item(rec["index"], kind=mutation_engine.SERATO_PATHS, source=target,
                             expected_sha256=rec["after_sha256"], require_backup=False,
                             extra_intent={"undo_of": str(jp), "restore_from": str(backup)}) as txn:
                txn.mark_mutated()
                mutation_engine.restore_from_backup(backup, target, rec["before_sha256"])
                txn.record(restored_sha256=sr.sha256_file(target))
            restored.append(str(target))
        engine.finish({"restored": len(restored)})
    return {"status": "RESTORED", "restored": restored}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serato-dir", required=True, type=Path)
    ap.add_argument("--library-root", required=True, type=Path)
    ap.add_argument("--sqlite-db", action="append", type=Path, default=[])
    ap.add_argument("--prefix", help="only paths under this library-relative folder, e.g. Afrobeat/")
    ap.add_argument("--out", type=Path, help="dry-run plan output")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", type=Path, help="journal of an apply run to reverse")
    ap.add_argument("--plan", type=Path)
    ap.add_argument("--backup-manifest", type=Path, help="PASS manifest for --serato-dir")
    ap.add_argument("--sqlite-backup-manifest", type=Path, help="PASS manifest for the SQLite dir")
    ap.add_argument("--lock-folder", default="Serato")
    ap.add_argument("--session")
    ap.add_argument("--journal", type=Path)
    ap.add_argument("--result", type=Path)
    ap.add_argument("--confirm-serato-closed", action="store_true")
    ap.add_argument("--drop-stale-memberships", action="store_true",
                    help="allow removing a live_twin SQLite row together with its container memberships, "
                         "only when its single live twin is already in every one of those containers")
    a = ap.parse_args()
    try:
        if not a.apply and not a.undo:
            if not a.out:
                raise PruneError("dry run needs --out")
            plan = build_plan(a.serato_dir, a.library_root, a.sqlite_db, a.prefix, a.drop_stale_memberships)
            sr.write_json(a.out, plan)
            print(json.dumps({"status": "DRY-RUN", **plan["summary"], "plan": str(a.out)}, indent=2))
            return 0
        if not a.confirm_serato_closed:
            raise PruneError("--apply/--undo require --confirm-serato-closed")
        sr.require_serato_closed()
        if not (a.session and a.backup_manifest and a.journal):
            raise PruneError("--apply/--undo need --session, --backup-manifest and --journal")
        heartbeat, sman = mutation_engine.run_preflight(lock_folder=a.lock_folder, session=a.session,
                                                       manifest_path=a.backup_manifest, source_root=a.serato_dir)
        qman = None
        if a.sqlite_db:
            if not a.sqlite_backup_manifest:
                raise PruneError("--sqlite-db with --apply/--undo needs --sqlite-backup-manifest")
            qman = backup_manifest.require_pass(a.sqlite_backup_manifest, Path(a.sqlite_db[0]).resolve().parent)
        if a.undo:
            res = undo(journal_path=a.undo, serato_dir=a.serato_dir, databases=a.sqlite_db, serato_manifest=sman,
                       sqlite_manifest=qman, heartbeat=heartbeat, undo_journal=a.journal)
        else:
            if not (a.plan and a.result):
                raise PruneError("--apply needs --plan and --result")
            plan = load_and_validate_plan(a.plan, a.serato_dir, a.library_root, a.sqlite_db)
            res = apply_plan(plan=plan, serato_dir=a.serato_dir, library_root=a.library_root,
                             databases=a.sqlite_db, serato_manifest=sman, sqlite_manifest=qman,
                             heartbeat=heartbeat, journal_path=a.journal, result_path=a.result)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    except (PruneError, sr.SeratoRewriteError, backup_manifest.BackupGateError,
            mutation_engine.MutationRefused, sqlite3.Error) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
