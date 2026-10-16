#!/usr/bin/env python3
"""Upgrade ID3v2.2 tags to ID3v2.3 on an explicit, owner-approved list of MP3s.

The Grouping, Genre and Year tools skip ID3v2.2 files ("ID3v2.2 is skipped,
never converted"): mutagen can read v2.2 but only saves v2.3/v2.4, so any
write would silently convert the tag. This script is the separately
previewed and approved conversion CLAUDE.md asks for. It changes nothing but
the tag version. Once a file is v2.3 the other tools can write to it.

Three modes:

  preview   --root R --file F [--file F ...] [--file-list L] --out plan.json
            Read-only. Inventories every text frame straight from the raw
            v2.2 bytes, then does a trial upgrade on a temporary copy (never
            the library file) and records whether anything would be lost.
  apply     --root R --plan plan.json --apply --lock-folder X --session S
            --backup-manifest M
            Refuses the whole plan if any item is blocked or drifted. Each
            file is one mutation-engine transaction: write-ahead intent,
            rollback from the manifest's backup copy, readback verification,
            completion record, and an fsync'd undo log.
  undo      --undo undo.jsonl --lock-folder X --session S
            Restores each file from its verified backup copy, refusing any
            file that changed after the upgrade (for example a Grouping
            write that came later -- undoing would lose it).

What must survive, or the item fails closed (blocked in preview, rolled back
in apply):

  * every text frame value (T*, TXX, COM, ULT), compared by decoding the raw
    frame bytes of both the v2.2 source and the v2.3 result, not through
    mutagen's own model. Multi-value text that v2.3 would join with "/",
    TCON "(17)"-style genres mutagen would rename, and frames v2.3 cannot
    hold (for example TRD) all count as a loss;
  * every other frame, attribute by attribute (encoding excepted, since
    v2.2 -> v2.3 may re-encode the same text);
  * the audio payload (tag_formats.audio_payload);
  * Serato GEOB payloads (apply_grouping_plan.serato_payload_digest, the
    check the Grouping tool uses);
  * an ID3v1 trailer, byte for byte.

A frame mutagen cannot parse, or one it would drop on save, blocks the file.
"""

from __future__ import annotations

# Every library mutation in this module goes through scripts/mutation_engine.py.
SAFETY_CLASSIFICATION = "engine-user"

# Read by scripts/mutation_surface.py. upgrade_tag() is called from inside
# `with engine.item(...)` in apply(); the only other caller is
# trial_upgrade(), which runs it on a temporary copy outside the library and
# checks that before calling it.
ENGINE_MUTATION_HELPERS = ("upgrade_tag",)

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from mutagen.id3 import ID3, Frames_2_2

import apply_grouping_plan
import backup_manifest
import lock_guard
import mutation_engine
import tag_formats

CONVERSION = {"from": "2.2", "to": "2.3"}

