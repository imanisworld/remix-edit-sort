#!/usr/bin/env python3
"""
Step 4 — Apply the plan: move each file into place and write its ID3 tags.

The only script here that touches disk. Defaults to --dry-run; you have to pass
--apply for anything to actually move. Writes an undo log so a bad run can be
reversed with --undo.

Usage:
    python3 apply_plan.py --dry-run            # default: report only
    python3 apply_plan.py --apply              # move + tag
    python3 apply_plan.py --apply --copy       # leave originals in place
    python3 apply_plan.py --apply --tags-only  # tag where they sit, don't move
    python3 apply_plan.py --undo               # reverse the last apply

DEPRECATED: this is the legacy Genre/Remixer apply engine for flat
SoundCloud/DJ-pool downloads. Library-folder cleanup (the actual work this
repo has been doing under handoff/) uses apply_version_genre_plan.py,
apply_grouping_plan.py, and apply_artist_title_plan.py instead -- they have
stronger per-item audio-payload/Serato-frame verification and mandatory
locking. This script still works for its original narrow use case but
requires --legacy-confirm to run --apply/--undo, so it can't be reached by
accident.
"""

# Hard-disabled legacy mutator. Kept as history; cannot run.
SAFETY_CLASSIFICATION = "disabled-legacy"
import os, sys, json, shutil, argparse, collections

import lock_guard

try:
    from mutagen.id3 import (ID3, ID3NoHeaderError, TIT2, TPE1, TPE2, TPE4,
                             TALB, TCOM, TCON, TBPM, TDRC, COMM)
except ImportError:
    sys.exit("mutagen is required:  python3 -m pip install --user mutagen")

UNDO_LOG = "outputs/undo.json"


def write_tags(path, tags):
    """Fields chosen because Rekordbox, Serato, Apple Music and Plex all read
    them. TPE4 is the actual ID3 'remixer' frame — Rekordbox shows it in its
    own Remixer column, which is what makes the library sortable by remixer."""
    try:
        t = ID3(path)
    except ID3NoHeaderError:
        t = ID3()
    except Exception as e:
        return f"unreadable: {e}"

    def setf(frame, key, value):
        if value:
            t.setall(key, [frame(encoding=3, text=str(value))])

    setf(TIT2, "TIT2", tags.get("title"))
    setf(TPE1, "TPE1", tags.get("artist"))
    setf(TPE2, "TPE2", tags.get("albumartist"))
    setf(TALB, "TALB", tags.get("album"))
    setf(TPE4, "TPE4", tags.get("remixer"))
    setf(TCOM, "TCOM", tags.get("composer"))
    setf(TCON, "TCON", tags.get("genre"))
    setf(TDRC, "TDRC", tags.get("year"))
    if tags.get("bpm"):
        t.setall("TBPM", [TBPM(encoding=3, text=str(int(round(float(tags["bpm"])))))])

    # Keep the original SoundCloud URL comment; add the hashtags as a second
    # comment frame so crate filters can match on them without losing the source.
    if tags.get("comment_tags"):
        keep = [c for c in t.getall("COMM") if getattr(c, "desc", "") != "djtags"]
        t.setall("COMM", keep + [COMM(encoding=3, lang="eng", desc="djtags",
                                      text=tags["comment_tags"])])
    try:
        t.save(path, v2_version=3)     # v2.3 — the version Rekordbox is happiest with
        return None
    except Exception as e:
        return f"save failed: {e}"


