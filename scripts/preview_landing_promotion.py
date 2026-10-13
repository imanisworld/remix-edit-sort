#!/usr/bin/env python3
"""Preview promotion OUT of a DJ-music landing folder into confirmed library folders.

This is read-only toward audio, Serato, crates and library folders. It consumes
the normal inventory_library.py inventory.csv plus explicit, operator-confirmed
destination paths and post-tag genre approvals. The output is NEVER an apply plan.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "preview-only"

import argparse
from collections import Counter
import csv
import hashlib
import json
import os
from pathlib import Path
import unicodedata

from build_version_genre_plan import NEEDS_VERSION, VERSION_CATEGORIES, classify_version
from inventory_library import AUDIO_EXTS, audio_ext

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MASTER = ROOT / "references" / "genre-learner-rules.json"


class PromotionRefused(ValueError):
    """Plan inputs cannot be trusted as an authoritative inventory."""


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_sha256(value: dict) -> str:
    """Evidence fingerprint for a review, never a signature or approval."""
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def verified_directory(lib: Path, landing: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise PromotionRefused("destination directory must be a nonempty relative path")
    candidate = Path(relative)
    if candidate.is_absolute() or any(part in ("..", ".") for part in candidate.parts):
        raise PromotionRefused(f"invalid destination path: {relative!r}")
    directory = lib / candidate
    if not directory.is_dir() or directory.is_symlink() or directory.resolve() != directory:
        raise PromotionRefused(f"destination folder not present as a real directory: {relative!r}")
    if not inside(directory, lib) or inside(directory, landing):
        raise PromotionRefused(f"destination must be outside landing and inside library: {relative!r}")
    return directory


def inventory_rows(path: Path, landing: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        fields = set(reader.fieldnames or [])
        rows = list(reader)
    must = {"full_path", "sha256", "genre", "title"}
    if not must.issubset(fields):
        raise PromotionRefused(f"inventory missing columns: {sorted(must - fields)}")
    seen = set()
    for row in rows:
        raw = Path(row["full_path"]).absolute()
        # macOS may expose /var as /private/var. Canonicalize the parent only
        # so the final filename's literal spelling is still verified.
        source = raw.parent.resolve() / raw.name
        if source in seen:
            raise PromotionRefused(f"duplicate inventory path: {source}")
        seen.add(source)
        if not inside(source, landing) or not source.is_file() or source.is_symlink():
            raise PromotionRefused(f"unsafe or absent source: {source}")
        if source.resolve() != source:
            raise PromotionRefused(f"source contains a symlink: {source}")
        if audio_ext(source) not in AUDIO_EXTS:
            raise PromotionRefused(f"unsupported inventory item: {source}")
        if len(row["sha256"]) != 64 or sha256(source) != row["sha256"].casefold():
            raise PromotionRefused(f"source hash changed since inventory: {source}")
        row["full_path"] = str(source)
    # A partial or stale inventory cannot authorize declaring the landing clear.
    disk_paths = set()
    for dirpath, dirnames, filenames in os.walk(landing, followlinks=False):
        for dirname in dirnames:
            if (Path(dirpath) / dirname).is_symlink():
                raise PromotionRefused(f"symlinked directory in landing: {dirpath}/{dirname}")
        for name in filenames:
            source = Path(dirpath) / name
            if audio_ext(source) in AUDIO_EXTS and not name.startswith("._"):
                disk_paths.add(source.absolute())
    if disk_paths != seen:
        raise PromotionRefused(
            f"stale inventory: {len(disk_paths - seen)} unlisted audio file(s), "
            f"{len(seen - disk_paths)} absent audio file(s)"
        )
    return rows


def records(value, key: str) -> dict[str, dict]:
    """Content-bound operator records keyed by landing-relative path."""
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise PromotionRefused(f"{key} needs an items list")
    found = {}
    for item in value["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("source_relative"), str):
            raise PromotionRefused(f"{key} has malformed item")
        name = item["source_relative"]
        if name in found or Path(name).is_absolute() or ".." in Path(name).parts:
            raise PromotionRefused(f"{key} has duplicate/unsafe relative source {name!r}")
        digest = item.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise PromotionRefused(f"{key} has invalid sha256 for {name!r}")
        found[name] = item
    return found


def make_preview(
    library_root: Path,
    landing_root: Path,
    inventory: Path,
    destinations: dict,
    approvals: dict,
    event_holds: dict,
    master: dict,
) -> dict:
    # Check the literal root paths *before* resolving them. Once resolved,
    # is_symlink() cannot detect that an operator selected an alias/symlink.
    if library_root.is_symlink() or landing_root.is_symlink():
        raise PromotionRefused("library and landing roots must be real directories, not symlinks")
    lib, landing = library_root.resolve(), landing_root.resolve()
    if lib == landing or not inside(landing, lib):
        raise PromotionRefused("landing must be a subfolder of library_root")
    if not lib.is_dir() or not landing.is_dir():
        raise PromotionRefused("library and landing roots must be existing directories")
    if destinations.get("schema_version") != 1 or not isinstance(destinations.get("routes"), dict):
        raise PromotionRefused("destination map requires schema_version 1 and routes object")
    if not isinstance(master.get("canonical_genres"), list):
        raise PromotionRefused("invalid master genre list")
    canonical = set(master["canonical_genres"])
    for genre in destinations["routes"]:
        if genre not in canonical:
            raise PromotionRefused(f"destination map has noncanonical genre: {genre!r}")
    # Needs-review is a queue/status, never a newly invented physical folder.
    # Keep unapproved tracks where they are until a review confirms Genre and route.
    approved = records(approvals, "approvals")
    protected = records(event_holds, "event_holds")
    rows = inventory_rows(inventory, landing)
    relative_paths = {str(Path(row["full_path"]).relative_to(landing)) for row in rows}
    if (set(approved) | set(protected)) - relative_paths:
        raise PromotionRefused("approval/event-hold list contains paths not in current inventory")

    used_destinations: dict[str, str] = {}
    items: list[dict] = []
    for row in rows:
        source = Path(row["full_path"])
        rel = str(source.relative_to(landing))
        genre = row["genre"].strip()
        approval = approved.get(rel)
        hold = protected.get(rel)
        folder_versions = {
            part for part in source.relative_to(landing).parts[:-1]
            if part in VERSION_CATEGORIES and part != NEEDS_VERSION
        }
        named_version = classify_version(source.stem, row.get("title", ""))[0]
        version = sorted(folder_versions)[0] if len(folder_versions) == 1 else named_version
        version_conflict = len(folder_versions) > 1 or (
            len(folder_versions) == 1
            and named_version != NEEDS_VERSION
            and named_version != version
        )
        outcome = {"source_relative": rel, "sha256": row["sha256"], "genre": genre,
                   "version": version, "status": "BLOCKED", "destination": None, "reason": None}

        if approval is not None and approval["sha256"].casefold() != row["sha256"].casefold():
            raise PromotionRefused(f"approval content hash is stale for {rel}")
        if hold is not None:
            if hold["sha256"].casefold() != row["sha256"].casefold():
                raise PromotionRefused(f"event hold content hash is stale for {rel}")
            outcome["status"], outcome["reason"] = "EVENT_HOLD", "explicit upcoming-event hold"
        elif any(part.casefold() == "events" for part in source.relative_to(landing).parts[:-1]):
            outcome["status"], outcome["reason"] = "EVENT_HOLD", "event subfolder requires explicit history/active review"
        elif version_conflict:
            outcome["status"], outcome["reason"] = "NEEDS_REVIEW", (
                "version evidence conflicts between file name and existing folder; "
                "no route until separately reviewed"
            )
        else:
            route = destinations["routes"].get(genre)
            approved_usable = (
                approval is not None
                and approval.get("sha256", "").casefold() == row["sha256"].casefold()
                and approval.get("genre") == genre
                and isinstance(approval.get("approved_by"), str)
                and bool(approval["approved_by"].strip())
                and isinstance(approval.get("evidence"), str)
                and bool(approval["evidence"].strip())
            )
            target_rel = None
            if genre in canonical and approved_usable and isinstance(route, dict):
                versions = route.get("versions", {})
                if not isinstance(versions, dict):
                    raise PromotionRefused(f"invalid version route map for {genre}")
                target_rel = versions.get(version, route.get("default"))
            if not target_rel:
                outcome["status"] = "NEEDS_REVIEW"
                outcome["reason"] = (
                    "genre unapproved, noncanonical, or no verified destination; "
                    "leave in landing folder until resolved"
                )
            else:
                try:
                    destination_dir = verified_directory(lib, landing, target_rel)
                except PromotionRefused as exc:
                    outcome["reason"] = str(exc)
                else:
                    dest = destination_dir / source.name
                    key = unicodedata.normalize("NFC", str(dest)).casefold()
                    # APFS may be case/Unicode-insensitive. Treat an existing
                    # case-only name collision as occupied even on Linux CI.
                    existing_names = {
                        unicodedata.normalize("NFC", child.name).casefold()
                        for child in destination_dir.iterdir()
                    }
                    name_key = unicodedata.normalize("NFC", source.name).casefold()
                    if (dest.exists() or dest.is_symlink()
                            or name_key in existing_names or key in used_destinations):
                        outcome["reason"] = f"destination collision or occupied: {dest}"
                    else:
                        used_destinations[key] = rel
                        outcome["destination"] = str(dest)
                        outcome["status"] = "ROUTE_CANDIDATE"
                        outcome["reason"] = (
                            "approved canonical genre and explicit confirmed destination; "
                            "Serato/Rekordbox path review still required"
                        )
        items.append(outcome)
    summary = dict(Counter(item["status"] for item in items))
    return {
        "plan_type": "landing-promotion-preview",
        "mode": "preview-only",
        "source_root": str(landing),
        "library_root": str(lib),
        # Bind this preview to the exact inventory and decisions observed.
        # A hash proves reproducibility, not the operator's consent to move.
        "input_fingerprints": {
            "inventory_csv_sha256": sha256(inventory),
            "destinations_sha256": manifest_sha256(destinations),
            "approvals_sha256": manifest_sha256(approvals),
            "event_holds_sha256": manifest_sha256(event_holds),
            "genre_master_sha256": manifest_sha256(master),
        },
        "counts": {"scanned": len(items), **summary},
        "serato_dependency": "Every proposed physical move requires separate verified Serato path-impact review and preservation/relink plan.",
        "approved_apply": False,
        "items": items,
    }



def serato_rename_map(preview: dict, volume_root: Path) -> dict:
    """Generate a candidate map for the EXISTING Serato read-only rewrite preview.

    No Serato file is inspected or changed here. The map is evidence for the
    separate `rewrite_serato_paths.py preview` command only. Its match counts
    must be reviewed against real Serato sources before approving any move.
    """
    from rewrite_serato_paths import SeratoRewriteError, normalize_rename_map

    volume = Path(volume_root).resolve()
    lib = Path(preview["library_root"]).resolve()
    if not volume.is_dir() or not os.path.ismount(volume) or not inside(lib, volume):
        raise PromotionRefused("Serato volume root must be a mounted volume root containing the music library")
    renames = []
    for row in preview["items"]:
        if row["status"] != "ROUTE_CANDIDATE":
            continue
        source = Path(preview["source_root"]) / row["source_relative"]
        destination = Path(row["destination"])
        if not inside(source, volume) or not inside(destination, volume):
            raise PromotionRefused(f"candidate path outside Serato volume: {source}")
        old, new = source.relative_to(volume), destination.relative_to(volume)
        renames.append({"from": old.as_posix(), "to": new.as_posix()})
    if renames:
        try:
            validated = normalize_rename_map({"renames": renames})
        except SeratoRewriteError as exc:
            raise PromotionRefused(f"Serato candidate rename map unsafe: {exc}") from exc
        if len(validated) != len(renames):
            raise PromotionRefused("incomplete Serato rename-map normalization")
    return {
        "renames": renames,
        "_note": ("CANDIDATES ONLY: review Serato database/crate reference counts "
                  "with rewrite_serato_paths.py preview before considering a move. "
                  "This document authorizes no audio or Serato mutations."),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("library-root", "landing-root", "inventory", "destinations",
                 "approvals", "event-holds", "out"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--master", type=Path, default=DEFAULT_MASTER)
    parser.add_argument("--serato-volume-root", type=Path,
                        help="Optional verified volume root for Serato path candidates")
    parser.add_argument("--rename-map-out", type=Path,
                        help="Optional candidate map for separate Serato read-only preview")
    args = parser.parse_args()
    try:
        library = args.library_root.resolve()
        if inside(args.out.resolve(), library):
            raise PromotionRefused("preview output must stay outside the music library")
        if bool(args.rename_map_out) != bool(args.serato_volume_root):
            raise PromotionRefused("Serato candidate map requires BOTH --serato-volume-root and --rename-map-out")
        if args.rename_map_out and (
            inside(args.rename_map_out.resolve(), library)
            or args.rename_map_out.resolve() == args.out.resolve()
        ):
            raise PromotionRefused("Serato candidate map output must be outside Music and distinct from preview")
        report = make_preview(
            args.library_root, args.landing_root, args.inventory,
            read_json(args.destinations), read_json(args.approvals),
            read_json(args.event_holds), read_json(args.master),
        )
    except (ValueError, OSError, json.JSONDecodeError, KeyError) as exc:
        parser.exit(1, f"REFUSED: {exc}\n")
    try:
        candidate_map = serato_rename_map(report, args.serato_volume_root) if args.rename_map_out else None
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"REFUSED: {exc}\n")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if candidate_map is not None:
        args.rename_map_out.parent.mkdir(parents=True, exist_ok=True)
        args.rename_map_out.write_text(
            json.dumps(candidate_map, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report["counts"], indent=2))
    print(f"PREVIEW ONLY; no move or Serato relink authorized. {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
