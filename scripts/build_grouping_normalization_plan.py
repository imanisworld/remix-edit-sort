#!/usr/bin/env python3
"""Build a PREVIEW-ONLY Grouping normalization plan for one canonical tag
(e.g. "Old Head"), for apply_grouping_plan.py to execute.

Writes a plan report only. Never touches the library.

audit_maintenance.py --tags surfaces Grouping drift (case variants, typos,
inconsistently-separated combined tags) but doesn't fix anything. This is
the fix-plan half: given one canonical value, it finds every Grouping value
that's a variant of it and classifies each into:

A value is split on ";" first (the join convention apply_clean_dirty_grouping.py
already uses for appending Clean/Dirty onto whatever was there) -- most real
values are already a properly-separated "old head; Event: ...; Clean" and
only the one segment mentioning the canonical term needs touching. Any
segment that doesn't mention the canonical term at all (an Event/Source/
Clean-Dirty tag riding alongside it) is left completely untouched -- it
doesn't need to be "known vocabulary" to be valid, it's not what's being fixed.
Only within a segment that DOES mention the canonical term does further
splitting (on "," or whitespace/hyphen, longest phrase first) happen:

  AUTO-FIX   Every canonical-mentioning segment resolves entirely into known
             tags -- either the segment IS the canonical value (case/
             whitespace only), or every token it splits into is
             independently attested elsewhere in the library as ITS OWN
             standalone Grouping value. Nothing here is guessed: a token only
             counts as "known" because it already exists on its own on some
             other track. Rebuilt segment-by-segment, "; "-joined, in
             original order -- untouched segments pass through verbatim.

  NEEDS_REVIEW  At least one segment mentions the canonical term but has a
             token with no independent attestation elsewhere (e.g.
             "old head party" -- nothing in this library uses bare "Party" as
             a Grouping value, so this could be real information worth its
             own tag, or could be noise; a human decides, this never guesses).
             Suspected typos (no clean split at all, e.g. "old heda") land
             here too, for the same reason -- never auto-corrected. One bad
             segment sends the WHOLE value to review, never a partial fix.

  UNSUPPORTED_FORMAT  Matched but not MP3 -- apply_grouping_plan.py only
             writes ID3 TIT1, so these are reported but excluded from the
             applyable plan.

Only AUTO-FIX MP3 items go into the plan's "items" list, in the exact
{path, current_grouping, combined_grouping, id3_version} shape
apply_grouping_plan.py's load_and_validate() requires. NEEDS_REVIEW and
UNSUPPORTED_FORMAT are reported alongside for a human to look at, but are
never part of what could be applied.

--clusters lets multiple canonicals share ONE pass over the library instead
of each doing its own full rescan: pass a JSON file of
[{"canonical": "...", "pattern": "..." (optional)}, ...] instead of
--canonical/--pattern. A file whose Grouping value mentions two different
clusters' terms gets both fixed in the same plan item -- the first cluster
whose pattern matches a given ";"-segment "owns" that segment (never falls
through to a later cluster on the same segment).
"""

from __future__ import annotations

# Read-only against the library; the only write is the plan JSON itself.
SAFETY_CLASSIFICATION = "preview-only"

import argparse
import json
import re
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.mp4 import MP4

REPO = Path(__file__).resolve().parent.parent
MUSIC_ROOT = Path("/Users/djb.a.e/Music/DJ-Music/Music").resolve()
AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".wav", ".aiff"}
MAX_MATCH_WINDOW = 3

# Segments carrying one of these prefixes are established free-text labels
# (an event name, a source pool name), never a tag-list -- see module
# docstring. A generic-word canonical (e.g. "women", "white") can otherwise
# match incidentally inside one ("Event: Gay Women Party", "Event: white
# dance party") and get wrongly sent to needs_review trying to decompose a
# sentence as if it were a comma-joined tag list. Distinctive multi-word
# canonicals (Old Head, Lounge, White Club) never collided with this in
# practice, but it surfaced immediately on a batch of generic single-word
# clusters -- caught by reviewing the needs_review list before applying.
FREE_TEXT_PREFIX_RE = re.compile(r"^(event|source)\s*:", re.IGNORECASE)


def build_vocabulary(tag_audit_report: Path) -> dict[str, str]:
    """casefold(value) -> most-natural casing, from every Grouping value in
    the report that appears on its own (no comma) somewhere in the library.
    These are the only tokens a combined value is allowed to resolve into."""
    report = json.loads(tag_audit_report.read_text(encoding="utf-8"))
    vocab: dict[str, str] = {}
    for value, count in report["grouping_values_in_use"].items():
        if "," in value:
            continue
        fold = value.strip().casefold()
        if fold and fold not in vocab:
            vocab[fold] = value.strip()
    return vocab


def default_pattern(canonical: str) -> re.Pattern:
    words = [re.escape(w) for w in canonical.split()]
    return re.compile(r"\s*".join(words).join((r"\b", r"\b")).replace(r"\b\b", r"\b"), re.IGNORECASE)


