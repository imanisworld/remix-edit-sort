#!/usr/bin/env python3
"""Apply an approved Artist/Title backfill plan, with verification and undo.

Same verify/rollback/undo discipline as apply_grouping_plan.py and
apply_version_genre_plan.py: audio payload and Serato frames are hashed
before and after every write, any mismatch rolls the file back immediately,
and a verified-benign Serato encoding-only difference (see
apply_version_genre_plan.py's SeratoEncodingOnlyChange for why that can
legitimately happen) is treated as a skip rather than a hard failure.
"""

from __future__ import annotations

# Every library mutation in this module goes through scripts/mutation_engine.py.
SAFETY_CLASSIFICATION = "engine-user"

# Read by scripts/mutation_surface.py. The small, explicitly-reviewed set of
# functions allowed to contain a mutation primitive without being lexically
# inside a `with engine.item(...)` block. The scanner additionally proves each
# one IS called from inside such a block. Keep this list tiny: it is exactly
# what a human reviewer has to check by hand.
#
# write_artist_title() is called only from inside `with engine.item(...)`.
ENGINE_MUTATION_HELPERS = ("write_artist_title",)

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError, TPE1, TIT2
from mutagen.mp4 import MP4
from mutagen.wave import WAVE

import tag_formats

import backup_manifest
import lock_guard
import mutation_engine


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _wav_data_chunk(data: bytes) -> bytes:
    """Return the raw PCM bytes from a WAV's 'data' RIFF chunk, ignoring
    every other chunk (fmt , LIST/INFO, id3 , cue , etc.) -- those carry
    metadata, not audio, and are exactly what an Artist/Title write touches."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise RuntimeError("not a RIFF/WAVE file")
    pos, end = 12, len(data)
    while pos + 8 <= end:
        chunk_id = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        body_start = pos + 8
        body_end = body_start + size
        if body_end > end:
            raise RuntimeError(f"truncated WAV chunk {chunk_id!r}")
        if chunk_id == b"data":
            return data[body_start:body_end]
        pos = body_end + (size & 1)  # chunks are word-aligned
    raise RuntimeError("no data chunk found")


def _is_m4a(path: Path) -> bool:
    return path.suffix.casefold() == ".m4a"


def audio_payload(path: Path) -> bytes:
    if _is_m4a(path):
        # MP4: the mdat media data, via the shared helper used by the grouping/genre tools.
        return tag_formats.audio_payload(path)
    data = path.read_bytes()
    if path.suffix.casefold() == ".wav":
        return _wav_data_chunk(data)
    start, end = 0, len(data)
    if data[:3] == b"ID3" and len(data) >= 10:
        size = ((data[6] & 127) << 21) | ((data[7] & 127) << 14) | ((data[8] & 127) << 7) | (data[9] & 127)
        start = 10 + size + (10 if data[5] & 16 else 0)
    if len(data) >= 128 and data[-128:-125] == b"TAG":
        end -= 128
    return data[start:end]


def serato_digest(tags: ID3) -> str:
    values = []
    for frame in tags.values():
        haystack = " ".join((
            frame.HashKey,
            str(getattr(frame, "desc", "")),
            str(getattr(frame, "owner", "")),
            str(getattr(frame, "filename", "")),
        )).casefold()
        if "serato" not in haystack:
            continue
        value = {
            "key": frame.HashKey,
            "frame": frame.FrameID,
            "encoding": str(getattr(frame, "encoding", None)),
            "desc": str(getattr(frame, "desc", "")),
            "owner": str(getattr(frame, "owner", "")),
            "filename": str(getattr(frame, "filename", "")),
        }
        data = getattr(frame, "data", None)
        if isinstance(data, bytes):
            value["data_sha256"] = sha256_bytes(data)
            value["data_length"] = len(data)
        text = getattr(frame, "text", None)
        if text is not None:
            value["text"] = [str(item) for item in text]
        values.append(value)
    payload = json.dumps(sorted(values, key=lambda item: item["key"]), ensure_ascii=False).encode()
    return sha256_bytes(payload)


def serato_payload_digest(tags: ID3) -> str:
    values = []
    for frame in tags.values():
        haystack = " ".join((
            frame.HashKey,
            str(getattr(frame, "desc", "")),
            str(getattr(frame, "owner", "")),
        )).casefold()
        if "serato" not in haystack:
            continue
        data = getattr(frame, "data", None)
        value = {"key": frame.HashKey}
        if isinstance(data, bytes):
            value["data_sha256"] = sha256_bytes(data)
            value["data_length"] = len(data)
        else:
            text = getattr(frame, "text", None)
            if text is not None:
                value["text"] = [str(item) for item in text]
        values.append(value)
    payload = json.dumps(sorted(values, key=lambda item: item["key"]), ensure_ascii=False).encode()
    return sha256_bytes(payload)


class SeratoEncodingOnlyChange(Exception):
    pass


def _load_tags(path: Path) -> tuple[ID3, int]:
    """Return (tags, id3_version) for either a bare-ID3 file (mp3) or a
    RIFF-wrapped one (wav), always as a real ID3 object so serato_digest()/
    serato_payload_digest() (which iterate tags.values()) work unchanged.
    An untagged file gets a fresh empty ID3() -- nothing to iterate, and
    version defaults to 4 (matches read_artist_title's untagged default)."""
    if path.suffix.casefold() == ".wav":
        w = WAVE(path)
        if w.tags is None:
            return ID3(), 4
        return w.tags, w.tags.version[1]
    try:
        tags = ID3(path)
        return tags, tags.version[1]
    except ID3NoHeaderError:
        return ID3(), 4


def read_artist_title(path: Path) -> tuple[str, str, int | None]:
    if _is_m4a(path):
        # MP4 atoms: \xa9ART (Artist) and \xa9nam (Title). No ID3 version -> None.
        tags = MP4(path).tags or {}
        artist = str((tags.get("\xa9ART") or [""])[0]).strip()
        title = str((tags.get("\xa9nam") or [""])[0]).strip()
        return artist, title, None
    # .wav is RIFF-wrapped: its ID3 chunk lives inside the RIFF container, not
    # at byte 0. mutagen.id3.ID3(path) doesn't know that -- for an untagged
    # WAV it raises, but for a TAGGED one it can misread the RIFF header as
    # garbage. mutagen.wave.WAVE(path) understands the container and exposes
    # the same ID3-shaped .tags either way.
    if path.suffix.casefold() == ".wav":
        w = WAVE(path)
        if w.tags is None:
            return "", "", 4
        artist_frames = w.tags.getall("TPE1")
        title_frames = w.tags.getall("TIT2")
        artist = str(artist_frames[0]).strip() if artist_frames else ""
        title = str(title_frames[0]).strip() if title_frames else ""
        return artist, title, w.tags.version[1]
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return "", "", 4
    artist_frames = tags.getall("TPE1")
    title_frames = tags.getall("TIT2")
    artist = str(artist_frames[0]).strip() if artist_frames else ""
    title = str(title_frames[0]).strip() if title_frames else ""
    return artist, title, tags.version[1]


def write_artist_title(path: Path, artist: str, title: str, version: int | None) -> None:
    if _is_m4a(path):
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        audio.tags["\xa9ART"] = [artist]
        audio.tags["\xa9nam"] = [title]
        audio.save()
        return
    if version not in {3, 4}:
        raise RuntimeError(
            f"ID3v2.{version} is not writable without an upgrade; refusing to change {path}"
        )
    encoding = 3 if version == 4 else 1
    if path.suffix.casefold() == ".wav":
        # Writing via a bare mutagen.id3.ID3() and calling .save(path) treats
        # the file as a raw ID3 stream and PREPENDS the tag at byte 0 --
        # clobbering the "RIFF....WAVE" header a WAV file requires. The WAVE
        # wrapper knows to embed the tag as a proper RIFF "id3 " sub-chunk
        # instead, leaving the container structure intact.
        w = WAVE(path)
        if w.tags is None:
            w.add_tags()
        w.tags.delall("TPE1")
        w.tags.add(TPE1(encoding=encoding, text=[artist]))
        w.tags.delall("TIT2")
        w.tags.add(TIT2(encoding=encoding, text=[title]))
        w.save(path, v2_version=version)
        return
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.delall("TPE1")
    tags.add(TPE1(encoding=encoding, text=[artist]))
    tags.delall("TIT2")
    tags.add(TIT2(encoding=encoding, text=[title]))
    tags.save(path, v2_version=version)


def load_and_validate(plan_path: Path) -> tuple[dict, list[dict]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("mode") != "preview-only" or not plan.get("rules", {}).get("approval_required"):
        raise RuntimeError("unexpected plan mode")
    items = plan["items"]

    errors = []
    seen_paths = set()
    for item in items:
        missing = [key for key in (
            "path", "sha256", "current_artist", "current_title",
            "proposed_artist", "proposed_title",
        ) if key not in item]
        if missing:
            errors.append(f"plan item missing required field(s): {', '.join(missing)}")
            continue
        path = Path(item["path"]).resolve()
        item["path"] = str(path)
        if path in seen_paths:
            errors.append(f"duplicate path in plan: {path}")
            continue
        seen_paths.add(path)
        if path.suffix.casefold() not in {".mp3", ".wav", ".m4a"}:
            errors.append(f"unsupported Artist/Title format: {path}")
            continue
        if not path.is_file():
            errors.append(f"missing file: {path}")
            continue
        if sha256_file(path) != item["sha256"]:
            errors.append(f"file hash changed since preview: {path}")
            continue
        current_artist, current_title, id3_version = read_artist_title(path)
        item["id3_version"] = id3_version
        if current_artist != item["current_artist"] or current_title != item["current_title"]:
            errors.append(
                f"Artist/Title changed since preview: {path}: "
                f"({current_artist!r}, {current_title!r}) != ({item['current_artist']!r}, {item['current_title']!r})"
            )
    if errors:
        raise RuntimeError("Artist/Title plan validation failed:\n" + "\n".join(errors))
    return plan, items


def apply(plan_path: Path, manifest: dict, backup: Path, backup_relative_to: Path, out_dir: Path, label: str, heartbeat: lock_guard.LockHeartbeat) -> None:
    plan, items = load_and_validate(plan_path)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    undo_path = out_dir / f"{label}-artist-title-undo-{stamp}.jsonl"
    result_path = out_dir / f"{label}-artist-title-result-{stamp}.json"
    journal_path = out_dir / f"{label}-recovery-{stamp}.jsonl"
    engine = mutation_engine.MutationEngine(
        journal_path, heartbeat, manifest, backup_relative_to, label,
    )
    engine.start({"plan": str(plan_path), "items": len(items)})
    completed = []
    skipped_id3v22 = []
    skipped_encoding_only = []

    for index, item in enumerate(items, start=1):
        path = Path(item["path"])
        relative = path.relative_to(backup_relative_to)
        backup_copy = backup / relative

        # The engine performs the heartbeat, the source-exists and
        # backup-evidence checks (both the manifest entry for this exact file
        # and this per-item backup copy's hash), the write-ahead intent
        # record, the rollback on failure, and the completion record.
        with engine.item(
            index,
            kind=mutation_engine.ARTIST_TITLE,
            source=path,
            expected_sha256=item["sha256"],
            backup_path=backup_copy,
            extra_intent={
                "current_artist": item["current_artist"], "current_title": item["current_title"],
                "proposed_artist": item["proposed_artist"], "proposed_title": item["proposed_title"],
            },
        ) as txn:
            before_hash = sha256_file(path)
            txn.on_rollback(
                lambda p=path, b=backup_copy, h=before_hash:
                mutation_engine.restore_from_backup(b, p, h)
            )
            before_audio = sha256_bytes(audio_payload(path))
            if _is_m4a(path):
                before_serato = tag_formats.serato_digest(path)
                write_artist_title(path, item["proposed_artist"], item["proposed_title"], None)
                txn.mark_mutated()
                after_artist, after_title, _ = read_artist_title(path)
                if after_artist != item["proposed_artist"] or after_title != item["proposed_title"]:
                    raise RuntimeError("Artist/Title verification failed")
                if sha256_bytes(audio_payload(path)) != before_audio:
                    raise RuntimeError("audio payload changed")
                if tag_formats.serato_digest(path) != before_serato:
                    raise RuntimeError("Serato data changed")
            else:
                before_tags, version = _load_tags(path)
                before_serato = serato_digest(before_tags)
                before_serato_payload = serato_payload_digest(before_tags)

            if not _is_m4a(path) and version == 2:
                reason = "ID3v2.2 preserved; Artist/Title metadata write skipped"
                skipped_id3v22.append({"path": str(path), "reason": reason})
                txn.record(tag_skipped=True, reason=reason)
                continue

            try:
                if not _is_m4a(path):  # m4a was written and verified above
                    write_artist_title(path, item["proposed_artist"], item["proposed_title"], version)
                    txn.mark_mutated()
                    after_artist, after_title, after_version = read_artist_title(path)
                    if after_artist != item["proposed_artist"] or after_title != item["proposed_title"]:
                        raise RuntimeError("Artist/Title verification failed")
                    if after_version != version:
                        raise RuntimeError("ID3 version changed")
                    if sha256_bytes(audio_payload(path)) != before_audio:
                        raise RuntimeError("audio payload changed")
                    after_tags, _ = _load_tags(path)
                    if serato_digest(after_tags) != before_serato:
                        if serato_payload_digest(after_tags) == before_serato_payload:
                            raise SeratoEncodingOnlyChange(
                                "Artist/Title write forced an ID3-version-compliance re-encoding of a "
                                "Serato frame's encoding byte (payload verified byte-identical); "
                                "write rolled back"
                            )
                        raise RuntimeError("Serato frames changed")
            except SeratoEncodingOnlyChange as exc:
                # Verified benign, not a safety failure: restore and skip this
                # file rather than aborting the whole run.
                mutation_engine.restore_from_backup(backup_copy, path, before_hash)
                skipped_encoding_only.append({"path": str(path), "reason": str(exc)})
                txn.record(skipped_serato_encoding_only=True, reason=str(exc))
                continue

            record = {
                "index": index,
                "path": str(path),
                "backup": str(backup_copy),
                "old_artist": item["current_artist"],
                "old_title": item["current_title"],
                "new_artist": item["proposed_artist"],
                "new_title": item["proposed_title"],
                "before_sha256": before_hash,
                "after_sha256": sha256_file(path),
                "audio_payload_sha256": before_audio,
                "serato_frames_sha256": before_serato,
            }
            txn.record(**{k: v for k, v in record.items() if k not in {"index", "path"}})
            with undo_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            completed.append(record)

    result = {
        "mode": f"{label}-artist-title-apply",
        "plan": str(plan_path),
        "verified_backup": str(backup),
        "tracks_in_plan": len(items),
        "tags_changed": len(completed),
        "tags_skipped_id3v22": skipped_id3v22,
        "skipped_serato_encoding_only": skipped_encoding_only,
        "audio_payloads_changed": 0,
        "serato_frames_changed": 0,
        "undo_log": str(undo_path),
        "recovery_journal": str(journal_path),
    }
    engine.finish(result)
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({**result, "result_path": str(result_path)}, indent=2))


def undo(undo_path: Path, heartbeat: lock_guard.LockHeartbeat) -> None:
    """Reverse an apply run.

    An undo is a mutation. It rewrites tags, so it gets the same treatment as
    an apply: ownership is re-verified before EVERY restore, not once at
    startup, and each restore is journaled write-ahead so a crash mid-undo is
    recoverable too. A half-finished undo nobody can reconstruct is exactly as
    damaging as a half-finished apply.
    """
    records = [json.loads(line) for line in undo_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    journal_path = undo_path.with_name(undo_path.stem + "-undo-recovery.jsonl")
    engine = mutation_engine.MutationEngine(
        journal_path, heartbeat, manifest=None, source_root=None,
        label="artist-title-undo", allow_unbacked=True,
    )
    engine.start({"undo_log": str(undo_path), "records": len(records)})

    restored = 0
    for position, record in enumerate(reversed(records), start=1):
        path = Path(record["path"])
        backup = Path(record["backup"])
        with engine.item(
            position,
            kind=mutation_engine.ARTIST_TITLE,
            source=path,
            backup_path=None,
            require_backup=False,
            extra_intent={"undoing_index": record.get("index"),
                          "restore_from": str(backup),
                          "restore_to_sha256": record["before_sha256"]},
        ) as txn:
            if sha256_file(backup) != record["before_sha256"]:
                raise RuntimeError(f"backup mismatch during undo: {backup}")
            shutil.copy2(backup, path)
            txn.mark_mutated()
            if sha256_file(path) != record["before_sha256"]:
                raise RuntimeError(f"undo verification failed: {path}")
            txn.record(restored_to=str(path), sha256=record["before_sha256"])
            restored += 1

    result = {"mode": "artist-title-undo", "restored": restored, "recovery_journal": str(journal_path)}
    engine.finish(result)
    print(json.dumps(result, indent=2))


def _dry_run_backup_gate(manifest_path, root) -> str:
    """Report, without mutating, whether the backup gate would let an apply run."""
    if not manifest_path or root is None:
        return "not checked -- pass --backup-manifest to validate the backup gate"
    try:
        manifest = backup_manifest.require_pass(manifest_path, root)
    except backup_manifest.BackupGateError as exc:
        return f"WOULD REFUSE: {exc}"
    return f"PASS ({manifest['backup_id']}, {manifest['file_count']} files)"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--backup", type=Path,
                        help="pre-apply backup directory the per-item copies live in "
                             "(required with --apply, alongside --backup-manifest)")
    parser.add_argument("--backup-manifest", type=Path,
                        help="PASS manifest from backup_manifest.py verify (required with --apply). "
                             "A directory existing is not proof of a backup; this is.")
    parser.add_argument("--require-serato-backup", action="store_true",
                        help="also require the manifest to carry PASS Serato backup evidence")
    parser.add_argument("--backup-relative-to", type=Path, help="root the backup mirrors (paths in plan are relative to this)")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--label", default="artist-title")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--undo", type=Path)
    parser.add_argument("--lock-folder", help="Top-level library folder locked via folder_lock.py (required with --apply/--undo)")
    parser.add_argument("--session", help="Session id that holds the lock (required with --apply/--undo)")
    args = parser.parse_args()

    if args.undo:
        if not args.lock_folder or not args.session:
            raise SystemExit("--lock-folder and --session are required with --undo")
        lock = lock_guard.require_lock_or_exit(args.lock_folder, args.session)
        heartbeat = lock_guard.LockHeartbeat(args.lock_folder, args.session)
        heartbeat.pin_run_id(lock)
        undo(args.undo.resolve(), heartbeat)
        return

    if not args.plan:
        raise SystemExit("--plan is required")
    out_dir = args.out_dir or args.plan.resolve().parent

    if args.apply:
        if not args.backup or not args.backup_relative_to or not args.backup_manifest:
            raise SystemExit(
                "--backup, --backup-manifest and --backup-relative-to are required with --apply.\n"
                "Produce a manifest with:\n"
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
                source_root=args.backup_relative_to.resolve(),
                serato_required=args.require_serato_backup,
            )
        except backup_manifest.BackupGateError as exc:
            raise SystemExit(f"REFUSING TO MUTATE: {exc}") from exc
        apply(args.plan, manifest, args.backup.resolve(), args.backup_relative_to.resolve(), out_dir, args.label, heartbeat)
    else:
        plan, items = load_and_validate(args.plan)
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "tracks": len(items),
                    "id3v22_tag_writes_would_be_skipped": sum(
                        item.get("id3_version") == 2 for item in items
                    ),
                    "backup_gate": _dry_run_backup_gate(args.backup_manifest, args.backup_relative_to.resolve() if args.backup_relative_to else None),
                    "changes_made": 0,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
