#!/usr/bin/env python3
"""
Reclassify already-sorted files into a different genre folder — moving them and
updating ONLY the genre tag, leaving filenames and every other tag untouched.

Why this exists as its own step: re-running the full pipeline over files it has
already normalized re-parses their new filenames, and a title like
"Under The Boardwalk - The Drifters, Phil Hooton Rework" gets re-read as a remix
credit and rebuilt worse. Sorting `_Unclassified` is a recurring job, so it needs
a path that can't degrade what's already right.

    <Top>/_Unclassified/<Sub>/file.mp3  ->  <Top>/<Genre>/<Sub>/file.mp3

Usage:
    python3 reclassify.py --tracks outputs/tracks.json               # dry run
    python3 reclassify.py --tracks outputs/tracks.json --apply
"""
import os, sys, json, shutil, argparse, collections

try:
    from mutagen.id3 import ID3, ID3NoHeaderError, TCON
except ImportError:
    sys.exit("mutagen is required:  python3 -m pip install --user mutagen")

UNDO_LOG = "outputs/undo-reclassify.json"
TOPS = ("Remixes & Edits", "Originals")


def split_path(path, root):
    """-> (top, genre_folder, subfolder, filename) relative to the library root."""
    rel = os.path.relpath(path, root).split(os.sep)
    top = rel[0] if rel and rel[0] in TOPS else None
    if not top or len(rel) < 3:
        return None
    return top, rel[1], os.sep.join(rel[2:-1]), rel[-1]


def set_genre(path, genre):
    try:
        t = ID3(path)
    except ID3NoHeaderError:
        t = ID3()
    except Exception as e:
        return f"unreadable: {e}"
    t.setall("TCON", [TCON(encoding=3, text=genre)])
    try:
        t.save(path, v2_version=3)
        return None
    except Exception as e:
        return f"save failed: {e}"


def unique_dest(dest):
    if not os.path.exists(dest):
        return dest
    stem, ext = os.path.splitext(dest)
    n = 2
    while os.path.exists(f"{stem} ({n}){ext}"):
        n += 1
    return f"{stem} ({n}){ext}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracks", default="outputs/tracks.json")
    ap.add_argument("--root", default=os.path.expanduser("~/Downloads/SoundCloud"))
    ap.add_argument("--from-genre", default="_Unclassified",
                    help="only touch files currently in this genre folder")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", action="store_true")
    args = ap.parse_args()

    if args.undo:
        return undo()

    root = os.path.abspath(os.path.expanduser(args.root))
    tracks = json.load(open(args.tracks))["tracks"]

    moves, skipped, unresolved = [], 0, 0
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
            unresolved += 1
            continue
        dest = os.path.join(root, top, t["genre"], sub, fname)
        if os.path.abspath(dest) == os.path.abspath(t["path"]):
            skipped += 1
            continue
        moves.append({"from": t["path"], "to": dest, "genre": t["genre"]})

    by_genre = collections.Counter(m["genre"] for m in moves)
    print(f"{len(moves)} files to reclassify, {unresolved} still unresolved, "
          f"{skipped} untouched", file=sys.stderr)
    for g, n in by_genre.most_common():
        print(f"   {n:>3}  -> {g}", file=sys.stderr)

    if not args.apply:
        print("\nDRY RUN — re-run with --apply to move and retag.", file=sys.stderr)
        for m in moves[:10]:
            print(f"   {os.path.basename(m['from'])[:58]}  ->  {m['genre']}", file=sys.stderr)
        return

    done, errors = [], []
    for m in moves:
        if not os.path.exists(m["from"]):
            errors.append((m["from"], "source missing"))
            continue
        os.makedirs(os.path.dirname(m["to"]), exist_ok=True)
        target = unique_dest(m["to"])
        try:
            shutil.move(m["from"], target)
        except Exception as e:
            errors.append((m["from"], f"move failed: {e}"))
            continue
        err = set_genre(target, m["genre"])
        if err:
            errors.append((target, err))
        done.append({"from": m["from"], "to": target})

    os.makedirs(os.path.dirname(UNDO_LOG) or ".", exist_ok=True)
    json.dump({"root": root, "moves": done}, open(UNDO_LOG, "w"),
              indent=2, ensure_ascii=False)
    prune_empty(root)
    print(f"\nMoved and retagged {len(done)} files. Undo log: {UNDO_LOG}", file=sys.stderr)
    if errors:
        print(f"{len(errors)} errors:", file=sys.stderr)
        for p, e in errors[:10]:
            print(f"   {os.path.basename(p)[:55]}: {e}", file=sys.stderr)


def prune_empty(root):
    for dirpath, _, _ in os.walk(root, topdown=False):
        if os.path.abspath(dirpath) == os.path.abspath(root):
            continue
        entries = os.listdir(dirpath)
        if not entries or entries == [".DS_Store"]:
            try:
                if entries:
                    os.remove(os.path.join(dirpath, ".DS_Store"))
                os.rmdir(dirpath)
            except OSError:
                pass


def undo():
    if not os.path.exists(UNDO_LOG):
        sys.exit(f"No undo log at {UNDO_LOG}")
    data = json.load(open(UNDO_LOG))
    back = 0
    for m in reversed(data["moves"]):
        if not os.path.exists(m["to"]):
            continue
        os.makedirs(os.path.dirname(m["from"]), exist_ok=True)
        try:
            shutil.move(m["to"], unique_dest(m["from"]))
            back += 1
        except Exception as e:
            print(f"  ! {os.path.basename(m['to'])}: {e}", file=sys.stderr)
    prune_empty(data["root"])
    print(f"Reversed {back}/{len(data['moves'])} moves "
          f"(genre tags not reverted).", file=sys.stderr)


if __name__ == "__main__":
    main()