def unique_dest(dest):
    """Never overwrite. If something's already there, add a numeric suffix."""
    if not os.path.exists(dest):
        return dest
    stem, ext = os.path.splitext(dest)
    n = 2
    while os.path.exists(f"{stem} ({n}){ext}"):
        n += 1
    return f"{stem} ({n}){ext}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="outputs/plan.json")
    ap.add_argument("--apply", action="store_true", help="actually move and tag")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--copy", action="store_true", help="copy instead of move")
    ap.add_argument("--tags-only", action="store_true", help="write tags, don't move")
    ap.add_argument("--skip-review", action="store_true",
                    help="leave everything flagged needs_review where it is")
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--legacy-confirm", action="store_true",
                    help="required alongside --apply/--undo -- see DEPRECATED note above")
    ap.add_argument("--lock-folder", help="Top-level library folder locked via folder_lock.py (required with --apply/--undo)")
    ap.add_argument("--session", help="Session id that holds the lock (required with --apply/--undo)")
    args = ap.parse_args()

    if args.apply or args.undo:
        if not args.legacy_confirm:
            raise SystemExit(
                "apply_plan.py is deprecated for library-folder cleanup -- use "
                "apply_version_genre_plan.py / apply_grouping_plan.py / "
                "apply_artist_title_plan.py instead. If this narrow original use case "
                "(flat SoundCloud/DJ-pool downloads) is genuinely what you need, pass "
                "--legacy-confirm to proceed."
            )
        if not args.lock_folder or not args.session:
            raise SystemExit("--lock-folder and --session are required with --apply/--undo")
        lock_guard.require_lock_or_exit(args.lock_folder, args.session)

    if args.undo:
        return undo()

    plan = json.load(open(args.plan))
    items = plan["items"]
    live = args.apply
    heartbeat = lock_guard.LockHeartbeat(args.lock_folder, args.session) if live else None

    if args.skip_review:
        items = [i for i in items if not i["needs_review"]]

    moved, tagged, errors, skipped = [], 0, [], 0
    for i in items:
        if heartbeat:
            heartbeat.beat()
        src, dest = i["path"], i["dest"]
        if not os.path.exists(src):
            errors.append((src, "source missing"))
            continue

        target = src if args.tags_only else dest
        if not args.tags_only and os.path.abspath(src) == os.path.abspath(dest):
            skipped += 1
            target = src

        if live:
            if target != src:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                target = unique_dest(target)
                try:
                    if args.copy:
                        shutil.copy2(src, target)
                    else:
                        shutil.move(src, target)
                    moved.append({"from": src, "to": target, "copied": args.copy})
                except Exception as e:
                    errors.append((src, f"move failed: {e}"))
                    continue
            err = write_tags(target, i["tags"])
            if err:
                errors.append((target, err))
            else:
                tagged += 1
        else:
            moved.append({"from": src, "to": target, "copied": args.copy})

    if live:
        # Append, never overwrite. A two-pass run (--skip-review, then the rest)
        # would otherwise lose the first pass's undo history entirely.
        os.makedirs(os.path.dirname(UNDO_LOG) or ".", exist_ok=True)
        history = {"runs": []}
        if os.path.exists(UNDO_LOG):
            prev = json.load(open(UNDO_LOG))
            if isinstance(prev, dict) and "runs" in prev:
                history = prev
            elif isinstance(prev, dict):
                history["runs"] = [prev]
            elif isinstance(prev, list):
                history["runs"] = [{"root": plan["dest_root"], "moves": prev}]
        history["runs"].append({"root": plan["dest_root"], "moves": moved})
        json.dump(history, open(UNDO_LOG, "w"), indent=2, ensure_ascii=False)
        print(f"Undo history now has {len(history['runs'])} run(s)", file=sys.stderr)
        prune_empty_dirs(plan["root"])

    verb = "Moved" if live else "WOULD move"
    b = collections.Counter(i["bucket"] for i in items)
    print(f"\n{verb} {len(moved)} files "
          f"({b['remix'] + b['remix_unknown']} remixes, {b['original']} originals, "
          f"{b['duplicate']} duplicates, {b['unsorted']} unsorted)", file=sys.stderr)
    if live:
        print(f"Tagged {tagged} files", file=sys.stderr)
        print(f"Undo log: {UNDO_LOG}  (reverse with --undo)", file=sys.stderr)
    else:
        print("This was a DRY RUN. Re-run with --apply to make it real.", file=sys.stderr)
    if skipped:
        print(f"{skipped} already in place", file=sys.stderr)
    if errors:
        print(f"\n{len(errors)} errors:", file=sys.stderr)
        for p, e in errors[:15]:
            print(f"  {os.path.basename(p)[:60]}: {e}", file=sys.stderr)


def prune_empty_dirs(root):
    """Remove folders the move emptied out. Never removes the root."""
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        if os.path.abspath(dirpath) == os.path.abspath(root):
            continue
        if not os.listdir(dirpath):
            try:
                os.rmdir(dirpath)
            except OSError:
                pass
        elif os.listdir(dirpath) == [".DS_Store"]:
            try:
                os.remove(os.path.join(dirpath, ".DS_Store"))
                os.rmdir(dirpath)
            except OSError:
                pass


def undo():
    if not os.path.exists(UNDO_LOG):
        sys.exit(f"No undo log at {UNDO_LOG}")
    raw = json.load(open(UNDO_LOG))
    if isinstance(raw, dict) and "runs" in raw:
        runs = raw["runs"]
        if not runs:
            sys.exit("Undo history is empty.")
        if os.environ.get("UNDO_ALL"):
            log = [m for r in runs for m in r["moves"]]
            root = runs[-1].get("root")
            remaining = []
        else:
            last = runs[-1]
            log, root = last["moves"], last.get("root")
            remaining = runs[:-1]
            print(f"Reversing the most recent run only "
                  f"({len(runs) - 1} earlier run(s) kept; UNDO_ALL=1 to reverse everything)",
                  file=sys.stderr)
    else:
        log = raw["moves"] if isinstance(raw, dict) else raw
        root = raw.get("root") if isinstance(raw, dict) else None
        remaining = None
    back = 0
    for entry in reversed(log):
        src, dst = entry["to"], entry["from"]
        if not os.path.exists(src):
            continue
        if entry.get("copied"):
            os.remove(src)
            back += 1
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.move(src, unique_dest(dst))
            back += 1
        except Exception as e:
            print(f"  ! {os.path.basename(src)}: {e}", file=sys.stderr)
    if remaining is not None:
        json.dump({"runs": remaining}, open(UNDO_LOG, "w"), indent=2, ensure_ascii=False)
    if root and os.path.isdir(root):
        prune_empty_dirs(root)
    print(f"Reversed {back}/{len(log)} moves. "
          f"Note: ID3 tags are NOT reverted — re-run the scan to see current state.",
          file=sys.stderr)


if __name__ == "__main__":
    main()
