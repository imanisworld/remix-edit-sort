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
PLAN_VERSION = 5
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


_NAME_SUFFIX = re.compile(r"[\U000F0000-\U0010FFFF][0-9a-fA-F]{32,40}$")


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


def _is_smart(conn, cid) -> bool:
    return _has_table(conn, "smart_crate_rules") and conn.execute(
        "SELECT 1 FROM smart_crate_rules WHERE container_id = ?", (cid,)).fetchone() is not None


def _plan_add(conn, stale_ca_id, cid, twin_id, next_order) -> dict | str:
    """Insert spec that mirrors Serato's own container_asset rows, or a reason string if unsafe."""
    cols = {r[1]: r for r in conn.execute('PRAGMA table_info("container_asset")')}
    st = conn.execute("SELECT ca.*, sa.space_id FROM container_asset ca JOIN space_asset sa ON sa.id = ca.space_asset_id "
                      "WHERE ca.id = ?", (stale_ca_id,)).fetchone()
    names = [d[0] for d in conn.execute("SELECT ca.*, sa.space_id FROM container_asset ca JOIN space_asset sa "
                                        "ON sa.id = ca.space_asset_id LIMIT 0").description]
    st = dict(zip(names, st))
    tsa = conn.execute("SELECT id FROM space_asset WHERE asset_id = ? AND space_id = ?", (twin_id, st["space_id"])).fetchone()
    if not tsa:
        return "live copy has no space_asset in the same space"
    values = {"space_asset_id": tsa[0]}
    if "container_id" in cols:
        values["container_id"] = cid; group = ("container_id", cid)
    elif "location_container_id" in cols:
        lc = st["location_container_id"]
        if _has_table(conn, "location_container") and "location_id" in [r[1] for r in conn.execute('PRAGMA table_info("asset")')]:
            loc = conn.execute("SELECT location_id FROM location_container WHERE id = ?", (lc,)).fetchone()
            tloc = conn.execute("SELECT location_id FROM asset WHERE id = ?", (twin_id,)).fetchone()
            sloc = conn.execute("SELECT location_id FROM asset WHERE id = (SELECT asset_id FROM space_asset WHERE id = ?)",
                                (st["space_asset_id"],)).fetchone()
            if not (loc and tloc and sloc) or tloc[0] != sloc[0]:
                return "live copy is on a different location than the stale row"
        values["location_container_id"] = lc; group = ("location_container_id", lc)
    else:
        return "unknown container_asset layout"
    if "asset_id" in cols:
        values["asset_id"] = twin_id
    if "external_container_asset_id" in cols:
        values["external_container_asset_id"] = None
    if "revision" in cols:
        if not _has_table(conn, "serato"):
            return "no serato.revision to stamp"
        values["revision"] = conn.execute("SELECT revision FROM serato").fetchone()[0]
    if "list_order" in cols:
        key = group
        if key not in next_order:
            mx, nn = conn.execute(f'SELECT max(list_order), count(list_order) FROM container_asset WHERE "{group[0]}" = ?',
                                  (group[1],)).fetchone()
            next_order[key] = None if nn == 0 else int(mx) + 1
        values["list_order"] = next_order[key]
        if next_order[key] is not None:
            next_order[key] += 1
        elif cols["list_order"][3]:
            return "container has no list_order to append after"
    for name, (_, _, _, notnull, default, pk) in cols.items():
        if notnull and default is None and not pk and name not in values:
            return f"cannot fill required column {name}"
    dup = conn.execute(f'SELECT 1 FROM container_asset WHERE space_asset_id = ? AND "{group[0]}" = ?',
                       (tsa[0], group[1])).fetchone()
    if dup:
        return "live copy already present (duplicate)"
    return {"values": values}


