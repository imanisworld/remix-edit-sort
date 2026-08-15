#!/usr/bin/env python3
"""
Step 3 — Turn the scan + BPM results into a concrete move-and-tag plan, plus a
human-readable preview to approve before anything on disk changes.

    Remixes & Edits/<Genre>/<Remixer>/Title (Remixer Remix) - Original Artist.mp3
    Originals/<Genre>/<Artist>/Title - Artist.mp3
    _Unsorted/

Usage:
    python3 build_plan.py [--tracks outputs/tracks.json] [--bpm outputs/bpm.json]
                          [--dest <folder>] [--out outputs/plan.json]
"""
import os, re, sys, json, argparse, collections, unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from skill_paths import load_reference

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = json.load(open(os.path.join(HERE, "..", "references", "genre-rules.json")))

REMIX_DIR, ORIG_DIR, UNSORTED = "Remixes & Edits", "Originals", "_Unsorted"
UNKNOWN_REMIXER, UNCLASSIFIED = "_Unknown Remixer", "_Unclassified"

# Reuse the stylized-name dictionary music-library-sort already curated.
CASING = load_reference("music-library-sort", "casing-overrides.json", default={}) or {}
ACRONYMS = {a.upper() for a in CASING.get("acronyms", [])}
LOWERS = {w.lower() for w in CASING.get("lowers", [])}
OVERRIDES = {k.upper(): v for k, v in CASING.get("artist_overrides", {}).items()}

ILLEGAL = re.compile(r'[/\\:*?"<>|\x00-\x1f]')
# Names already settled in genre-rules.json are the canonical spelling —
# TRAADSARU and NuBASS are stylized, not shouting.
ALIAS_CANON = {v.upper(): v for v in RULES["remixer_aliases"].values()}


def smart_case(s):
    """Tame SHOUTING without flattening names that are stylized on purpose."""
    if not s:
        return s
    if s.upper() in ALIAS_CANON:
        return ALIAS_CANON[s.upper()]
    if s.upper() in OVERRIDES:
        return OVERRIDES[s.upper()]
    if not any(c.isalpha() for c in s):
        return s
    # Anything already mixed-case is deliberate (FKA twigs, maxcy, nextdimensional)
    if s != s.upper():
        return s
    out = []
    for i, word in enumerate(re.split(r"(\s+)", s)):
        if not word.strip():
            out.append(word)
            continue
        bare = re.sub(r"[^A-Za-z0-9'&]", "", word)
        if bare.upper() in ACRONYMS or (len(bare) >= 12 and bare.isalpha()):
            out.append(word)                       # DJ, MQ, FULLBODYYYYY
        elif i > 0 and bare.lower() in LOWERS:
            out.append(word.lower())
        else:
            out.append(word.capitalize())
    res = "".join(out)
    return re.sub(r"\b([A-Za-z0-9]+)'S\b", r"\1's", res)   # 304'S -> 304's


def safe(name, limit=110):
    n = ILLEGAL.sub("-", (name or "").strip())
    n = re.sub(r"\s{2,}", " ", n).strip(" .")
    n = unicodedata.normalize("NFC", n)
    if len(n) > limit:
        n = n[:limit].rsplit(" ", 1)[0].rstrip(" -,&")
    return n or "Unknown"


