#!/usr/bin/env python3
"""The repository-wide truth model for what "duplicate" means.

`build_plan.py` used to call two files a `"duplicate"` when they produced the
same destination filename and their durations were within 2 seconds. That is
not duplicate proof. It is a *collision* signal, and in this library it fires
on exactly the pairs that must never be conflated:

    Song (Clean).mp3   vs  Song (Dirty).mp3      -- same length, different masters
    Song (Intro).mp3   vs  Song (Intro Dirty).mp3
    Song (X Edit).mp3  vs  Song (Y Edit).mp3     -- same source, different edits

A DJ library's entire value is in those distinctions. So this module gives
one vocabulary that every tool in the repo must use, and one rule about which
verdicts may ever authorize destructive action.

## The four verdicts

EXACT_DUPLICATE
    Identical SHA-256 over the whole file. Nothing else qualifies. This is
    the ONLY verdict that may be handed to a deletion tool, and even then
    deletion stays manual and human-approved -- see `may_authorize_deletion`.

AUDIO_EQUIVALENT
    Bytes differ, but strong audio-fingerprint evidence says the decoded
    audio is the same recording (e.g. the same master with different tags or
    a different container). Produced by `classify_audio_match` from
    Chromaprint evidence (scripts/audio_fingerprint.py). Never authorizes
    deletion on its own; only the owner's audio-match rule below can make an
    AUDIO_EQUIVALENT pair removable, through
    `delete_verified_duplicates.py --mode audio-match`.

RELATED_VERSION
    An intentional, meaningful variant: Clean / Dirty / Intro / Outro /
    Extended / Short / Radio / Instrumental / Acapella / Remix / Edit /
    Bootleg / Flip / VIP / Live. These are DIFFERENT TRACKS. Never a
    duplicate, never a collision to auto-resolve, never deletable.

REVIEW
    Everything else that merely looks similar -- same filename, same
    normalized title/artist, same rounded duration, any combination of those.
    Surfaced to a human. Authorizes nothing.

## The one hard rule

Filename, title, artist and duration similarity -- in any combination, at any
threshold -- can produce at most REVIEW. Only a byte-identical SHA-256 can
produce EXACT_DUPLICATE.

## Owner audio-match rule (approved by Imani, 2026-10-15 16:57 ET)

One narrow, owner-approved extension. A file may be removed in favour of a
kept copy when ALL of these hold, re-checked at delete time:

  1. Same version markers (filename + Title tag). Clean vs Dirty, Intro,
     Remix, Edit, ... still always differ and are never removable.
  2. Either the audio payload (tags ignored) is identical, OR
       - Chromaprint similarity >= 0.85, AND
       - durations within 3 s, AND
       - the removed copy is lower quality: lower bitrate, or the kept copy
         is lossless and the removed copy is not.
  3. The kept copy exists, and a human approved the exact keep/remove list.

Anything else stays REVIEW. Ties in bitrate with differing audio are NOT
removable.
"""

from __future__ import annotations

import re

SAFETY_CLASSIFICATION = "read-only"

EXACT_DUPLICATE = "EXACT_DUPLICATE"
AUDIO_EQUIVALENT = "AUDIO_EQUIVALENT"
RELATED_VERSION = "RELATED_VERSION"
REVIEW = "REVIEW"

VERDICTS = (EXACT_DUPLICATE, AUDIO_EQUIVALENT, RELATED_VERSION, REVIEW)

# Only this verdict may ever be passed to a deletion tool. Deletion is still
# never automatic -- see may_authorize_deletion().
DELETABLE_VERDICTS = frozenset({EXACT_DUPLICATE})

# Version markers that make two files intentionally different records.
VERSION_MARKERS = (
    "clean", "dirty", "explicit", "intro", "outro", "extended", "extend",
    "short", "radio", "edit", "remix", "rework", "flip", "bootleg", "vip",
    "instrumental", "instrumentals", "acapella", "acappella", "a capella",
    "live", "dub", "mixshow", "quick hit", "quickhit", "transition",
    "hype", "slowed", "sped up", "spedup", "chopped", "screwed",
)

_MARKER_RX = re.compile(
    r"\b(" + "|".join(re.escape(marker) for marker in VERSION_MARKERS) + r")\b",
    re.IGNORECASE,
)


def version_markers(name: str) -> frozenset[str]:
    """Version markers present in a filename or title, casefolded."""
    return frozenset(match.group(1).casefold() for match in _MARKER_RX.finditer(name or ""))


def are_related_versions(name_a: str, name_b: str) -> bool:
    """True when two names carry different version markers.

    Different marker sets means an intentional variant pair (Clean vs Dirty,
    Intro vs plain). Identical marker sets says nothing either way -- two
    unrelated tracks both marked "(Dirty)" are not related versions of each
    other -- so that case falls through to REVIEW.
    """
    markers_a, markers_b = version_markers(name_a), version_markers(name_b)
    return markers_a != markers_b and bool(markers_a or markers_b)