def resolve_token(token: str, canonical_fold: str, canonical: str, vocab: dict[str, str]) -> str | None:
    fold = " ".join(token.strip().casefold().split())
    if fold == canonical_fold:
        return canonical
    return vocab.get(fold)


def dedupe_join(parts: list[str]) -> str:
    seen, ordered = set(), []
    for part in parts:
        fold = part.casefold()
        if fold not in seen:
            seen.add(fold)
            ordered.append(part)
    return "; ".join(ordered)


def resolve_words(text: str, canonical_fold: str, canonical: str, vocab: dict[str, str]) -> str | None:
    """Comma- or whitespace/hyphen-joined text (no ";" -- that's handled one
    level up, per-segment) -> fully-resolved "; "-joined string, or None if
    any token in it has no independent attestation as its own standalone
    Grouping value elsewhere in the library."""
    whole_fold = " ".join(text.strip().casefold().split())
    if whole_fold == canonical_fold:
        return canonical

    if "," in text:
        tokens = [t for t in text.split(",") if t.strip()]
        resolved = [resolve_token(t, canonical_fold, canonical, vocab) for t in tokens]
        if all(resolved):
            return dedupe_join(resolved)
        return None

    words = text.replace("-", " ").split()
    parts, i = [], 0
    while i < len(words):
        match = None
        for window in range(min(MAX_MATCH_WINDOW, len(words) - i), 0, -1):
            candidate = " ".join(words[i:i + window])
            resolved = resolve_token(candidate, canonical_fold, canonical, vocab)
            if resolved:
                match = (resolved, window)
                break
        if not match:
            return None
        parts.append(match[0])
        i += match[1]
    return dedupe_join(parts)


def resolve_segment(segment: str, clusters: list[dict], vocab: dict[str, str]) -> str | None:
    """One ';'-delimited segment of a Grouping value, checked against every
    configured cluster (in order). A segment that doesn't mention ANY
    cluster's canonical term at all (an Event/Source/Clean-Dirty tag already
    living happily alongside it) is returned untouched -- it's not what
    we're fixing and doesn't need to be "known vocabulary" to be valid. The
    first cluster whose pattern matches "owns" this segment; if that
    cluster's resolution isn't fully evidence-backed, this returns None --
    it never falls through to try a later cluster on the same segment.
    Free-text Event:/Source: segments are never touched, full stop -- see
    FREE_TEXT_PREFIX_RE."""
    if FREE_TEXT_PREFIX_RE.match(segment):
        return segment
    for cluster in clusters:
        if cluster["pattern"].search(segment):
            return resolve_words(segment, cluster["canonical_fold"], cluster["canonical"], vocab)
    return segment


def resolve_value(raw: str, clusters: list[dict], vocab: dict[str, str]) -> str | None:
    """Full raw Grouping value -> resolved value, or None if any segment
    that mentions a cluster's canonical term can't be fully resolved."""
    segments = [s.strip() for s in raw.split(";") if s.strip()] if ";" in raw else [raw.strip()]
    resolved_segments = []
    for segment in segments:
        resolved = resolve_segment(segment, clusters, vocab)
        if resolved is None:
            return None
        resolved_segments.append(resolved)
    return "; ".join(resolved_segments)


def read_grouping_raw(path: Path) -> tuple[list[str], int | None]:
    """Every raw TIT1/©grp/grouping value on this file, unsplit -- the exact
    granularity apply_grouping_plan.py's read_grouping() uses for MP3, so an
    MP3 item's current_grouping here will match what that script re-reads at
    apply time. id3_version is None for non-MP3 (apply_grouping_plan.py can't
    write those; see SAFETY_CLASSIFICATION note in the module docstring) AND
    for ID3v2.2 MP3s: mutagen's ID3.save() only accepts v2_version 3 or 4 and
    raises ValueError otherwise. apply_grouping_plan.py's write_grouping()
    does not guard against this (confirmed live: it crashed mid-write on a
    real v2.2 file rather than skipping it -- caught safely by the mutation
    engine's backup/rollback path with zero bytes written, but it should
    never have been offered as an auto-fix item in the first place)."""
    suffix = path.suffix.casefold()
    if suffix == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            return [], 4
        except Exception:
            return [], None
        raw = [str(v).strip() for frame in tags.getall("TIT1")
               for v in getattr(frame, "text", []) if str(v).strip()]
        version = tags.version[1]
        return raw, (version if version in (3, 4) else None)
    if suffix == ".m4a":
        try:
            raw = [str(v).strip() for v in MP4(path).get("\xa9grp", []) if str(v).strip()]
        except Exception:
            raw = []
        return raw, None
    if suffix == ".flac":
        try:
            raw = [str(v).strip() for v in FLAC(path).get("grouping", []) if str(v).strip()]
        except Exception:
            raw = []
        return raw, None
    return [], None