def _membership_decision(conn, rowid, path, astore_rows, cls, add_live=False, next_order=None,
                         drop_smart=False, twin_map=None) -> dict:
    name = path.rsplit("/", 1)[-1]
    twins = [(r, p) for r, p in astore_rows if r != rowid and p.rsplit("/", 1)[-1] == name and cls.exists(p)]
    out = {"live_twin_rowids": [r for r, _ in twins], "live_twin_paths": [p for _, p in twins]}
    if len(twins) > 1 and twin_map and path in twin_map:
        # Several live files share the name: pick the one the approved move map
        # says this old path became.  It must be exactly one live record.
        chosen = [(r, p) for r, p in twins if p == twin_map[path]]
        if len(chosen) == 1:
            twins = chosen; out["twin_chosen_by"] = "move map"
        else:
            out["ok"] = False
            out["why"] = f"move map target {twin_map[path]!r} matches {len(chosen)} live records (need exactly 1)"
            return out
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
    adds, smart, smart_dropped, blocked, seen = [], [], [], [], set()
    for c in conts:
        if c["live_copy_present"] or c["container_id"] in seen:
            continue
        seen.add(c["container_id"])
        if _is_smart(conn, c["container_id"]):
            (smart_dropped if drop_smart else smart).append(c["container"]); continue
        if not add_live:
            blocked.append(f"{c['container']}: live copy missing"); continue
        spec = _plan_add(conn, c["container_asset_id"], c["container_id"], twins[0][0],
                         next_order if next_order is not None else {})
        if isinstance(spec, str):
            blocked.append(f"{c['container']}: {spec}")
        else:
            adds.append({"container_id": c["container_id"], "container": c["container"], **spec})
    ok = not smart and not blocked
    why = None
    if not ok:
        bits = []
        if smart:
            bits.append(f"live copy missing from {len(smart)} smart crate(s) (rule-based, not inserted): {sorted(smart)}")
        if blocked:
            bits.append(f"live copy missing from {len(blocked)} container(s): {blocked}")
        why = "; ".join(bits)
    out.update(containers=conts, live_twin_path=twins[0][1], live_twin_rowid=twins[0][0], lacking=lacking,
               smart_lacking=sorted(smart), smart_dropped_without_live=sorted(smart_dropped),
               smart_dropped_ids=sorted({c["container_id"] for c in conts
                                         if not c["live_copy_present"] and c["container"] in smart_dropped}),
               blocked=blocked, adds=adds if ok else [], ok=ok, why=why)
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

def load_twin_map(path) -> dict | None:
    """Composed map ([{from,to}] absolute or Serato-relative) -> {old stored: new stored}."""
    if not path:
        return None
    entries = sr.normalize_rename_map(json.loads(Path(path).read_text(encoding="utf-8")))
    return {e["from"]: e["to"] for e in entries}


