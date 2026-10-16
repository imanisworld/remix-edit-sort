#!/usr/bin/env python3
"""Apply a reviewed smart-crate preview to BOTH the Serato crate files and Serato's SQLite libraries.

Serato DJ 3 keeps every smart crate twice: as ``_Serato_/SmartCrates/<Parent>≫≫<Name>.scrate``
and as rows in ``master.sqlite`` / ``root.sqlite`` (``container`` + ``smart_crate_rules`` +
``dj_container_metadata.filename`` and, in master, ``location_container`` mirrors). Changing
only the files lets Serato ignore or overwrite them, so this tool changes both, all-or-nothing.

Input is the preview folder made by the read-only builder (``apply-plan.json`` + ``staged/``).
Actions:

* ``fix``     new rules for an existing crate (file replaced by the staged bytes, SQLite rules updated)
* ``rename``  new rules and a new name (new file written, old file moved to the retire folder)
* ``create``  new smart crate (and its parent folder if missing), cloned from an existing
              smart crate's rows so every Serato table gets the same shape
* ``retire``  crate file moved to the retire folder (never deleted); its SQLite rows are
              snapshotted into the journal, then removed, so undo can put them back

Modes: dry run (default, needs ``--out``), ``--apply --plan`` (rebuilds the dry run and refuses on
any drift), ``--undo JOURNAL`` (row-level reverse of every SQLite change + file moves back).
Every mode that writes refuses if Serato is running, needs the ``Serato`` folder lock and verified
backup manifests for ``_Serato_`` and the SQLite folder, and checks every original hash first.
A failure anywhere rolls back everything done so far (files from the verified backups, databases
from the verified backups, moves reversed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import backup_manifest
import lock_guard  # noqa: F401  (engine-user contract: lock via run_preflight)
import mutation_engine
import rewrite_serato_paths as sr
import rewrite_serato_sqlite_paths as sq

SAFETY_CLASSIFICATION = "engine-user"
ENGINE_MUTATION_HELPERS = ("_run_sql", "_put_new_file", "_move_file", "_restore_file")

PLAN_TYPE = "serato-smart-crate-apply"
PLAN_VERSION = 1
SEP = "≫≫"
ROOT_NAME = "Serato Library root"
TEMPLATE_CRATE = ("Twerk",)      # an existing top-level smart crate in both databases
TEMPLATE_FOLDER = ("Clean",)     # an existing top-level crate folder in both databases
FIELDS = {9: "genre", 19: "grouping", 4: "file_name"}
FIELD_IDS = {v: k for k, v in FIELDS.items()}
ACTIONS = ("fix", "rename", "create", "retire")


class SmartCrateError(Exception):
    pass


# ------------------------------------------------------------------ .scrate codec

def _u(b: bytes) -> str:
    return b.decode("utf-16-be").rstrip("\x00")


def decode_rules(data: bytes, context: str = "scrate") -> dict:
    """Return {"conjunction": AND|OR, "rules": [{"attribute","value"}]} for a CONTAINS-only crate."""
    match, rules = None, []
    for tag, value in sr.parse_tlv_strict(data, context):
        if tag == "rart":
            match = "AND" if value[-1] == 1 else "OR"
        elif tag == "rurt":
            d = dict(sr.parse_tlv_strict(value, context + ":rurt"))
            op, fid = _u(d["trft"]), int.from_bytes(d["urkt"], "big")
            if op != "cond_con_str" or fid not in FIELDS or "trpt" not in d:
                raise SmartCrateError(f"{context}: only 'contains' rules on genre/grouping/file name are supported")
            rules.append({"attribute": FIELDS[fid], "value": _u(d["trpt"])})
    if match is None or not rules:
        raise SmartCrateError(f"{context}: no match mode or no rules")
    return {"conjunction": match, "rules": rules}


def encode_scrate(template: bytes, conjunction: str, rules: list[dict]) -> bytes:
    """Rules-only .scrate built on an existing crate's header/columns; Serato fills the song list."""
    out, done = [], False
    for tag, value in sr.parse_tlv_strict(template, "template"):
        if tag == "otrk":
            continue
        if tag == "rart":
            value = value[:-1] + bytes([1 if conjunction == "AND" else 0])
        if tag == "rurt":
            if done:
                continue
            done = True
            for r in rules:
                inner = (sr.encode_tlv("trft", "cond_con_str".encode("utf-16-be"))
                         + sr.encode_tlv("urkt", FIELD_IDS[r["attribute"]].to_bytes(4, "big"))
                         + sr.encode_tlv("trpt", r["value"].encode("utf-16-be")))
                out.append(sr.encode_tlv("rurt", inner))
            continue
        out.append(sr.encode_tlv(tag, value))
    return b"".join(out)


def sqlite_rules_json(old_json: str | None, spec: dict) -> str:
    base = json.loads(old_json) if old_json else {"spaces": ["Serato Library", "iTunes"], "live_updates": True}
    base["conjunction"] = spec["conjunction"]
    base["rules"] = [{"type": "STRING", "attribute": r["attribute"], "operation": "CONTAINS", "version": 1,
                      "value": r["value"]} for r in spec["rules"]]
    keys = ["spaces", "conjunction", "live_updates", "rules"]
    return json.dumps({k: base[k] for k in keys if k in base} | {k: v for k, v in base.items() if k not in keys},
                      separators=(",", ":"), ensure_ascii=False)


