#!/usr/bin/env python3
"""
PREVIEW-ONLY. Proposes moving already-sorted files into a different genre folder.

Why this exists as its own step: re-running the full pipeline over files it has
already normalized re-parses their new filenames, and a title like
"Under The Boardwalk - The Drifters, Phil Hooton Rework" gets re-read as a remix
credit and rebuilt worse. Sorting `_Unclassified` is a recurring job, so it needs
a path that can't degrade what's already right.

    <Top>/_Unclassified/<Sub>/file.mp3  ->  <Top>/<Genre>/<Sub>/file.mp3

## Why this no longer applies anything

A safety audit found this script moving files and rewriting Genre on a live
music library outside every control the rest of the repo enforces:

  * no folder lock, so it could run concurrently with another agent session;
  * no backup requirement;
  * no hash, audio-payload or Serato-frame verification around the write;
  * a forced `v2_version=3` on every file it tagged, silently downgrading
    ID3v2.4 tags and clobbering the ID3v2.2 blocks the approved engine
    deliberately preserves;
  * `prune_empty_dirs()` deleting directories and `.DS_Store` files as a side
    effect of a "reclassify" run;
  * an undo that moved files back but explicitly did NOT revert the Genre tag
    it had overwritten ("genre tags not reverted"). The original Genre value
    was simply gone.

The move+Genre mutation class is already implemented properly by
`apply_version_genre_plan.py`, which verifies audio payload and Serato frames
around every write, preserves ID3v2.2, holds the lock, and has a real undo. So
this script now only proposes; that engine applies.

    python3 scripts/reclassify.py --tracks tracks.json --out plan.json
    python3 scripts/backup_manifest.py verify --source-root ... --backup ... --out manifest.json
    python3 scripts/folder_lock.py acquire --folder ... --session ...
    python3 scripts/apply_version_genre_plan.py --apply --backup-manifest manifest.json ...

Usage:
    python3 reclassify.py --tracks outputs/tracks.json                    # preview
    python3 reclassify.py --tracks outputs/tracks.json --out plan.json    # preview + plan
"""
import os, sys, json, argparse, collections

try:
    from mutagen.id3 import ID3, ID3NoHeaderError, TCON
except ImportError:
    sys.exit("mutagen is required:  python3 -m pip install --user mutagen")

TOPS = ("Remixes & Edits", "Originals")

# Preview-only: builds a plan for human review. Never mutates library media.
SAFETY_CLASSIFICATION = "preview-only"

RETIRED_MUTATION_MESSAGE = (
    "reclassify.py no longer moves files or writes Genre tags.\n"
    "\n"
    "It mutated the library with no lock, no backup, no verification, a forced\n"
    "ID3v2.3 downgrade, directory deletion as a side effect, and an undo that\n"
    "left the overwritten Genre permanently lost. It is now a preview/plan\n"
    "builder only.\n"
    "\n"
    "Apply move+Genre changes through the approved engine instead:\n"
    "  1. python3 scripts/reclassify.py --tracks tracks.json --out plan.json   # review this\n"
    "  2. python3 scripts/backup_manifest.py verify --source-root ... --backup ... --out manifest.json\n"
    "  3. python3 scripts/folder_lock.py acquire --folder ... --session ...\n"
    "  4. python3 scripts/apply_version_genre_plan.py --apply \\\n"
    "         --backup-manifest manifest.json --lock-folder ... --session ...\n"
)


def split_path(path, root):
    """-> (top, genre_folder, subfolder, filename) relative to the library root."""
    rel = os.path.relpath(path, root).split(os.sep)
    top = rel[0] if rel and rel[0] in TOPS else None
    if not top or len(rel) < 3:
        return None
    return top, rel[1], os.sep.join(rel[2:-1]), rel[-1]


def read_genre(path):
    """Current Genre, for the preview's before/after column. Read-only."""
    try:
        tags = ID3(path)
    except (ID3NoHeaderError, Exception):  # noqa: B014 - unreadable is not fatal here
        return ""
    frames = tags.getall("TCON")
    return str(frames[0]).strip() if frames else ""


def proposed_dest(dest):
    """Report a destination collision; never silently invent a " (2)" name.

    The old code resolved collisions itself with unique_dest() at apply time,
    which meant a genuine name clash between two different recordings was
    quietly renamed rather than surfaced. Collisions are now part of the
    preview a human reviews, and the engine's `--on-collision` policy decides.
    """
    return {"destination": dest, "collision": os.path.exists(dest)}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--tracks", default="outputs/tracks.json")
    ap.add_argument("--root", default=os.path.expanduser("~/Downloads/SoundCloud"))
    ap.add_argument("--from-genre", default="_Unclassified",
                    help="only consider files currently in this genre folder")
    ap.add_argument("--apply", action="store_true",
                    help="RETIRED -- refuses to run; route through the approved engine instead")
    ap.add_argument("--undo", action="store_true",
                    help="RETIRED -- refuses to run; this script no longer mutates anything")
    ap.add_argument("--out", help="write the proposals as JSON for review/plan building")
    args = ap.parse_args()

    if args.apply or args.undo:
        raise SystemExit(RETIRED_MUTATION_MESSAGE)

    root = os.path.abspath(os.path.expanduser(args.root))
    tracks = json.load(open(args.tracks))["tracks"]

    proposals, skipped, unresolved = [], 0, 0
    for t in tracks:
        parts = split_path(t["path"], root)
        if not parts:
            skipped += 1
            continue
        top, cur_genre, sub, fname = parts
        if cur_genre != args.from_genre:
            skipped += 1
            continue
        if not t.get("genre"):
            # No evidenced genre. Never guessed -- left where it is for a human.
            unresolved += 1
            continue
        dest = os.path.join(root, top, t["genre"], sub, fname)
        if os.path.abspath(dest) == os.path.abspath(t["path"]):
            skipped += 1
            continue
        destination = proposed_dest(dest)
        proposals.append({
            "source": t["path"],
            "destination": destination["destination"],
            "destination_collision": destination["collision"],
            "move": True,
            "current_genre": read_genre(t["path"]),
            "proposed_genre": t["genre"],
            "tag_change": True,
        })

    by_genre = collections.Counter(p["proposed_genre"] for p in proposals)
    collisions = [p for p in proposals if p["destination_collision"]]
    print(f"{len(proposals)} files proposed for reclassification, "
          f"{unresolved} still unresolved (no evidenced genre), "
          f"{skipped} untouched", file=sys.stderr)
    for g, n in by_genre.most_common():
        print(f"   {n:>3}  -> {g}", file=sys.stderr)
    if collisions:
        print(f"   {len(collisions)} destination collision(s) -- these need a human "
              f"decision, they are NOT auto-renamed and NOT duplicates", file=sys.stderr)

    for p in proposals[:10]:
        print(f"   {os.path.basename(p['source'])[:58]}  ->  {p['proposed_genre']}", file=sys.stderr)

    if args.out:
        plan = {
            "mode": "preview-only",
            "generator": "reclassify.py",
            "root": root,
            "rules": {"approval_required": True, "mutations_performed": 0},
            "counts": {
                "proposed": len(proposals),
                "unresolved": unresolved,
                "skipped": skipped,
                "destination_collisions": len(collisions),
            },
            "items": proposals,
        }
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(plan, handle, indent=2, ensure_ascii=False)
        print(f"Plan: {args.out}", file=sys.stderr)

    print("\nPREVIEW ONLY — nothing was moved or retagged.", file=sys.stderr)
    print("Apply via apply_version_genre_plan.py (lock + verified backup manifest + engine).",
          file=sys.stderr)


if __name__ == "__main__":
    main()
