#!/usr/bin/env python3
"""
PREVIEW-ONLY. Proposes standardized filenames and matching Title/Artist tags.

Operates on the canonical shapes this skill produces:
    Title (Remixer Type) - Original Artist.mp3
    Title - Artist.mp3

It cleans the *content* of those fields — promo spam, stray punctuation, damaged
acronyms — without changing the shape, and proposes TIT2/TPE1 values so tags and
filenames agree. It writes a preview and a plan. It does not touch the library.

## Why this no longer applies anything

A safety audit found this script mutating a live music library outside every
control the rest of the repo enforces. It could rename files and rewrite
Artist/Title with:

  * no folder lock, so it could run concurrently with another agent session
    mid-apply on the same folder;
  * no backup requirement of any kind;
  * no per-file hash, audio-payload or Serato-frame verification, so a
    corrupting write would be noticed by nobody;
  * a forced `v2_version=3`, silently rewriting every ID3v2.4 tag it touched
    down to v2.3 and destroying ID3v2.2 blocks the rest of the repo goes out
    of its way to preserve;
  * an undo log recording only renames, so `--undo` restored filenames and
    left the rewritten Artist/Title values permanently in place. That is not
    an undo.

Rather than bolt five safety systems onto a second, divergent apply path, the
mutation half is removed. This script now produces evidence; the approved
engine performs writes:

    python3 scripts/standardize_names.py <library> --out plan.json
    # review the preview, then run the Artist/Title plan through the engine:
    python3 scripts/build_artist_title_plan.py ...
    python3 scripts/apply_artist_title_plan.py --apply \\
        --backup-manifest ... --lock-folder ... --session ...

Usage:
    python3 standardize_names.py <library>                    # preview
    python3 standardize_names.py <library> --out plan.json    # preview + machine-readable plan
"""
import os, re, sys, json, argparse, collections

try:
    from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4
except ImportError:
    sys.exit("mutagen is required:  python3 -m pip install --user mutagen")

AUDIO = (".mp3", ".m4a", ".aif", ".aiff", ".wav", ".flac")

# Preview-only: builds a plan for human review. Never mutates library media.
SAFETY_CLASSIFICATION = "preview-only"

RETIRED_MUTATION_MESSAGE = (
    "standardize_names.py no longer renames or retags anything.\n"
    "\n"
    "It mutated the library with no lock, no backup, no verification, a forced\n"
    "ID3v2.3 downgrade, and an undo that never restored metadata. It is now a\n"
    "preview/plan builder only.\n"
    "\n"
    "To actually apply Artist/Title changes, go through the approved engine:\n"
    "  1. python3 scripts/standardize_names.py <library> --out plan.json   # review this\n"
    "  2. python3 scripts/build_artist_title_plan.py ...\n"
    "  3. python3 scripts/backup_manifest.py verify --source-root ... --backup ... --out manifest.json\n"
    "  4. python3 scripts/folder_lock.py acquire --folder ... --session ...\n"
    "  5. python3 scripts/apply_artist_title_plan.py --apply --backup-manifest manifest.json \\\n"
    "         --lock-folder ... --session ...\n"
)

# --- promo / distribution spam that is not part of any title -----------------
PROMO = [
    r"\bbuy\s*=\s*free\s*d\.?\s?l\.?\b", r"\bfree\s*d\.?\s?l\.?\b",
    r"\bfree\s*download\b", r"\bout\s*now\s*!*", r"\[?\s*premiere\s*\]?",
    r"\btl\s*premiere\b", r"\bexclusive\b", r"\bclick\s*buy\b",
    r"\bdl\s*in\s*desc\w*\b", r"\bhit\s*-?\s*buy\s*-?\s*link\]?",
    r"@\w+", r"#\w+", r"\bstrictly\s+the\s+best\s+vol\.?\s*\d+\b",
]
PROMO_RX = [re.compile(p, re.I) for p in PROMO]

# --- damaged acronyms and catalogue numbers ---------------------------------
# smart_case turned BT005 into Bt005, T.NO into T.no, OJC into Ojc. Undo that.
CATALOG_RX = re.compile(r"\b([A-Z][a-z]{0,3})(\d{2,})\b")          # Bt005, Nr023
# P.i.m.p -> P.I.M.P.  The trailing \.? must be INSIDE the match or the rule is
# not idempotent: a second pass turns "P.I.M.P." into "P.I.M.P..".
DOTTED_RX = re.compile(r"\b([A-Za-z](?:\.[A-Za-z]){1,4})\.?(?!\w)")
SHORT_DOT_RX = re.compile(r"\b([A-Z])\.([a-z]{2,3})\b")             # T.no

