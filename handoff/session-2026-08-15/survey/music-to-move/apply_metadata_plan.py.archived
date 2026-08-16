#!/usr/bin/env python3
"""Apply reviewed missing-tag proposals without changing audio or Serato frames."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from mutagen.id3 import ID3, TBPM, TDRC, TIT2, TPE1


HERE = Path(__file__).resolve().parent
ROOT = Path("/Users/djb.a.e/Music/DJ-Music/Music/music to move").resolve()
PLAN = HERE / "version-folder-plan.json"

FRAME_MAP = {
    "artist": ("TPE1", TPE1),
    "title": ("TIT2", TIT2),
    "year": ("TDRC", TDRC),
    "bpm": ("TBPM", TBPM),
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def audio_payload(path: Path) -> bytes:
    data = path.read_bytes()
    start, end = 0, len(data)
    if data[:3] == b"ID3" and len(data) >= 10:
        size = ((data[6] & 127) << 21) | ((data[7] & 127) << 14) | ((data[8] & 127) << 7) | (data[9] & 127)
        start = 10 + size + (10 if data[5] & 16 else 0)
    if data[-128:-125] == b"TAG":
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
        if "serato" in haystack:
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
    return sha256_bytes(json.dumps(sorted(values, key=lambda item: item["key"]), ensure_ascii=False).encode())


def items_with_proposals() -> list[dict]:
    plan = json.loads(PLAN.read_text())
    return [item for item in plan["items"] if item["proposed_metadata"]]


def validate() -> list[tuple[Path, dict]]:
    prepared = []
    errors = []
    for item in items_with_proposals():
        path = Path(item["proposed_destination"]).resolve()
        if ROOT not in path.parents:
            errors.append(f"outside scope: {path}")
            continue
        if not path.is_file():
            errors.append(f"missing moved file: {path}")
            continue
        if path.suffix.lower() != ".mp3":
            errors.append(f"unsupported for this pass: {path}")
            continue
        unsupported = set(item["proposed_metadata"]) - set(FRAME_MAP)
        if unsupported:
            errors.append(f"unsupported fields {sorted(unsupported)}: {path}")
            continue
        tags = ID3(path)
        for field, proposal in item["proposed_metadata"].items():
            frame_name, _klass = FRAME_MAP[field]
            current = str(tags.getall(frame_name)[0]).strip() if tags.getall(frame_name) else ""
            if current:
                errors.append(f"refusing to overwrite {field}={current!r}: {path}")
            if proposal.get("confidence") != "review":
                errors.append(f"unexpected proposal confidence for {field}: {path}")
        prepared.append((path, item["proposed_metadata"]))
    if errors:
        raise RuntimeError("Metadata validation failed:\n" + "\n".join(errors))
    return prepared


def apply() -> None:
    prepared = validate()
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    backup_root = HERE / "tag-file-backups" / stamp
    results = []
    for path, proposals in prepared:
        relative = path.relative_to(ROOT)
        backup = backup_root / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)

        before_file_hash = sha256_file(path)
        before_audio_hash = sha256_bytes(audio_payload(path))
        before_tags = ID3(path)
        before_serato = serato_digest(before_tags)
        changed = {}
        try:
            tags = ID3(path)
            original_v2_version = tags.version[1]
            text_encoding = 3 if original_v2_version == 4 else 1
            for field, proposal in proposals.items():
                frame_name, frame_class = FRAME_MAP[field]
                tags.delall(frame_name)
                tags.add(frame_class(encoding=text_encoding, text=[str(proposal["value"])]))
                changed[field] = str(proposal["value"])
            tags.save(path, v2_version=original_v2_version)

            after_tags = ID3(path)
            after_audio_hash = sha256_bytes(audio_payload(path))
            after_serato = serato_digest(after_tags)
            if after_audio_hash != before_audio_hash:
                raise RuntimeError("audio payload changed")
            if after_serato != before_serato:
                raise RuntimeError("Serato frames changed")
            for field, value in changed.items():
                frame_name, _klass = FRAME_MAP[field]
                actual = str(after_tags.getall(frame_name)[0]).strip() if after_tags.getall(frame_name) else ""
                if actual != value:
                    raise RuntimeError(f"{field} verification failed: {actual!r} != {value!r}")
        except Exception:
            shutil.copy2(backup, path)
            raise

        results.append({
            "path": str(path),
            "backup": str(backup),
            "changed": changed,
            "before_sha256": before_file_hash,
            "after_sha256": sha256_file(path),
            "audio_payload_sha256": before_audio_hash,
            "serato_frames_sha256": before_serato,
        })

    result = {
        "mode": "metadata-apply",
        "files_changed": len(results),
        "fields_changed": sum(len(item["changed"]) for item in results),
        "backup_root": str(backup_root),
        "audio_payloads_changed": 0,
        "serato_frames_changed": 0,
        "items": results,
    }
    result_path = HERE / f"metadata-result-{stamp}.json"
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({**result, "items": f"{len(results)} recorded in {result_path}"}, indent=2))


def undo(result_path: Path) -> None:
    result = json.loads(result_path.read_text())
    restored = 0
    for item in result["items"]:
        path = Path(item["path"])
        backup = Path(item["backup"])
        if sha256_file(backup) != item["before_sha256"]:
            raise RuntimeError(f"backup hash mismatch: {backup}")
        shutil.copy2(backup, path)
        if sha256_file(path) != item["before_sha256"]:
            raise RuntimeError(f"restore verification failed: {path}")
        restored += 1
    print(json.dumps({"mode": "metadata-undo", "restored": restored}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--undo", type=Path)
    args = parser.parse_args()
    if args.undo:
        undo(args.undo.resolve())
    elif args.apply:
        apply()
    else:
        prepared = validate()
        print(f"DRY RUN: {len(prepared)} files with evidence-based missing-tag proposals; no changes made")


if __name__ == "__main__":
    main()