def classify(
    *,
    sha256_a: str | None = None,
    sha256_b: str | None = None,
    name_a: str = "",
    name_b: str = "",
    duration_a: float | None = None,
    duration_b: float | None = None,
    audio_fingerprint_match: bool = False,
) -> dict:
    """Return {"verdict", "reason", "deletable"} for a candidate pair.

    Evaluation order is deliberate: byte identity first (the only proof),
    then intentional-version evidence (which must win over any similarity
    heuristic), then fingerprint evidence, then review.
    """
    if sha256_a and sha256_b and sha256_a == sha256_b:
        return {
            "verdict": EXACT_DUPLICATE,
            "reason": "identical SHA-256 over the full file",
            "deletable": True,
        }

    if are_related_versions(name_a, name_b):
        return {
            "verdict": RELATED_VERSION,
            "reason": (
                f"different version markers ({sorted(version_markers(name_a))} vs "
                f"{sorted(version_markers(name_b))}) -- intentionally different tracks"
            ),
            "deletable": False,
        }

    if audio_fingerprint_match:
        return {
            "verdict": AUDIO_EQUIVALENT,
            "reason": "audio fingerprint match with differing bytes; review before any action",
            "deletable": False,
        }

    similarities = []
    if name_a and name_b and name_a.casefold() == name_b.casefold():
        similarities.append("same filename")
    if duration_a is not None and duration_b is not None and abs(duration_a - duration_b) < 2.0:
        similarities.append("duration within 2s")
    return {
        "verdict": REVIEW,
        "reason": (
            ("possible collision: " + ", ".join(similarities) + " -- similarity is not duplicate proof")
            if similarities
            else "no duplicate evidence"
        ),
        "deletable": False,
    }


def may_authorize_deletion(verdict: str) -> bool:
    """Only EXACT_DUPLICATE, and even then a human still approves each pair.

    Nothing in this repository deletes automatically. This function answers
    "is this verdict even eligible to be put in front of a human as a
    deletion candidate", not "delete it".
    """
    return verdict in DELETABLE_VERDICTS


def assert_not_called_duplicate(verdict: str, label: str) -> None:
    """Guard for callers that render user-facing labels.

    Raises if anything other than EXACT_DUPLICATE is about to be shown to a
    human as a confirmed duplicate.
    """
    if "duplicate" in label.casefold() and verdict != EXACT_DUPLICATE:
        raise ValueError(
            f"refusing to label a {verdict} pair {label!r}: only EXACT_DUPLICATE "
            "(identical SHA-256) may be called a duplicate"
        )


MIN_FINGERPRINT_SIMILARITY = 0.85
MAX_DURATION_DIFF_SECONDS = 3.0
OWNER_AUDIO_MATCH_RULE = "owner-audio-match-2026-10-15"


def classify_audio_match(*, delete: dict, keep: dict, similarity: float) -> dict:
    """Apply the owner audio-match rule to two probes (see module docstring).

    `delete`/`keep` carry: markers, payload_sha256, duration, bitrate, lossless.
    Returns {"verdict", "reason", "owner_rule_removable"}.
    """
    if sorted(delete["markers"]) != sorted(keep["markers"]):
        return {"verdict": RELATED_VERSION, "owner_rule_removable": False,
                "reason": f"different version markers ({sorted(delete['markers'])} vs {sorted(keep['markers'])})"}
    if delete["payload_sha256"] and delete["payload_sha256"] == keep["payload_sha256"]:
        return {"verdict": AUDIO_EQUIVALENT, "owner_rule_removable": True,
                "reason": "identical audio payload (tags differ)"}
    if similarity < MIN_FINGERPRINT_SIMILARITY:
        return {"verdict": REVIEW, "owner_rule_removable": False,
                "reason": f"fingerprint similarity {similarity} < {MIN_FINGERPRINT_SIMILARITY}"}
    if abs(float(delete["duration"]) - float(keep["duration"])) > MAX_DURATION_DIFF_SECONDS:
        return {"verdict": REVIEW, "owner_rule_removable": False,
                "reason": "durations differ by more than 3 s"}
    lower = (keep["lossless"] and not delete["lossless"]) or (
        delete["lossless"] == keep["lossless"] and int(delete["bitrate"]) < int(keep["bitrate"]))
    if not lower:
        return {"verdict": AUDIO_EQUIVALENT, "owner_rule_removable": False,
                "reason": "same recording but the removed copy is not lower quality"}
    return {"verdict": AUDIO_EQUIVALENT, "owner_rule_removable": True,
            "reason": f"fingerprint {similarity}, durations within 3 s, removed copy lower quality"}


def may_authorize_audio_match_deletion(result: dict) -> bool:
    """True only for an AUDIO_EQUIVALENT verdict that satisfies the owner rule.
    Still needs a human-approved keep/remove list; nothing deletes automatically."""
    return result.get("verdict") == AUDIO_EQUIVALENT and bool(result.get("owner_rule_removable"))