# --- structural junk ---------------------------------------------------------
LEAD_TRACKNO_RX = re.compile(r"^\s*\d{1,2}\s*[.\-]\s+(?=\S)")   # "06. Title", "01 - Title"
LEAD_BARE_NUM_RX = re.compile(r"^\s*0\d\s+(?=[A-Za-z])")         # "01 Lai Q" (leading zero only)
# -Whenever You're Around- : the dashes must HUG the text. Without that guard
# "Wine, Stop - May 2013 - Jam 2 Records" reads as wrapped and loses both
# separators.
WRAPPED_RX = re.compile(r"(?<!\S)-(\S[^-]{0,58}\S)-(?!\S)")
DUP_TAIL_RX = re.compile(r"\s*\((\d)\)\s*$")
# Only a stray extension, never one inside a name — "] Kiddy.Wav" is an artist.
EXT_IN_NAME_RX = re.compile(r"(?:\s+\.|\s+)(mp3|m4a|wav|aiff?|flac)\b(?=\s|$)", re.I)
ORPHAN_RX = re.compile(r"(?:\s[\(\[\)\]]+)+\s*$|^\s*[\)\]]+\s*")


def fix_acronyms(s):
    s = CATALOG_RX.sub(lambda m: m.group(1).upper() + m.group(2), s)
    s = SHORT_DOT_RX.sub(lambda m: f"{m.group(1)}.{m.group(2).upper()}", s)
    s = DOTTED_RX.sub(lambda m: m.group(1).upper() + ".", s)
    return re.sub(r"\.{2,}", ".", s)     # belt and braces against double dots


def clean_field(s, is_title):
    if not s:
        return s
    out = s
    out = EXT_IN_NAME_RX.sub("", out)
    for rx in PROMO_RX:
        out = rx.sub(" ", out)
    out = WRAPPED_RX.sub(r"\1", out)          # -Text- -> Text
    if is_title:
        out = LEAD_TRACKNO_RX.sub("", out)
        out = LEAD_BARE_NUM_RX.sub("", out)
    out = fix_acronyms(out)
    out = out.replace("``", "'").replace("`", "'").replace("''", "'")
    out = re.sub(r"\s*-{2,}\s*", " - ", out)   # "--" -> " - "
    out = re.sub(r"(\s-\s)+", " - ", out)      # " - - " -> " - "
    out = re.sub(r"\(\s*\)", "", out)          # empty parens
    out = ORPHAN_RX.sub("", out)               # brackets promo removal left behind
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,!?])", r"\1", out)
    # Only collapse " ." when it really is punctuation. "Anderson .Paak" and
    # "DJ co.kr" style their dots on purpose.
    out = re.sub(r"\s+\.(?=\s|$)", ".", out)
    out = re.sub(r"\s-\s+(?=\()", " ", out)    # "Your Man - (X Bootleg)" -> "Your Man (X Bootleg)"
    out = balance_brackets(out)
    out = out.strip(" -–,&.")
    return out


def balance_brackets(s):
    """Drop unmatched brackets left behind by promo removal, so we never emit
    "I Want You (lhap Rework, Numz" with an opener that never closes."""
    for opener, closer in (("(", ")"), ("[", "]")):
        while s.count(opener) > s.count(closer):
            i = s.rfind(opener)
            s = (s[:i] + s[i + 1:]).strip()
        while s.count(closer) > s.count(opener):
            i = s.find(closer)
            s = (s[:i] + s[i + 1:]).strip()
    return re.sub(r"\s{2,}", " ", s)


def split_name(stem):
    """-> (title_part, artist_part, dup_suffix). Splits on the LAST ' - '."""
    dup = ""
    m = DUP_TAIL_RX.search(stem)
    if m:
        dup = f" ({m.group(1)})"
        stem = DUP_TAIL_RX.sub("", stem)
    if " - " in stem:
        title, artist = stem.rsplit(" - ", 1)
    else:
        title, artist = stem, ""
    return title.strip(), artist.strip(), dup


ILLEGAL = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def rebuild(stem):
    title, artist, dup = split_name(stem)
    t2 = clean_field(title, is_title=True)
    a2 = clean_field(artist, is_title=False)
    # If cleaning ate the whole title — the title WAS the promo text, as in
    # "Free DL (Loucas Groove Edit)" — keep the original. A bare "(X Edit)" is
    # worse than leaving it alone for a human to name.
    if not t2 or t2.lstrip().startswith("("):
        t2 = title.strip() or stem
    new = f"{t2} - {a2}" if a2 else t2
    new = ILLEGAL.sub("-", new)
    new = re.sub(r"\s{2,}", " ", new).strip(" .")
    return new + dup, t2, a2


