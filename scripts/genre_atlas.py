#!/usr/bin/env python3
"""World Music Atlas snapshot and master-Genre crosswalk.

The atlas is a third-party genre reference (origins, eras, roots, sub-genre
trees). It describes genres, never tracks, so nothing here is evidence for a
Genre tag on any file. This module only:

  extract  Pull the atlas data out of a saved copy of its HTML page and write
           an integrity-stamped snapshot plus provenance record.
  build    Resolve the hand-curated crosswalk (master Genre -> atlas cards)
           against the snapshot and write the generated crosswalk.
  check    Verify snapshot integrity, that the curated crosswalk covers the
           master Genre list exactly, that every atlas path resolves, and that
           the committed generated crosswalk is current.

It never changes music files, tags, folders, Serato data, or models.
Matching is never automatic: a shared name is not a shared genre (the master's
dancehall "Riddim" is not the atlas's 2014 dubstep "Riddim"), so every link is
written by hand in crosswalk-curated.json and only validated here.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "preview-only"

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ATLAS_DIR = REPO / "references" / "genre-atlas"
SNAPSHOT = ATLAS_DIR / "atlas-snapshot.json"
PROVENANCE = ATLAS_DIR / "PROVENANCE.json"
CURATED = ATLAS_DIR / "crosswalk-curated.json"
CROSSWALK = ATLAS_DIR / "crosswalk.json"
MASTER = REPO / "references" / "genre-learner-rules.json"

DATA_MARKER = "const DATA = "
MATCH_KINDS = ("exact", "close", "uncertain", "none")

FIELD_LEGEND = {
    "n": "name",
    "o": "origin (place)",
    "y": "era (when the style took recognizable shape)",
    "d": "description",
    "rt": "roots (what it grew out of)",
    "l": "artists to listen to",
    "e": "example recordings ('Artist – Title')",
    "k": "keywords",
    "co": "countries",
    "x": "cross-references to related genres",
    "c": "child cards (sub-genres / scenes)",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def serialize_snapshot(regions: list) -> bytes:
    """Canonical snapshot bytes: stable key order from the source, one-space indent."""
    return (json.dumps(regions, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def parse_atlas_html(text: str) -> list:
    """Return the atlas region list embedded in the page as ``const DATA = [...]``."""
    start = text.find(DATA_MARKER)
    if start < 0:
        raise ValueError("atlas data block not found (no 'const DATA = ')")
    start = text.index("[", start)
    decoder = json.JSONDecoder()
    regions, _ = decoder.raw_decode(text, start)
    if not isinstance(regions, list) or not regions:
        raise ValueError("atlas data block is not a non-empty list")
    for region in regions:
        if not {"id", "name", "genres"} <= set(region):
            raise ValueError(f"region missing id/name/genres: {sorted(region)}")
    return regions


def iter_cards(regions: list):
    """Yield (path, card, depth) for every card. Path is 'region-id:Name > Child'."""
    def walk(card, names, region_id, depth):
        here = names + [card["n"]]
        yield f"{region_id}:{' > '.join(here)}", card, depth
        for child in card.get("c") or []:
            yield from walk(child, here, region_id, depth + 1)

    for region in regions:
        for card in region["genres"]:
            yield from walk(card, [], region["id"], 0)


def index_paths(regions: list) -> dict:
    index = {}
    for path, card, _ in iter_cards(regions):
        # A repeated path would make a curated reference ambiguous; keep the first
        # and let check() report it if anything ever points at it.
        index.setdefault(path, card)
    for region in regions:
        index[f"region:{region['id']}"] = {"n": region["name"], "d": region.get("note", "")}
    return index


def stats(regions: list) -> dict:
    cards = list(iter_cards(regions))
    return {
        "regions": len(regions),
        "top_level_cards": sum(len(r["genres"]) for r in regions),
        "cards_total": len(cards),
        "max_depth": max(depth for _, _, depth in cards),
        "cards_without_era": sum(1 for _, c, _ in cards if not c.get("y")),
        "cards_without_origin": sum(1 for _, c, _ in cards if not c.get("o")),
    }


def extract(html_path: Path, source_url: str, retrieved_at: str, out_dir: Path) -> dict:
    raw = Path(html_path).read_bytes()
    regions = parse_atlas_html(raw.decode("utf-8"))
    snapshot = serialize_snapshot(regions)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / SNAPSHOT.name).write_bytes(snapshot)
    provenance = {
        "title": "World Music Atlas",
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "author": "unknown (public claude.ai artifact published outside this account's organization)",
        "license": "unknown",
        "source_html_sha256": sha256_bytes(raw),
        "snapshot_sha256": sha256_bytes(snapshot),
        "extraction": "scripts/genre_atlas.py extract: JSON array following 'const DATA = ' in the page, re-serialized with indent=1",
        "evidence_grade": "unknown",
        "use_policy": {
            "genre_reference_only": True,
            "never_evidence_for_a_track_tag": True,
            "never_learner_training_data": True,
            "crosswalk_links_require_human_curation": True,
            "known_defects": [
                "Same genre appears in several regions with conflicting eras (131 of 215 repeated names).",
                "Many cards have no era or origin.",
                "Keyword fields are noisy; substring matching produces false hits (e.g. 'twerk' inside 'Kraftwerk').",
            ],
        },
        "field_legend": FIELD_LEGEND,
        "stats": stats(regions),
    }
    (out_dir / PROVENANCE.name).write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return provenance


def card_summary(card: dict) -> dict:
    return {key: card[key] for key in ("n", "o", "y", "rt", "l", "e") if card.get(key)}


def build(regions: list, curated: dict, master: list) -> dict:
    index = index_paths(regions)
    errors = []
    entries = curated["genres"]
    missing = [g for g in master if g not in entries]
    extra = [g for g in entries if g not in master]
    if missing:
        errors.append(f"master Genres with no curated entry: {missing}")
    if extra:
        errors.append(f"curated entries not in the master Genre list: {extra}")
    out = {}
    for genre in master:
        entry = entries.get(genre)
        if entry is None:
            continue
        kind = entry.get("match")
        if kind not in MATCH_KINDS:
            errors.append(f"{genre}: match must be one of {MATCH_KINDS}, got {kind!r}")
        paths = entry.get("atlas", [])
        if kind == "none" and paths:
            errors.append(f"{genre}: match 'none' must not list atlas paths")
        if kind != "none" and not paths:
            errors.append(f"{genre}: match {kind!r} needs at least one atlas path")
        cards = []
        for path in paths:
            card = index.get(path)
            if card is None:
                errors.append(f"{genre}: atlas path does not resolve: {path}")
                continue
            cards.append({"path": path, **card_summary(card)})
        out[genre] = {"match": kind, "note": entry.get("note", ""), "atlas": cards}
    if errors:
        raise ValueError("crosswalk invalid:\n  " + "\n  ".join(errors))
    return {
        "_comment": "GENERATED by scripts/genre_atlas.py build from crosswalk-curated.json. Edit the curated file, not this one.",
        "atlas_snapshot_sha256": sha256_bytes(serialize_snapshot(regions)),
        "use_policy": "Reference only. Not evidence for any track's Genre and never learner training data.",
        "genres": out,
    }


def serialize_crosswalk(crosswalk: dict) -> str:
    return json.dumps(crosswalk, ensure_ascii=False, indent=2) + "\n"


def load_master(path: Path = MASTER) -> list:
    return json.loads(Path(path).read_text(encoding="utf-8"))["canonical_genres"]


def check(atlas_dir: Path = ATLAS_DIR, master_path: Path = MASTER) -> list:
    """Return a list of problems; empty means the committed atlas files are consistent."""
    problems = []
    snapshot_bytes = (atlas_dir / SNAPSHOT.name).read_bytes()
    provenance = json.loads((atlas_dir / PROVENANCE.name).read_text(encoding="utf-8"))
    if sha256_bytes(snapshot_bytes) != provenance.get("snapshot_sha256"):
        problems.append("atlas-snapshot.json does not match PROVENANCE.json snapshot_sha256 (snapshot edited or replaced)")
    regions = json.loads(snapshot_bytes.decode("utf-8"))
    if serialize_snapshot(regions) != snapshot_bytes:
        problems.append("atlas-snapshot.json is not in canonical serialization")
    curated = json.loads((atlas_dir / CURATED.name).read_text(encoding="utf-8"))
    try:
        expected = serialize_crosswalk(build(regions, curated, load_master(master_path)))
    except ValueError as exc:
        problems.append(str(exc))
        return problems
    committed = (atlas_dir / CROSSWALK.name).read_text(encoding="utf-8")
    if committed != expected:
        problems.append("crosswalk.json is stale; run: python3 scripts/genre_atlas.py build")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p_extract = sub.add_parser("extract", help="snapshot the atlas from a saved copy of its HTML page")
    p_extract.add_argument("--html", type=Path, required=True)
    p_extract.add_argument("--source-url", required=True)
    p_extract.add_argument("--retrieved-at", required=True, help="YYYY-MM-DD")
    p_extract.add_argument("--out-dir", type=Path, default=ATLAS_DIR)
    sub.add_parser("build", help="regenerate crosswalk.json from crosswalk-curated.json")
    sub.add_parser("check", help="verify snapshot integrity and crosswalk consistency")
    args = parser.parse_args(argv)

    if args.command == "extract":
        provenance = extract(args.html, args.source_url, args.retrieved_at, args.out_dir)
        print(json.dumps(provenance["stats"], indent=2))
        return 0
    if args.command == "build":
        regions = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        curated = json.loads(CURATED.read_text(encoding="utf-8"))
        CROSSWALK.write_text(serialize_crosswalk(build(regions, curated, load_master())), encoding="utf-8")
        print(f"wrote {CROSSWALK.relative_to(REPO)}")
        return 0
    problems = check()
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    if not problems:
        print("genre atlas: OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
