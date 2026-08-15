#!/usr/bin/env python3
"""
Standardize filenames and the title/artist tags to match, across a sorted library.

Operates on the canonical shapes this skill produces:
    Title (Remixer Type) - Original Artist.mp3
    Title - Artist.mp3

It cleans the *content* of those fields — promo spam, stray punctuation, damaged
acronyms — without changing the shape, and rewrites TIT2/TPE1 so tags and
filenames stay in agreement. Dry-run by default.

Usage:
    python3 standardize_names.py <library>              # preview to outputs/_rename_preview.md
    python3 standardize_names.py <library> --apply
    python3 standardize_names.py <library> --undo
"""
import os, re, sys, json, shutil, argparse, collections

try:
    from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1
except ImportError:
    sys.exit("mutagen is required:  python3 -m pip install --user mutagen")

UNDO_LOG = "outputs/undo-rename.json"
AUDIO = (".mp3", ".m4a", ".aif", ".aiff", ".wav", ".flac")

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
    try:
        t = ID3(path)
    except Exception:
        return None, None, None
    g = lambda k: (str(t.getall(k)[0]) if t.getall(k) else "")
    return t, g("TIT2"), g("TPE1")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=os.path.expanduser("~/Downloads/SoundCloud"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--preview", default="outputs/_rename_preview.md")
    args = ap.parse_args()

    if args.undo:
        return undo()

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
            _, cur_title, cur_artist = read_tag_pair(src)
            name_changed = new_stem != stem
            # keep the remix credit in the title tag; only clean it. Same guard
            # as rebuild(): never reduce a title to a bare "(X Edit)".
            tag_title = clean_field(cur_title, is_title=True) if cur_title else ""
            if not tag_title or tag_title.lstrip().startswith("("):
                tag_title = cur_title
            tag_artist = clean_field(cur_artist, is_title=False) if cur_artist else ""
            if not tag_artist:
                tag_artist = cur_artist
            tag_changed = (tag_title and tag_title != cur_title) or \
                          (tag_artist and tag_artist != cur_artist)
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

    if not args.apply:
        print("\nDRY RUN — re-run with --apply to rename and retag.", file=sys.stderr)
        return

    done, errors = [], []
    for c in changes:
        target = c["path"]
        if c["name_changed"]:
            dest = os.path.join(os.path.dirname(c["path"]), c["new_name"])
            # macOS APFS is case-insensitive: "Bt005.mp3" and "BT005.mp3" are the
            # SAME path. os.path.exists(dest) is therefore True for a case-only
            # rename, and a naive collision check appends " (2)" to a file that
            # was never colliding. Detect same-file first, and go via a temp name
            # so the rename actually lands.
            case_only = (os.path.normcase(os.path.abspath(dest)) ==
                         os.path.normcase(os.path.abspath(c["path"])))
            try:
                if case_only:
                    if dest != c["path"]:
                        tmp = c["path"] + ".__case_tmp"
                        shutil.move(c["path"], tmp)
                        shutil.move(tmp, dest)
                else:
                    if os.path.exists(dest):
                        stem, ext = os.path.splitext(dest)
                        n = 2
                        while os.path.exists(f"{stem} ({n}){ext}"):
                            n += 1
                        dest = f"{stem} ({n}){ext}"
                    shutil.move(c["path"], dest)
                done.append({"from": c["path"], "to": dest})
                target = dest
            except Exception as e:
                errors.append((c["path"], f"rename failed: {e}"))
                continue
        if c["tag_changed"]:
            try:
                t = ID3(target)
                if c["new_title"]:
                    t.setall("TIT2", [TIT2(encoding=3, text=c["new_title"])])
                if c["new_artist"]:
                    t.setall("TPE1", [TPE1(encoding=3, text=c["new_artist"])])
                t.save(target, v2_version=3)
            except Exception as e:
                errors.append((target, f"tag failed: {e}"))

    os.makedirs(os.path.dirname(UNDO_LOG) or ".", exist_ok=True)
    json.dump({"root": root, "moves": done}, open(UNDO_LOG, "w"),
              indent=2, ensure_ascii=False)
    print(f"\nRenamed {len(done)} files, retagged {sum(c['tag_changed'] for c in changes)}.",
          file=sys.stderr)
    print(f"Undo log: {UNDO_LOG}", file=sys.stderr)
    if errors:
        print(f"{len(errors)} errors:", file=sys.stderr)
        for p, e in errors[:10]:
            print(f"   {os.path.basename(p)[:55]}: {e}", file=sys.stderr)


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


def undo():
    if not os.path.exists(UNDO_LOG):
        sys.exit(f"No undo log at {UNDO_LOG}")
    data = json.load(open(UNDO_LOG))
    back = 0
    for m in reversed(data["moves"]):
        if os.path.exists(m["to"]):
            try:
                shutil.move(m["to"], m["from"])
                back += 1
            except Exception as e:
                print(f"  ! {os.path.basename(m['to'])}: {e}", file=sys.stderr)
    print(f"Reversed {back}/{len(data['moves'])} renames (tags not reverted).", file=sys.stderr)


if __name__ == "__main__":
    main()