def slug(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "unknown"


def plan_one(t, bpm_rec):
    genre = t["genre"] or UNCLASSIFIED
    title = smart_case(t["title_clean"])
    originals = [smart_case(a) for a in t["original_artists"] if a]
    og_str = ", ".join(originals[:3])
    remixer_name = smart_case(t["remixer"]) if t["remixer"] else ""

    # On a re-run the files have moved, so the BPM cache (keyed by path) misses.
    # The tag we wrote last time is already the corrected value — reuse it.
    bpm = (bpm_rec or {}).get("bpm") or t.get("existing_bpm")
    conf = (bpm_rec or {}).get("confidence", 0.85 if t.get("existing_bpm") else 0)

    if t.get("is_long_mix"):
        folder = os.path.join(UNSORTED, "Mixes & Radio Sets")
        fname = safe(f"{og_str + ' - ' if og_str else ''}{title}") + ".mp3"
        bucket = "long_mix"
    elif t["is_remix"] and remixer_name:
        folder = os.path.join(REMIX_DIR, safe(genre, 40), safe(remixer_name, 60))
        credit = f"{title} ({remixer_name} {t['remix_type']})"
        fname = safe(f"{credit} - {og_str}" if og_str else credit) + ".mp3"
        bucket = "remix"
    elif t["is_remix"]:
        # "(Club Edit)" / "(Vogue Remix)" — a genre label, not a person
        folder = os.path.join(REMIX_DIR, safe(genre, 40), UNKNOWN_REMIXER)
        credit = f"{title} ({t['remixer_raw']} {t['remix_type']})" if t["remixer_raw"] else title
        fname = safe(f"{credit} - {og_str}" if og_str else credit) + ".mp3"
        bucket = "remix_unknown"
    elif originals and title:
        folder = os.path.join(ORIG_DIR, safe(genre, 40), safe(originals[0], 60))
        fname = safe(f"{title} - {og_str}") + ".mp3"
        bucket = "original"
    else:
        folder = UNSORTED
        fname = safe(t["filename"].rsplit(".", 1)[0]) + ".mp3"
        bucket = "unsorted"

    tags = {
        # Beatport convention: artist stays the ORIGINAL act, the remixer is
        # credited in the title and in the remixer/composer fields.
        "title": f"{title} ({remixer_name} {t['remix_type']})" if bucket == "remix" else title,
        "artist": og_str or remixer_name,
        "albumartist": remixer_name if bucket == "remix" else (originals[0] if originals else ""),
        "album": remixer_name if bucket == "remix" else (originals[0] if originals else ""),
        "remixer": remixer_name,
        "composer": remixer_name,
        "genre": t["genre"] or "",
        "bpm": bpm,
        "year": t["year"],
        "comment_tags": " ".join(filter(None, [
            f"#remix-by-{slug(remixer_name)}" if remixer_name else "",
            f"#og-{slug(originals[0])}" if originals else "",
            f"#genre-{slug(t['genre'])}" if t["genre"] else "",
            f"#{slug(t['remix_type'])}" if t["remix_type"] else "",
        ])),
    }

    return {
        "path": t["path"],
        "bucket": bucket,
        "dest_dir": folder,
        "dest_name": fname,
        "genre": genre,
        "remixer": remixer_name,
        "original_artists": originals,
        "duration": t.get("duration", 0),
        "tags": tags,
        "bpm_confidence": conf,
        "bpm_note": (bpm_rec or {}).get("note", ""),
        "bpm_conflict": (bpm_rec or {}).get("conflict"),
        "needs_review": bool(
            t["genre"] is None or (bpm and conf < 0.35) or t.get("is_long_mix")
            or bucket in ("unsorted", "remix_unknown")),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracks", default="outputs/tracks.json")
    ap.add_argument("--bpm", default="outputs/bpm.json")
    ap.add_argument("--dest", default=None, help="defaults to the scanned folder")
    ap.add_argument("--out", default="outputs/plan.json")
    ap.add_argument("--preview", default="outputs/_preview.md")
    args = ap.parse_args()

    data = json.load(open(args.tracks))
    tracks, root = data["tracks"], data["root"]
    dest_root = os.path.abspath(os.path.expanduser(args.dest)) if args.dest else root
    bpms = json.load(open(args.bpm)) if os.path.exists(args.bpm) else {}

    items = [plan_one(t, bpms.get(t["path"])) for t in tracks]

    # Two files landing on one name are usually the same track downloaded twice
    # (SoundCloud appends "(1)"). Same name AND same rounded duration = duplicate;
    # otherwise they're different records that need disambiguating.
    seen = {}
    for it in items:
        key = os.path.join(it["dest_dir"], it["dest_name"]).lower()
        if key not in seen:
            seen[key] = it
            it["duplicate_of"] = None
        else:
            first = seen[key]
            if abs(it["duration"] - first["duration"]) < 2.0:
                it["duplicate_of"] = first["path"]
                it["bucket"] = "duplicate"
                it["dest_dir"] = os.path.join(UNSORTED, "Duplicates")
                it["needs_review"] = True
            else:
                it["duplicate_of"] = None
                stem, ext = os.path.splitext(it["dest_name"])
                n = sum(1 for x in items if x.get("dest_name", "").startswith(stem)) + 1
                it["dest_name"] = f"{stem} ({n}){ext}"
        it["dest"] = os.path.join(dest_root, it["dest_dir"], it["dest_name"])

    json.dump({"root": root, "dest_root": dest_root, "items": items},
              open(args.out, "w"), indent=2, ensure_ascii=False)
    write_preview(items, dest_root, args.preview)
    print(f"Wrote {args.out} and {args.preview}", file=sys.stderr)

    b = collections.Counter(i["bucket"] for i in items)
    print(f"  {b['remix'] + b['remix_unknown']} remixes/edits, {b['original']} originals, "
          f"{b['duplicate']} duplicates, {b['long_mix']} long mixes, {b['unsorted']} unsorted",
          file=sys.stderr)
    print(f"  {sum(1 for i in items if i['needs_review'])} flagged for review", file=sys.stderr)


def write_preview(items, dest_root, path):
    by_bucket = collections.Counter(i["bucket"] for i in items)
    remix_tree = collections.defaultdict(lambda: collections.defaultdict(list))
    orig_tree = collections.defaultdict(lambda: collections.defaultdict(list))
    for i in items:
        if i["bucket"] in ("remix", "remix_unknown"):
            remix_tree[i["genre"]][i["remixer"] or UNKNOWN_REMIXER].append(i)
        elif i["bucket"] == "original":
            orig_tree[i["genre"]][i["original_artists"][0] if i["original_artists"] else "?"].append(i)

    L = ["# Remix & Edit sort — preview\n\n",
         f"Destination: `{dest_root}`\n\n",
         f"**{len(items)} files** — "
         f"{by_bucket['remix'] + by_bucket['remix_unknown']} remixes/edits, "
         f"{by_bucket['original']} originals, {by_bucket['duplicate']} duplicates, "
         f"{by_bucket['long_mix']} long mixes, {by_bucket['unsorted']} unsorted\n\n"]
    review = [i for i in items if i["needs_review"]]
    L.append(f"**{len(review)} need review** — unclassified genre, low-confidence BPM, "
             f"unknown remixer, duplicate, or a recorded set.\n")

    L.append("\n---\n\n## Remixes & Edits\n")
    for genre in sorted(remix_tree, key=lambda g: -sum(len(v) for v in remix_tree[g].values())):
        n = sum(len(v) for v in remix_tree[genre].values())
        L.append(f"\n### {genre} — {n} tracks\n")
        for remixer in sorted(remix_tree[genre], key=lambda r: (-len(remix_tree[genre][r]), r.lower())):
            tr = remix_tree[genre][remixer]
            L.append(f"\n**{remixer}** ({len(tr)})\n")
            for i in tr[:6]:
                bpm = i["tags"]["bpm"]
                flag = "  ⚠️" if i["needs_review"] else ""
                L.append(f"- `{i['dest_name']}` · {bpm or '—'} BPM{flag}\n")
            if len(tr) > 6:
                L.append(f"- …and {len(tr) - 6} more\n")

    L.append("\n---\n\n## Originals\n")
    for genre in sorted(orig_tree, key=lambda g: -sum(len(v) for v in orig_tree[g].values())):
        n = sum(len(v) for v in orig_tree[genre].values())
        artists = sorted(orig_tree[genre], key=lambda a: (-len(orig_tree[genre][a]), a.lower()))
        L.append(f"\n### {genre} — {n} tracks, {len(artists)} artists\n")
        L.append(", ".join(f"{a} ({len(orig_tree[genre][a])})" for a in artists[:25]) + "\n")
        if len(artists) > 25:
            L.append(f"\n…and {len(artists) - 25} more artists\n")

    if review:
        L.append("\n---\n\n## Needs review\n\n")
        for i in review[:80]:
            why = []
            if i["genre"] == UNCLASSIFIED: why.append("no genre")
            if i["bpm_conflict"]: why.append(f"BPM {i['bpm_conflict']}")
            if i["bucket"] == "long_mix": why.append("long mix")
            if i["bucket"] == "unsorted": why.append("unparseable")
            if i["bucket"] == "duplicate": why.append("duplicate")
            if i["bucket"] == "remix_unknown": why.append("unknown remixer")
            L.append(f"- `{os.path.basename(i['path'])[:70]}` → `{i['dest_dir']}` "
                     f"({', '.join(why) or 'check'})\n")
        if len(review) > 80:
            L.append(f"\n…and {len(review) - 80} more.\n")
    open(path, "w").write("".join(L))


if __name__ == "__main__":
    main()
