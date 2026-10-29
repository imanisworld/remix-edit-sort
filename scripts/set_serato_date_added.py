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
  --batch-csv CSV --out PLAN            batch plan, many tracks, one file write
  --plan PLAN --simulate-on COPY        dry run on a copy of database V2
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


def index_tracks(data: bytes) -> dict[str, list[dict]]:
    """portable_id -> [field records], one pass over the whole database."""
    out: dict[str, list[dict]] = {}
    i = 0
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
                s0, ln = fields[b"pfil"]
                rec = {"otrk_offset": i}
                for name in (b"tadd", b"uadd"):
                    if name in fields:
                        rec[name.decode()] = {"offset": fields[name][0], "length": fields[name][1]}
                out.setdefault(data[s0:s0 + ln].decode("utf-16-be"), []).append(rec)
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


def changed_ranges_ok_many(before: bytes, after: bytes, recs: list[dict]) -> tuple[bool, int]:
    if len(before) != len(after):
        return False, -1
    allowed = set()
    for rec in recs:
        for name in ("tadd", "uadd"):
            if name in rec:
                allowed.update(range(rec[name]["offset"], rec[name]["offset"] + rec[name]["length"]))
    diffs = [i for i in range(len(before)) if before[i] != after[i]]
    return all(i in allowed for i in diffs), len(diffs)


def rebuild_many(data: bytes, items: list[dict]) -> bytes:
    out = data
    for item in items:
        out = rebuild(out, item["fields"], item["new_epoch"])
    return out


def make_batch_plan(db: Path, wanted: list[tuple[str, int, str]]) -> tuple[dict, dict]:
    """wanted: (portable_id, new_epoch, reason). Returns (plan, skipped counts)."""
    import collections
    data = db.read_bytes()
    idx = index_tracks(data)
    items, skipped = [], collections.Counter()
    for pid, epoch, reason in wanted:
        recs = idx.get(pid, [])
        if len(recs) != 1:
            skipped["not in database V2" if not recs else "duplicate record in database V2"] += 1
            continue
        cur = read_values(data, recs[0])
        if not cur:
            skipped["no date field in database V2"] += 1
            continue
        if any(v <= epoch for v in cur.values()):
            skipped["database V2 date already earlier or equal"] += 1
            continue
        if len(str(epoch)) * 2 != recs[0].get("tadd", {}).get("length", len(str(epoch)) * 2):
            skipped["new date would change field length"] += 1
            continue
        items.append({"portable_id": pid, "fields": recs[0], "current": cur,
                      "current_iso": {k: datetime.fromtimestamp(v).isoformat() for k, v in cur.items()},
                      "new_epoch": epoch, "new_iso": datetime.fromtimestamp(epoch).isoformat(), "reason": reason})
    proposed = rebuild_many(data, items)
    plan = {"mode": "preview-only", "rules": {"approval_required": True, "earlier_only": True, "batch": True},
            "database": str(db), "sha256": mutation_engine.sha256_bytes(data), "items": items,
            "proposed_sha256": mutation_engine.sha256_bytes(proposed)}
    return plan, dict(skipped)


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


def load_and_validate(plan_path: Path, serato_dir: Path, db_override: Path | None = None) -> tuple[dict, bytes]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("mode") != "preview-only" or not plan.get("rules", {}).get("approval_required"):
        raise DateAddedError("unexpected plan mode")
    db = Path(plan["database"]).resolve()
    if db != (serato_dir / "database V2").resolve():
        raise DateAddedError(f"not {serato_dir}/database V2: {db}")
    if len(plan["items"]) != 1 and not plan["rules"].get("batch"):
        raise DateAddedError("single-track plan must have exactly one item")
    if not plan["items"]:
        raise DateAddedError("plan has no items")
    data = (db_override or db).read_bytes()
    if mutation_engine.sha256_bytes(data) != plan["sha256"]:
        raise DateAddedError("database V2 changed since the plan was made")
    idx = index_tracks(data)
    for item in plan["items"]:
        recs = idx.get(item["portable_id"], [])
        if recs != [item["fields"]] or read_values(data, recs[0]) != item["current"]:
            raise DateAddedError(f"track record no longer matches the plan: {item['portable_id']}")
        if any(v <= item["new_epoch"] for v in item["current"].values()):
            raise DateAddedError(f"not an earlier date: {item['portable_id']}")
    proposed = rebuild_many(data, plan["items"])
    if mutation_engine.sha256_bytes(proposed) != plan["proposed_sha256"]:
        raise DateAddedError("plan does not reproduce")
    return plan, proposed


