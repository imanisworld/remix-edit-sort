#!/usr/bin/env python3
"""Run a user-invoked, read-only DJ library review; never organize files itself.

Scans an explicitly selected incoming/download folder, checks byte-identical
duplicates against a current local index if supplied, compares existing DJ
software import snapshots, and previews verified set-time overlap from already
normalized playback fixtures. Writes reports ONLY to an external report folder.
No watcher, file move, deletion, database creation, tagging, or app rewrite.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "preview-only"

import argparse
import hashlib
import html
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import dj_event_time
import dj_source_drift
import inventory_library

TOOL_VERSION = "1.0.0"
MAX_DEFAULT = 500
PLACEHOLDER_GENRES = {"", "other", "unknown", "n/a", "?"}


class ReviewRefused(ValueError):
    """An input or report destination is unsafe or insufficient."""


def within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _regular_source(path: Path, *, directory: bool = False) -> Path:
    raw = path.expanduser().absolute()
    if raw.is_symlink():
        raise ReviewRefused(f"source is symlinked: {raw}")
    if directory and not raw.is_dir():
        raise ReviewRefused(f"missing input directory: {raw}")
    if not directory and not raw.is_file():
        raise ReviewRefused(f"missing input file: {raw}")
    return raw.resolve()


def stable_hash(path: Path) -> tuple[str, int]:
    if path.is_symlink() or not path.is_file():
        raise ReviewRefused(f"file must be regular and not symlinked: {path}")
    before = path.stat(follow_symlinks=False)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    after = path.stat(follow_symlinks=False)
    fields = lambda st: (st.st_size, st.st_mtime_ns, st.st_dev, st.st_ino)
    if fields(before) != fields(after):
        raise ReviewRefused(f"file changed while inspected: {path}")
    return digest.hexdigest(), after.st_size


def read_index(db: Path | None) -> dict | None:
    if db is None:
        return None
    path = _regular_source(db)
    uri = "file:" + quote(os.fspath(path), safe="/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        meta = dict(conn.execute("SELECT key,value FROM meta").fetchall())
        if meta.get("schema_version") != "3" or not meta.get("root_canonical"):
            raise ReviewRefused("index has missing/wrong DJ library schema or root")
        latest = conn.execute(
            "SELECT scan_id,completed_at,completed_successfully "
            "FROM scans WHERE completed_successfully=1 "
            "ORDER BY scan_id DESC LIMIT 1"
        ).fetchone()
        if latest is None:
            raise ReviewRefused("library index has no completed scan")
        records = [dict(r) for r in conn.execute(
            "SELECT file_id,canonical_path,display_path,size_bytes,sha256 FROM files"
        )]
    except sqlite3.Error as exc:
        raise ReviewRefused("cannot read the existing DJ library index") from exc
    finally:
        conn.close()
    by_sha: dict[str, list[dict]] = defaultdict(list)
    by_id: dict[int, dict] = {}
    for record in records:
        by_id[record["file_id"]] = record
        by_sha[record["sha256"]].append(record)
    return {
        "root": str(Path(meta["root_canonical"]).resolve()),
        "latest_scan": int(latest["scan_id"]),
        "completed_at": latest["completed_at"],
        "files_indexed": len(records),
        "by_sha": by_sha,
        "by_id": by_id,
    }


def current_indexed_copy(record: dict, expected_hash: str) -> bool:
    path = Path(record["canonical_path"])
    if not path.is_absolute():
        return False
    try:
        actual, size = stable_hash(path)
        return size == record["size_bytes"] and actual == expected_hash
    except (OSError, ReviewRefused):
        return False


def inspect_downloads(incoming: Path, index: dict | None, max_files: int = MAX_DEFAULT) -> dict:
    root = _regular_source(incoming, directory=True)
    if not isinstance(max_files, int) or max_files < 1:
        raise ReviewRefused("--max-files must be at least 1")
    audio_paths = []
    skipped_symlinks, skipped_partial, errors = [], [], []
    for dirname, subfolders, filenames in os.walk(root, followlinks=False):
        kept = []
        for subfolder in sorted(subfolders):
            subpath = Path(dirname) / subfolder
            if subpath.is_symlink():
                skipped_symlinks.append(str(subpath))
            else:
                kept.append(subfolder)
        subfolders[:] = kept
        for name in sorted(filenames):
            path = Path(dirname) / name
            if path.is_symlink():
                skipped_symlinks.append(str(path))
                continue
            if name.startswith("._"):
                continue
            if name.casefold().endswith((".part", ".crdownload", ".download")):
                skipped_partial.append(str(path))
                continue
            if inventory_library.audio_ext(path) in inventory_library.AUDIO_EXTS:
                audio_paths.append(path)
    audio_paths.sort(key=lambda p: str(p).casefold())
    truncated = len(audio_paths) > max_files
    if truncated:
        errors.append(f"{len(audio_paths)-max_files} audio files excluded by scan budget; report is incomplete")
    items = []
    hashes: dict[str, list[str]] = defaultdict(list)
    for path in audio_paths[:max_files]:
        name = path.relative_to(root).as_posix()
        item = {
            "relative_path": name, "sha256": None, "status": "NEEDS_REVIEW",
            "genre_tag": None, "artist": None, "title": None,
            "reasons": [], "same_bytes_in_incoming": [],
            "same_bytes_in_library": [], "library_duplicate_check": (
                "NOT_CHECKED_NO_INDEX" if index is None else "CHECKED"
            ),
        }
        try:
            before = path.stat(follow_symlinks=False)
            metadata = inventory_library.read_row(path)
            after = path.stat(follow_symlinks=False)
            stat = lambda v: (v.st_size, v.st_mtime_ns, v.st_dev, v.st_ino)
            if stat(before) != stat(after):
                raise ReviewRefused("file changed during metadata read")
            digest = metadata["sha256"]
            if not isinstance(digest, str) or len(digest) != 64:
                raise ReviewRefused("metadata inventory omitted reliable hash")
            item["sha256"] = digest
            item["genre_tag"] = metadata.get("genre") or ""
            item["artist"] = metadata.get("artist") or ""
            item["title"] = metadata.get("title") or ""
            hashes[digest].append(name)
            if item["genre_tag"].strip().casefold() in PLACEHOLDER_GENRES:
                item["reasons"].append("GENRE_MISSING_OR_UNDECIDED")
            else:
                item["reasons"].append("GENRE_TAG_UNVERIFIED_AGAINST_APPROVED_RULES")
            if not item["artist"] or not item["title"]:
                item["reasons"].append("ARTIST_OR_TITLE_MISSING")
            if index:
                for recorded in index["by_sha"].get(digest, []):
                    if os.path.realpath(recorded["canonical_path"]) == os.path.realpath(path):
                        continue
                    if current_indexed_copy(recorded, digest):
                        item["same_bytes_in_library"].append({
                            "file_id": recorded["file_id"],
                            "path": recorded["display_path"],
                            "verified_sha256": digest,
                        })
                if item["same_bytes_in_library"]:
                    item["reasons"].append("EXACT_BYTES_ALREADY_IN_LIBRARY")
        except (OSError, ValueError, TypeError) as exc:
            item["status"] = "UNREADABLE_OR_CHANGING"
            item["reasons"] = [str(exc)]
        items.append(item)
    for item in items:
        digest = item["sha256"]
        if digest:
            item["same_bytes_in_incoming"] = [
                name for name in hashes[digest] if name != item["relative_path"]
            ]
            if item["same_bytes_in_incoming"]:
                item["reasons"].append("EXACT_BYTES_DUPLICATE_IN_INCOMING")
        if item["status"] != "UNREADABLE_OR_CHANGING":
            item["status"] = ("DUPLICATE_REVIEW" if
                              item["same_bytes_in_incoming"] or item["same_bytes_in_library"]
                              else "NEEDS_REVIEW")
    status_counts = Counter(item["status"] for item in items)
    return {
        "root": str(root),
        "audio_files_discovered": len(audio_paths),
        "audio_files_scanned": len(items),
        "scan_truncated": truncated,
        "status_counts": dict(status_counts),
        "skipped_partial_downloads": skipped_partial,
        "skipped_symlinks": skipped_symlinks,
        "warnings": errors,
        "duplicate_claim": "SHA256 exact full-file bytes only; never deletes or assumes versions match.",
        "items": items,
    }


def fingerprints(paths: list[Path]) -> list[dict]:
    observed = []
    for candidate in paths:
        path = _regular_source(candidate)
        digest, size = stable_hash(path)
        observed.append({"name": path.name, "path": str(path),
                         "sha256": digest, "size_bytes": size,
                         "parsing": "UNVERIFIED_EXPORT_CONTENT"})
    return observed


def _normalize_play_identities(plays: list[dict], index: dict, sources: list[dict]) -> tuple[list[dict], list[dict]]:
    """Validate claimed local file IDs and supplied export-hash provenance."""
    digests = {item["sha256"] for item in sources}
    normalized, rejected = [], []
    verified_files: dict[int, bool] = {}
    for item in plays:
        if not isinstance(item, dict):
            raise ReviewRefused("play rows must be objects")
        row = dict(item)
        fid = row.get("file_id")
        record = index["by_id"].get(fid) if type(fid) is int and fid > 0 else None
        track_hash = row.get("track_sha256")
        reasons = []
        if record is None or not isinstance(track_hash, str) or record["sha256"] != track_hash:
            reasons.append("file ID missing or track SHA256 disagrees with library index")
        else:
            if fid not in verified_files:
                verified_files[fid] = current_indexed_copy(record, record["sha256"])
            if not verified_files[fid]:
                reasons.append("indexed audio file missing or changed since scan")
        if row.get("source_artifact_sha256") not in digests:
            reasons.append("original History export missing or does not match source fingerprint")
        if reasons:
            row["identity_status"] = "REVIEW"
            row["file_id"] = None
            row["source_artifact_sha256"] = None
        else:
            # Caller must already have an exact source-backed identity.
            if row.get("identity_status") != "EXACT_INDEXED_FILE":
                reasons.append("History-to-track identity has not been established")
        normalized.append(row)
        rejected.append({"play_id": item.get("play_id"), "reasons": reasons})
    return normalized, rejected


def inspect_history(events_file: Path, plays_file: Path, index: dict,
                    raw_sources: list[dict]) -> dict:
    if not raw_sources:
        raise ReviewRefused("source History export required before playback matching")
    events = json.loads(_regular_source(events_file).read_text(encoding="utf-8"))
    plays = json.loads(_regular_source(plays_file).read_text(encoding="utf-8"))
    if not isinstance(events, list) or not isinstance(plays, list):
        raise ReviewRefused("events and normalized plays must both be JSON arrays")
    validated, validation = _normalize_play_identities(plays, index, raw_sources)
    candidates = dj_event_time.preview(events, validated)
    candidates.update({
        "identity_checks": validation,
        "raw_history_parser": "NOT_IMPLEMENTED_REAL_EXPORT_SAMPLE_REQUIRED",
        "candidate_origin": "caller-supplied normalized fixture; rows unproven against raw History export",
        "proof_of_play": False,
    })
    return candidates


def _safe_report_root(out_root: Path, restricted: list[Path]) -> Path:
    target = out_root.expanduser().absolute()
    if target.is_symlink():
        raise ReviewRefused("report destination cannot be a symlink")
    canonical = target.resolve()
    for source in restricted:
        if within(canonical, source) or canonical == source:
            raise ReviewRefused(f"report must stay outside music/data source: {source}")
    return canonical


def build_report(incoming: Path | None, db: Path | None, serato_dir: Path | None,
                 rekordbox_xml: Path | None, events: Path | None, plays: Path | None,
                 history_exports: list[Path], max_files: int = MAX_DEFAULT) -> dict:
    if incoming is None and db is None and not history_exports:
        raise ReviewRefused("choose an incoming folder, existing index or History export")
    if (events is None) != (plays is None):
        raise ReviewRefused("--events and --plays must be supplied together")
    if events is not None and db is None:
        raise ReviewRefused("track-history matching requires the existing DJ library index")
    index = read_index(db)
    downloads = inspect_downloads(incoming, index, max_files) if incoming else None
    sources = fingerprints(history_exports)
    drift = None
    if db is not None:
        drift = dj_source_drift.report(db, serato_dir=serato_dir, rekordbox_xml=rekordbox_xml)
    linked = inspect_history(events, plays, index, sources) if events is not None else None
    return {
        "tool": "DJ Library Review", "version": TOOL_VERSION, "mode": "preview-only",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "automatic_moves_enabled": False, "files_changed": False,
        "index": ({
            "root": index["root"], "latest_completed_scan": index["latest_scan"],
            "indexed_files": index["files_indexed"], "indexed_at": index["completed_at"],
            "snapshot_only": True,
        } if index else {"state": "NOT_SUPPLIED", "library_duplicate_check": "NOT_CHECKED"}),
        "downloads": downloads,
        "source_drift": drift,
        "original_history_exports": sources,
        "event_play_candidates": linked,
        "limits": [
            "No file/tag/folder changes or automatic duplicates deletion.",
            "No trustworthy raw Serato/Rekordbox playback History parser until real samples are reviewed.",
            "A source fingerprint does not prove a normalized playback row exists in that export.",
            "No track moves until exact approved destinations, backups and DJ app relocation tests.",
        ],
    }


def render_html(report: dict) -> str:
    esc = lambda v: html.escape(str(v), quote=True)
    dl = report.get("downloads")
    ix = report["index"]
    summary = []
    if dl:
        summary.append(f"<li>{dl['audio_files_scanned']} tracks scanned from the selected inbox; "
                       f"{sum(1 for r in dl['items'] if r['status']=='DUPLICATE_REVIEW')} exact duplicate review candidates</li>")
        if dl["scan_truncated"]:
            summary.append("<li><strong>Scan incomplete:</strong> file budget reached</li>")
    summary.append(f"<li>Existing library index: {esc(ix.get('indexed_files','not supplied'))} indexed files</li>")
    drift = report.get("source_drift")
    if drift:
        summary.append(f"<li>Serato: {esc(drift['serato']['state'])}; "
                       f"Rekordbox export: {esc(drift['rekordbox']['state'])}</li>")
    event = report.get("event_play_candidates")
    if event:
        summary.append(f"<li>Event/time candidates: "
                       f"{sum(r['state']=='CANDIDATE_ONLY' for r in event['plays'])} candidate-only "
                       f"and {sum(r['state']=='REVIEW' for r in event['plays'])} review</li>")
    rows = []
    if dl:
        for entry in dl["items"]:
            reason = ", ".join(entry["reasons"])
            rows.append(
                "<tr><td>" + esc(entry["relative_path"]) + "</td><td>" +
                esc(entry["status"]) + "</td><td>" + esc(entry["genre_tag"]) +
                "</td><td>" + esc(reason) + "</td></tr>"
            )
    if not rows:
        rows.append('<tr><td colspan="4">No incoming track rows in this run.</td></tr>')
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>DJ Library Review</title><style>
body{font:15px -apple-system,BlinkMacSystemFont,Arial,sans-serif;max-width:1080px;margin:36px auto;
padding:0 18px;background:#101319;color:#e5e7eb;line-height:1.55}
h1,h2{color:#fafafa}p,.sub{color:#aeb4c1}table{width:100%;border-collapse:collapse}
td,th{padding:10px;border-bottom:1px solid #38404d;text-align:left;vertical-align:top}
th{color:#cbd5e1}a{color:#a7c9fa}code{color:#dce9ff}
small{color:#b9bdc7}
</style></head><body><h1>DJ Library Review</h1>
<p>Read-only preview · No track moved or deleted · No metadata changed</p>
<h2>Summary</h2><ul>""" + "\n".join(summary) + """</ul><h2>Incoming tracks</h2>
<table><thead><tr><th>Track</th><th>Status</th><th>Current Genre</th><th>Review reason</th></tr></thead>
<tbody>""" + "\n".join(rows) + """</tbody></table>
<p>Genre tags shown here are existing values and have not been approved or written.</p>
<p>Full duplicate matches, source drift, event evidence and provenance are in
<code>review.json</code> next to this file.</p>
<small>Results describe supplied files at scan time. Verify before taking any action.</small>
</body></html>"""


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--incoming", type=Path, help="explicit Downloads or music-to-move folder")
    parser.add_argument("--db", type=Path, help="existing local DJ library-index SQLite file")
    parser.add_argument("--serato-dir", type=Path, help="optional _Serato_ snapshot directory")
    parser.add_argument("--rekordbox-xml", type=Path, help="optional *freshly exported* Rekordbox XML")
    parser.add_argument("--events", type=Path, help="verified local DJ set window JSON (optional)")
    parser.add_argument("--plays", type=Path, help="normalized play fixture JSON (optional)")
    parser.add_argument("--history-export", type=Path, action="append", default=[],
                        help="unmodified original Serato/Rekordbox History export; repeatable")
    parser.add_argument("--out-dir", type=Path, required=True, help="external report location")
    parser.add_argument("--max-files", type=int, default=MAX_DEFAULT)
    args = parser.parse_args(argv)
    try:
        input_dir = _regular_source(args.incoming, directory=True) if args.incoming else None
        index = read_index(args.db) if args.db else None
        restricted = [p for p in [input_dir, Path(index["root"]) if index else None] if p]
        if args.serato_dir:
            restricted.append(_regular_source(args.serato_dir, directory=True))
        out_root = _safe_report_root(args.out_dir, restricted)
        # All input validation, including available snapshots, precedes report writes.
        report = build_report(args.incoming, args.db, args.serato_dir,
                              args.rekordbox_xml, args.events, args.plays,
                              args.history_export, args.max_files)
        out_root.mkdir(parents=True, exist_ok=True)
        folder = out_root / ("review-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        folder.mkdir(exist_ok=False)
        (folder / "review.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (folder / "review.html").write_text(render_html(report), encoding="utf-8")
    except (ReviewRefused, dj_event_time.ClockEvidenceError, ValueError,
            OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"DJ Library Review refused: {exc}", file=sys.stderr)
        return 1
    print("Read-only review complete. No audio or DJ application files changed.")
    print(f"Open: {folder / 'review.html'}")
    print(f"Data: {folder / 'review.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
