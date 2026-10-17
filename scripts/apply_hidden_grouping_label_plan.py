#!/usr/bin/env python3
"""Move a record-pool watermark out of the hidden second Grouping frame into
Label, or drop an owner-approved stray value from that frame.

MP3s carry two Grouping frames: TIT1 (what Serato shows; written by
apply_grouping_plan.py) and GRP1 (iTunes "Grouping", hidden in Serato). Some
files have a record-pool watermark in GRP1 ("VirDiKO Global Music
Promotion....", "www.bpmsupreme.com") or a stray value ("November").

Owner rules (Imani, 2026-10-17):
  - a watermark is never deleted: it MOVES to Label (TPUB). If Label is empty
    it becomes the watermark; if Label already has a value, the watermark is
    appended after "; " (skipped if already present, case-insensitive).
  - an approved stray value (e.g. "November") may simply be dropped.
  - every other GRP1 value is refused (the item fails validation).

Two modes:
  --build   Read-only. Takes --paths (one absolute path per line) plus the
            exact --watermark / --drop values, writes hidden-grouping-plan.json
            and hidden-grouping-preview.csv. Never touches media.
  --apply   Executes an approved plan through scripts/mutation_engine.py
            (lock, PASS backup manifest, per-item backup copy, write-ahead
            journal, read-back verification, rollback, undo log).
  --undo    Restores every file of an apply run from its per-item backup.

MP3 (ID3v2.3/2.4) only. ID3v2.2 and other formats are skipped at build time.
TIT1 (the visible Grouping), the audio payload and Serato frames are verified
byte-identical after every write.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "engine-user"
# write_hidden_grouping_label() is called only from inside `with engine.item(...)`.
ENGINE_MUTATION_HELPERS = ("write_hidden_grouping_label",)

import argparse
import csv
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mutagen.id3 import ID3, ID3NoHeaderError, GRP1, TPUB  # noqa: E402

import backup_manifest  # noqa: E402
import lock_guard  # noqa: E402
import mutation_engine  # noqa: E402
from apply_grouping_plan import (  # noqa: E402
    audio_payload, serato_digest, sha256_bytes, sha256_file,
)

PLAN_NAME = "hidden-grouping-plan.json"
PREVIEW_NAME = "hidden-grouping-preview.csv"


def _texts(tags: ID3, frame_id: str) -> list[str]:
    values = []
    for frame in tags.getall(frame_id):
        values.extend(str(v).strip() for v in getattr(frame, "text", []) if str(v).strip())
    return values


def read_state(path: Path) -> dict:
    """Read-only snapshot of the three fields this tool reasons about."""
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return {"id3_version": None, "grp1": [], "label": [], "tit1": []}
    return {
        "id3_version": tags.version[1],
        "grp1": _texts(tags, "GRP1"),
        "label": _texts(tags, "TPUB"),
        "tit1": _texts(tags, "TIT1"),
    }


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(";") if part.strip()]


def derive(state: dict, watermarks: list[str], drops: list[str]) -> dict:
    """The exact result the owner rules produce for one file.

    Returns {"new_grp1": [...], "new_label": str, "moved": [...], "dropped": [...],
    "refused": [...]}; any refused value means the file must not be planned."""
    wm = {w.casefold(): w for w in watermarks}
    dr = {d.casefold() for d in drops}
    keep, moved, dropped, refused = [], [], [], []
    for raw in state["grp1"]:
        for value in _split(raw):
            key = value.casefold()
            if key in wm:
                moved.append(value)
            elif key in dr:
                dropped.append(value)
            else:
                refused.append(value)
                keep.append(value)
    label_parts = [p for raw in state["label"] for p in _split(raw)]
    for value in moved:
        if value.casefold() not in {p.casefold() for p in label_parts}:
            label_parts.append(value)
    return {
        "new_grp1": ["; ".join(keep)] if keep else [],
        "new_label": "; ".join(label_parts),
        "moved": moved,
        "dropped": dropped,
        "refused": refused,
    }


def build(paths: list[Path], watermarks: list[str], drops: list[str], out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    items, skipped, rows = [], [], []
    for path in paths:
        if path.suffix.casefold() != ".mp3" or not path.is_file():
            skipped.append({"path": str(path), "reason": "not an existing mp3"})
            continue
        state = read_state(path)
        if state["id3_version"] not in {3, 4}:
            skipped.append({"path": str(path), "reason": f"ID3v2.{state['id3_version']} not supported"})
            continue
        result = derive(state, watermarks, drops)
        row = {
            "path": str(path),
            "current_hidden_grouping": " || ".join(state["grp1"]),
            "current_label": " || ".join(state["label"]),
            "visible_grouping": " || ".join(state["tit1"]),
            "proposed_hidden_grouping": " || ".join(result["new_grp1"]),
            "proposed_label": result["new_label"],
            "moved_to_label": "; ".join(result["moved"]),
            "dropped": "; ".join(result["dropped"]),
            "status": "",
        }
        if result["refused"]:
            row["status"] = "REFUSED: unapproved value " + "; ".join(result["refused"])
            skipped.append({"path": str(path), "reason": row["status"]})
        elif not (result["moved"] or result["dropped"]):
            row["status"] = "nothing to change"
            skipped.append({"path": str(path), "reason": row["status"]})
        else:
            row["status"] = "planned"
            items.append({
                "path": str(path),
                "id3_version": state["id3_version"],
                "current_grp1": state["grp1"],
                "current_label": state["label"],
                "current_tit1": state["tit1"],
                "new_grp1": result["new_grp1"],
                "new_label": result["new_label"],
                "provenance": {"current_sha256": sha256_file(path)},
            })
        rows.append(row)
    plan = {
        "mode": "preview-only",
        "rules": {
            "approval_required": True,
            "hidden_grouping_label": {"watermarks_to_label": watermarks, "drop_values": drops},
        },
        "items": items,
        "skipped": skipped,
    }
    (out_dir / PLAN_NAME).write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (out_dir / PREVIEW_NAME).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["path"])
        writer.writeheader()
        writer.writerows(rows)
    return {"planned": len(items), "skipped": len(skipped), "plan": str(out_dir / PLAN_NAME)}


def load_and_validate(plan_path: Path, root: Path) -> tuple[dict, list[dict]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    rules = plan.get("rules", {})
    if plan.get("mode") != "preview-only" or not rules.get("approval_required"):
        raise RuntimeError("unexpected plan mode")
    hg = rules.get("hidden_grouping_label") or {}
    watermarks, drops = hg.get("watermarks_to_label", []), hg.get("drop_values", [])
    if not (watermarks or drops):
        raise RuntimeError("plan carries no approved watermark/drop values")
    errors, seen = [], set()
    for item in plan["items"]:
        path = Path(item["path"]).resolve()
        if str(path) in seen:
            errors.append(f"duplicate path: {path}")
        seen.add(str(path))
        if root not in path.parents:
            errors.append(f"outside target root: {path}")
            continue
        if not path.is_file():
            errors.append(f"missing file: {path}")
            continue
        state = read_state(path)
        if (state["grp1"] != item["current_grp1"] or state["label"] != item["current_label"]
                or state["tit1"] != item["current_tit1"] or state["id3_version"] != item["id3_version"]):
            errors.append(f"tags changed since preview: {path}")
            continue
        expected = derive(state, watermarks, drops)
        if expected["refused"]:
            errors.append(f"unapproved hidden Grouping value: {path}: {expected['refused']}")
        if expected["new_grp1"] != item["new_grp1"] or expected["new_label"] != item["new_label"]:
            errors.append(f"item does not match the approved rules: {path}")
    if errors:
        raise RuntimeError("Hidden-grouping plan validation failed:\n" + "\n".join(errors))
    return plan, plan["items"]


def write_hidden_grouping_label(path: Path, new_grp1: list[str], new_label: str, version: int) -> None:
    tags = ID3(path)
    encoding = 3 if version == 4 else 1
    tags.delall("GRP1")
    if new_grp1:
        tags.add(GRP1(encoding=encoding, text=new_grp1))
    tags.delall("TPUB")
    if new_label:
        tags.add(TPUB(encoding=encoding, text=[new_label]))
    tags.save(path, v2_version=version)


def apply(plan_path: Path, manifest: dict, backup: Path, root: Path, out_dir: Path,
          label: str, heartbeat) -> dict:
    plan, items = load_and_validate(plan_path, root)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    undo_path = out_dir / f"{label}-hidden-grouping-undo-{stamp}.jsonl"
    result_path = out_dir / f"{label}-hidden-grouping-result-{stamp}.json"
    journal_path = out_dir / f"{label}-recovery-{stamp}.jsonl"
    engine = mutation_engine.MutationEngine(journal_path, heartbeat, manifest, root, label)
    engine.start({"plan": str(plan_path), "items": len(items)})
    completed = []
    for index, item in enumerate(items, start=1):
        path = Path(item["path"]).resolve()
        backup_copy = backup / path.relative_to(root)
        with engine.item(
            index,
            kind=mutation_engine.HIDDEN_GROUPING_LABEL,
            source=path,
            expected_sha256=item["provenance"]["current_sha256"],
            backup_path=backup_copy,
            extra_intent={k: item[k] for k in ("current_grp1", "current_label", "new_grp1", "new_label")},
        ) as txn:
            before_hash = sha256_file(path)
            txn.on_rollback(lambda p=path, b=backup_copy, h=before_hash:
                            mutation_engine.restore_from_backup(b, p, h))
            before_audio = sha256_bytes(audio_payload(path))
            before_serato = serato_digest(ID3(path))
            write_hidden_grouping_label(path, item["new_grp1"], item["new_label"], item["id3_version"])
            txn.mark_mutated()
            after = read_state(path)
            if after["grp1"] != item["new_grp1"]:
                raise RuntimeError("hidden Grouping verification failed")
            if after["label"] != ([item["new_label"]] if item["new_label"] else []):
                raise RuntimeError("Label verification failed")
            if after["tit1"] != item["current_tit1"] or after["id3_version"] != item["id3_version"]:
                raise RuntimeError("visible Grouping or ID3 version changed")
            if sha256_bytes(audio_payload(path)) != before_audio:
                raise RuntimeError("audio payload changed")
            if serato_digest(ID3(path)) != before_serato:
                raise RuntimeError("Serato frames changed")
            record = {
                "index": index, "path": str(path), "backup": str(backup_copy),
                "old_grp1": item["current_grp1"], "old_label": item["current_label"],
                "new_grp1": item["new_grp1"], "new_label": item["new_label"],
                "before_sha256": before_hash, "after_sha256": sha256_file(path),
            }
            txn.record(**{k: v for k, v in record.items() if k not in {"index", "path"}})
            with undo_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            completed.append(record)
    result = {
        "mode": f"{label}-hidden-grouping-apply", "plan": str(plan_path),
        "verified_backup": str(backup), "tracks_in_plan": len(items),
        "changed": len(completed), "undo_log": str(undo_path),
    }
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    engine.finish(result)
    print(json.dumps({**result, "result_path": str(result_path)}, indent=2))
    return result


def undo(undo_path: Path, heartbeat) -> None:
    records = [json.loads(line) for line in undo_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    journal_path = undo_path.with_name(undo_path.stem + "-undo-recovery.jsonl")
    engine = mutation_engine.MutationEngine(journal_path, heartbeat, manifest=None, source_root=None,
                                            label="hidden-grouping-undo", allow_unbacked=True)
    engine.start({"undo_log": str(undo_path), "records": len(records)})
    restored = 0
    for position, record in enumerate(reversed(records), start=1):
        path, backup = Path(record["path"]), Path(record["backup"])
        with engine.item(position, kind=mutation_engine.HIDDEN_GROUPING_LABEL, source=path,
                         backup_path=None, require_backup=False,
                         extra_intent={"undoing_index": record.get("index"), "restore_from": str(backup),
                                       "restore_to_sha256": record["before_sha256"]}) as txn:
            if sha256_file(backup) != record["before_sha256"]:
                raise RuntimeError(f"backup mismatch during undo: {backup}")
            shutil.copy2(backup, path)
            txn.mark_mutated()
            if sha256_file(path) != record["before_sha256"]:
                raise RuntimeError(f"undo verification failed: {path}")
            txn.record(restored_to=str(path), sha256=record["before_sha256"])
            restored += 1
    result = {"mode": "hidden-grouping-undo", "restored": restored, "recovery_journal": str(journal_path)}
    engine.finish(result)
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--paths", type=Path, help="text file, one absolute mp3 path per line (--build)")
    parser.add_argument("--watermark", action="append", default=[], help="exact value to MOVE to Label (repeatable)")
    parser.add_argument("--drop", action="append", default=[], help="exact value to DROP (repeatable)")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--backup-manifest", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--label")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--undo", type=Path)
    parser.add_argument("--lock-folder")
    parser.add_argument("--session")
    args = parser.parse_args()

    if args.build:
        if not args.paths or not args.out_dir:
            raise SystemExit("--build needs --paths and --out-dir")
        paths = [Path(line.strip()) for line in args.paths.read_text(encoding="utf-8").splitlines() if line.strip()]
        print(json.dumps(build(paths, args.watermark, args.drop, args.out_dir), indent=2))
        return
    if args.undo:
        if not args.lock_folder or not args.session:
            raise SystemExit("--lock-folder and --session are required with --undo")
        lock = lock_guard.require_lock_or_exit(args.lock_folder, args.session)
        heartbeat = lock_guard.LockHeartbeat(args.lock_folder, args.session)
        heartbeat.pin_run_id(lock)
        undo(args.undo.resolve(), heartbeat)
        return
    if not args.plan or not args.root:
        raise SystemExit("--plan and --root are required")
    root = args.root.resolve()
    out_dir = args.out_dir or args.plan.resolve().parent
    label = args.label or root.name.casefold().replace(" ", "-")
    if not args.apply:
        plan, items = load_and_validate(args.plan, root)
        print(json.dumps({"mode": "dry-run", "tracks": len(items), "changes_made": 0}, indent=2))
        return
    if not args.backup or not args.backup_manifest:
        raise SystemExit("--backup and --backup-manifest are required with --apply")
    if not args.lock_folder or not args.session:
        raise SystemExit("--lock-folder and --session are required with --apply")
    try:
        heartbeat, manifest = mutation_engine.run_preflight(
            lock_folder=args.lock_folder, session=args.session,
            manifest_path=args.backup_manifest, source_root=root, serato_required=False)
    except backup_manifest.BackupGateError as exc:
        raise SystemExit(f"REFUSING TO MUTATE: {exc}") from exc
    apply(args.plan, manifest, args.backup.resolve(), root, out_dir, label, heartbeat)


if __name__ == "__main__":
    main()