def build_plan(serato_dir: Path, library_root: Path, databases: list[Path], prefix: str | None,
               drop_stale_memberships: bool = False, add_live_memberships: bool = False,
               drop_stale_smart_memberships: bool = False, twin_map: dict | None = None) -> dict:
    if drop_stale_smart_memberships and not drop_stale_memberships:
        raise PruneError("--drop-stale-smart-memberships needs --drop-stale-memberships")
    if add_live_memberships and not drop_stale_memberships:
        raise PruneError("--add-live-memberships needs --drop-stale-memberships")
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
            rows = []; next_order: dict = {}
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
                        order_snapshot = dict(next_order)
                        decision = _membership_decision(conn, rowid, path, astore_rows, cls,
                                                        add_live_memberships, next_order,
                                                        drop_stale_smart_memberships, twin_map)
                        if not decision["ok"]:
                            next_order.clear(); next_order.update(order_snapshot)  # held rows reserve no positions
                        resolved = len(decision.get("containers", []))
                        total = crate_mem.get("container_asset(via space_asset)", crate_mem.get("container_asset"))
                        if decision["ok"] and set(crate_mem) <= {"container_asset", "container_asset(via space_asset)"} \
                                and total == resolved:
                            n_add = len(decision["adds"])
                            action, reason = "remove", (f"{c}; add live copy to {n_add} container(s), drop {resolved} "
                                                        "stale membership(s)" if n_add else
                                                        f"{c}; drop {resolved} stale membership(s), live copy in every container")
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
            has_ca = _has_table(conn, "container_asset")
            db_entries.append({
                "container_asset_max_id": (conn.execute("SELECT max(id) FROM container_asset").fetchone()[0] or 0) if has_ca else None,
                "planned_adds": sum(len(r.get("membership_check", {}).get("adds", [])) for r in rows if r["action"] == "remove"),
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
        summary[f"{e['name']}_memberships_added"] = e["planned_adds"]
        summary[f"{e['name']}_smart_memberships_dropped_without_live"] = sum(
            len(r.get("membership_check", {}).get("smart_dropped_ids", [])) for r in e["rows"] if r["action"] == "remove")
        summary[f"{e['name']}_memberships_dropped"] = sum(len(r.get("drop_container_asset_ids", []))
                                                          for r in e["rows"] if r["action"] == "remove")
    return {
        "plan_type": PLAN_TYPE, "plan_version": PLAN_VERSION, "mode": "dry-run",
        "approval_required": True, "created_at": datetime.now().astimezone().isoformat(),
        "serato_dir": str(serato_dir), "library_root": str(library_root), "prefix": prefix,
        "drop_stale_memberships": bool(drop_stale_memberships),
        "add_live_memberships": bool(add_live_memberships),
        "drop_stale_smart_memberships": bool(drop_stale_smart_memberships),
        "twin_map": dict(sorted((twin_map or {}).items())),
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
                         bool(plan.get("drop_stale_memberships")), bool(plan.get("add_live_memberships")),
                       bool(plan.get("drop_stale_smart_memberships")), plan.get("twin_map") or None)
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
        for r in rows:  # 1) add the live copy where it is missing (append at end, like Serato)
            for add in r.get("membership_check", {}).get("adds", []):
                v = add["values"]
                grp = "container_id" if "container_id" in v else "location_container_id"
                if conn.execute(f'SELECT 1 FROM container_asset WHERE space_asset_id = ? AND "{grp}" = ?',
                                (v["space_asset_id"], v[grp])).fetchone():
                    raise mutation_engine.MutationVerificationError(f"{database.name}: live copy appeared in {add['container']}")
                cols = list(v)
                conn.execute(f'INSERT INTO container_asset ({", ".join(chr(34)+c+chr(34) for c in cols)}) '
                             f'VALUES ({", ".join("?" for _ in cols)})', [v[c] for c in cols])
        for r in rows:  # 2) drop the stale memberships and the stale row
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
        if ca_digest is not None:
            mx = entry.get("container_asset_max_id") or 0
            new_ids = [i for (i,) in conn.execute("SELECT id FROM container_asset WHERE id > ?", (mx,))]
            if _table_digest(conn, "container_asset", new_ids) != ca_digest:
                raise mutation_engine.MutationVerificationError(f"{database.name}: an original membership changed")
            if len(new_ids) != entry.get("planned_adds", 0):
                raise mutation_engine.MutationVerificationError(
                    f"{database.name}: {len(new_ids)} memberships added, planned {entry.get('planned_adds', 0)}")
            for r in entry["rows"]:
                mc = r.get("membership_check")
                if r["action"] != "remove" or not mc:
                    continue
                now_in = {c for _, c in (_membership_rows(conn, mc["live_twin_rowid"]) or [])}
                missing = {c["container_id"] for c in mc["containers"]} - now_in - set(mc.get("smart_dropped_ids", []))
                if missing:
                    raise mutation_engine.MutationVerificationError(
                        f"{database.name}: live copy of {r['path']} not in former container(s) {sorted(missing)}")
        if entry["history_digest"] is not None and _table_digest(conn, sq.HISTORY_TABLE) != entry["history_digest"]:
            raise mutation_engine.MutationVerificationError(f"{database.name}: history_entry changed")
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()
    return {"rows_removed": len(removed), "foreign_key_check_rows": len(fk)}


def post_check(serato_dir, library_root, databases, prefix, drop_stale_memberships=False, add_live=False,
               drop_smart=False, twin_map=None) -> dict:
    again = build_plan(serato_dir, library_root, databases, prefix, drop_stale_memberships, add_live, drop_smart,
                       twin_map)
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
                       bool(plan.get("drop_stale_memberships")), bool(plan.get("add_live_memberships")),
                       bool(plan.get("drop_stale_smart_memberships")), plan.get("twin_map") or None)
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
    ap.add_argument("--drop-stale-smart-memberships", action="store_true",
                    help="with --drop-stale-memberships: a stale live_twin row's memberships in SMART crates may be "
                         "dropped even if the live copy is not there yet (smart crates rebuild from their rules); "
                         "regular crates still need the live copy (or --add-live-memberships)")
    ap.add_argument("--twin-map", type=Path,
                    help="composed move map (compose_serato_rename_map.py --out): when a stale row's file name has "
                         "several live copies, use the copy this map says the old path became")
    ap.add_argument("--add-live-memberships", action="store_true",
                    help="with --drop-stale-memberships: first add the live twin to each normal (non-smart) crate "
                         "it is missing from, appended at the end; smart crates are reported, never inserted into")
    a = ap.parse_args()
    try:
        if not a.apply and not a.undo:
            if not a.out:
                raise PruneError("dry run needs --out")
            plan = build_plan(a.serato_dir, a.library_root, a.sqlite_db, a.prefix, a.drop_stale_memberships,
                              a.add_live_memberships, a.drop_stale_smart_memberships, load_twin_map(a.twin_map))
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
