#!/usr/bin/env python3
"""
Step 1 — Scan a folder of mp3s, read ID3 tags, and work out for each track:
whether it's a remix/edit, who remixed it, whose record it originally was,
and what genre it is.

Zero installs beyond mutagen (already present). No ffmpeg, no Homebrew.

Usage:
    python3 scan_library.py <folder> [--out outputs/tracks.json]
"""

# Read-only: inspects the library and writes reports/derived data only.
SAFETY_CLASSIFICATION = "read-only"
import os, re, sys, json, argparse, unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from skill_paths import load_reference

try:
    from mutagen.id3 import ID3, ID3NoHeaderError
    from mutagen.mp3 import MP3
except ImportError:
    sys.exit("mutagen is required:  python3 -m pip install --user mutagen")

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = json.load(open(os.path.join(HERE, "..", "references", "genre-rules.json")))

AUDIO_EXT = (".mp3", ".m4a", ".wav", ".aiff", ".aif", ".flac")
# Folders this skill creates itself, plus junk. Never rescan our own output.
SKIP_DIRS = {"Remixes & Edits", "Originals", "_Unsorted", "outputs", ".git"}

TYPES = RULES["remix_types"]
FOLDER_GENRES = {folder: genre for genre, folder in RULES.get("folder_names", {}).items()}
TYPE_RX = "|".join(sorted((re.escape(t) for t in TYPES), key=len, reverse=True))

# "Title (Someone Special Remix)" — take the LAST parenthetical so
# "Love Hangover (Frankie Knuckles Remix)(K&F's Midnight Re- Edit)" credits K&F.
PAREN_RX = re.compile(r"\(([^()]{1,70}?)\s+(" + TYPE_RX + r")\)", re.I)
# "Ann Nesby - Can I Get A Witness - Mousse T's Garage Mix".
# Capped at four words, or it swallows titles: "Amadeezy - Where They Hang Boss
# Town Playaz Remix" would credit the whole phrase instead of Boss Town Playaz.
DASH_RX = re.compile(r"\s[-–]\s((?:[^-–\s]+\s+){0,3}[^-–\s]+)\s+(" + TYPE_RX + r")\s*$", re.I)
# Same thing with no parens and no dash. Three words max, same reason.
BARE_RX = re.compile(r"\s((?:[A-Za-z0-9'&.]+\s+){0,2}[A-Za-z0-9'&.]+)\s+(" + TYPE_RX + r")\s*$", re.I)

DUP_RX = re.compile(r"\s*\((\d+)\)\s*$")
SC_URL_RX = re.compile(r"soundcloud\.com/([^/\s]+)", re.I)
NOISE_RX = re.compile(
    r"^\s*(free\s*dl|free\s*download|out\s*now|premiere|tl\s*premiere|"
    r"\[?premiere\]?|buy\s*=\s*free|click\s*buy|dl\s*in\s*desc)\b[\s.:\-]*", re.I)

# music-library-sort's vocabulary -> ours
LABEL_REMAP = {
    "hip-hop/rap": "Hip-Hop", "ghetto house": "Ghetto House",
    "ghetto tech": "Ghettotech", "ukg": "UK Garage",
    "baile funk": "Baile", "vogue": "Ballroom Vogue",
    "jersey/atl club": None,  # compound: do not select either club style
    "house": "House", "techno": "Techno",
    "blues": None, "electronic": None,
    "r&b/soul": "R&B", "funk/soul": "Funk & Soul",
    # Named genres without an approved correspondence stay unclassified.
    "club": None, "soul": None,
}
ARTIST_GENRE = {}

# Input aliases are for interpreting existing tags, never a license to
# overwrite them using a producer name, folder, or title keyword.
_VERSION_RULES_PATH = os.path.join(HERE, "..", "references", "version-genre-rules.json")
with open(_VERSION_RULES_PATH, encoding="utf-8") as _version_file:
    _VERSION_RULES = json.load(_version_file)

_UNDECIDED_EXISTING_TAGS = {"afrobeat", "funk", "soul", "boogie", "dub"}


def canonical_existing_genre(value):
    raw = (value or "").strip()
    if not raw or raw.casefold() in {"other", "unknown"}:
        return None
    if raw.casefold() in _UNDECIDED_EXISTING_TAGS:
        return None
    mapped = _VERSION_RULES.get("tag_aliases", {}).get(raw.casefold(), raw)
    if isinstance(mapped, str) and mapped in RULES["canonical_genres"]:
        return mapped
    return None