def crate_parts(relative: str) -> tuple[str, ...]:
    """'SmartCrates/A≫≫B.scrate' -> ('A', 'B'), decoding Serato's ␛␛XX escapes ("/" is ␛␛2f)."""
    stem = Path(relative).name
    if not stem.endswith(".scrate"):
        raise SmartCrateError(f"not a .scrate: {relative}")
    stem = stem[: -len(".scrate")]
    return tuple(re.sub(r"␛␛([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), p) for p in stem.split(SEP))


def safe_scrate_path(serato_dir: Path, relative: str) -> Path:
    """A crate is exactly one non-symlink file directly under SmartCrates."""
    if not isinstance(relative, str):
        raise SmartCrateError("smart-crate path must be a string")
    rel = Path(relative)
    if rel.is_absolute() or len(rel.parts) != 2 or rel.parts[0] != "SmartCrates":
        raise SmartCrateError(f"unsafe smart-crate path (outside SmartCrates): {relative!r}")
    if rel.parts[1] in ("", ".", "..") or not rel.parts[1].endswith(".scrate"):
        raise SmartCrateError(f"unsafe smart-crate file name: {relative!r}")
    root = Path(serato_dir)
    smart = root / "SmartCrates"
    target = root / rel
    if smart.is_symlink() or target.is_symlink():
        raise SmartCrateError(f"symlink smart-crate path refused: {relative!r}")
    if target.resolve(strict=False).parent != smart.resolve(strict=False):
        raise SmartCrateError(f"smart-crate path escapes SmartCrates: {relative!r}")
    return target


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ------------------------------------------------------------------ SQLite model

def _leaf(name: str) -> str:
    return re.split("[\U00100000-\U0010FFFF]", name, maxsplit=1)[0]


def _cols(conn, table) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def _has(conn, table) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _root_id(conn) -> int:
    rows = conn.execute("SELECT id FROM container WHERE name = ? AND parent_id = 0", (ROOT_NAME,)).fetchall()
    if len(rows) != 1:
        raise SmartCrateError(f"expected exactly one '{ROOT_NAME}' container, found {len(rows)}")
    return rows[0][0]


def _resolve(conn, parts: tuple[str, ...]) -> int | None:
    cid = _root_id(conn)
    for part in parts:
        kids = [i for i, n in conn.execute("SELECT id, name FROM container WHERE parent_id = ?", (cid,))
                if _leaf(n).casefold() == part.casefold()]
        if len(kids) > 1:
            raise SmartCrateError(f"{' > '.join(parts)}: {len(kids)} containers named {part!r}")
        if not kids:
            return None
        cid = kids[0]
    return cid


def _row(conn, table, where, args) -> dict | None:
    cur = conn.execute(f'SELECT * FROM "{table}" WHERE {where}', args)
    r = cur.fetchone()
    return dict(zip([d[0] for d in cur.description], r)) if r else None


def _rows(conn, table, where, args) -> list[dict]:
    cur = conn.execute(f'SELECT * FROM "{table}" WHERE {where}', args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _container_tables(conn) -> list[str]:
    return sorted(t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if t != "container" and "container_id" in _cols(conn, t)
                  and not t.startswith(("updated_", "moved_", "expanded_")))


def _plan_db(database: Path, items: list[dict]) -> dict:
    conn = sq.open_immutable(database)
    try:
        is_root = _has(conn, "serato") and "revision" in _cols(conn, "container")
        ops, problems = [], []
        tmpl_crate, tmpl_folder = _resolve(conn, TEMPLATE_CRATE), _resolve(conn, TEMPLATE_FOLDER)
        if tmpl_crate is None or _row(conn, "smart_crate_rules", "container_id = ?", (tmpl_crate,)) is None:
            problems.append(f"template smart crate {TEMPLATE_CRATE} missing")
        if tmpl_folder is None:
            problems.append(f"template folder {TEMPLATE_FOLDER} missing")
        planned_folders = {}
        for it in items:
            parts = crate_parts(it.get("source") or it["target"])
            cid = _resolve(conn, parts) if it["action"] != "create" else None
            if it["action"] in ("fix", "rename", "retire"):
                if cid is None:
                    problems.append(f"{it['action']} {' > '.join(parts)}: no such container"); continue
                rules = _row(conn, "smart_crate_rules", "container_id = ?", (cid,))
                if rules is None:
                    problems.append(f"{' > '.join(parts)}: container {cid} is not a smart crate"); continue
                if conn.execute("SELECT 1 FROM container WHERE parent_id = ?", (cid,)).fetchone():
                    problems.append(f"{' > '.join(parts)}: has child containers"); continue
                c = _row(conn, "container", "id = ?", (cid,))
                op = {"op": it["action"], "key": it["key"], "container_id": cid, "path": list(parts),
                      "old_name": c["name"], "old_rules": rules["rules"]}
                if it["action"] in ("fix", "rename"):
                    op["new_rules"] = sqlite_rules_json(rules["rules"], it["rules"])
                if it["action"] == "rename":
                    new_parts = crate_parts(it["target"])
                    if new_parts[:-1] != parts[:-1]:
                        problems.append(f"rename {parts} -> {new_parts}: moving between folders is not supported"); continue
                    if _resolve(conn, new_parts) is not None:
                        problems.append(f"rename target {' > '.join(new_parts)} already exists"); continue
                    op["new_name"] = new_parts[-1] + c["name"][len(_leaf(c["name"])):]
                    md = _row(conn, "dj_container_metadata", "container_id = ?", (cid,))
                    op["old_filename"] = md["filename"] if md else None
                    op["new_filename"] = Path(it["target"]).name if md else None
                ops.append(op)
            else:  # create
                if _resolve(conn, parts) is not None:
                    problems.append(f"create {' > '.join(parts)}: already exists"); continue
                parent = parts[:-1]
                if len(parent) > 1:
                    problems.append(f"create {parts}: only one folder level is supported"); continue
                parent_ref = _resolve(conn, parent) if parent else _root_id(conn)
                if parent and parent_ref is None and parent not in planned_folders:
                    planned_folders[parent] = f"folder:{SEP.join(parent)}"
                    ops.append({"op": "create_folder", "key": planned_folders[parent], "path": list(parent),
                                "template_id": tmpl_folder, "name": parent[-1]})
                tname = _row(conn, "container", "id = ?", (tmpl_crate,))["name"] if tmpl_crate else ""
                ops.append({"op": "create", "key": it["key"], "path": list(parts),
                            "parent": parent_ref if parent_ref is not None else planned_folders[parent],
                            "template_id": tmpl_crate, "name": parts[-1] + tname[len(_leaf(tname)):],
                            "filename": Path(it["target"]).name, "new_rules": sqlite_rules_json(None, it["rules"])})
        return {"name": database.name, "path": str(database), "sha256": sr.sha256_file(database),
                "is_root_db": is_root, "ops": ops, "problems": problems}
    finally:
        conn.close()


# ------------------------------------------------------------------ plan

def build_plan(preview_dir: Path, serato_dir: Path, databases: list[Path], retire_dir: Path) -> dict:
    preview_dir, serato_dir, retire_dir = Path(preview_dir), Path(serato_dir), Path(retire_dir)
    spec = json.loads((preview_dir / "apply-plan.json").read_text(encoding="utf-8"))
    if len(databases) != 2 or {Path(d).name for d in databases} != {"master.sqlite", "root.sqlite"}:
        raise SmartCrateError("pass both --sqlite-db master.sqlite and --sqlite-db root.sqlite")
    template = (serato_dir / "SmartCrates" / ("SEP".join(TEMPLATE_CRATE) + ".scrate").replace("SEP", SEP))
    if not template.is_file():
        raise SmartCrateError(f"template crate file missing: {template}")
    items, problems = [], []
    for n, it in enumerate(spec["items"], start=1):
        act = it["action"]
        if act not in ACTIONS:
            raise SmartCrateError(f"unknown action {act!r}")
        item = {"key": f"{n}:{act}", "action": act}
        src = it.get("source") if act != "create" else None
        if src:
            p = safe_scrate_path(serato_dir, src)
            if not p.is_file():
                problems.append(f"missing source {src}"); continue
            cur = sr.sha256_file(p)
            if cur != it["source_sha256"]:
                problems.append(f"{src} changed since the preview ({cur[:12]} != {it['source_sha256'][:12]})")
            item.update(source=src, source_sha256=cur)
        if act == "retire":
            dest = retire_dir / Path(src).name
            if dest.exists():
                problems.append(f"retire destination exists: {dest}")
            item["retire_to"] = str(dest)
        else:
            target = safe_scrate_path(serato_dir, it["target"])
            if it.get("staged") != f"staged/{it['target']}":
                raise SmartCrateError(f"staged path does not match target: {it.get('staged')!r}")
            staged = preview_dir / it["staged"]
            if (preview_dir / "staged").is_symlink() or (preview_dir / "staged" / "SmartCrates").is_symlink() or staged.is_symlink():
                raise SmartCrateError(f"staged symlink refused: {staged}")
            data = staged.read_bytes()
            if sha(data) != it["staged_sha256"]:
                problems.append(f"staged file changed: {staged}")
            item.update(target=it["target"], staged=str(staged), staged_sha256=sha(data),
                        rules=decode_rules(data, it["target"]))
            if act in ("create", "rename") and target.exists():
                problems.append(f"{act} target already exists: {it['target']}")
            if act == "rename":
                dest = retire_dir / Path(src).name
                if dest.exists():
                    problems.append(f"retire destination exists: {dest}")
                item["retire_to"] = str(dest)
            if act == "fix" and it["target"] != src:
                problems.append(f"fix must keep the file name: {src}")
        items.append(item)
    dbs = sorted((_plan_db(Path(d), items) for d in databases), key=lambda e: not e["is_root_db"])
    if [e["is_root_db"] for e in dbs] != [True, False]:
        raise SmartCrateError("could not tell root.sqlite from master.sqlite")
    for e in dbs:
        problems += [f"{e['name']}: {p}" for p in e["problems"]]
    if retire_dir.exists() and retire_dir.stat().st_dev != serato_dir.stat().st_dev:
        problems.append("retire folder must be on the same disk as _Serato_ (moves, not copies)")
    if not retire_dir.exists() and Path(retire_dir).parent.stat().st_dev != serato_dir.stat().st_dev:
        problems.append("retire folder must be on the same disk as _Serato_ (moves, not copies)")
    summary = {a: sum(i["action"] == a for i in items) for a in ACTIONS}
    summary.update({f"{e['name']}_ops": len(e["ops"]) for e in dbs})
    summary["problems"] = len(problems)
    return {"plan_type": PLAN_TYPE, "plan_version": PLAN_VERSION, "mode": "dry-run",
            "created_at": datetime.now().astimezone().isoformat(), "preview_dir": str(preview_dir),
            "serato_dir": str(serato_dir), "retire_dir": str(retire_dir),
            "database_v2_sha256": sr.sha256_file(serato_dir / sr.DATABASE_NAME),
            "items": items, "sqlite": dbs, "problems": problems, "summary": summary,
            "status": "READY" if not problems else "REFUSED"}


def _comparable(plan: dict) -> dict:
    p = json.loads(json.dumps(plan, sort_keys=True)); p.pop("created_at", None); p.pop("mode", None)
    return p


def load_and_validate_plan(plan_path: Path, databases, serato_dir=None) -> dict:
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    if plan.get("plan_type") != PLAN_TYPE or plan.get("plan_version") != PLAN_VERSION:
        raise SmartCrateError("not a smart-crate apply plan")
    rebuilt = build_plan(Path(plan["preview_dir"]), Path(serato_dir or plan["serato_dir"]), databases,
                         Path(plan["retire_dir"]))
    if _comparable(rebuilt) != _comparable(plan):
        raise SmartCrateError("plan no longer matches the current files/databases; rebuild the dry run")
    if plan["status"] != "READY":
        raise SmartCrateError("plan has problems: " + "; ".join(plan["problems"][:5]))
    return plan


# ------------------------------------------------------------------ mutation helpers

TRIGGER_FILLED = ("container_keep_alive_list_tbl",)  # Serato triggers may already have added these rows


def _insert(conn, table, row: dict) -> int:
    cols = list(row)
    verb = "INSERT OR IGNORE" if table in TRIGGER_FILLED else "INSERT"
    cur = conn.execute(f'{verb} INTO "{table}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) '
                       f'VALUES ({", ".join("?" for _ in cols)})', [row[c] for c in cols])
    return cur.lastrowid


def _one(cur, what):
    if cur.rowcount != 1:
        raise mutation_engine.MutationVerificationError(f"{what}: expected 1 row, changed {cur.rowcount}")


def _sql_ops(conn, entry: dict, ctx: dict, journal: list) -> None:
    """Execute one database's ops inside the caller's transaction, appending reversible records."""
    root = entry["is_root_db"]
    fk_before = _fk(conn)
    rev = None
    if root:
        rev = conn.execute("SELECT revision FROM serato").fetchone()[0] + 1
        conn.execute("UPDATE serato SET revision = ?", (rev,))
    has_lc = _has(conn, "location_container")
    ids = {}
    for op in entry["ops"]:
        k = op["op"]
        if k in ("fix", "rename"):
            extra = ", revision = ?, needs_refresh = 1" if root else ""
            args = [op["new_rules"]] + ([rev] if root else []) + [op["container_id"], op["old_rules"]]
            _one(conn.execute(f"UPDATE smart_crate_rules SET rules = ?{extra} WHERE container_id = ? AND rules = ?", args),
                 f"rules of {op['path']}")
            if k == "rename":
                extra = ", revision = ?" if root else ""
                args = [op["new_name"]] + ([rev] if root else []) + [op["container_id"], op["old_name"]]
                _one(conn.execute(f"UPDATE container SET name = ?{extra} WHERE id = ? AND name = ?", args), f"name of {op['path']}")
                if op.get("old_filename") is not None:
                    args = [op["new_filename"]] + ([rev] if root else []) + [op["container_id"], op["old_filename"]]
                    _one(conn.execute(f"UPDATE dj_container_metadata SET filename = ?{extra} WHERE container_id = ? AND filename = ?",
                                      args), f"filename of {op['path']}")
            journal.append({**op, "rev": rev})
        elif k in ("create_folder", "create"):
            t = _row(conn, "container", "id = ?", (op["template_id"],))
            parent = op["parent"] if k == "create" else _root_id(conn)
            parent = ids.get(parent, parent)
            if not isinstance(parent, int):
                raise mutation_engine.MutationVerificationError(f"unresolved parent {parent}")
            row = {c: v for c, v in t.items() if c != "id"}
            order = conn.execute("SELECT coalesce(max(list_order), 0) + 1 FROM container WHERE parent_id = ?", (parent,)).fetchone()[0]
            row.update(parent_id=parent, name=op["name"], list_order=order, time_added=int(time.time()), expanded=0)
            if root:
                row["revision"] = rev
            new = _insert(conn, "container", row)
            ids[op["key"]] = new
            ctx.setdefault("root_ids" if root else "master_ids", {})[op["key"]] = new
            if has_lc:  # master mirrors root containers per location
                troot = ctx["template_root_ids"][op["template_id"]] if op["template_id"] in ctx.get("template_root_ids", {}) else None
                for lc in _rows(conn, "location_container", "container_id = ?", (op["template_id"],)):
                    ext = lc["external_container_id"]
                    if ext is not None:
                        if troot is None or ext != troot:
                            raise mutation_engine.MutationVerificationError(
                                f"template {op['template_id']} location {lc['location_id']} mirrors an unknown container")
                        ext = ctx["root_ids"][op["key"]]
                    _insert(conn, "location_container", {"container_id": new, "location_id": lc["location_id"],
                                                         "external_container_id": ext})
            for table in _container_tables(conn):
                if table in ("location_container", "container_asset", "alias_container"):
                    continue
                for r in _rows(conn, table, "container_id = ?", (op["template_id"],)):
                    if table == "smart_crate_rules":
                        if k != "create":
                            continue
                        r["rules"] = op["new_rules"]
                        if "needs_refresh" in r:
                            r["needs_refresh"] = 1
                    if table == "dj_container_metadata":
                        r["filename"] = op.get("filename") if k == "create" else None
                        if r["filename"] is None:
                            continue
                    if "revision" in r and root:
                        r["revision"] = rev
                    r["container_id"] = new
                    _insert(conn, table, r)
            if k == "create" and not _row(conn, "smart_crate_rules", "container_id = ?", (new,)):
                raise mutation_engine.MutationVerificationError("template had no smart_crate_rules row")
            journal.append({**op, "new_id": new, "rev": rev})
        elif k == "retire":
            cid = op["container_id"]
            snap = {"container": [_row(conn, "container", "id = ?", (cid,))]}
            for table in _container_tables(conn):
                snap[table] = _rows(conn, table, "container_id = ?", (cid,))
            if has_lc:
                lcs = [r["id"] for r in snap.get("location_container", [])]
                snap["container_asset"] = [r for i in lcs for r in _rows(conn, "container_asset", "location_container_id = ?", (i,))]
            if snap["container"][0]["name"] != op["old_name"]:
                raise mutation_engine.MutationVerificationError(f"{op['path']} renamed since the plan")
            _one(conn.execute("DELETE FROM container WHERE id = ?", (cid,)), f"retire {op['path']}")
            journal.append({**op, "snapshot": _jsonable(snap), "rev": rev})
        else:
            raise mutation_engine.MutationVerificationError(f"unknown op {k}")
    bad = _fk(conn) - fk_before
    if bad:
        raise mutation_engine.MutationVerificationError(f"{entry['name']}: new foreign key problems {sorted(bad)[:3]}")


def _jsonable(snap):
    return {t: [{c: ({"__hex__": v.hex()} if isinstance(v, bytes) else v) for c, v in r.items()} for r in rows]
            for t, rows in snap.items()}


def _unjson(v):
    return bytes.fromhex(v["__hex__"]) if isinstance(v, dict) and "__hex__" in v else v


def _fk(conn) -> set:
    """Foreign-key violations as a set; Serato's own root rows (parent_id 0) are pre-existing ones."""
    return {tuple(r) for r in conn.execute("PRAGMA foreign_key_check").fetchall()}


def _sql_undo(conn, entry_name: str, records: list[dict], is_root: bool) -> None:
    fk_before = _fk(conn)
    rev = None
    if is_root:
        rev = conn.execute("SELECT revision FROM serato").fetchone()[0] + 1
        conn.execute("UPDATE serato SET revision = ?", (rev,))
    rset = ", revision = ?" if is_root else ""
    for op in reversed(records):
        k = op["op"]
        if k in ("fix", "rename"):
            args = [op["old_rules"]] + ([rev] if is_root else []) + [op["container_id"], op["new_rules"]]
            _one(conn.execute(f"UPDATE smart_crate_rules SET rules = ?{rset} WHERE container_id = ? AND rules = ?", args),
                 f"undo rules {op['path']} (changed since apply?)")
            if k == "rename":
                args = [op["old_name"]] + ([rev] if is_root else []) + [op["container_id"], op["new_name"]]
                _one(conn.execute(f"UPDATE container SET name = ?{rset} WHERE id = ? AND name = ?", args), f"undo name {op['path']}")
                if op.get("old_filename") is not None:
                    args = [op["old_filename"]] + ([rev] if is_root else []) + [op["container_id"], op["new_filename"]]
                    _one(conn.execute(f"UPDATE dj_container_metadata SET filename = ?{rset} WHERE container_id = ? AND filename = ?",
                                      args), f"undo filename {op['path']}")
        elif k in ("create", "create_folder"):
            if conn.execute("SELECT 1 FROM container WHERE parent_id = ?", (op["new_id"],)).fetchone():
                raise mutation_engine.MutationVerificationError(f"undo {op['path']}: something was added inside it")
            _one(conn.execute("DELETE FROM container WHERE id = ? AND name = ?", (op["new_id"], op["name"])),
                 f"undo create {op['path']}")
        elif k == "retire":
            snap = op["snapshot"]
            order = ["container", "location_container"] + [t for t in snap if t not in ("container", "location_container", "container_asset")] + ["container_asset"]
            for table in order:
                for r in snap.get(table, []):
                    _insert(conn, table, {c: _unjson(v) for c, v in r.items()})
    bad = _fk(conn) - fk_before
    if bad:
        raise mutation_engine.MutationVerificationError(f"{entry_name}: new foreign key problems after undo {sorted(bad)[:3]}")


def _run_sql(txn, database: Path, fn) -> None:
    """One IMMEDIATE transaction on one database; commit + checkpoint, or roll back untouched."""
    sq.require_quiescent(database)
    conn = sqlite3.connect(str(database), timeout=0, isolation_level=None)
    in_tx = False
    try:
        conn.execute("PRAGMA busy_timeout = 0"); conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE"); in_tx = True
        fn(conn)
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


def _put_new_file(txn, path: Path, data: bytes) -> None:
    path = Path(path)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)  # never overwrites
    txn.mark_mutated()
    with os.fdopen(fd, "wb") as fh:
        fh.write(data); fh.flush(); os.fsync(fh.fileno())
    if sr.sha256_file(path) != sha(data):
        raise mutation_engine.MutationVerificationError(f"write verification failed: {path}")


def _park(path: Path, folder: Path) -> None:
    """Rollback of a file this run created: move it out of _Serato_ (never deleted)."""
    if Path(path).exists():
        _move_file(None, Path(path), Path(folder) / Path(path).name)


def _move_file(txn, source: Path, destination: Path) -> None:
    """Move without overwriting. ``txn=None`` only for rollback of files this same run created."""
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    if txn is None:
        if Path(destination).exists():
            raise mutation_engine.MutationRefused(f"destination exists: {destination}")
        os.rename(source, destination)
        return
    txn.rename_no_overwrite(Path(source), Path(destination), purpose="smart-crate-retire")


def _restore_file(txn, backup: Path, target: Path, expected: str, sqlite_db: bool = False) -> None:
    """Copy a verified backup back over ``target``; for a database, park stale WAL/SHM first."""
    if txn is not None:
        txn.mark_mutated()
    if sqlite_db:
        for side in sq.sidecar_paths(Path(target)).values():
            if side.exists():
                side.rename(side.with_name(f"{side.name}.discarded-{os.getpid()}"))
    mutation_engine.restore_from_backup(Path(backup), Path(target), expected)


def _restore_db(database: Path, backup: Path, expected: str) -> None:
    _restore_file(None, backup, database, expected, sqlite_db=True)


# ------------------------------------------------------------------ apply / undo

def apply_plan(*, plan: dict, serato_dir: Path, databases: list[Path], serato_manifest: dict, sqlite_manifest: dict,
               heartbeat, journal_path: Path, result_path: Path, process_lister=None, fail_after: str | None = None) -> dict:
    """All-or-nothing. ``fail_after`` is a test hook that raises after the named step."""
    sr.require_serato_closed(process_lister)
    serato_dir = Path(serato_dir).resolve()
    dbs = {Path(d).name: Path(d).resolve() for d in databases}
    retire_dir = Path(plan["retire_dir"])
    journal_path = Path(journal_path)
    sql_records = {}
    park = retire_dir / f"rolled-back-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    undo_stack = []   # callables that reverse completed steps, newest last
    ctx = {}
    # Defense in depth: the CLI also validates and hash-rebuilds the plan.
    # Prevent a caller supplying a plan that writes outside SmartCrates.
    for it in plan["items"]:
        if it.get("source"):
            safe_scrate_path(serato_dir, it["source"])
        if it.get("target"):
            safe_scrate_path(serato_dir, it["target"])

    def hook(step):
        if fail_after == step:
            raise mutation_engine.MutationVerificationError(f"test failure after {step}")

    try:
        engine = mutation_engine.MutationEngine(Path(f"{journal_path}.sqlite.jsonl"), heartbeat, sqlite_manifest,
                                                dbs["root.sqlite"].parent, PLAN_TYPE)
        engine.start({"plan_type": PLAN_TYPE, "store": "sqlite"})
        for i, e in enumerate(plan["sqlite"], start=1):  # root first, then master (needs new root ids)
            db = dbs[e["name"]]
            if e["name"] == "master.sqlite":
                ctx["template_root_ids"] = {}
                rconn, mconn = sq.open_immutable(dbs["root.sqlite"]), sq.open_immutable(db)
                try:
                    for path in (TEMPLATE_CRATE, TEMPLATE_FOLDER):
                        ctx["template_root_ids"][_resolve(mconn, path)] = _resolve(rconn, path)
                finally:
                    rconn.close(); mconn.close()
            recs = []
            with engine.item(i, kind=mutation_engine.SERATO_PATHS, source=db, expected_sha256=e["sha256"],
                             extra_intent={"relative_path": e["name"], "ops": len(e["ops"])}) as txn:
                backup = Path(txn.intent["backup_path"])
                txn.on_rollback(lambda db=db, backup=backup, s=e["sha256"]: _restore_db(db, backup, s))
                _run_sql(txn, db, lambda conn, e=e, recs=recs: _sql_ops(conn, e, ctx, recs))
                txn.record(relative_path=e["name"], after_sha256=sr.sha256_file(db), records=recs)
            sql_records[e["name"]] = recs
            undo_stack.append(lambda db=db, backup=backup, s=e["sha256"]: _restore_db(db, backup, s))
            hook(e["name"])
        engine.finish({"databases": len(plan["sqlite"])})

        fengine = mutation_engine.MutationEngine(journal_path, heartbeat, serato_manifest, serato_dir, PLAN_TYPE)
        fengine.start({"plan_type": PLAN_TYPE, "store": "_Serato_", "items": len(plan["items"])})
        idx = 0
        creates = [it for it in plan["items"] if it["action"] == "create"]
        for it in plan["items"]:
            if it["action"] == "create":
                continue
            idx += 1
            src = serato_dir / it["source"]
            with fengine.item(idx, kind=mutation_engine.SERATO_PATHS, source=src, expected_sha256=it["source_sha256"],
                              extra_intent={"action": it["action"], "relative_path": it["source"]}) as txn:
                backup = Path(txn.intent["backup_path"])
                if it["action"] == "fix":
                    data = Path(it["staged"]).read_bytes()
                    txn.on_rollback(lambda b=backup, s=src, h=it["source_sha256"]: mutation_engine.restore_from_backup(b, s, h))
                    sr._atomic_replace(txn, src, data, it["source_sha256"])
                    if sr.sha256_file(src) != it["staged_sha256"]:
                        raise mutation_engine.MutationVerificationError(f"{src}: post-write hash mismatch")
                    undo_stack.append(lambda b=backup, s=src, h=it["source_sha256"]: mutation_engine.restore_from_backup(b, s, h))
                elif it["action"] == "rename":
                    new = serato_dir / it["target"]; data = Path(it["staged"]).read_bytes()
                    txn.on_rollback(lambda n=new: _park(n, park))
                    _put_new_file(txn, new, data)
                    txn.on_rollback(lambda s=src, d=Path(it["retire_to"]): d.exists() and not s.exists() and os.rename(d, s))
                    _move_file(txn, src, Path(it["retire_to"]))
                    undo_stack.append(lambda n=new: _park(n, park))
                    undo_stack.append(lambda s=src, d=Path(it["retire_to"]): d.exists() and not s.exists() and os.rename(d, s))
                else:  # retire
                    txn.on_rollback(lambda s=src, d=Path(it["retire_to"]): d.exists() and not s.exists() and os.rename(d, s))
                    _move_file(txn, src, Path(it["retire_to"]))
                    undo_stack.append(lambda s=src, d=Path(it["retire_to"]): d.exists() and not s.exists() and os.rename(d, s))
                txn.record(action=it["action"], relative_path=it["source"], target=it.get("target"),
                           retire_to=it.get("retire_to"), after_sha256=sr.sha256_file(src) if src.exists() else None)
            hook(it["key"])
        if creates:
            anchor = serato_dir / sr.DATABASE_NAME
            with fengine.item(idx + 1, kind=mutation_engine.SERATO_PATHS, source=anchor,
                              expected_sha256=plan["database_v2_sha256"],
                              extra_intent={"action": "create", "files": [c["target"] for c in creates],
                                            "note": "database V2 is not changed; it anchors the new crate files"}) as txn:
                made = []
                txn.on_rollback(lambda made=made: [_park(p, park) for p in made])
                for c in creates:
                    p = serato_dir / c["target"]
                    _put_new_file(txn, p, Path(c["staged"]).read_bytes()); made.append(p)
                txn.record(action="create", created=[str(p) for p in made])
                undo_stack.append(lambda made=list(made): [_park(p, park) for p in made])
            hook("create")
        # A completed engine journal is not proof of global success. Check both
        # stores while rollback is still armed, before finishing the file journal.
        check = verify_applied(plan, serato_dir, dbs)
        if check["status"] != "PASS":
            raise mutation_engine.MutationVerificationError(
                f"smart-crate post-check failed: {check['problems'][:5]}")
        hook("post-check")
        fengine.finish({"items": idx + bool(creates)})
        result = {"status": "PASS", "plan_type": PLAN_TYPE, "journal": str(journal_path),
                  "retire_dir": str(retire_dir), "sqlite_records": sql_records, "check": check}
        sr.write_json(result_path, result)
        return result
    except BaseException as exc:
        errors = []
        for fn in reversed(undo_stack):
            try:
                fn()
            except Exception as rollback_exc:  # noqa: BLE001
                errors.append(f"{type(rollback_exc).__name__}: {rollback_exc}")
        status = "PARTIAL" if errors else "ROLLED_BACK"
        sr.write_json(Path(f"{journal_path}.rollback.json"),
                      {"status": status, "rolled_back_steps": len(undo_stack),
                       "errors": errors, "original_error": f"{type(exc).__name__}: {exc}"})
        if errors:
            sr.write_json(result_path,
                          {"status": "PARTIAL", "rollback_errors": errors, "journal": str(journal_path),
                           "action_required": "DO NOT OPEN SERATO; restore from verified backups."})
            raise SmartCrateError("PARTIAL rollback; DO NOT OPEN SERATO. "
                                  f"See {journal_path}.rollback.json") from exc
        raise


def verify_applied(plan: dict, serato_dir: Path, dbs: dict) -> dict:
    problems = []
    for it in plan["items"]:
        if it["action"] in ("fix", "rename", "create"):
            p = Path(serato_dir) / it["target"]
            if not p.is_file() or sr.sha256_file(p) != it["staged_sha256"]:
                problems.append(f"{it['target']} not as staged")
        if it["action"] in ("rename", "retire"):
            if (Path(serato_dir) / it["source"]).exists() or not Path(it["retire_to"]).is_file():
                problems.append(f"{it['source']} not moved to the retire folder")
    for name, db in dbs.items():
        conn = sq.open_immutable(db)
        try:
            for it in plan["items"]:
                parts = crate_parts(it.get("target") or it["source"])
                cid = _resolve(conn, parts)
                if it["action"] == "retire":
                    if cid is not None:
                        problems.append(f"{name}: {parts} still present")
                    continue
                r = _row(conn, "smart_crate_rules", "container_id = ?", (cid,)) if cid else None
                if r is None:
                    problems.append(f"{name}: {parts} missing"); continue
                got = json.loads(r["rules"])
                if got["conjunction"] != it["rules"]["conjunction"] or \
                        [(x["attribute"], x["value"]) for x in got["rules"]] != [(x["attribute"], x["value"]) for x in it["rules"]["rules"]]:
                    problems.append(f"{name}: {parts} rules differ")
            if any(r[0] != "container" or r[2] != "container" for r in _fk(conn)):
                problems.append(f"{name}: foreign key problems")
        finally:
            conn.close()
    return {"status": "PASS" if not problems else "FAIL", "problems": problems}


def undo(*, journal_path: Path, result_path: Path, serato_dir: Path, databases: list[Path], serato_manifest: dict,
         sqlite_manifest: dict, heartbeat, undo_journal: Path, process_lister=None) -> dict:
    """Row-level reverse of the SQLite changes (works after Serato has run) + files back."""
    sr.require_serato_closed(process_lister)
    serato_dir = Path(serato_dir).resolve(); dbs = {Path(d).name: Path(d).resolve() for d in databases}
    res = json.loads(Path(result_path).read_text(encoding="utf-8"))
    file_done = [json.loads(l) for l in Path(journal_path).read_text(encoding="utf-8").splitlines()]
    file_done = [r for r in file_done if r.get("type") == "completed" and r.get("mutated")]
    engine = mutation_engine.MutationEngine(Path(f"{undo_journal}.sqlite.jsonl"), heartbeat, sqlite_manifest,
                                            dbs["root.sqlite"].parent, PLAN_TYPE + "-undo")
    engine.start({"undo_of": str(journal_path)})
    for i, name in enumerate(("master.sqlite", "root.sqlite"), start=1):  # master first: it mirrors root ids
        recs = res["sqlite_records"].get(name) or []
        if not recs:
            continue
        db = dbs[name]
        with engine.item(i, kind=mutation_engine.SERATO_PATHS, source=db, extra_intent={"undo_ops": len(recs)}) as txn:
            backup = Path(txn.intent["backup_path"]); before = txn.intent["before_sha256"]
            txn.on_rollback(lambda db=db, b=backup, s=before: _restore_db(db, b, s))
            _run_sql(txn, db, lambda conn, n=name, r=recs: _sql_undo(conn, n, r, n == "root.sqlite"))
    engine.finish({})
    fengine = mutation_engine.MutationEngine(Path(f"{undo_journal}.files.jsonl"), heartbeat, serato_manifest, serato_dir,
                                             PLAN_TYPE + "-undo")
    fengine.start({"undo_of": str(journal_path)})
    restored = []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for n, rec in enumerate(reversed(file_done), start=1):
        act = rec.get("action")
        if act == "create":
            for p in map(Path, rec["created"]):
                if not p.exists():
                    continue
                with fengine.item(n * 1000 + len(restored), kind=mutation_engine.SERATO_PATHS, source=p, require_backup=False,
                                  extra_intent={"undo": "move created crate out"}) as txn:
                    _move_file(txn, p, Path(res["retire_dir"]) / f"undone-created-{stamp}" / p.name)
                restored.append(str(p))
        elif act == "fix":
            target = Path(rec["source"])
            with fengine.item(n, kind=mutation_engine.SERATO_PATHS, source=target, extra_intent={"undo": "restore original"}) as txn:
                _restore_file(txn, Path(rec["backup_path"]), target, rec["before_sha256"])
            restored.append(str(target))
        elif act in ("rename", "retire"):
            src, parked = Path(rec["source"]), Path(rec["retire_to"])
            if act == "rename":
                new = serato_dir / rec["target"]
                if new.exists():
                    with fengine.item(n * 1000 + 1, kind=mutation_engine.SERATO_PATHS, source=new, require_backup=False,
                                      extra_intent={"undo": "move renamed crate out"}) as txn:
                        _move_file(txn, new, parked.parent / f"undone-created-{stamp}" / new.name)
            with fengine.item(n, kind=mutation_engine.SERATO_PATHS, source=parked, expected_sha256=rec["before_sha256"],
                              require_backup=False, extra_intent={"undo": "move original back"}) as txn:
                _move_file(txn, parked, src)
            restored.append(str(src))
    fengine.finish({"restored": len(restored)})
    return {"status": "UNDONE", "files": restored}


# ------------------------------------------------------------------ CLI

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preview-dir", type=Path, help="folder with apply-plan.json + staged/ (dry run)")
    ap.add_argument("--serato-dir", required=True, type=Path)
    ap.add_argument("--sqlite-db", action="append", type=Path, default=[], required=True)
    ap.add_argument("--retire-dir", type=Path)
    ap.add_argument("--out", type=Path, help="dry-run plan output")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--plan", type=Path)
    ap.add_argument("--undo", type=Path, help="files journal of the apply run")
    ap.add_argument("--result", type=Path, help="apply result JSON (written on apply, read on undo)")
    ap.add_argument("--backup-manifest", type=Path)
    ap.add_argument("--sqlite-backup-manifest", type=Path)
    ap.add_argument("--lock-folder", default="Serato")
    ap.add_argument("--session")
    ap.add_argument("--journal", type=Path)
    ap.add_argument("--confirm-serato-closed", action="store_true")
    a = ap.parse_args()
    try:
        if not a.apply and not a.undo:
            if not (a.out and a.preview_dir and a.retire_dir):
                raise SmartCrateError("dry run needs --preview-dir, --retire-dir and --out")
            sr.require_serato_closed()
            plan = build_plan(a.preview_dir, a.serato_dir, a.sqlite_db, a.retire_dir)
            sr.write_json(a.out, plan)
            print(json.dumps({"status": plan["status"], **plan["summary"], "problems": plan["problems"][:10],
                              "plan": str(a.out)}, indent=2, ensure_ascii=False))
            return 0 if plan["status"] == "READY" else 2
        if not a.confirm_serato_closed:
            raise SmartCrateError("--apply/--undo require --confirm-serato-closed")
        sr.require_serato_closed()
        if not (a.session and a.backup_manifest and a.sqlite_backup_manifest and a.journal and a.result):
            raise SmartCrateError("--apply/--undo need --session, --backup-manifest, --sqlite-backup-manifest, --journal, --result")
        heartbeat, sman = mutation_engine.run_preflight(lock_folder=a.lock_folder, session=a.session,
                                                       manifest_path=a.backup_manifest, source_root=a.serato_dir)
        qman = backup_manifest.require_pass(a.sqlite_backup_manifest, Path(a.sqlite_db[0]).resolve().parent)
        if a.undo:
            res = undo(journal_path=a.undo, result_path=a.result, serato_dir=a.serato_dir, databases=a.sqlite_db,
                       serato_manifest=sman, sqlite_manifest=qman, heartbeat=heartbeat, undo_journal=a.journal)
        else:
            if not a.plan:
                raise SmartCrateError("--apply needs --plan")
            plan = load_and_validate_plan(a.plan, a.sqlite_db, a.serato_dir)
            res = apply_plan(plan=plan, serato_dir=a.serato_dir, databases=a.sqlite_db, serato_manifest=sman,
                             sqlite_manifest=qman, heartbeat=heartbeat, journal_path=a.journal, result_path=a.result)
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str)[:4000])
        return 0
    except (SmartCrateError, sr.SeratoRewriteError, sq.SeratoSqliteError, backup_manifest.BackupGateError,
            mutation_engine.MutationRefused, mutation_engine.MutationVerificationError, sqlite3.Error) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
