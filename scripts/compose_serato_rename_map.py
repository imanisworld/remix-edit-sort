#!/usr/bin/env python3
"""Read-only: chain several approved rename maps into one exact per-path map.

Old Serato references can be several moves behind (e.g. inner folder rename,
then top-level rename, then Clean/Dirty moves). ``rewrite_serato_paths``
applies exactly one rule per path, so this tool walks every stored path in
database V2, Subcrates/** and SmartCrates/** through each map *in order*
and emits one ``{"from", "to"}`` entry per path whose final location exists
on disk. Paths whose chain ends at a missing file are reported as
unmappable and left out (never guessed).

Feed the output to ``rewrite_serato_paths.py preview --rename-map``. Writes
only the --out / --report files; never touches Serato or the library.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rewrite_serato_paths as sr  # noqa: E402

SAFETY_CLASSIFICATION = "preview-only"


def load_maps(paths: list[Path]) -> list[list[dict]]:
    return [sr.load_rename_map(path) for path in paths]


def chain(path: str, maps: list[list[dict]]) -> tuple[str, list[int]]:
    """Apply every map in order; return final path and the map indexes that fired."""
    fired = []
    for index, rules in enumerate(maps):
        path, rule = sr.rewrite_path(path, rules)
        if rule is not None:
            fired.append(index)
    return path, fired


def stored_references(serato_dir: Path) -> dict[str, list[str]]:
    """Relative source file -> stored paths (one per otrk)."""
    serato_dir = Path(serato_dir).resolve()
    out: dict[str, list[str]] = {}
    for source in sr.serato_source_files(serato_dir):
        relative = str(source.relative_to(serato_dir))
        field = sr._field_for(relative)
        paths = []
        for tag, value in sr.parse_tlv_strict(source.read_bytes(), relative):
            if tag != "otrk":
                continue
            for inner_tag, inner_value in sr.parse_tlv_strict(value, f"{relative}:otrk"):
                if inner_tag == field:
                    paths.append(sr._decode_path_value(inner_value, relative)[0])
        out[relative] = paths
    return out


def sqlite_references(databases) -> dict[str, list[str]]:
    """Read-only (immutable) asset.portable_id values from Serato's library DBs."""
    import sqlite3
    import rewrite_serato_sqlite_paths as sq
    out = {}
    for database in databases or []:
        database = Path(database).resolve()
        sq.require_quiescent(database)  # an unfolded WAL holds rows immutable=1 cannot see
        conn = sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True)
        try:
            out[f"sqlite:{database.name}"] = [
                row[0] for row in conn.execute("SELECT portable_id FROM asset")
                if isinstance(row[0], str)
            ]
        finally:
            conn.close()
    return out


def compose(serato_dir: Path, maps: list[list[dict]], exists=None, databases=None) -> dict:
    exists = exists or sr._literal_stored_path_is_file
    refs = stored_references(serato_dir)
    refs.update(sqlite_references(databases))
    database_paths = set(refs.get(sr.DATABASE_NAME, []))
    mapping: dict[str, str] = {}
    unmappable: dict[str, dict] = {}
    per_file: dict[str, Counter] = defaultdict(Counter)
    for relative, paths in refs.items():
        for stored in paths:
            final, fired = chain(stored, maps)
            if not fired or final == stored:  # e.g. A -> B then B -> A: nothing to repoint
                continue
            if ("\\" in stored or "\\" in final) and (
                stored.rsplit("/", 1)[-1] != final.rsplit("/", 1)[-1]
                or "\\" in stored.rsplit("/", 1)[0] or "\\" in final.rsplit("/", 1)[0]
            ):
                # Backslash only allowed in an unchanged file name; hold anything else.
                entry = unmappable.setdefault(stored, {"from": stored, "chained_to": final, "in": [],
                                                        "why": "backslash outside an unchanged file name"})
                entry["in"].append(relative)
                per_file[relative]["held_backslash"] += 1
                continue
            if exists(final):
                mapping[stored] = final
                per_file[relative]["repoint"] += 1
            else:
                entry = unmappable.setdefault(stored, {"from": stored, "chained_to": final, "in": []})
                entry["in"].append(relative)
                per_file[relative]["unmappable"] += 1
    ghosts = sorted(
        old for old, new in mapping.items() if old in database_paths and new in database_paths
    )
    composed = [{"from": "/" + old, "to": "/" + new} for old, new in sorted(mapping.items())]
    if composed:
        # Fail here, not later in rewrite_serato_paths: a composed map that
        # chains (X -> Y plus Z -> X) cannot be applied in one pass.
        sr.normalize_rename_map(composed)
    return {
        "map": composed,
        "report": {
            "unique_paths_to_repoint": len(mapping),
            "per_file": {
                name: dict(counts) for name, counts in sorted(per_file.items())
            },
            "unmappable": sorted(unmappable.values(), key=lambda item: item["from"]),
            "database_ghosts_with_live_twin": ghosts,
            "database_old_paths_without_twin": sorted(
                old for old, new in mapping.items() if old in database_paths and new not in database_paths
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serato-dir", required=True, type=Path)
    parser.add_argument("--map", action="append", required=True, type=Path,
                        help="approved rename map; repeat in the order the moves happened")
    parser.add_argument("--out", required=True, type=Path, help="composed per-path rename map (JSON)")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--sqlite-db", action="append", type=Path, default=[],
                        help="also map asset paths in master.sqlite/root.sqlite (read-only)")
    args = parser.parse_args()
    try:
        result = compose(args.serato_dir, load_maps(args.map), databases=args.sqlite_db)
    except sr.SeratoRewriteError as exc:  # includes the SQLite WAL refusal
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    sr.write_json(args.out, result["map"])
    sr.write_json(args.report, result["report"])
    report = result["report"]
    print(json.dumps({
        "unique_paths_to_repoint": report["unique_paths_to_repoint"],
        "unmappable": len(report["unmappable"]),
        "database_ghosts_with_live_twin": len(report["database_ghosts_with_live_twin"]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