def canonical_artist_genre(value):
    """Interpret legacy artist style names without inventing a Genre."""
    if not isinstance(value, str):
        return None
    raw = value.strip()
    mapped = LABEL_REMAP.get(raw.casefold(), raw)
    return mapped if mapped in RULES["canonical_genres"] else None


def norm(s):
    """Casefold + strip accents/punctuation for comparing names."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def load_artist_map():
    """Reuse the ~125-artist genre map music-library-sort already curated,
    plus any local additions. Optional — the skill works without it."""
    for skill, fname in (("music-library-sort", "genre-overrides.json"),):
        raw = load_reference(skill, fname, default={}) or {}
        for artist, label in raw.items():
            if not isinstance(label, str):
                continue
            g = canonical_artist_genre(label)
            if g:
                ARTIST_GENRE[norm(artist)] = g
    local = os.path.join(HERE, "..", "references", "artist-genre-extra.json")
    if os.path.exists(local):
        for artist, label in json.load(open(local)).items():
            g = canonical_artist_genre(label)
            if g:
                ARTIST_GENRE[norm(artist)] = g


def read_tags(path):
    out = {"title": "", "artist": "", "album": "", "albumartist": "", "genre": "",
           "bpm": None, "key": "", "year": "", "comment": "", "duration": 0, "bitrate": 0}
    try:
        audio = MP3(path)
        out["duration"] = round(audio.info.length, 1)
        out["bitrate"] = audio.info.bitrate // 1000
    except Exception:
        pass
    try:
        t = ID3(path)
    except Exception:
        return out
    g = lambda k: (str(t.getall(k)[0]) if t.getall(k) else "").strip()
    out["title"], out["artist"] = g("TIT2"), g("TPE1")
    out["album"], out["albumartist"] = g("TALB"), g("TPE2")
    out["genre"], out["key"], out["year"] = g("TCON"), g("TKEY"), g("TDRC")
    bpm = g("TBPM")
    if bpm:
        try:
            v = float(bpm)
            out["bpm"] = round(v, 1) if v > 0 else None
        except ValueError:
            pass
    comms = [str(c) for c in t.getall("COMM")]
    out["comment"] = comms[0] if comms else ""
    return out


def _strip_one_suffix(name):
    n = name.strip()
    low = " " + norm(n) + " "
    for suf in RULES["genre_suffixes"]:          # already longest-first
        if low.endswith(" " + norm(suf) + " "):
            words = n.split()
            keep = words[:len(words) - len(suf.split())]
            genre = next((r["genre"] for r in RULES["keyword_rules"]
                          if norm(suf) in [norm(m) for m in r["match"]]), None)
            return " ".join(keep).strip(" -,&"), genre
    return n, None


def strip_genre_suffix(name):
    """'ZUES Cumbia' -> ('ZUES', 'Cumbia'). Loops, so "NEUTRON'S VOGUE CLUB"
    sheds both 'club' and 'vogue' and keeps the first (most specific) genre."""
    n, genre = name.strip(), None
    for _ in range(4):
        stripped, g = _strip_one_suffix(n)
        if stripped == n:
            break
        n = stripped
        genre = genre or g
        if not n:
            break
    return n, genre


def canon_remixer(name):
    """Fold spelling/casing variants of the same producer together."""
    n = re.sub(r"'s$", "", name.strip(), flags=re.I).strip(" -,&")
    key = norm(n)
    for alias_key, folder in RULES["remixer_aliases"].items():
        if key == norm(alias_key):
            return folder
    return n


def find_remix(title, stem):
    """Return (remixer_raw, remix_type, matched_text).

    matched_text is whichever string the credit was found in — the title has to
    be cleaned from that same string, or the strip silently misses.
    """
    for rx in (PAREN_RX, DASH_RX, BARE_RX):
        for text in (title, stem):
            if not text:
                continue
            hits = list(rx.finditer(text))
            if not hits:
                continue
            m = hits[-1]      # last parenthetical wins: the outermost re-edit
            # "Bass Check" / "Skinny Pimp" must not read as a Mix
            if rx is BARE_RX and m.group(2).lower() in ("mix", "dub", "version", "special"):
                continue
            return m.group(1).strip(), m.group(2).title(), text
    return None, None, None


def clean_title(text, remixer_raw, remix_type):
    """Strip the remix credit and download-spam off the title."""
    t = NOISE_RX.sub("", text or "").strip()
    t = DUP_RX.sub("", t).strip()
    if remixer_raw:
        t = PAREN_RX.sub("", t)
        t = DASH_RX.sub("", t)
        t = BARE_RX.sub("", t)
    return re.sub(r"\s{2,}", " ", t).strip(" -–,&.")


def split_artists(field):
    parts = re.split(r"\s*,\s*|\s+(?:feat\.?|ft\.?|featuring|with)\s+|\s+[x×&]\s+", field or "")
    return [NOISE_RX.sub("", p).strip() for p in parts if p and p.strip()]


def classify_genre(track):
    """Return (genre, source) without overriding an existing meaningful tag.

    Existing accepted Genre outranks producer/folder/title heuristics.
    Unrecognized or disputed legacy tags are held for review. A conflicting
    explicit filename genre never authorizes an automatic genre switch.
    """
    original = (track.get("existing_genre") or "").strip()
    if original and original.casefold() != "other":
        canonical = canonical_existing_genre(original)
        if canonical is None:
            return None, "existing_tag_requires_review"
        filename = (track.get("filename") or "").casefold()
        for hint in _VERSION_RULES.get("filename_keywords", []):
            if hint["contains"].casefold() in filename and hint["genre"] != canonical:
                return None, "existing_tag_filename_conflict"
        return canonical, "existing_tag"
    if track["remixer_genre"]:
        return track["remixer_genre"], "remixer_suffix"

    # A known producer's edit takes their genre, not the source record's:
    # Masters At Work editing Loleatta Holloway is house, not disco.
    for name in filter(None, [track.get("remixer"), track.get("remixer_raw")]):
        hit = ARTIST_GENRE.get(norm(name))
        if hit:
            return hit, "remixer_map"

    for a in track["original_artists"]:
        hit = ARTIST_GENRE.get(norm(a))
        if hit:
            return hit, "artist_map"

    folder = track.get("source_folder") or ""
    # A same-spelling physical folder is not sufficient Genre evidence.
    # Only an explicitly approved folder_names entry may supply a folder
    # hint; otherwise leave classification to actual tag/filename evidence.
    folder_genre = FOLDER_GENRES.get(folder)
    if folder_genre and folder_genre in RULES["canonical_genres"]:
        return folder_genre, "existing_folder"

    haystack = norm(" ".join(filter(None, [
        track["title_clean"], track["remixer_raw"] or "", track["artist_raw"],
        track["album_raw"], track["uploader"] or ""])))
    for rule in RULES["keyword_rules"]:
        for kw in rule["match"]:
            k = norm(kw)
            if re.search(r"(^| )" + re.escape(k) + r"( |$)", haystack):
                return rule["genre"], "keyword"
    return None, "unclassified"


def scan_one(path, root):
    stem_raw = os.path.splitext(os.path.basename(path))[0]
    dup = DUP_RX.search(stem_raw)
    stem = DUP_RX.sub("", stem_raw).strip()

    tags = read_tags(path)
    rel_dir = os.path.relpath(os.path.dirname(path), root)
    source_folder = "" if rel_dir == "." else rel_dir.split(os.sep)[0]

    title_raw = tags["title"] or stem
    artist_raw = tags["artist"]

    # SoundCloud sometimes merges "Artist - Title" into TPE1 and leaves the mix
    # name alone in TIT2. Rebuild from the filename when that happens.
    if " - " in artist_raw and artist_raw.lower() in stem.lower():
        head = stem[:stem.lower().rfind(" - ")] if " - " in stem else artist_raw
        if " - " in head:
            artist_raw, title_from_stem = head.split(" - ", 1)
            title_raw = title_from_stem + (" - " + tags["title"] if tags["title"] else "")
    if not artist_raw:
        artist_raw = stem.split(" - ")[0] if " - " in stem else ""

    remixer_raw, remix_type, matched_in = find_remix(title_raw, stem)
    remixer_clean, remixer_genre = (None, None)
    is_generic = False
    if remixer_raw:
        base, remixer_genre = strip_genre_suffix(remixer_raw)
        if not base or norm(base) in [norm(x) for x in RULES["generic_remixer_names"]]:
            is_generic = True
            remixer_clean = None
        else:
            remixer_clean = canon_remixer(base)

    uploader = None
    m = SC_URL_RX.search(tags["comment"] or "")
    if m:
        uploader = m.group(1)

    # Clean the title from whichever string the remix credit was found in.
    base_text = matched_in or title_raw
    title_clean = clean_title(base_text, remixer_raw, remix_type)
    # If that string was the whole filename, the artist prefix is still on it.
    if base_text == stem and " - " in title_clean:
        title_clean = title_clean.split(" - ", 1)[1].strip()
    if not title_clean:
        title_clean = clean_title(title_raw, remixer_raw, remix_type) or stem
    # Drop a leading artist credit left behind by the split.
    for a in split_artists(artist_raw):
        if a and norm(title_clean).startswith(norm(a)) and norm(title_clean) != norm(a):
            title_clean = title_clean[len(a):].strip(" -–,&")
            break

    # Original artists = the credit list minus the remixer's own name.
    all_artists = split_artists(artist_raw)
    rm_keys = {norm(x) for x in filter(None, [remixer_raw, remixer_clean]) if norm(x)}
    originals, seen = [], set()
    for a in all_artists:
        k = norm(a)
        if not k or k in seen:
            continue
        if k in rm_keys or any(k in r or r in k for r in rm_keys):
            continue
        seen.add(k)
        originals.append(a)
    if not originals:
        # Self-remix ("2AD, 2AD MIAMI BASS - MUTT (2AD MIAMI BASS Remix)"):
        # every credit was the remixer, so the remixer IS the original artist.
        originals = [remixer_clean] if remixer_clean else all_artists[:1]

    # Some uploaders swap the fields outright: "i wanna be down, lake chon -
    # brandy (lake chon Remix)" files the song as the artist. If the parsed
    # title is a name we know and the parsed artist isn't, they're reversed.
    swapped = False
    if originals and title_clean:
        if norm(title_clean) in ARTIST_GENRE and not any(norm(a) in ARTIST_GENRE for a in originals):
            title_clean, originals = originals[0], [title_clean]
            swapped = True

    rec = {
        "path": path,
        "filename": os.path.basename(path),
        "source_folder": source_folder,
        "duplicate_index": int(dup.group(1)) if dup else 0,
        "is_remix": bool(remixer_raw),
        "remix_type": remix_type,
        "remixer_raw": remixer_raw,
        "remixer": remixer_clean,
        "remixer_generic": is_generic,
        "remixer_genre": remixer_genre,
        "original_artists": originals,
        "primary_artist": originals[0] if originals else "",
        "artist_raw": artist_raw,
        "album_raw": tags["album"],
        "title_raw": title_raw,
        "title_clean": title_clean,
        "fields_swapped": swapped,
        "uploader": uploader,
        "existing_bpm": tags["bpm"],
        "existing_key": tags["key"],
        "existing_genre": tags["genre"],
        "year": tags["year"][:4] if tags["year"] else "",
        "comment": tags["comment"],
        "duration": tags["duration"],
        "bitrate": tags["bitrate"],
    }
    # A 40-minute file is a recorded set or radio show, not a track to file
    # under a remixer. Flag it so build_plan routes it to _Unsorted.
    rec["is_long_mix"] = rec["duration"] > 1200
    rec["genre"], rec["genre_source"] = classify_genre(rec)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--out", default="outputs/tracks.json")
    args = ap.parse_args()

    load_artist_map()
    print(f"Artist->genre map: {len(ARTIST_GENRE)} entries", file=sys.stderr)

    root = os.path.abspath(os.path.expanduser(args.root))
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in sorted(filenames):
            if f.lower().endswith(AUDIO_EXT) and not f.startswith("."):
                files.append(os.path.join(dirpath, f))

    print(f"Scanning {len(files)} audio files under {root}", file=sys.stderr)
    tracks = []
    for i, p in enumerate(files, 1):
        try:
            tracks.append(scan_one(p, root))
        except Exception as e:
            print(f"  ! failed on {os.path.basename(p)}: {e}", file=sys.stderr)
        if i % 200 == 0:
            print(f"  …{i}/{len(files)}", file=sys.stderr)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    json.dump({"root": root, "tracks": tracks}, open(args.out, "w"),
              indent=2, ensure_ascii=False)

    remixes = [t for t in tracks if t["is_remix"]]
    named = [t for t in remixes if t["remixer"]]
    unclass = [t for t in tracks if not t["genre"]]
    print(f"\n  {len(tracks)} tracks", file=sys.stderr)
    print(f"  {len(remixes)} remixes/edits ({len(named)} with an identifiable remixer)", file=sys.stderr)
    print(f"  {len(set(t['remixer'] for t in named))} unique remixers", file=sys.stderr)
    print(f"  {len(tracks) - len(unclass)} genre-classified, {len(unclass)} unclassified", file=sys.stderr)
    print(f"  {sum(1 for t in tracks if t['existing_bpm'])} already have a BPM tag", file=sys.stderr)
    print(f"  {sum(1 for t in tracks if t['fields_swapped'])} had artist/title reversed at the source", file=sys.stderr)
    print(f"\nWrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