def verify_result(before: bytes, after: bytes, plan: dict) -> dict:
    """Byte-compare + read back every planned date. Raises on any problem."""
    if mutation_engine.sha256_bytes(after) != plan["proposed_sha256"]:
        raise RuntimeError("database V2 bytes do not match the plan")
    ok, diff = changed_ranges_ok_many(before, after, [i["fields"] for i in plan["items"]])
    if not ok:
        raise RuntimeError("bytes outside tadd/uadd changed")
    bad = [i["portable_id"] for i in plan["items"] if set(read_values(after, i["fields"]).values()) != {i["new_epoch"]}]
    if bad:
        raise RuntimeError(f"read-back mismatch on {len(bad)} tracks, e.g. {bad[0]}")
    untouched = index_tracks(after)
    planned = {i["portable_id"] for i in plan["items"]}
    before_idx = index_tracks(before)
    drift = [p for p, recs in before_idx.items() if p not in planned
             and [read_values(before, r) for r in recs] != [read_values(after, r) for r in untouched.get(p, [])]]
    if drift:
        raise RuntimeError(f"{len(drift)} unplanned tracks changed date")
    return {"bytes_changed": diff, "length": len(after), "tracks_read_back": len(plan["items"]),
            "unplanned_tracks_unchanged": len(before_idx) - len(planned)}


def simulate(plan_path: Path, serato_dir: Path, copy: Path) -> dict:
    """Dry run on a COPY of database V2: write the plan into the copy and verify it."""
    if copy.resolve() == (serato_dir / "database V2").resolve():
        raise DateAddedError("--simulate-on must be a copy, not the live database V2")
    plan, proposed = load_and_validate(plan_path, serato_dir, db_override=copy)
    before = copy.read_bytes()
    copy.write_bytes(proposed)
    result = verify_result(before, copy.read_bytes(), plan)
    return {"mode": "simulated-on-copy", "copy": str(copy), "items": len(plan["items"]), **result}


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
                     backup_path=backup_copy, extra_intent={"tracks": len(plan["items"]),
                                                            "first": item["portable_id"]}) as txn:
        before = db.read_bytes()
        txn.on_rollback(lambda: mutation_engine.restore_from_backup(backup_copy, db, plan["sha256"]))
        write_db_bytes(db, proposed)
        txn.mark_mutated()
        after = db.read_bytes()
        check = verify_result(before, after, plan)
        diff = check["bytes_changed"]
        record = {"database": str(db), "backup": str(backup_copy), "portable_id": item["portable_id"] if len(plan["items"]) == 1 else f"{len(plan['items'])} tracks",
                  "tracks": len(plan["items"]),
                  "before_sha256": plan["sha256"], "after_sha256": plan["proposed_sha256"],
                  "old": item["current"], "new_epoch": item["new_epoch"], "bytes_changed": diff,
                  "length": len(after)}
        txn.record(**record)
        with undo_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    result = {"mode": f"{label}-date-added-apply", "changed": len(plan["items"]), "bytes_changed": diff, "undo_log": str(undo_path)}
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
    ap.add_argument("--batch-csv", type=Path, help="CSV with portable_id,new_epoch,reason -> batch plan (--out)")
    ap.add_argument("--simulate-on", type=Path, help="dry-run the plan on this COPY of database V2")
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
        if a.batch_csv and a.out:
            import csv
            with a.batch_csv.open(encoding="utf-8") as handle:
                wanted = [(r["portable_id"], int(r["new_epoch"]), r.get("reason", "")) for r in csv.DictReader(handle)]
            plan, skipped = make_batch_plan(db, wanted)
            a.out.write_text(json.dumps(plan, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            print(json.dumps({"planned": len(plan["items"]), "skipped": skipped}, indent=2))
            return
        if a.plan and a.simulate_on:
            print(json.dumps(simulate(a.plan, a.serato_dir, a.simulate_on), indent=2))
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