def make_cluster(canonical: str, pattern: re.Pattern | None = None) -> dict:
    return {
        "canonical": canonical,
        "canonical_fold": canonical.casefold(),
        "pattern": pattern or default_pattern(canonical),
    }


def build_plan_clusters(library_root: Path, clusters: list[dict], vocab: dict[str, str]) -> dict:
    """Same classification as build_plan(), for any number of canonicals in
    ONE pass over the library -- one filesystem walk, one tag read per file,
    regardless of how many clusters are configured. A file whose Grouping
    value mentions two different clusters' terms (e.g. one segment is an Old
    Head variant, another a Lounge variant) gets both fixed in the same
    plan item, rather than needing two separate build+apply cycles that
    would each re-scan the whole library and re-lock/re-backup the same
    leaf directory."""
    auto_fix, needs_review, unsupported = [], [], []

    for path in sorted(library_root.rglob("*")):
        if not (path.is_file() and path.suffix.lower() in AUDIO_EXTS and not path.is_symlink()):
            continue
        raw_values, id3_version = read_grouping_raw(path)
        matching_raw = [v for v in raw_values if any(c["pattern"].search(v) for c in clusters)]
        if not matching_raw:
            continue

        if id3_version is None:
            if path.suffix.casefold() == ".mp3":
                reason = "ID3v2.2 -- apply_grouping_plan.py's write_grouping() only supports v2_version 3 or 4"
            else:
                reason = f"format {path.suffix} not writable by apply_grouping_plan.py"
            for raw in matching_raw:
                unsupported.append({"path": str(path), "current_grouping": raw, "reason": reason})
            continue

        for raw in matching_raw:
            resolved = resolve_value(raw, clusters, vocab)
            if resolved is None:
                needs_review.append({"path": str(path), "current_grouping": raw})
            elif resolved != raw:
                auto_fix.append({
                    "path": str(path),
                    "current_grouping": [raw],
                    "combined_grouping": resolved,
                    "id3_version": id3_version,
                })

    plan = {
        "mode": "preview-only",
        "rules": {
            "approval_required": True,
            "canonicals": [c["canonical"] for c in clusters],
            "note": "Only AUTO-FIX items with a full evidence-based token resolution are in 'items'. "
                    "needs_review/unsupported_format are report-only, never applyable as-is.",
        },
        "items": auto_fix,
        "needs_review": needs_review,
        "unsupported_format": unsupported,
        "counts": {
            "auto_fix": len(auto_fix),
            "needs_review": len(needs_review),
            "unsupported_format": len(unsupported),
        },
    }
    return plan


def build_plan(library_root: Path, canonical: str, pattern: re.Pattern, vocab: dict[str, str]) -> dict:
    """Single-canonical convenience wrapper, unchanged behavior/shape from
    before multi-cluster support existed -- see build_plan_clusters() for
    the one-scan-many-canonicals path."""
    plan = build_plan_clusters(library_root, [make_cluster(canonical, pattern)], vocab)
    plan["rules"]["canonical"] = canonical
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--canonical", help='single-cluster mode, e.g. "Old Head"')
    parser.add_argument("--pattern", help="single-cluster mode: override regex (default: derived from --canonical)")
    parser.add_argument(
        "--clusters", type=Path,
        help='multi-cluster mode: JSON file [{"canonical": "...", "pattern": "..." (optional)}, ...] -- '
             "scans the library ONCE for all of them instead of once per canonical",
    )
    parser.add_argument("--library-root", type=Path, default=MUSIC_ROOT)
    parser.add_argument("--tag-audit-report", type=Path, required=True,
                         help="a handoff/maintenance-audits/tag-audit-*.json from audit_maintenance.py --tags, "
                              "used only to build the known-standalone-tag vocabulary")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if bool(args.clusters) == bool(args.canonical):
        parser.error("pass exactly one of --canonical or --clusters")

    vocab = build_vocabulary(args.tag_audit_report)
    library_root = args.library_root.resolve()

    if args.clusters:
        raw_clusters = json.loads(args.clusters.read_text(encoding="utf-8"))
        clusters = [
            make_cluster(c["canonical"], re.compile(c["pattern"], re.IGNORECASE) if c.get("pattern") else None)
            for c in raw_clusters
        ]
        plan = build_plan_clusters(library_root, clusters, vocab)
        canonical_label = ", ".join(repr(c["canonical"]) for c in clusters)
    else:
        pattern = re.compile(args.pattern, re.IGNORECASE) if args.pattern else default_pattern(args.canonical)
        plan = build_plan(library_root, args.canonical, pattern, vocab)
        canonical_label = repr(args.canonical)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"canonical(s): {canonical_label}")
    print(f"auto-fix:          {plan['counts']['auto_fix']}")
    print(f"needs review:      {plan['counts']['needs_review']}")
    print(f"unsupported format:{plan['counts']['unsupported_format']}")
    print(f"\nplan written to {args.out}")


if __name__ == "__main__":
    main()
