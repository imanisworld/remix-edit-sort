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

from build_version_genre_plan import VERSION_CATEGORIES, classify_version
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
        rows = list(csv.DictReader(file))
    if not rows:
        raise PromotionRefused("inventory is empty; verify the landing folder explicitly")
    must = {"full_path", "sha256", "genre", "title"}
    if not must.issubset(rows[0]):
        raise PromotionRefused(f"inventory missing columns: {sorted(must - set(rows[0]))}")
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
    lib, landing = library_root.resolve(), landing_root.resolve()
    if lib == landing or not inside(landing, lib):
        raise PromotionRefused("landing must be a subfolder of library_root")
    if landing.is_symlink() or not landing.is_dir():
        raise PromotionRefused("landing must be an existing non-symlink directory")
    if destinations.get("schema_version") != 1 or not isinstance(destinations.get("routes"), dict):
        raise PromotionRefused("destination map requires schema_version 1 and routes object")
    if not isinstance(master.get("canonical_genres"), list):
        raise PromotionRefused("invalid master genre list")
    canonical = set(master["canonical_genres"])
    for genre in destinations["routes"]:
        if genre not in canonical:
            raise PromotionRefused(f"destination map has noncanonical genre: {genre!r}")
    review_dir = verified_directory(lib, landing, destinations.get("review_destination"))
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
        version = next(
            (part for part in source.relative_to(landing).parts[:-1] if part in VERSION_CATEGORIES),
            None,
        )
        if version is None:
            version = classify_version(source.stem, row.get("title", ""))[0]
        outcome = {"source_relative": rel, "sha256": row["sha256"], "genre": genre,
                   "version": version, "status": "BLOCKED", "destination": None, "reason": None}

        if hold is not None:
            if hold["sha256"].casefold() != row["sha256"].casefold():
                raise PromotionRefused(f"event hold content hash is stale for {rel}")
            outcome["status"], outcome["reason"] = "EVENT_HOLD", "explicit upcoming-event hold"
        elif "Events" in source.relative_to(landing).parts[:-1]:
            outcome["status"], outcome["reason"] = "EVENT_HOLD", "event subfolder requires explicit history/active review"
        else:
            route = destinations["routes"].get(genre)
            approved_usable = (
                approval is not None
                and approval.get("sha256", "").casefold() == row["sha256"].casefold()
                and approval.get("genre") == genre
                and bool(str(approval.get("approved_by", "")).strip())
                and bool(str(approval.get("evidence", "")).strip())
            )
            target_rel = None
            if genre in canonical and approved_usable and isinstance(route, dict):
                versions = route.get("versions", {})
                if not isinstance(versions, dict):
                    raise PromotionRefused(f"invalid version route map for {genre}")
                target_rel = versions.get(version, route.get("default"))
            try:
                destination_dir = verified_directory(
                    lib, landing,
                    target_rel if target_rel else destinations["review_destination"],
                )
            except PromotionRefused as exc:
                outcome["reason"] = str(exc)
            else:
                dest = destination_dir / source.name
                key = unicodedata.normalize("NFC", str(dest)).casefold()
                if dest.exists() or dest.is_symlink() or key in used_destinations:
                    outcome["reason"] = f"destination collision or occupied: {dest}"
                else:
                    used_destinations[key] = rel
                    outcome["destination"] = str(dest)
                    outcome["status"] = "ROUTE_CANDIDATE" if target_rel else "REVIEW_CANDIDATE"
                    outcome["reason"] = (
                        "approved canonical genre and explicit confirmed destination"
                        if target_rel else
                        "genre or destination not approved; central review queue"
                    )
        items.append(outcome)
    summary = dict(Counter(item["status"] for item in items))
    return {
        "plan_type": "landing-promotion-preview",
        "mode": "preview-only",
        "source_root": str(landing),
        "library_root": str(lib),
        "counts": {"scanned": len(items), **summary},
        "serato_dependency": "Every proposed physical move requires separate verified Serato path-impact review and preservation/relink plan.",
        "approved_apply": False,
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("library-root", "landing-root", "inventory", "destinations",
                 "approvals", "event-holds", "out"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--master", type=Path, default=DEFAULT_MASTER)
    args = parser.parse_args()
    try:
        library = args.library_root.resolve()
        if inside(args.out.resolve(), library):
            raise PromotionRefused("preview output must stay outside the music library")
        report = make_preview(
            args.library_root, args.landing_root, args.inventory,
            read_json(args.destinations), read_json(args.approvals),
            read_json(args.event_holds), read_json(args.master),
        )
    except (ValueError, OSError, json.JSONDecodeError, KeyError) as exc:
        parser.exit(1, f"REFUSED: {exc}\n")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], indent=2))
    print(f"PREVIEW ONLY; no move or Serato relink authorized. {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
