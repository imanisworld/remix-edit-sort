#!/usr/bin/env python3
"""Read, plan and rewrite the rules of a Serato smart crate (.scrate).

Format (verified on 4,930 real .scrate files, 2026-10-18): a flat sequence of
TLV chunks: 4-byte ASCII tag, 4-byte big-endian length, value. Strings are
UTF-16BE without a terminator. Relevant top-level chunks:

  vrsn  version string
  rart  "brut" + uint32 1 + 1 byte: 1 = match ALL rules, 0 = match ANY
  rlut  "brut" + uint32 1 + 1 byte: live update
  rurt  one rule, itself a TLV list:
          trft  operator (e.g. "cond_con_str" = contains text)
          urkt  uint32 field code (confirmed: 9 Genre, 19 Grouping, 15 BPM, 25 Date added)
          trpt  text value  (or trtt date / urpt uint value)
  osrt, ovct, otrk  sort, column and cached-track chunks (never edited here)

Only fields whose codes are confirmed above may be written. Every chunk that
is not a rule (and the rart byte, if unchanged) is re-emitted from its
original bytes, so an unedited crate round-trips byte-identically.

Modes:
  --show CRATE               print the parsed rules (read-only)
  --plan ... --out PLAN      write an exact-value plan (read-only)
  --apply --plan PLAN        rewrite through scripts/mutation_engine.py: lock,
                             PASS backup manifest of _Serato_, per-item backup
                             copy, write-ahead journal, atomic replace,
                             read-back, rollback, undo log
  --undo UNDO_LOG            restore the crate from its backup
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "engine-user"
# write_crate_bytes() is called only from inside `with engine.item(...)`.
ENGINE_MUTATION_HELPERS = ("write_crate_bytes",)

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

CONFIRMED_FIELDS = {9: "genre", 19: "grouping", 15: "bpm", 25: "added"}
TEXT_OPERATORS = {"cond_con_str"}  # only "contains text" is confirmed for writing


class CrateError(Exception):
    pass


def tlv(data: bytes) -> list[tuple[str, bytes]]:
    out, i = [], 0
    while i < len(data):
        if i + 8 > len(data):
            raise CrateError(f"truncated chunk header at {i}")
        tag = data[i:i + 4].decode("latin-1")
        n = struct.unpack(">I", data[i + 4:i + 8])[0]
        if i + 8 + n > len(data):
            raise CrateError(f"truncated chunk {tag!r} at {i}")
        out.append((tag, data[i + 8:i + 8 + n]))
        i += 8 + n
    return out


def chunk(tag: str, value: bytes) -> bytes:
    return tag.encode("latin-1") + struct.pack(">I", len(value)) + value


def u16(s: str) -> bytes:
    return s.encode("utf-16-be")


def parse(data: bytes) -> dict:
    chunks = tlv(data)
    rules, match_all = [], None
    for tag, value in chunks:
        if tag == "rart":
            match_all = value[-1] == 1
        if tag == "rurt":
            rule = {}
            for t2, v2 in tlv(value):
                if t2 == "urkt":
                    rule["field"] = struct.unpack(">I", v2)[0]
                elif t2 == "trft":
                    rule["op"] = v2.decode("utf-16-be")
                elif t2 in ("trpt", "trtt"):
                    rule["value"] = v2.decode("utf-16-be")
                elif t2 == "urpt":
                    rule["value"] = struct.unpack(">I", v2)[0]
            rules.append(rule)
    return {"match_all": match_all, "rules": rules, "chunks": chunks}


def encode_rule(rule: dict) -> bytes:
    if rule["field"] not in CONFIRMED_FIELDS:
        raise CrateError(f"field code {rule['field']} is not confirmed; refusing to write it")
    if rule["op"] not in TEXT_OPERATORS or not isinstance(rule["value"], str):
        raise CrateError(f"only confirmed text rules can be written, got {rule}")
    # Same sub-chunk order Serato writes: trft, urkt, trpt.
    return chunk("rurt", chunk("trft", u16(rule["op"])) + chunk("urkt", struct.pack(">I", rule["field"]))
                 + chunk("trpt", u16(rule["value"])))


def rebuild(data: bytes, new_rules: list[dict], match_all: bool) -> bytes:
    """Replace the rule chunks (in place of the first one) and the rart flag;
    every other chunk is copied byte-for-byte."""
    chunks = tlv(data)
    out, placed = [], False
    for tag, value in chunks:
        if tag == "rurt":
            if not placed:
                out.extend(encode_rule(r) for r in new_rules)
                placed = True
            continue
        if tag == "rart":
            flag = b"\x01" if match_all else b"\x00"
            out.append(chunk(tag, value if value[-1:] == flag else value[:-1] + flag))
            continue
        out.append(chunk(tag, value))
    if not placed:
        raise CrateError("crate has no rules to replace (not a smart crate?)")
    return b"".join(out)


def write_crate_bytes(path: Path, content: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp-edit")
    tmp.write_bytes(content)
    with tmp.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def make_plan(crate: Path, rules: list[dict], match_all: bool) -> dict:
    data = crate.read_bytes()
    cur = parse(data)
    proposed = rebuild(data, rules, match_all)
    return {
        "mode": "preview-only", "rules": {"approval_required": True, "smart_crate_rules_only": True},
        "items": [{
            "crate": str(crate), "sha256": mutation_engine.sha256_bytes(data),
            "current": {"match_all": cur["match_all"], "rules": cur["rules"]},
            "proposed": {"match_all": match_all, "rules": rules},
            "proposed_sha256": mutation_engine.sha256_bytes(proposed),
        }],
    }


def load_and_validate(plan_path: Path, serato_dir: Path) -> list[dict]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("mode") != "preview-only" or not plan.get("rules", {}).get("approval_required"):
        raise CrateError("unexpected plan mode")
    errors = []
    for item in plan["items"]:
        crate = Path(item["crate"]).resolve()
        if crate.parent != (serato_dir / "SmartCrates").resolve() or crate.suffix != ".scrate":
            errors.append(f"not a smart crate under {serato_dir}/SmartCrates: {crate}")
            continue
        data = crate.read_bytes()
        if mutation_engine.sha256_bytes(data) != item["sha256"]:
            errors.append(f"crate changed since plan: {crate}")
            continue
        proposed = rebuild(data, item["proposed"]["rules"], item["proposed"]["match_all"])
        if mutation_engine.sha256_bytes(proposed) != item["proposed_sha256"]:
            errors.append(f"plan does not reproduce: {crate}")
    if errors:
        raise CrateError("smart crate plan validation failed:\n" + "\n".join(errors))
    return plan["items"]


def apply(plan_path: Path, serato_dir: Path, manifest: dict, backup: Path, out_dir: Path, label: str, heartbeat) -> dict:
    items = load_and_validate(plan_path, serato_dir)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    undo_path = out_dir / f"{label}-smart-crate-undo-{stamp}.jsonl"
    engine = mutation_engine.MutationEngine(out_dir / f"{label}-recovery-{stamp}.jsonl", heartbeat, manifest,
                                            serato_dir, label)
    engine.start({"plan": str(plan_path), "items": len(items)})
    done = []
    for index, item in enumerate(items, start=1):
        crate = Path(item["crate"]).resolve()
        backup_copy = backup / crate.relative_to(serato_dir.resolve())
        with engine.item(index, kind=mutation_engine.SMART_CRATE, source=crate, expected_sha256=item["sha256"],
                         backup_path=backup_copy, extra_intent={"proposed": item["proposed"]}) as txn:
            before = crate.read_bytes()
            txn.on_rollback(lambda p=crate, b=backup_copy, h=item["sha256"]: mutation_engine.restore_from_backup(b, p, h))
            content = rebuild(before, item["proposed"]["rules"], item["proposed"]["match_all"])
            write_crate_bytes(crate, content)
            txn.mark_mutated()
            after = crate.read_bytes()
            got = parse(after)
            if mutation_engine.sha256_bytes(after) != item["proposed_sha256"]:
                raise RuntimeError("crate bytes do not match the plan")
            if got["rules"] != item["proposed"]["rules"] or got["match_all"] != item["proposed"]["match_all"]:
                raise RuntimeError("crate read-back does not match the plan")
            non_rule = lambda cs: [(t, v) for t, v in cs if t not in ("rurt", "rart")]  # noqa: E731
            if non_rule(got["chunks"]) != non_rule(tlv(before)):
                raise RuntimeError("non-rule chunks changed")
            record = {"index": index, "crate": str(crate), "backup": str(backup_copy),
                      "before_sha256": item["sha256"], "after_sha256": item["proposed_sha256"],
                      "old": item["current"], "new": item["proposed"]}
            txn.record(**{k: v for k, v in record.items() if k not in {"index"}})
            with undo_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            done.append(record)
    result = {"mode": f"{label}-smart-crate-apply", "changed": len(done), "undo_log": str(undo_path)}
    engine.finish(result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def undo(undo_path: Path, heartbeat) -> None:
    records = [json.loads(x) for x in undo_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    engine = mutation_engine.MutationEngine(undo_path.with_name(undo_path.stem + "-undo-recovery.jsonl"), heartbeat,
                                            manifest=None, source_root=None, label="smart-crate-undo",
                                            allow_unbacked=True)
    engine.start({"undo_log": str(undo_path), "records": len(records)})
    for pos, rec in enumerate(reversed(records), start=1):
        crate, backup = Path(rec["crate"]), Path(rec["backup"])
        with engine.item(pos, kind=mutation_engine.SMART_CRATE, source=crate, backup_path=None,
                         require_backup=False, extra_intent={"restore_from": str(backup)}) as txn:
            if mutation_engine.sha256_file(backup) != rec["before_sha256"]:
                raise RuntimeError(f"backup mismatch during undo: {backup}")
            shutil.copy2(backup, crate)
            txn.mark_mutated()
            if mutation_engine.sha256_file(crate) != rec["before_sha256"]:
                raise RuntimeError(f"undo verification failed: {crate}")
            txn.record(restored=str(crate))
    result = {"mode": "smart-crate-undo", "restored": len(records)}
    engine.finish(result)
    print(json.dumps(result, indent=2))


def _rule_arg(text: str) -> dict:
    field, op, value = text.split(":", 2)
    return {"op": op, "field": int(field), "value": value}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", type=Path)
    ap.add_argument("--plan", type=Path)
    ap.add_argument("--crate", type=Path)
    ap.add_argument("--rule", action="append", type=_rule_arg, default=[],
                    help="FIELD:OPERATOR:VALUE, e.g. 9:cond_con_str:r&b (repeatable, in order)")
    ap.add_argument("--match", choices=["all", "any"])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--serato-dir", type=Path, default=Path.home() / "Music/_Serato_")
    ap.add_argument("--backup", type=Path)
    ap.add_argument("--backup-manifest", type=Path)
    ap.add_argument("--out-dir", type=Path)
    ap.add_argument("--label", default="smart-crate")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", type=Path)
    ap.add_argument("--lock-folder", default="Serato")
    ap.add_argument("--session")
    a = ap.parse_args()
    try:
        if a.show:
            p = parse(a.show.read_bytes())
            print(json.dumps({"match_all": p["match_all"], "rules": p["rules"]}, indent=2, ensure_ascii=False))
            return
        if a.undo:
            lock = lock_guard.require_lock_or_exit(a.lock_folder, a.session)
            hb = lock_guard.LockHeartbeat(a.lock_folder, a.session)
            hb.pin_run_id(lock)
            undo(a.undo.resolve(), hb)
            return
        if a.crate and a.out:
            if not a.rule or not a.match:
                raise CrateError("--plan building needs --rule and --match")
            plan = make_plan(a.crate, a.rule, a.match == "all")
            a.out.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(json.dumps(plan["items"][0], indent=2, ensure_ascii=False))
            return
        if a.plan and a.apply:
            if not (a.backup and a.backup_manifest and a.session):
                raise CrateError("--apply needs --backup, --backup-manifest and --session")
            hb, manifest = mutation_engine.run_preflight(lock_folder=a.lock_folder, session=a.session,
                                                         manifest_path=a.backup_manifest, source_root=a.serato_dir)
            apply(a.plan, a.serato_dir, manifest, a.backup, a.out_dir or a.plan.resolve().parent, a.label, hb)
            return
        if a.plan:
            print(json.dumps({"mode": "dry-run", "items": len(load_and_validate(a.plan, a.serato_dir)),
                              "changes_made": 0}, indent=2))
            return
        ap.error("nothing to do")
    except (CrateError, backup_manifest.BackupGateError, mutation_engine.MutationRefused) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
