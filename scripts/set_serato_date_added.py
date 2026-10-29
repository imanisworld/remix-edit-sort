#!/usr/bin/env python3
"""Set the "date added" of named tracks in _Serato_/database V2 (owner-approved test).

Serato keeps date added per track in database V2 as two fields of the track's
``otrk`` record: ``tadd`` (UTF-16BE decimal epoch seconds) and ``uadd``
(big-endian uint32 epoch seconds). This tool rewrites ONLY those two field
values for the planned tracks. The new value must have the same byte length,
so no record or file length changes; the apply byte-compares the result and
refuses unless the only differing bytes are inside those fields.
master.sqlite is never opened for writing (or at all).

Modes:
  --path P --date ISO --out PLAN        exact-value plan (read-only)
  --apply --plan PLAN ...               write through mutation_engine (lock,
                                        PASS manifest of _Serato_, per-item
                                        backup, journal, atomic replace,
                                        read-back, rollback, undo log)
  --undo UNDO_LOG                       restore database V2 from its backup
  --show P                              print the current fields (read-only)
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "engine-user"
# write_db_bytes() is called only from inside `with engine.item(...)`.
ENGINE_MUTATION_HELPERS = ("write_db_bytes",)

import argparse
import json
import os
import shutil
import struct
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup_manifest  # noqa: E402
import lock_guard  # noqa: E402
import mutation_engine  # noqa: E402


class DateAddedError(Exception):
    pass


def track_fields(data: bytes, portable_id: str) -> list[dict]:
    """Every otrk whose pfil == portable_id, with absolute offsets of tadd/uadd values."""
    out, i = [], 0
    while i + 8 <= len(data):
        tag, length = data[i:i + 4], struct.unpack(">I", data[i + 4:i + 8])[0]
        body_start = i + 8
        if tag == b"otrk":
            fields, j = {}, body_start
            while j + 8 <= body_start + length:
                t, ln = data[j:j + 4], struct.unpack(">I", data[j + 4:j + 8])[0]
                fields[t] = (j + 8, ln)
                j += 8 + ln
            if b"pfil" in fields:
                s, ln = fields[b"pfil"]
                if data[s:s + ln].decode("utf-16-be") == portable_id:
                    rec = {"otrk_offset": i}
                    for name in (b"tadd", b"uadd"):
                        if name in fields:
                            rec[name.decode()] = {"offset": fields[name][0], "length": fields[name][1]}
                    out.append(rec)
        i = body_start + length
    return out


def read_values(data: bytes, rec: dict) -> dict:
    vals = {}
    if "tadd" in rec:
        s, ln = rec["tadd"]["offset"], rec["tadd"]["length"]
        vals["tadd"] = int(data[s:s + ln].decode("utf-16-be"))
    if "uadd" in rec:
        s = rec["uadd"]["offset"]
        vals["uadd"] = struct.unpack(">I", data[s:s + 4])[0]
    return vals


def rebuild(data: bytes, rec: dict, new_epoch: int) -> bytes:
    buf = bytearray(data)
    if "tadd" in rec:
        s, ln = rec["tadd"]["offset"], rec["tadd"]["length"]
        new = str(new_epoch).encode("utf-16-be")
        if len(new) != ln:
            raise DateAddedError("new tadd would change the record length; refusing")
        buf[s:s + ln] = new
    if "uadd" in rec:
        if rec["uadd"]["length"] != 4:
            raise DateAddedError("unexpected uadd length")
        s = rec["uadd"]["offset"]
        buf[s:s + 4] = struct.pack(">I", new_epoch)
    return bytes(buf)


def changed_ranges_ok(before: bytes, after: bytes, rec: dict) -> bool:
    if len(before) != len(after):
        return False
    allowed = set()
    for name in ("tadd", "uadd"):
        if name in rec:
            allowed.update(range(rec[name]["offset"], rec[name]["offset"] + rec[name]["length"]))
    return all(i in allowed for i in range(len(before)) if before[i] != after[i])


def make_plan(db: Path, portable_id: str, new_epoch: int, reason: str) -> dict:
    data = db.read_bytes()
    recs = track_fields(data, portable_id)
    if len(recs) != 1:
        raise DateAddedError(f"expected exactly one otrk for {portable_id}, found {len(recs)}")
    rec = recs[0]
    current = read_values(data, rec)
    if not current:
        raise DateAddedError("track has no tadd/uadd field")
    if any(v <= new_epoch for v in current.values()):
        raise DateAddedError("new date is not earlier than the current one; refusing")
    proposed = rebuild(data, rec, new_epoch)
    return {"mode": "preview-only", "rules": {"approval_required": True, "earlier_only": True},
            "database": str(db), "sha256": mutation_engine.sha256_bytes(data),
            "items": [{"portable_id": portable_id, "fields": rec, "current": current,
                       "current_iso": {k: datetime.fromtimestamp(v).isoformat() for k, v in current.items()},
                       "new_epoch": new_epoch, "new_iso": datetime.fromtimestamp(new_epoch).isoformat(),
                       "reason": reason}],
            "proposed_sha256": mutation_engine.sha256_bytes(proposed)}


def load_and_validate(plan_path: Path, serato_dir: Path) -> tuple[dict, bytes]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("mode") != "preview-only" or not plan.get("rules", {}).get("approval_required"):
        raise DateAddedError("unexpected plan mode")
    db = Path(plan["database"]).resolve()
    if db != (serato_dir / "database V2").resolve():
        raise DateAddedError(f"not {serato_dir}/database V2: {db}")
    if len(plan["items"]) != 1:
        raise DateAddedError("this tool changes exactly one track per run")
    data = db.read_bytes()
    if mutation_engine.sha256_bytes(data) != plan["sha256"]:
        raise DateAddedError("database V2 changed since the plan was made")
    item = plan["items"][0]
    recs = track_fields(data, item["portable_id"])
    if recs != [item["fields"]] or read_values(data, recs[0]) != item["current"]:
        raise DateAddedError("track record no longer matches the plan")
    proposed = rebuild(data, recs[0], item["new_epoch"])
    if mutation_engine.sha256_bytes(proposed) != plan["proposed_sha256"]:
        raise DateAddedError("plan does not reproduce")
    return plan, proposed


def write_db_bytes(path: Path, content: bytes) -> None:
    tmp = path.with_name(f".{path.name}.date-added.tmp")
    with tmp.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    shutil.copystat(path, tmp)
    os.replace(tmp, path)


def apply(plan_path: Path, serato_dir: Path, manifest: dict, backup: Path, out_dir: Path, label: str, heartbeat) -> dict:
    plan, proposed = load_and_validate(plan_path, serato_dir)
    item = plan["items"][0]
    db = (serato_dir / "database V2").resolve()
    backup_copy = backup / "database V2"
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    undo_path = out_dir / f"{label}-date-added-undo-{stamp}.jsonl"
    engine = mutation_engine.MutationEngine(out_dir / f"{label}-recovery-{stamp}.jsonl", heartbeat, manifest,
                                            serato_dir.resolve(), label)
    engine.start({"plan": str(plan_path), "items": 1})
    with engine.item(1, kind=mutation_engine.DATE_ADDED, source=db, expected_sha256=plan["sha256"],
                     backup_path=backup_copy, extra_intent={"portable_id": item["portable_id"],
                                                            "new_epoch": item["new_epoch"]}) as txn:
        before = db.read_bytes()
        txn.on_rollback(lambda: mutation_engine.restore_from_backup(backup_copy, db, plan["sha256"]))
        write_db_bytes(db, proposed)
        txn.mark_mutated()
        after = db.read_bytes()
        if mutation_engine.sha256_bytes(after) != plan["proposed_sha256"]:
            raise RuntimeError("database V2 bytes do not match the plan")
        if not changed_ranges_ok(before, after, item["fields"]):
            raise RuntimeError("bytes outside tadd/uadd changed")
        got = read_values(after, item["fields"])
        if set(got.values()) != {item["new_epoch"]}:
            raise RuntimeError("read-back does not match the new date")
        diff = sum(1 for a, b in zip(before, after) if a != b)
        record = {"database": str(db), "backup": str(backup_copy), "portable_id": item["portable_id"],
                  "before_sha256": plan["sha256"], "after_sha256": plan["proposed_sha256"],
                  "old": item["current"], "new_epoch": item["new_epoch"], "bytes_changed": diff,
                  "length": len(after)}
        txn.record(**record)
        with undo_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    result = {"mode": f"{label}-date-added-apply", "changed": 1, "bytes_changed": diff, "undo_log": str(undo_path)}
    engine.finish(result)
    print(json.dumps(result, indent=2))
    return result


def undo(undo_path: Path, heartbeat) -> None:
    records = [json.loads(x) for x in undo_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    engine = mutation_engine.MutationEngine(undo_path.with_name(undo_path.stem + "-undo-recovery.jsonl"), heartbeat,
                                            manifest=None, source_root=None, label="date-added-undo",
                                            allow_unbacked=True)
    engine.start({"undo_log": str(undo_path), "records": len(records)})
    for pos, rec in enumerate(reversed(records), start=1):
        db, backup = Path(rec["database"]), Path(rec["backup"])
        with engine.item(pos, kind=mutation_engine.DATE_ADDED, source=db, backup_path=None,
                         require_backup=False, extra_intent={"restore_from": str(backup)}) as txn:
            if mutation_engine.sha256_file(db) != rec["after_sha256"]:
                raise RuntimeError("database V2 changed since the apply (Serato rewrote it?); "
                                   "refusing a blind restore -- re-plan instead")
            if mutation_engine.sha256_file(backup) != rec["before_sha256"]:
                raise RuntimeError(f"backup mismatch during undo: {backup}")
            shutil.copy2(backup, db)
            txn.mark_mutated()
            if mutation_engine.sha256_file(db) != rec["before_sha256"]:
                raise RuntimeError(f"undo verification failed: {db}")
            txn.record(restored=str(db))
    result = {"mode": "date-added-undo", "restored": len(records)}
    engine.finish(result)
    print(json.dumps(result, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serato-dir", type=Path, default=Path.home() / "Music/_Serato_")
    ap.add_argument("--show")
    ap.add_argument("--path", help="Serato portable path (no leading /)")
    ap.add_argument("--date", help="new date added, local ISO e.g. 2017-07-04T23:38:26")
    ap.add_argument("--reason", default="")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--plan", type=Path)
    ap.add_argument("--backup", type=Path, help="folder holding the backup copy of 'database V2'")
    ap.add_argument("--backup-manifest", type=Path)
    ap.add_argument("--out-dir", type=Path)
    ap.add_argument("--label", default="date-added")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", type=Path)
    ap.add_argument("--lock-folder", default="Serato")
    ap.add_argument("--session")
    a = ap.parse_args()
    db = a.serato_dir / "database V2"
    try:
        if a.show:
            data = db.read_bytes()
            print(json.dumps([{**r, "values": read_values(data, r)} for r in track_fields(data, a.show)], indent=2))
            return
        if a.undo:
            lock = lock_guard.require_lock_or_exit(a.lock_folder, a.session)
            hb = lock_guard.LockHeartbeat(a.lock_folder, a.session)
            hb.pin_run_id(lock)
            undo(a.undo.resolve(), hb)
            return
        if a.path and a.date and a.out:
            plan = make_plan(db, a.path, int(datetime.fromisoformat(a.date).timestamp()), a.reason)
            a.out.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(json.dumps(plan["items"][0], indent=2, ensure_ascii=False))
            return
        if a.plan and a.apply:
            if not (a.backup and a.backup_manifest and a.session):
                raise DateAddedError("--apply needs --backup, --backup-manifest and --session")
            hb, manifest = mutation_engine.run_preflight(lock_folder=a.lock_folder, session=a.session,
                                                         manifest_path=a.backup_manifest, source_root=a.serato_dir)
            apply(a.plan, a.serato_dir, manifest, a.backup, a.out_dir or a.plan.resolve().parent, a.label, hb)
            return
        if a.plan:
            load_and_validate(a.plan, a.serato_dir)
            print(json.dumps({"mode": "dry-run", "valid": True, "changes_made": 0}))
            return
        ap.error("nothing to do")
    except (DateAddedError, backup_manifest.BackupGateError, mutation_engine.MutationRefused) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