# Text-carrying frames whose values are checked from raw bytes. COM/ULT carry
# a 3-byte language before the text.
LANGUAGE_FRAMES = {"COMM", "USLT"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Raw ID3 parsing, independent of mutagen, so the text check does not trust
# the library that performs the conversion.


class RawTagError(Exception):
    pass


def _synchsafe(raw: bytes) -> int:
    return (raw[0] & 127) << 21 | (raw[1] & 127) << 14 | (raw[2] & 127) << 7 | (raw[3] & 127)


def raw_frames(data: bytes) -> tuple[int, list[tuple[str, bytes]]]:
    """-> (major version, [(frame id, frame body)]) for a v2.2 or v2.3 tag."""
    if data[:3] != b"ID3" or len(data) < 10:
        raise RawTagError("no ID3v2 header")
    major, flags = data[3], data[5]
    body = data[10:10 + _synchsafe(data[6:10])]
    if major not in {2, 3}:
        raise RawTagError(f"unexpected ID3v2.{major}")
    if flags & 0x40:
        # v2.2: compression (no defined scheme); v2.3: extended header.
        raise RawTagError(f"ID3v2.{major} header flag 0x40 is not supported")
    if flags & 0x80:
        body = body.replace(b"\xff\x00", b"\xff")
    id_len, size_len = (3, 3) if major == 2 else (4, 4)
    header_len = id_len + size_len + (0 if major == 2 else 2)
    frames, pos = [], 0
    while pos + header_len <= len(body):
        frame_id = body[pos:pos + id_len]
        if frame_id[0] == 0:
            break  # padding
        size = int.from_bytes(body[pos + id_len:pos + id_len + size_len], "big")
        if major == 3 and body[pos + 9] & 0xE0:
            raise RawTagError(f"compressed/encrypted/grouped v2.3 frame {frame_id!r}")
        start = pos + header_len
        if size == 0 or start + size > len(body):
            raise RawTagError(f"bad frame size for {frame_id!r}")
        try:
            name = frame_id.decode("ascii")
        except UnicodeDecodeError as exc:
            raise RawTagError(f"non-ASCII frame id {frame_id!r}") from exc
        frames.append((name, body[start:start + size]))
        pos = start + size
    return major, frames


def _split_text(encoding: int, raw: bytes) -> list[str]:
    if encoding in {0, 3}:
        codec = "latin-1" if encoding == 0 else "utf-8"
        parts = raw.split(b"\x00")
    elif encoding in {1, 2}:
        codec = "utf-16" if encoding == 1 else "utf-16-be"
        parts, current = [], bytearray()
        for i in range(0, len(raw) - 1, 2):
            pair = raw[i:i + 2]
            if pair == b"\x00\x00":
                parts.append(bytes(current))
                current = bytearray()
            else:
                current += pair
        parts.append(bytes(current))
    else:
        raise RawTagError(f"unknown text encoding {encoding}")
    values = [part.decode(codec) for part in parts]
    while values and values[-1] == "":
        values.pop()  # terminators
    return values


def mapped_frame_id(major: int, frame_id: str) -> str:
    """Name a frame by its v2.3 id, so v2.2 and v2.3 tags compare directly."""
    if major == 3:
        return frame_id
    cls = Frames_2_2.get(frame_id)
    if cls is None:
        raise RawTagError(f"unknown ID3v2.2 frame {frame_id!r} would be dropped")
    return cls.__mro__[1].__name__


def raw_text_values(data: bytes) -> dict[str, list[list[str]]]:
    """{v2.3 frame id: sorted list of decoded value lists} for text frames."""
    major, frames = raw_frames(data)
    values: dict[str, list[list[str]]] = {}
    for frame_id, body in frames:
        name = mapped_frame_id(major, frame_id)
        if name.startswith("T"):
            decoded = _split_text(body[0], body[1:])
        elif name in LANGUAGE_FRAMES:
            decoded = [body[1:4].decode("latin-1"), *_split_text(body[0], body[4:])]
        else:
            continue
        values.setdefault(name, []).append(decoded)
    return {key: sorted(found) for key, found in sorted(values.items())}


# ---------------------------------------------------------------------------
# State capture and comparison.


def _semantic(value):
    if isinstance(value, bytes):
        return {"bytes": len(value), "sha256": sha256_bytes(value)}
    if isinstance(value, (list, tuple)):
        return [_semantic(item) for item in value]
    if isinstance(value, (str, bool)) or value is None:
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def frame_snapshot(tags: ID3) -> list[dict]:
    """Every frame's identity and attributes except encoding (mutagen's v2.4 view)."""
    frames = []
    for frame in tags.values():
        frames.append({
            "hash_key": frame.HashKey,
            "attributes": {
                key: _semantic(value)
                for key, value in sorted(vars(frame).items()) if key != "encoding"
            },
        })
    return sorted(frames, key=lambda f: json.dumps(f, sort_keys=True, ensure_ascii=False))


def id3v1_trailer(data: bytes) -> str | None:
    return sha256_bytes(data[-128:]) if len(data) >= 128 and data[-128:-125] == b"TAG" else None


def capture_state(path: Path) -> dict:
    data = Path(path).read_bytes()
    tags = ID3(path)
    return {
        "id3_version": ".".join(str(part) for part in tags.version[:2]),
        "text_frames": raw_text_values(data),
        "frames": frame_snapshot(tags),
        "audio_payload_sha256": sha256_bytes(tag_formats.audio_payload(path)),
        "serato_payload_sha256": apply_grouping_plan.serato_payload_digest(tags),
        "id3v1_sha256": id3v1_trailer(data),
    }


def source_problems(path: Path) -> list[str]:
    """Reasons a v2.2 source cannot be upgraded safely, found before any write."""
    path = Path(path)
    if tag_formats.kind(path) != "mp3":
        return [f"not an MP3: {path}"]
    data = path.read_bytes()
    if data[:3] != b"ID3":
        return ["no ID3v2 tag"]
    if data[3] != 2:
        return [f"tag is ID3v2.{data[3]}, not ID3v2.2"]
    try:
        _, frames = raw_frames(data)
        raw_text_values(data)
    except (RawTagError, UnicodeDecodeError) as exc:
        return [f"raw tag not readable: {exc}"]
    tags = ID3(path, translate=False)
    problems = []
    if tags.unknown_frames:
        problems.append(f"{len(tags.unknown_frames)} frame(s) mutagen does not know would be dropped")
    loaded = len(tags.values()) + len(tags.unknown_frames)
    if loaded != len(frames):
        problems.append(
            f"mutagen loaded {loaded} frame(s) but the tag holds {len(frames)} "
            f"(unparseable or merged frames would change)"
        )
    return problems


def compare_states(before: dict, after: dict) -> list[str]:
    problems = []
    if after["id3_version"] != CONVERSION["to"]:
        problems.append(f"tag is ID3v{after['id3_version']} after upgrade, expected {CONVERSION['to']}")
    for key in sorted(set(before["text_frames"]) | set(after["text_frames"])):
        old = before["text_frames"].get(key)
        new = after["text_frames"].get(key)
        if old != new:
            problems.append(f"text frame {key} would change: {old!r} -> {new!r}")
    if before["frames"] != after["frames"]:
        old = Counter(json.dumps(f, sort_keys=True) for f in before["frames"])
        new = Counter(json.dumps(f, sort_keys=True) for f in after["frames"])
        lost = sorted({json.loads(f)["hash_key"] for f in old - new})
        gained = sorted({json.loads(f)["hash_key"] for f in new - old})
        problems.append(f"frames changed (lost/altered: {lost}, new/altered: {gained})")
    for field, label in (
        ("audio_payload_sha256", "audio payload"),
        ("serato_payload_sha256", "Serato frame payload"),
        ("id3v1_sha256", "ID3v1 trailer"),
    ):
        if before[field] != after[field]:
            problems.append(f"{label} changed")
    return problems


def upgrade_tag(path: Path) -> None:
    """The one write: reload the v2.2 tag and save it as v2.3.

    translate=False matters. mutagen's default load converts to its v2.4
    model (TYE -> TYER -> TDRC), and save(v2_version=3) does NOT convert
    back, so a default load would write an off-spec TDRC frame into a v2.3
    tag. Untranslated, v2.2 frames load as their v2.3 equivalents (TYE ->
    TYER, PIC -> APIC, GEO -> GEOB) and are written as-is.
    """
    tags = ID3(path, translate=False)
    if tags.version[:2] != (2, 2):
        raise RuntimeError(f"refusing to upgrade a non-v2.2 tag: {path}")
    tags.save(path, v2_version=3)


def trial_upgrade(path: Path) -> list[str]:
    """Upgrade a temporary copy and report anything that would not survive."""
    with tempfile.TemporaryDirectory(prefix="id3v22-trial-") as scratch:
        copy = Path(scratch) / ("trial" + Path(path).suffix)
        copy.write_bytes(Path(path).read_bytes())
        if Path(scratch).resolve() not in copy.resolve().parents:
            raise RuntimeError("trial copy escaped its temporary directory")
        before = capture_state(copy)
        try:
            upgrade_tag(copy)
            return compare_states(before, capture_state(copy))
        except Exception as exc:  # noqa: BLE001 - any failure blocks the item
            return [f"trial upgrade failed: {type(exc).__name__}: {exc}"]


# ---------------------------------------------------------------------------
# Preview.


def _inside(root: Path, path: Path) -> bool:
    return root in path.parents


def build_plan(root: Path, files: list[Path]) -> dict:
    items, seen = [], set()
    for raw in files:
        path = Path(raw).resolve()
        item = {"path": str(path)}
        problems = []
        if str(path) in seen:
            problems.append("listed more than once")
        seen.add(str(path))
        if not _inside(root, path):
            problems.append("outside --root")
        elif not path.is_file():
            problems.append("missing file")
        if not problems:
            item["relative"] = str(path.relative_to(root))
            item["sha256"] = sha256_file(path)
            problems = source_problems(path)
            if not problems:
                state = capture_state(path)
                item.update({
                    "id3_version": state["id3_version"],
                    "text_frames": state["text_frames"],
                    "audio_payload_sha256": state["audio_payload_sha256"],
                    "serato_payload_sha256": state["serato_payload_sha256"],
                    "has_id3v1": state["id3v1_sha256"] is not None,
                })
                problems = trial_upgrade(path)
        item["status"] = "blocked" if problems else "ready"
        item["problems"] = problems
        items.append(item)
    blocked = sum(1 for item in items if item["status"] == "blocked")
    return {
        "mode": "preview-only",
        "tool": "upgrade_id3v22_tags.py",
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "root": str(root),
        "rules": {
            "approval_required": True,
            "id3_upgrade": CONVERSION,
            "preserved": [
                "every text frame value (raw bytes compared)",
                "every other frame (attributes, encoding excepted)",
                "audio payload", "Serato frame payloads", "ID3v1 trailer",
            ],
        },
        "counts": {"files": len(items), "ready": len(items) - blocked, "blocked": blocked},
        "items": items,
    }


# ---------------------------------------------------------------------------
# Apply and undo.


def load_and_validate(plan_path: Path, root: Path) -> tuple[dict, list[dict]]:
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    rules = plan.get("rules", {})
    if plan.get("mode") != "preview-only" or not rules.get("approval_required"):
        raise RuntimeError("unexpected plan mode")
    if rules.get("id3_upgrade") != CONVERSION:
        raise RuntimeError(f"plan must request exactly {CONVERSION}, got {rules.get('id3_upgrade')!r}")
    if Path(plan.get("root", "")).resolve() != root:
        raise RuntimeError(f"plan was built for {plan.get('root')!r}, not {root}")
    items = plan.get("items") or []
    if not items:
        raise RuntimeError("plan has no items")

    errors, seen = [], set()
    for item in items:
        path = Path(item["path"]).resolve()
        if item.get("status") != "ready" or item.get("problems"):
            errors.append(f"blocked in preview: {path}: {item.get('problems')}")
            continue
        if str(path) in seen:
            errors.append(f"listed more than once: {path}")
            continue
        seen.add(str(path))
        if not _inside(root, path):
            errors.append(f"outside target root: {path}")
            continue
        if not path.is_file():
            errors.append(f"missing file: {path}")
            continue
        if sha256_file(path) != item["sha256"]:
            errors.append(f"file changed since preview: {path}")
            continue
        problems = source_problems(path)
        if problems:
            errors.append(f"cannot upgrade: {path}: {problems}")
            continue
        if raw_text_values(path.read_bytes()) != item["text_frames"]:
            errors.append(f"text frames changed since preview: {path}")
    if errors:
        raise RuntimeError("ID3v2.2 upgrade plan validation failed:\n" + "\n".join(errors))
    return plan, items


def apply(plan_path: Path, manifest: dict, root: Path, out_dir: Path, label: str,
          heartbeat: lock_guard.LockHeartbeat) -> dict:
    _, items = load_and_validate(plan_path, root)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    undo_path = out_dir / f"{label}-id3-upgrade-undo-{stamp}.jsonl"
    result_path = out_dir / f"{label}-id3-upgrade-result-{stamp}.json"
    journal_path = out_dir / f"{label}-id3-upgrade-recovery-{stamp}.jsonl"
    engine = mutation_engine.MutationEngine(journal_path, heartbeat, manifest, root, label)
    engine.start({"plan": str(plan_path), "items": len(items), "id3_upgrade": CONVERSION})
    completed = []

    for index, item in enumerate(items, start=1):
        path = Path(item["path"]).resolve()
        # The engine does the heartbeat, the hash-since-preview check, the
        # manifest backup proof for this exact file (and resolves its backup
        # copy), the write-ahead intent, rollback on failure, and completion.
        with engine.item(
            index,
            kind=mutation_engine.METADATA,
            source=path,
            expected_sha256=item["sha256"],
            extra_intent={"id3_upgrade": CONVERSION},
        ) as txn:
            backup_copy = Path(txn.intent["backup_path"])
            before_hash = item["sha256"]
            txn.on_rollback(
                lambda b=backup_copy, p=path, h=before_hash:
                mutation_engine.restore_from_backup(b, p, h)
            )
            before = capture_state(path)
            try:
                upgrade_tag(path)
            finally:
                if sha256_file(path) != before_hash:
                    txn.mark_mutated()
            if not txn.mutated:
                raise mutation_engine.MutationVerificationError(f"upgrade did not change the file: {path}")
            problems = compare_states(before, capture_state(path))
            if problems:
                raise mutation_engine.MutationVerificationError(
                    f"ID3v2.2 -> v2.3 upgrade verification failed for {path}: " + "; ".join(problems)
                )
            record = {
                "index": index,
                "path": str(path),
                "backup": str(backup_copy),
                "id3_upgraded": "2.2->2.3",
                "before_sha256": before_hash,
                "after_sha256": sha256_file(path),
                "audio_payload_sha256": before["audio_payload_sha256"],
                "serato_payload_sha256": before["serato_payload_sha256"],
                "text_frames": before["text_frames"],
            }
            txn.record(**{k: v for k, v in record.items() if k not in {"index", "path", "text_frames"}})
            with undo_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            completed.append(record)

    result = {
        "mode": f"{label}-id3-upgrade-apply",
        "plan": str(plan_path),
        "files_in_plan": len(items),
        "id3_upgraded": len(completed),
        "audio_payloads_changed": 0,
        "serato_payloads_changed": 0,
        "text_frames_changed": 0,
        "undo_log": str(undo_path),
        "recovery_journal": str(journal_path),
    }
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    engine.finish(result)
    return {**result, "result_path": str(result_path)}


def undo(undo_path: Path, heartbeat: lock_guard.LockHeartbeat) -> dict:
    """Put each file back to its pre-upgrade bytes, newest first, under the engine."""
    records = [json.loads(line) for line in undo_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    journal_path = undo_path.with_name(undo_path.stem + "-undo-recovery.jsonl")
    engine = mutation_engine.MutationEngine(
        journal_path, heartbeat, manifest=None, source_root=None,
        label="id3-upgrade-undo", allow_unbacked=True,
    )
    engine.start({"undo_log": str(undo_path), "records": len(records)})
    restored = 0
    for position, record in enumerate(reversed(records), start=1):
        path = Path(record["path"])
        backup = Path(record["backup"])
        # expected_sha256 refuses a file that changed after the upgrade (for
        # example a later Grouping write), which this restore would erase.
        with engine.item(
            position,
            kind=mutation_engine.METADATA,
            source=path,
            expected_sha256=record["after_sha256"],
            require_backup=False,
            extra_intent={"undoing_index": record.get("index"),
                          "restore_from": str(backup),
                          "restore_to_sha256": record["before_sha256"]},
        ) as txn:
            after_hash = record["after_sha256"]
            mutation_engine.restore_from_backup(backup, path, record["before_sha256"])
            txn.mark_mutated()
            if ID3(path).version[:2] != (2, 2):
                raise mutation_engine.MutationVerificationError(f"undo did not restore ID3v2.2: {path}")
            txn.record(restored_to=str(path), sha256=record["before_sha256"], replaced_sha256=after_hash)
            restored += 1
    result = {"mode": "id3-upgrade-undo", "restored": restored, "recovery_journal": str(journal_path)}
    engine.finish(result)
    return result


def _read_file_list(path: Path) -> list[Path]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [Path(line.strip()) for line in lines if line.strip() and not line.lstrip().startswith("#")]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, help="library folder root the files live under")
    parser.add_argument("--file", type=Path, action="append", default=[],
                        help="an MP3 to upgrade (repeatable); preview mode")
    parser.add_argument("--file-list", type=Path, help="text file, one path per line; preview mode")
    parser.add_argument("--out", type=Path, help="where preview writes the plan JSON")
    parser.add_argument("--plan", type=Path, help="plan JSON from preview")
    parser.add_argument("--backup-manifest", type=Path,
                        help="PASS manifest from backup_manifest.py verify (required with --apply)")
    parser.add_argument("--require-serato-backup", action="store_true",
                        help="also require the manifest to carry PASS Serato backup evidence")
    parser.add_argument("--out-dir", type=Path, help="where apply writes undo/result/recovery files")
    parser.add_argument("--label", help="prefix for undo/result files")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--undo", type=Path)
    parser.add_argument("--lock-folder", help="top-level library folder locked via folder_lock.py")
    parser.add_argument("--session", help="session id that holds the lock")
    args = parser.parse_args()

    if args.undo:
        if not args.lock_folder or not args.session:
            raise SystemExit("--lock-folder and --session are required with --undo")
        lock = lock_guard.require_lock_or_exit(args.lock_folder, args.session)
        heartbeat = lock_guard.LockHeartbeat(args.lock_folder, args.session)
        heartbeat.pin_run_id(lock)
        print(json.dumps(undo(args.undo.resolve(), heartbeat), indent=2))
        return

    if not args.root:
        raise SystemExit("--root is required")
    root = args.root.resolve()

    if not args.plan:
        files = list(args.file) + (_read_file_list(args.file_list) if args.file_list else [])
        if not files or not args.out:
            raise SystemExit("preview needs --file/--file-list and --out")
        out = args.out.resolve()
        if _inside(root, out):
            raise SystemExit("refusing to write the plan inside the library root")
        plan = build_plan(root, files)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"mode": "preview", "plan": str(out), **plan["counts"], "changes_made": 0}, indent=2))
        for item in plan["items"]:
            if item["problems"]:
                print(f"BLOCKED {item['path']}: {'; '.join(item['problems'])}")
        if plan["counts"]["blocked"]:
            raise SystemExit(1)
        return

    label = args.label or root.name.casefold().replace(" ", "-")
    out_dir = (args.out_dir or args.plan.resolve().parent).resolve()
    if not args.apply:
        _, items = load_and_validate(args.plan, root)
        print(json.dumps({"mode": "dry-run", "files": len(items), "changes_made": 0}, indent=2))
        return

    if not args.backup_manifest:
        raise SystemExit(
            "--backup-manifest is required with --apply.\n"
            "Produce one with:\n"
            "  python3 scripts/backup_manifest.py verify "
            "--source-root <root> --backup <dir> --out manifest.json"
        )
    if not args.lock_folder or not args.session:
        raise SystemExit("--lock-folder and --session are required with --apply")
    try:
        heartbeat, manifest = mutation_engine.run_preflight(
            lock_folder=args.lock_folder,
            session=args.session,
            manifest_path=args.backup_manifest,
            source_root=root,
            serato_required=args.require_serato_backup,
        )
    except backup_manifest.BackupGateError as exc:
        raise SystemExit(f"REFUSING TO MUTATE: {exc}") from exc
    print(json.dumps(apply(args.plan, manifest, root, out_dir, label, heartbeat), indent=2))


if __name__ == "__main__":
    main()