def read_tag_pair(path):
    """-> (title, artist), both always strings (never None) so downstream
    boolean logic never has to deal with tri-state truthiness. Supports the
    three formats this library actually holds; anything else / anything
    unreadable comes back as ("", "") -- indistinguishable from "no tags",
    which is the correct, conservative read for a field we can't inspect."""
    suffix = os.path.splitext(path)[1].lower()

    def first_text(values):
        if not values:
            return ""
        value = values[0]
        if value is None:
            return ""
        return str(value)

    try:
        if suffix == ".mp3":
            t = ID3(path)
            g = lambda k: (str(t.getall(k)[0]) if t.getall(k) else "")
            return g("TIT2"), g("TPE1")
        if suffix == ".flac":
            t = FLAC(path)
            return first_text(t.get("title", [])), first_text(t.get("artist", []))
        if suffix == ".m4a":
            t = MP4(path)
            return first_text(t.get("\xa9nam", [])), first_text(t.get("\xa9ART", []))
    except Exception:
        return "", ""
    return "", ""


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("root", nargs="?", default=os.path.expanduser("~/Downloads/SoundCloud"))
    ap.add_argument("--apply", action="store_true",
                    help="RETIRED -- refuses to run; route through the approved engine instead")
    ap.add_argument("--undo", action="store_true",
                    help="RETIRED -- refuses to run; this script no longer mutates anything")
    ap.add_argument("--preview", default="outputs/_rename_preview.md")
    ap.add_argument("--out", help="also write the proposals as JSON for review/plan building")
    args = ap.parse_args()

    # Fail loudly rather than silently previewing: a caller passing --apply
    # believes it is about to change files, and must be told it is not.
    if args.apply or args.undo:
        raise SystemExit(RETIRED_MUTATION_MESSAGE)

    root = os.path.abspath(os.path.expanduser(args.root))
    changes = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for f in sorted(filenames):
            if not f.lower().endswith(AUDIO) or f.startswith("."):
                continue
            stem, ext = os.path.splitext(f)
            new_stem, new_title, new_artist = rebuild(stem)
            src = os.path.join(dirpath, f)
            cur_title, cur_artist = read_tag_pair(src)
            name_changed = new_stem != stem
            # keep the remix credit in the title tag; only clean it. Same guard
            # as rebuild(): never reduce a title to a bare "(X Edit)".
            tag_title = clean_field(cur_title, is_title=True) if cur_title else ""
            if not tag_title or tag_title.lstrip().startswith("("):
                tag_title = cur_title
            tag_artist = clean_field(cur_artist, is_title=False) if cur_artist else ""
            if not tag_artist:
                tag_artist = cur_artist
            tag_changed = bool((tag_title and tag_title != cur_title) or
                                (tag_artist and tag_artist != cur_artist))
            if name_changed or tag_changed:
                changes.append({
                    "path": src, "old_name": f, "new_name": new_stem + ext,
                    "old_title": cur_title, "new_title": tag_title or cur_title,
                    "old_artist": cur_artist, "new_artist": tag_artist or cur_artist,
                    "name_changed": name_changed, "tag_changed": tag_changed,
                })

    write_preview(changes, root, args.preview)
    print(f"{len(changes)} files need cleanup "
          f"({sum(c['name_changed'] for c in changes)} filename, "
          f"{sum(c['tag_changed'] for c in changes)} tag)", file=sys.stderr)
    print(f"Preview: {args.preview}", file=sys.stderr)

    if args.out:
        plan = {
            "mode": "preview-only",
            "generator": "standardize_names.py",
            "root": root,
            "rules": {"approval_required": True, "mutations_performed": 0},
            "case_only_renames": [
                c["path"] for c in changes
                if c["name_changed"] and _is_case_only_rename(c)
            ],
            "items": changes,
        }
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(plan, handle, indent=2, ensure_ascii=False)
        print(f"Plan: {args.out}", file=sys.stderr)

    print("\nPREVIEW ONLY — nothing was renamed or retagged.", file=sys.stderr)
    print("Apply Artist/Title changes via apply_artist_title_plan.py "
          "(lock + verified backup manifest + engine).", file=sys.stderr)


def _is_case_only_rename(change):
    """Flag renames that differ only by letter case.

    macOS APFS is case-insensitive but case-PRESERVING: "Bt005.mp3" and
    "BT005.mp3" are the same path, so a case-only rename needs a two-step
    move through a temp name to actually land, and a naive `if exists(dest)`
    collision check would wrongly append " (2)" to a file colliding only with
    itself. The approved engine handles this; the preview marks them so a
    reviewer can see which ones are in that category.
    """
    dest = os.path.join(os.path.dirname(change["path"]), change["new_name"])
    return (os.path.normcase(os.path.abspath(dest)) ==
            os.path.normcase(os.path.abspath(change["path"])))


def write_preview(changes, root, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    by_dir = collections.defaultdict(list)
    for c in changes:
        by_dir[os.path.relpath(os.path.dirname(c["path"]), root)].append(c)
    L = [f"# Filename & tag standardization — preview\n\n",
         f"**{len(changes)} files** would change under `{root}`.\n\n",
         "Nothing has been renamed yet.\n\n---\n"]
    for d in sorted(by_dir):
        L.append(f"\n### {d}\n\n")
        for c in by_dir[d]:
            if c["name_changed"]:
                L.append(f"- `{c['old_name']}`\n  → `{c['new_name']}`\n")
            else:
                L.append(f"- `{c['old_name']}` *(tags only)*\n")
            if c["tag_changed"]:
                if c["old_title"] != c["new_title"]:
                    L.append(f"    - title: `{c['old_title']}` → `{c['new_title']}`\n")
                if c["old_artist"] != c["new_artist"]:
                    L.append(f"    - artist: `{c['old_artist']}` → `{c['new_artist']}`\n")
    open(path, "w").write("".join(L))


if __name__ == "__main__":
    main()
