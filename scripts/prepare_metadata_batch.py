#!/usr/bin/env python3
"""Prepare or durably approve a fail-closed MP3 metadata batch.

Preparation is read-only against music and Serato.  It writes review evidence,
an immutable master manifest, and inert per-scope plans under ``handoff/``.
Approval is a separate explicit command bound to the reported master SHA.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "preview-only"

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError

import apply_new_music_intake_plan as intake
import build_version_genre_plan as classifier
import metadata_batch

import survey_new_music_intake as intake_survey

DEFAULT_LIBRARY = Path("/Users/djb.a.e/Music/DJ-Music/Music")
DEFAULT_SERATO = Path("/Users/djb.a.e/Music/_Serato_")
# 150 stays the no-questions-asked ceiling. A larger batch is legal only when
# the operator states the exact limit twice (--max N --confirm-max N); there
# is no uncapped mode.
DEFAULT_MAX_CANDIDATES = 150
LANES = ("genre", "clean-dirty", "intake", "strip-event-label", "strip-grouping-term")
# A transformation label is held only when its plain word match has no
# explicit bracketed form; a bracketed label is exact evidence and becomes
# its own controlled Grouping term via classifier.TRANSFORMATION_LABEL_RULES.
HOLD_RULES = {
    label: (plain, bracketed)
    for label, plain, bracketed, _ in classifier.TRANSFORMATION_LABEL_RULES
}
NEW_TERM_ORDER = {
    "Old Head": 10, "Plex": 10, "Lounge": 10, "Kids": 10,
    "Line Dance": 10, "Holiday": 10, "Christmas": 10, "Halloween": 10,
    "Top 40": 10, "Clean": 20, "Dirty": 20, "Acapella": 30,
    "Instrumental": 30, "Original": 30, "Remix": 40, "Edit": 40,
    "Mashup": 40, "Blend": 40, "Re-Drum": 40, "Transition Edit": 40,
    "Intro Edit": 40, "Outro Edit": 40, "Extended": 40, "Bootleg": 40,
    "Radio Edit": 40, "Flip": 40, "ReWork": 40,
}
SPECIFIC_EDIT_TERMS = {"Intro Edit", "Outro Edit", "Transition Edit", "Radio Edit"}
CLEAN_DIRTY_FOLDER_TERMS = {"clean": "Clean", "dirty": "Dirty"}
INTRO_CLEAN_RE = re.compile(r"\bintro\s+clean\b", re.I)
NEEDS_CHECK_FOLDERS = {"_GENRE NEEDS CHECK", "_NEEDS CHECK"}
# Explicitly out of scope for every automated lane (see CLAUDE.md).
OUT_OF_SCOPE_FOLDERS = {"Imani Drops"}
FIELD_NAMES = {9: "Genre", 19: "Grouping"}


def _text_frame(tags: ID3, frame_id: str) -> str:
    frames = tags.getall(frame_id)
    if not frames:
        return ""
    values = getattr(frames[0], "text", [])
    return str(values[0]).strip() if values else ""


def read_track(path: Path) -> dict:
    try:
        tags = ID3(path)
    except ID3NoHeaderError as exc:
        raise metadata_batch.BatchRefused(f"MP3 has no ID3 header: {path}") from exc
    grouping, major = intake.read_grouping(path)
    return {
        "path": str(path.resolve()),
        "sha256": intake.sha256_file(path),
        "bytes": path.stat().st_size,
        "mtime_ns": path.stat().st_mtime_ns,
        "id3_version": f"2.{major}",
        "id3_major": major,
        "artist": _text_frame(tags, "TPE1"),
        "title": _text_frame(tags, "TIT2"),
        "genre": intake.read_genre(path),
        "grouping": grouping,
        "audio_payload_sha256": intake.sha256_bytes(intake.audio_payload(path)),
        "serato_frames_sha256": intake.serato_digest(path),
    }


def _meaning_tokens(value: str) -> list[str]:
    text = value.casefold()
    text = re.sub(r"r\s*&\s*b", "rnb", text)
    parts = re.split(r"\s*[;,]\s*|\s+&\s+", text)
    return [re.sub(r"[^a-z0-9]+", "", part) for part in parts if re.sub(r"[^a-z0-9]+", "", part)]


def formatting_only(old: str, new: str) -> bool:
    """True only when serialization/case/spacing changed, not musical meaning."""
    return _meaning_tokens(old) == _meaning_tokens(new)


def _tlvs(data: bytes):
    pos = 0
    while pos + 8 <= len(data):
        key = data[pos:pos + 4].decode("ascii", "replace")
        size = int.from_bytes(data[pos + 4:pos + 8], "big")
        end = pos + 8 + size
        if size < 0 or end > len(data):
            break
        yield key, data[pos + 8:end]
        pos = end


def _utf16(payload: bytes) -> str:
    try:
        return payload.decode("utf-16-be").rstrip("\x00")
    except UnicodeDecodeError:
        return ""


def parse_smart_crates(serato_root: Path) -> list[dict]:
    """Read just the Genre/Grouping string rules from production .scrate files."""
    result = []
    smart_root = Path(serato_root) / "SmartCrates"
    if not smart_root.is_dir():
        raise metadata_batch.BatchRefused(f"Smart Crate directory is missing: {smart_root}")
    for crate in sorted(smart_root.glob("*.scrate")):
        top = list(_tlvs(crate.read_bytes()))
        bools = [payload[-1] for key, payload in top if key == "rart" and payload]
        match_all = bool(bools and bools[0])
        for key, payload in top:
            if key != "rurt":
                continue
            children = {child_key: child for child_key, child in _tlvs(payload)}
            # Serato container keys lose their leading type byte inside rurt:
            # the on-disk field key is exactly ``urkt`` (not ``rurkt``).
            field_payload = children.get("urkt", b"")
            field_id = int.from_bytes(field_payload, "big") if field_payload else -1
            if field_id not in FIELD_NAMES:
                continue
            operator_raw = _utf16(children.get("trft", b""))
            operator = {
                "cond_con_str": "contains",
                "cond_ncon_str": "does not contain",
                "cond_is_str": "is",
                "cond_is_not_str": "is not",
            }.get(operator_raw, operator_raw or "unknown")
            result.append({
                "crate": crate.stem,
                "path": str(crate.resolve()),
                "field": FIELD_NAMES[field_id],
                "operator": operator,
                "value": _utf16(children.get("trpt", b"")),
                "match_all": match_all,
            })
    return result


def _matches(value: str, operator: str, needle: str) -> bool | None:
    value, needle = value.casefold(), needle.casefold()
    if operator == "contains":
        return needle in value
    if operator == "does not contain":
        return needle not in value
    if operator == "is":
        return value == needle
    if operator == "is not":
        return value != needle
    return None


def smart_effects(current_genre: str, proposed_genre: str, current_grouping: str,
                  proposed_grouping: str, rules: list[dict]) -> tuple[list[dict], bool]:
    effects, genre_changed_membership = [], False
    for rule in rules:
        if rule["field"] == "Genre":
            before, after = current_genre, proposed_genre
        else:
            before, after = current_grouping, proposed_grouping
        old_match = _matches(before, rule["operator"], rule["value"])
        new_match = _matches(after, rule["operator"], rule["value"])
        if old_match is None or old_match == new_match:
            continue
        effect = "enters rule" if new_match else "exits rule"
        effects.append({**rule, "effect": effect})
        if rule["field"] == "Genre":
            genre_changed_membership = True
    return effects, genre_changed_membership


def _folder_stats(root: Path) -> tuple[int, int]:
    count = size = 0
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_file() and not path.is_symlink() and not name.startswith("._") and name != ".DS_Store":
                count += 1
                size += path.stat().st_size
    return count, size


def _held_transformation_label(label_text: str) -> str | None:
    """The label to hold on, when a plain match has no explicit bracketed form."""
    return next(
        (label for label, (plain, bracketed) in HOLD_RULES.items()
         if plain.search(label_text) and not bracketed.search(label_text)),
        None,
    )


def _grouping_changed(row: dict) -> bool:
    return bool(row["grouping_add_terms"]) or bool(row.get("grouping_remove_terms"))


def _select_stable(candidates: list[dict], exclusions: Counter, maximum: int) -> tuple[list[dict], Counter]:
    """Deterministically order, cap, and re-read the shared candidate pool."""
    candidates.sort(key=lambda row: (row["preference"], row["scope_root"].casefold(), row["path"].casefold()))
    selected = []
    for row in candidates:
        if len(selected) >= maximum:
            exclusions["eligible but deferred by batch cap"] += 1
            continue
        # A second complete read binds only stable live state into the batch.
        reread = read_track(Path(row["path"]))
        stable_fields = (
            "sha256", "bytes", "mtime_ns", "id3_version", "genre", "grouping",
            "audio_payload_sha256", "serato_frames_sha256",
        )
        if any(reread[field] != row[field] for field in stable_fields):
            exclusions["live state changed during preparation"] += 1
            continue
        selected.append(row)
    return selected, exclusions


def _coalesce_nested_scope_roots(rows: list[dict]) -> list[dict]:
    """Scope roots must be disjoint folder subtrees.

    A scope's backup and post-apply verification walk its source root
    recursively, so a scope root nested inside another scope's root would
    place the same subtree under two scopes. Reassign every candidate to
    the shallowest selected ancestor folder, then re-sort with the
    selection key so the recorded candidate order stays deterministic.
    """
    roots = {row["scope_root"] for row in rows}
    anchors: dict[str, str] = {}
    for root in roots:
        anchor = root
        for ancestor in Path(root).parents:
            if str(ancestor) in roots:
                # parents iterate upward, so the last match is the shallowest.
                anchor = str(ancestor)
        anchors[root] = anchor
    for row in rows:
        row["scope_root"] = anchors[row["scope_root"]]
    rows.sort(key=lambda row: (row["preference"], row["scope_root"].casefold(), row["path"].casefold()))
    return rows


def _refuse_overlapping_scope_roots(scope_roots: list[str]) -> None:
    """Fail closed: refuse PREPARE if any scope root equals or contains another."""
    resolved = [(Path(root).resolve(), root) for root in scope_roots]
    for i, (left, left_raw) in enumerate(resolved):
        for right, right_raw in resolved[i + 1:]:
            if left == right or right.is_relative_to(left) or left.is_relative_to(right):
                raise metadata_batch.BatchRefused(
                    f"scope roots overlap: {left_raw!r} and {right_raw!r} are not disjoint"
                )


def discover(library_root: Path, serato_root: Path, maximum: int,
            genre_only: bool = False) -> tuple[list[dict], Counter]:
    rules = classifier.load_genre_rules(None)
    crate_rules = parse_smart_crates(serato_root)
    exclusions: Counter = Counter()
    candidates = []
    for path in sorted(Path(library_root).rglob("*.mp3")):
        if "_GENRE NEEDS CHECK" not in path.parts:
            continue
        if OUT_OF_SCOPE_FOLDERS.intersection(path.parts):
            continue
        try:
            current = read_track(path)
        except Exception:
            exclusions["unreadable MP3/tag"] += 1
            continue
        label_text = f"{path.stem} {current['title']}"
        held = _held_transformation_label(label_text)
        if held:
            exclusions[held] += 1
            continue
        if current["id3_major"] == 2:
            exclusions["ID3v2.2"] += 1
            continue
        if current["id3_major"] not in {3, 4}:
            exclusions["unsupported ID3 version"] += 1
            continue
        if current["genre"].strip().casefold() == "other":
            exclusions["Other"] += 1
            continue
        genre_analysis = classifier.analyze_genre(
            {"filename": "", "genre": current["genre"]}, rules
        )
        if genre_analysis["resolution_status"] != "RESOLVED" or not genre_analysis["proposed_genre"]:
            exclusions[genre_analysis.get("category", "unknown/unmapped Genre")] += 1
            continue
        proposed_genre = genre_analysis["proposed_genre"]
        genre_change = proposed_genre != current["genre"]
        if genre_change and not formatting_only(current["genre"], proposed_genre):
            exclusions["semantic alias mapping held"] += 1
            continue

        if genre_only:
            additions: list[str] = []
            grouping_evidence = ["Genre-only batch: Grouping intentionally left unchanged"]
        else:
            grouping_analysis = classifier.classify_grouping(
                path.name, current["title"], current["grouping"], rules
            )
            if grouping_analysis["review_reasons"]:
                exclusions[grouping_analysis["review_reasons"][0]] += 1
                continue
            additions = sorted(
                grouping_analysis["add_terms"],
                key=lambda value: (NEW_TERM_ORDER.get(value, 999), value.casefold()),
            )
            specific_edit = SPECIFIC_EDIT_TERMS.intersection(additions)
            if "Edit" in additions and specific_edit:
                exclusions["overlapping generic/specific Edit evidence"] += 1
                continue
            grouping_evidence = grouping_analysis["evidence"]
        proposed_grouping_parts = [*current["grouping"], *additions]
        proposed_grouping = "; ".join(proposed_grouping_parts)
        current_grouping = "; ".join(current["grouping"])
        grouping_change = bool(additions)
        if not genre_change and not grouping_change:
            exclusions["already normalized / no approved change"] += 1
            continue
        effects, genre_dependency = smart_effects(
            current["genre"], proposed_genre, current_grouping, proposed_grouping, crate_rules
        )
        if genre_dependency:
            exclusions["Smart Crate-dependent Genre spelling held"] += 1
            continue
        scope = path.parent.resolve()
        try:
            relative = path.resolve().relative_to(Path(library_root).resolve())
        except ValueError:
            exclusions["outside library root"] += 1
            continue
        top_folder = relative.parts[0]
        candidates.append({
            **current,
            "proposed_genre": proposed_genre,
            "proposed_grouping": proposed_grouping,
            "grouping_add_terms": additions,
            "genre_evidence": genre_analysis["evidence"],
            "grouping_evidence": grouping_evidence,
            "smart_crate_effects": effects,
            "scope_root": str(scope),
            "lock_folder": top_folder,
            "preference": 0 if genre_change and not grouping_change and len(genre_analysis["recognized_genres"]) == 1 else
                          1 if genre_change and not grouping_change else
                          2 if genre_change and grouping_change else 3,
        })

    return _select_stable(candidates, exclusions, maximum)


def _scope_fields(path: Path, library_root: Path) -> tuple[str, str] | None:
    """(scope_root, lock_folder) for a candidate, or None when unusable."""
    try:
        relative = path.resolve().relative_to(Path(library_root).resolve())
    except ValueError:
        return None
    if len(relative.parts) < 2:
        return None
    return str(path.parent.resolve()), relative.parts[0]


def discover_event_label_strip(library_root: Path, serato_root: Path, event_folders: list[str],
                               maximum: int) -> tuple[list[dict], Counter]:
    """Deterministic discovery for removing one specific Grouping term,
    'Event: <folder name>', from every MP3 under an explicit list of
    Events/<name> subfolders. No semantic classification: a candidate is
    included only when its live Grouping already contains that exact
    literal term (casefold-matched); only that one term is proposed for
    removal, and every other Grouping value, Genre, and file location is
    left untouched.
    """
    crate_rules = parse_smart_crates(serato_root)
    exclusions: Counter = Counter()
    candidates = []
    events_root = Path(library_root) / "Events"
    for folder_name in event_folders:
        folder_root = events_root / folder_name
        if not folder_root.is_dir():
            raise metadata_batch.BatchRefused(f"event folder does not exist: {folder_root}")
        target_term = f"Event: {folder_name}"
        target_normalized = target_term.casefold()
        for path in sorted(folder_root.rglob("*.mp3")):
            if OUT_OF_SCOPE_FOLDERS.intersection(path.parts):
                exclusions["out of scope folder"] += 1
                continue
            try:
                current = read_track(path)
            except Exception:
                exclusions["unreadable MP3/tag"] += 1
                continue
            if current["id3_major"] == 2:
                exclusions["ID3v2.2"] += 1
                continue
            if current["id3_major"] not in {3, 4}:
                exclusions["unsupported ID3 version"] += 1
                continue
            matches = [term for term in current["grouping"] if term.strip().casefold() == target_normalized]
            if not matches:
                exclusions["no matching Event: label present"] += 1
                continue
            remaining = [term for term in current["grouping"] if term.strip().casefold() != target_normalized]
            proposed_grouping = "; ".join(remaining)
            current_grouping = "; ".join(current["grouping"])
            effects, _ = smart_effects(
                current["genre"], current["genre"], current_grouping, proposed_grouping, crate_rules
            )
            scope_fields = _scope_fields(path, library_root)
            if scope_fields is None:
                exclusions["outside library root"] += 1
                continue
            scope_root, lock_folder = scope_fields
            candidates.append({
                **current,
                "proposed_genre": current["genre"],
                "proposed_grouping": proposed_grouping,
                "grouping_add_terms": [],
                "grouping_remove_terms": matches,
                "genre_evidence": ["Event-label-strip batch: Genre intentionally left unchanged"],
                "grouping_evidence": [
                    f"removing exact literal term {target_term!r}; every other Grouping value preserved"
                ],
                "smart_crate_effects": effects,
                "scope_root": scope_root,
                "lock_folder": lock_folder,
                "preference": 0,
            })
    return _select_stable(candidates, exclusions, maximum)


def discover_grouping_term_strip(library_root: Path, serato_root: Path, remove_terms: list[str],
                                 maximum: int) -> tuple[list[dict], Counter]:
    """Deterministic, library-wide discovery for removing an explicit list
    of literal Grouping terms (e.g. leftover download-source labels like
    'www.bpmsupreme.com') wherever they appear. No semantic classification,
    no folder scoping: a candidate is included only when its live Grouping
    already contains at least one exact literal term (casefold-matched)
    from the given list; only those matched terms are proposed for
    removal, and every other Grouping value, Genre, and file location is
    left untouched.
    """
    crate_rules = parse_smart_crates(serato_root)
    target_normalized = {term.strip().casefold() for term in remove_terms if term.strip()}
    if not target_normalized:
        raise metadata_batch.BatchRefused("--remove-terms must contain at least one non-blank term")
    exclusions: Counter = Counter()
    candidates = []
    for path in sorted(Path(library_root).rglob("*.mp3")):
        if OUT_OF_SCOPE_FOLDERS.intersection(path.parts):
            exclusions["out of scope folder"] += 1
            continue
        try:
            current = read_track(path)
        except Exception:
            exclusions["unreadable MP3/tag"] += 1
            continue
        matches = [term for term in current["grouping"] if term.strip().casefold() in target_normalized]
        if not matches:
            exclusions["no matching term present"] += 1
            continue
        if current["id3_major"] == 2:
            exclusions["ID3v2.2"] += 1
            continue
        if current["id3_major"] not in {3, 4}:
            exclusions["unsupported ID3 version"] += 1
            continue
        remaining = [term for term in current["grouping"] if term.strip().casefold() not in target_normalized]
        proposed_grouping = "; ".join(remaining)
        current_grouping = "; ".join(current["grouping"])
        effects, _ = smart_effects(
            current["genre"], current["genre"], current_grouping, proposed_grouping, crate_rules
        )
        scope_fields = _scope_fields(path, library_root)
        if scope_fields is None:
            exclusions["outside library root"] += 1
            continue
        scope_root, lock_folder = scope_fields
        candidates.append({
            **current,
            "proposed_genre": current["genre"],
            "proposed_grouping": proposed_grouping,
            "grouping_add_terms": [],
            "grouping_remove_terms": matches,
            "genre_evidence": ["Grouping-term-strip batch: Genre intentionally left unchanged"],
            "grouping_evidence": [
                f"removing exact literal term(s) {matches!r}; every other Grouping value preserved"
            ],
            "smart_crate_effects": effects,
            "scope_root": scope_root,
            "lock_folder": lock_folder,
            "preference": 0,
        })
    return _select_stable(candidates, exclusions, maximum)


def load_transcript_dirty_evidence(path: Path) -> dict[str, str]:
    """path -> sha256 for CONFIRMED Dirty transcript hits only.

    Structural enforcement of the asymmetric evidence rule: Clean entries in
    a transcript-evidence file are never read into this map, so a caller
    that wires it into discover_clean_dirty has no way to treat transcript
    silence as proof of Clean, even by accident.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        row["path"]: row["sha256"]
        for row in payload.get("results", [])
        if row.get("classification") == "Dirty" and row.get("path") and row.get("sha256")
    }


def discover_clean_dirty(library_root: Path, serato_root: Path, maximum: int,
                         transcript_dirty_evidence: dict[str, str] | None = None) -> tuple[list[dict], Counter]:
    """Deterministic Clean/Dirty Grouping backfill across the sorted library.

    Evidence is a literal CLEAN/DIRTY folder component (the canonical unit
    apply_clean_dirty_grouping.py established), an explicit bracketed
    Clean/Dirty label, and/or (only for Dirty) a confirmed transcript hit
    from transcript_dirty_evidence (path -> sha256, revalidated live against
    the current file so stale evidence is never trusted). Genre is never
    changed, existing Grouping is only appended to, and any conflicting or
    ambiguous evidence is held. Transcript silence never adds evidence.
    """
    rules = classifier.load_genre_rules(None)
    crate_rules = parse_smart_crates(serato_root)
    transcript_dirty_evidence = transcript_dirty_evidence or {}
    exclusions: Counter = Counter()
    candidates = []
    library_root = Path(library_root).resolve()
    for path in sorted(library_root.rglob("*.mp3")):
        try:
            relative_parts = path.resolve().relative_to(library_root).parts
        except ValueError:
            continue
        if OUT_OF_SCOPE_FOLDERS.intersection(relative_parts):
            continue
        folder_terms = {
            CLEAN_DIRTY_FOLDER_TERMS[part.casefold()]
            for part in relative_parts[:-1]
            if part.casefold() in CLEAN_DIRTY_FOLDER_TERMS
        }
        stem_has_label = bool(
            classifier.CLEAN_RE.search(path.stem)
            or classifier.DIRTY_RE.search(path.stem)
            or INTRO_CLEAN_RE.search(path.stem)
        )
        transcript_candidate = str(path) in transcript_dirty_evidence
        if not folder_terms and not stem_has_label and not transcript_candidate:
            continue
        if NEEDS_CHECK_FOLDERS.intersection(relative_parts):
            exclusions["left to the needs-check lanes"] += 1
            continue
        scope_fields = _scope_fields(path, library_root)
        if scope_fields is None:
            exclusions["outside library root or directly at its top level"] += 1
            continue
        try:
            current = read_track(path)
        except Exception:
            exclusions["unreadable MP3/tag"] += 1
            continue
        if current["id3_major"] == 2:
            exclusions["ID3v2.2"] += 1
            continue
        if current["id3_major"] not in {3, 4}:
            exclusions["unsupported ID3 version"] += 1
            continue
        label_text = f"{path.stem} {current['title']}"
        held = _held_transformation_label(label_text)
        if held:
            exclusions[held] += 1
            continue
        grouping_analysis = classifier.classify_grouping(
            path.name, current["title"], current["grouping"], rules
        )
        if grouping_analysis["review_reasons"]:
            exclusions[grouping_analysis["review_reasons"][0]] += 1
            continue
        label_terms = set()
        if INTRO_CLEAN_RE.search(label_text) or classifier.CLEAN_RE.search(label_text):
            label_terms.add("Clean")
        if classifier.DIRTY_RE.search(label_text):
            label_terms.add("Dirty")
        transcript_terms = set()
        if transcript_candidate:
            if transcript_dirty_evidence[str(path)] == current["sha256"]:
                transcript_terms.add("Dirty")
            else:
                exclusions["transcript evidence stale (sha256 drift since transcription)"] += 1
                continue
        evident = folder_terms | label_terms | transcript_terms
        existing_status = {
            term for term in ("Clean", "Dirty")
            if any(value.casefold() == term.casefold() for value in current["grouping"])
        }
        if not evident:
            exclusions["no deterministic Clean/Dirty evidence"] += 1
            continue
        if len(evident | existing_status) > 1:
            exclusions["conflicting Clean/Dirty evidence"] += 1
            continue
        additions = sorted(evident - existing_status)
        if not additions:
            exclusions["already carries its Clean/Dirty status"] += 1
            continue
        evidence = []
        for term in additions:
            sources = []
            if term in folder_terms:
                sources.append(f"literal {term.upper()}/ folder component")
            if term in label_terms:
                sources.append(f"explicit bracketed {term} label in filename/title")
            if term in transcript_terms:
                sources.append("faster-whisper transcript: confirmed explicit-term hit")
            evidence.append(f"{term}: " + " and ".join(sources))
        proposed_grouping_parts = [*current["grouping"], *additions]
        proposed_grouping = "; ".join(proposed_grouping_parts)
        current_grouping = "; ".join(current["grouping"])
        effects, genre_dependency = smart_effects(
            current["genre"], current["genre"], current_grouping, proposed_grouping, crate_rules
        )
        if genre_dependency:
            exclusions["Smart Crate-dependent Genre spelling held"] += 1
            continue
        scope_root, lock_folder = scope_fields
        candidates.append({
            **current,
            "proposed_genre": current["genre"],
            "proposed_grouping": proposed_grouping,
            "grouping_add_terms": additions,
            "genre_evidence": "Clean/Dirty lane leaves Genre unchanged",
            "grouping_evidence": evidence,
            "smart_crate_effects": effects,
            "scope_root": scope_root,
            "lock_folder": lock_folder,
            # Transcript-confirmed evidence is the explicit deliverable of a
            # listening-check batch and must not be crowded out of the cap by
            # the much larger folder/bracket backlog competing at tie-break.
            "preference": 0 if transcript_terms else (1 if folder_terms else 2),
        })
    return _select_stable(candidates, exclusions, maximum)


def discover_intake(library_root: Path, serato_root: Path, intake_root: Path,
                    maximum: int) -> tuple[list[dict], Counter]:
    """New-download intake through the same fail-closed batch model.

    Reuses the survey's established deterministic rules: controlled Grouping
    terms from explicit wording, and Genre only from unanimous tagged-sibling
    consensus that also resolves to a recognized canonical value. Anything
    else is held.
    """
    rules = classifier.load_genre_rules(None)
    crate_rules = parse_smart_crates(serato_root)
    exclusions: Counter = Counter()
    candidates = []
    library_root = Path(library_root).resolve()
    intake_root = Path(intake_root).resolve()
    siblings = intake_survey.index_genre_siblings(intake_root)
    for path in sorted(intake_root.rglob("*.mp3")):
        if OUT_OF_SCOPE_FOLDERS.intersection(path.parts):
            continue
        if intake_survey.in_backlog_path(str(path.relative_to(intake_root))):
            exclusions["needs-check backlog inside intake root"] += 1
            continue
        scope_fields = _scope_fields(path, library_root)
        if scope_fields is None:
            exclusions["outside library root or directly at its top level"] += 1
            continue
        try:
            current = read_track(path)
        except Exception:
            exclusions["unreadable MP3/tag"] += 1
            continue
        if current["id3_major"] == 2:
            exclusions["ID3v2.2"] += 1
            continue
        if current["id3_major"] not in {3, 4}:
            exclusions["unsupported ID3 version"] += 1
            continue
        label_text = f"{path.stem} {current['title']}"
        held = _held_transformation_label(label_text)
        if held:
            exclusions[held] += 1
            continue
        grouping_analysis = classifier.classify_grouping(
            path.name, current["title"], current["grouping"], rules
        )
        if grouping_analysis["review_reasons"]:
            exclusions[grouping_analysis["review_reasons"][0]] += 1
            continue
        additions = sorted(
            grouping_analysis["add_terms"],
            key=lambda value: (NEW_TERM_ORDER.get(value, 999), value.casefold()),
        )
        specific_edit = SPECIFIC_EDIT_TERMS.intersection(additions)
        if "Edit" in additions and specific_edit:
            exclusions["overlapping generic/specific Edit evidence"] += 1
            continue

        proposed_genre = current["genre"]
        genre_evidence = "existing Genre left unchanged by the intake lane"
        if current["genre"].strip().casefold() in intake_survey.PLACEHOLDER_GENRES:
            genre_hold = None
            consensus = intake_survey.unanimous_sibling_genre(
                siblings.get(str(path.parent), Counter())
            )
            if consensus is None:
                genre_hold = "Genre placeholder without unanimous tagged-sibling consensus"
            else:
                consensus_analysis = classifier.analyze_genre(
                    {"filename": "", "genre": consensus}, rules
                )
                if (consensus_analysis["resolution_status"] == "RESOLVED"
                        and consensus_analysis["proposed_genre"]):
                    proposed_genre = consensus_analysis["proposed_genre"]
                    agreeing = siblings[str(path.parent)][consensus]
                    genre_evidence = (
                        f"unanimous sibling Genre consensus: {agreeing} tagged "
                        f"sibling(s) agree on {consensus!r}"
                    )
                else:
                    genre_hold = f"sibling consensus Genre is not a recognized canonical value: {consensus!r}"
            if genre_hold is not None and not additions:
                exclusions[genre_hold] += 1
                continue
        genre_change = proposed_genre != current["genre"]
        if not genre_change and not additions:
            exclusions["already normalized / no approved change"] += 1
            continue
        proposed_grouping_parts = [*current["grouping"], *additions]
        proposed_grouping = "; ".join(proposed_grouping_parts)
        current_grouping = "; ".join(current["grouping"])
        effects, genre_dependency = smart_effects(
            current["genre"], proposed_genre, current_grouping, proposed_grouping, crate_rules
        )
        if genre_dependency:
            exclusions["Smart Crate-dependent Genre spelling held"] += 1
            continue
        scope_root, lock_folder = scope_fields
        candidates.append({
            **current,
            "proposed_genre": proposed_genre,
            "proposed_grouping": proposed_grouping,
            "grouping_add_terms": additions,
            "genre_evidence": genre_evidence,
            "grouping_evidence": grouping_analysis["evidence"],
            "smart_crate_effects": effects,
            "scope_root": scope_root,
            "lock_folder": lock_folder,
            "preference": 0 if genre_change and not additions else
                          1 if genre_change else 2,
        })
    return _select_stable(candidates, exclusions, maximum)


def _write_preview(batch_dir: Path, batch_id: str, rows: list[dict], exclusions: Counter,
                   scopes: dict[str, dict]) -> tuple[Path, Path]:
    md_path = batch_dir / "BATCH-PREVIEW.md"
    csv_path = batch_dir / "batch-preview.csv"
    columns = [
        "path", "sha256", "id3_version", "artist", "title", "current_genre",
        "proposed_genre", "current_grouping", "proposed_grouping", "genre_evidence",
        "grouping_evidence", "smart_crate_effects", "backup_scope",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "path": row["path"], "sha256": row["sha256"], "id3_version": row["id3_version"],
                "artist": row["artist"], "title": row["title"], "current_genre": row["genre"],
                "proposed_genre": row["proposed_genre"],
                "current_grouping": "; ".join(row["grouping"]),
                "proposed_grouping": row["proposed_grouping"],
                "genre_evidence": row["genre_evidence"],
                "grouping_evidence": "; ".join(row["grouping_evidence"]) or "none",
                "smart_crate_effects": json.dumps(row["smart_crate_effects"], ensure_ascii=False),
                "backup_scope": row["scope_root"],
            })
    lines = [
        f"# Metadata batch {batch_id}", "", "**READ-ONLY REVIEW ARTIFACT — NOT APPROVAL.**", "",
        f"Candidates: **{len(rows)}**", "",
        "| # | Track | Genre | Grouping | Evidence | Smart Crate effect | Backup scope |",
        "|---:|---|---|---|---|---|---|",
    ]
    for index, row in enumerate(rows, 1):
        smart = "; ".join(f"{e['crate']} ({e['field']} {e['effect']})" for e in row["smart_crate_effects"]) or "membership retained"
        evidence = f"Genre: {row['genre_evidence']}; Grouping: " + ("; ".join(row["grouping_evidence"]) or "no change")
        lines.append(
            f"| {index} | `{row['path']}`<br>`{row['sha256']}` · ID3v{row['id3_version']} | "
            f"`{row['genre'] or '[blank]'}` → `{row['proposed_genre'] or '[blank]'}` | "
            f"`{'; '.join(row['grouping']) or '[blank]'}` → `{row['proposed_grouping'] or '[blank]'}` | "
            f"{evidence} | {smart} | `{row['scope_root']}` |"
        )
    lines += ["", "## Backup scopes", ""]
    for scope in scopes.values():
        lines.append(
            f"- `{scope['source_root']}` — {scope['full_folder_file_count']} files, "
            f"{scope['full_folder_bytes'] / 1024**3:.3f} GiB; future T5: `{scope['backup_root']}`"
        )
    lines += ["", "## Excluded / ambiguous", ""]
    lines.extend(f"- {reason}: {count}" for reason, count in sorted(exclusions.items()))
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, csv_path


def prepare(library_root: Path, serato_root: Path, handoff_root: Path, maximum: int,
            batch_format: str, lane: str = "genre", intake_root: Path | None = None,
            confirmed_maximum: int | None = None, genre_only: bool = False,
            transcript_evidence: Path | None = None,
            event_folders: list[str] | None = None,
            remove_terms: list[str] | None = None) -> dict:
    if batch_format.casefold() != "mp3":
        raise metadata_batch.BatchRefused("automated lane is MP3-only; FLAC requires its own proven canary")
    if lane not in LANES:
        raise metadata_batch.BatchRefused(f"unknown lane {lane!r}; expected one of {LANES}")
    if genre_only and lane != "genre":
        raise metadata_batch.BatchRefused("--genre-only is only valid with --lane genre")
    if transcript_evidence is not None and lane != "clean-dirty":
        raise metadata_batch.BatchRefused("--transcript-evidence is only valid with --lane clean-dirty")
    if transcript_evidence is not None and not Path(transcript_evidence).is_file():
        raise metadata_batch.BatchRefused(f"--transcript-evidence file does not exist: {transcript_evidence}")
    if lane == "strip-event-label" and not event_folders:
        raise metadata_batch.BatchRefused("--event-folders is required for --lane strip-event-label")
    if event_folders and lane != "strip-event-label":
        raise metadata_batch.BatchRefused("--event-folders is only valid with --lane strip-event-label")
    if lane == "strip-grouping-term" and not remove_terms:
        raise metadata_batch.BatchRefused("--remove-terms is required for --lane strip-grouping-term")
    if remove_terms and lane != "strip-grouping-term":
        raise metadata_batch.BatchRefused("--remove-terms is only valid with --lane strip-grouping-term")
    if maximum < 1:
        raise metadata_batch.BatchRefused("--max must be at least 1")
    if maximum > DEFAULT_MAX_CANDIDATES and confirmed_maximum != maximum:
        raise metadata_batch.BatchRefused(
            f"--max above {DEFAULT_MAX_CANDIDATES} is an explicit operator decision; "
            f"repeat the exact limit with --confirm-max {maximum}"
        )
    if confirmed_maximum is not None and maximum <= DEFAULT_MAX_CANDIDATES:
        raise metadata_batch.BatchRefused(
            f"--confirm-max is only valid with --max above {DEFAULT_MAX_CANDIDATES}"
        )
    library_root, serato_root, handoff_root = map(lambda p: Path(p).resolve(),
                                                  (library_root, serato_root, handoff_root))
    if not library_root.is_dir() or not serato_root.is_dir():
        raise metadata_batch.BatchRefused("library and Serato roots must already exist")
    if lane == "intake":
        if intake_root is None:
            raise metadata_batch.BatchRefused("--intake-root is required for the intake lane")
        intake_root = Path(intake_root).resolve()
        if not intake_root.is_dir():
            raise metadata_batch.BatchRefused(f"intake root must already exist: {intake_root}")
        try:
            intake_root.relative_to(library_root)
        except ValueError as exc:
            raise metadata_batch.BatchRefused(
                f"intake root must be inside the library root: {intake_root}"
            ) from exc
    elif intake_root is not None:
        raise metadata_batch.BatchRefused("--intake-root is only valid with --lane intake")
    lane_tag = {
        "genre": "", "clean-dirty": "cleandirty-", "intake": "intake-",
        "strip-event-label": "stripevent-", "strip-grouping-term": "stripterm-",
    }[lane]
    batch_id = datetime.now().astimezone().strftime(f"metadata-mp3-{lane_tag}%Y%m%d-%H%M%S")
    batch_dir = handoff_root / "batches" / batch_id
    state_path = handoff_root / "current-batch.json"
    metadata_batch.start_batch(state_path, batch_id, batch_dir, handoff_root=handoff_root)
    try:
        batch_dir.mkdir(parents=True, exist_ok=False)
        if lane == "clean-dirty":
            transcript_dirty = (
                load_transcript_dirty_evidence(transcript_evidence)
                if transcript_evidence is not None else None
            )
            rows, exclusions = discover_clean_dirty(
                library_root, serato_root, maximum, transcript_dirty_evidence=transcript_dirty
            )
        elif lane == "intake":
            rows, exclusions = discover_intake(library_root, serato_root, intake_root, maximum)
        elif lane == "strip-event-label":
            rows, exclusions = discover_event_label_strip(
                library_root, serato_root, event_folders, maximum
            )
        elif lane == "strip-grouping-term":
            rows, exclusions = discover_grouping_term_strip(
                library_root, serato_root, remove_terms, maximum
            )
        else:
            rows, exclusions = discover(library_root, serato_root, maximum, genre_only=genre_only)
        if not rows:
            raise metadata_batch.BatchRefused("no eligible deterministic MP3 candidates were found")
        rows = _coalesce_nested_scope_roots(rows)
        grouped: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            grouped[row["scope_root"]].append(row)
        _refuse_overlapping_scope_roots(list(grouped))
        scopes: dict[str, dict] = {}
        plans_dir = batch_dir / "plans"
        plans_dir.mkdir()
        for index, (scope_root, scope_rows) in enumerate(sorted(grouped.items()), 1):
            scope_id = f"S{index:03d}"
            file_count, byte_count = _folder_stats(Path(scope_root))
            backup_parent = metadata_batch.T5_BACKUP_ROOT / batch_id / scope_id
            plan_path = plans_dir / f"{scope_id}.json"
            plan = {
                "mode": "preview-only",
                "generator": "prepare_metadata_batch.py",
                "batch_id": batch_id,
                "scope_id": scope_id,
                "root": scope_root,
                "rules": {"approval_required": True, "mutations_performed": 0},
                "counts": {
                    "items": len(scope_rows),
                    "genre_fixes": sum(r["genre"] != r["proposed_genre"] for r in scope_rows),
                    "grouping_fixes": sum(_grouping_changed(r) for r in scope_rows),
                },
                "items": [],
            }
            for row in scope_rows:
                item = {"path": row["path"], "sha256": row["sha256"]}
                if row["genre"] != row["proposed_genre"]:
                    item["genre_fix"] = {"from": row["genre"], "to": row["proposed_genre"]}
                if _grouping_changed(row):
                    item["grouping_fix"] = {
                        "existing": row["grouping"], "add_terms": row["grouping_add_terms"],
                        "remove_terms": row.get("grouping_remove_terms", []),
                    }
                plan["items"].append(item)
                row["scope_id"] = scope_id
            plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            validated, _ = intake.load_and_validate(plan_path, Path(scope_root))
            if len(validated) != len(scope_rows):
                raise metadata_batch.BatchRefused(f"real validator did not accept every item in {scope_id}")
            scopes[scope_id] = {
                "scope_id": scope_id,
                "source_root": scope_root,
                "lock_folder": scope_rows[0]["lock_folder"],
                "plan_path": str(plan_path.resolve()),
                "plan_sha256": metadata_batch.sha256_file(plan_path),
                "item_count": len(scope_rows),
                "full_folder_file_count": file_count,
                "full_folder_bytes": byte_count,
                "backup_root": str((backup_parent / "source").resolve()),
                "backup_manifest": str((backup_parent / "backup-manifest.json").resolve()),
                "evidence_root": str((backup_parent / "evidence").resolve()),
            }
        md_path, csv_path = _write_preview(batch_dir, batch_id, rows, exclusions, scopes)
        master_path = batch_dir / "master-manifest.json"
        master = {
            "schema_version": 1,
            "mode": "durable-metadata-batch",
            "batch_id": batch_id,
            "created_at": metadata_batch.now(),
            "repo_commit": metadata_batch.repo_head(),
            "safety_file_sha256": metadata_batch.safety_file_hashes(),
            "format": "mp3",
            "lane": lane,
            "genre_only": genre_only,
            "transcript_evidence_path": str(Path(transcript_evidence).resolve()) if transcript_evidence else None,
            "transcript_evidence_sha256": (
                metadata_batch.sha256_file(Path(transcript_evidence)) if transcript_evidence else None
            ),
            "intake_root": str(intake_root) if intake_root is not None else None,
            "event_folders": list(event_folders) if event_folders else None,
            "remove_terms": list(remove_terms) if remove_terms else None,
            "batch_limit": maximum,
            "library_root": str(library_root),
            "serato_root": str(serato_root),
            "candidate_count": len(rows),
            "genre_changes": sum(r["genre"] != r["proposed_genre"] for r in rows),
            "grouping_changes": sum(_grouping_changed(r) for r in rows),
            "backup_bytes": sum(s["full_folder_bytes"] for s in scopes.values()),
            "preview": {
                "md_path": str(md_path.resolve()), "md_sha256": metadata_batch.sha256_file(md_path),
                "csv_path": str(csv_path.resolve()), "csv_sha256": metadata_batch.sha256_file(csv_path),
            },
            "scopes": list(scopes.values()),
            "candidates": rows,
            "exclusions": dict(exclusions),
            "ready_for_human_approval": bool(rows),
        }
        master["master_sha"] = metadata_batch.canonical_sha(master)
        master_path.write_text(json.dumps(master, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        metadata_batch.transition(
            state_path, "PREVIEWED", handoff_root=handoff_root,
            updates={"master_path": str(master_path.resolve()), "master_sha": master["master_sha"]},
        )
        return master
    except BaseException as exc:
        metadata_batch.transition(state_path, "BLOCKED", reason=f"preparation failed: {exc}",
                                  handoff_root=handoff_root)
        raise


def approve(master_sha: str, approved_by: str, handoff_root: Path) -> dict:
    handoff_root = Path(handoff_root).resolve()
    state_path = handoff_root / "current-batch.json"
    state = metadata_batch.read_json(state_path)
    if state.get("state") != "PREVIEWED":
        raise metadata_batch.BatchRefused(f"approval requires PREVIEWED, found {state.get('state')}")
    if not approved_by.strip():
        raise metadata_batch.BatchRefused("--approved-by must be explicit and non-blank")
    master_path = metadata_batch.require_handoff_path(Path(state["master_path"]), handoff_root)
    master = metadata_batch.verify_master(master_path, master_sha)
    if not master.get("ready_for_human_approval"):
        raise metadata_batch.BatchRefused("batch is not marked ready for human approval")
    approval = {
        "schema_version": 1,
        "batch_id": master["batch_id"],
        "master_sha": master_sha,
        "approved_by": approved_by.strip(),
        "approved_at": metadata_batch.now(),
        "repo_commit": master["repo_commit"],
        "safety_file_sha256": master["safety_file_sha256"],
        "plan_sha256": {
            scope["scope_id"]: scope["plan_sha256"] for scope in master["scopes"]
        },
        "candidate_count": master["candidate_count"],
        "statement": "I approve exactly the immutable master manifest identified by master_sha.",
    }
    approval_path = Path(state["batch_dir"]) / "approval.json"
    metadata_batch.atomic_write_json(approval_path, approval, handoff_root=handoff_root)
    metadata_batch.transition(
        state_path, "HUMAN_APPROVED", handoff_root=handoff_root,
        updates={"approval_path": str(approval_path.resolve()), "approved_by": approved_by.strip()},
    )
    return approval


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", default="mp3")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX_CANDIDATES)
    parser.add_argument("--confirm-max", type=int,
                        help=f"Required, and must equal --max, for any limit above {DEFAULT_MAX_CANDIDATES}")
    parser.add_argument("--lane", choices=LANES, default="genre")
    parser.add_argument("--genre-only", action="store_true",
                        help="Genre lane only: propose Genre changes and hold Grouping unchanged (0 additions)")
    parser.add_argument("--transcript-evidence", type=Path,
                        help="Clean-Dirty lane only: JSON with confirmed transcript Dirty hits "
                             "(path/sha256/classification records); silence never proves Clean")
    parser.add_argument("--intake-root", type=Path,
                        help="Where new downloads land (intake lane only; must be inside the library root)")
    parser.add_argument("--event-folders", type=Path,
                        help="strip-event-label lane only: JSON file with a list of Events/<name> "
                             "subfolder names whose 'Event: <name>' Grouping term should be removed")
    parser.add_argument("--remove-terms", type=Path,
                        help="strip-grouping-term lane only: JSON file with a list of literal "
                             "Grouping terms to remove wherever they appear, library-wide")
    parser.add_argument("--library-root", type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument("--serato-root", type=Path, default=DEFAULT_SERATO)
    parser.add_argument("--approve", metavar="MASTER_SHA")
    parser.add_argument("--approved-by")
    args = parser.parse_args()
    try:
        event_folders = (
            json.loads(args.event_folders.read_text(encoding="utf-8"))
            if args.event_folders is not None else None
        )
        remove_terms = (
            json.loads(args.remove_terms.read_text(encoding="utf-8"))
            if args.remove_terms is not None else None
        )
        if args.approve:
            if not args.approved_by:
                raise metadata_batch.BatchRefused("--approved-by is required with --approve")
            if (args.lane != "genre" or args.intake_root or args.confirm_max is not None
                    or args.genre_only or args.transcript_evidence or event_folders or remove_terms):
                raise metadata_batch.BatchRefused(
                    "--lane/--intake-root/--confirm-max/--genre-only/--transcript-evidence/"
                    "--event-folders/--remove-terms are only valid when preparing"
                )
            result = approve(args.approve, args.approved_by, metadata_batch.HANDOFF_ROOT)
            print(json.dumps({"HUMAN_APPROVED": True, **result}, indent=2))
            return 0
        if args.approved_by:
            raise metadata_batch.BatchRefused("--approved-by is only valid with --approve")
        result = prepare(args.library_root, args.serato_root, metadata_batch.HANDOFF_ROOT,
                         args.max, args.format, lane=args.lane, intake_root=args.intake_root,
                         confirmed_maximum=args.confirm_max, genre_only=args.genre_only,
                         transcript_evidence=args.transcript_evidence, event_folders=event_folders,
                         remove_terms=remove_terms)
        smart_count = sum(len(row["smart_crate_effects"]) for row in result["candidates"])
        print(f"BATCH ID: {result['batch_id']}")
        print(f"LANE: {result['lane']}")
        print(f"GENRE ONLY: {result['genre_only']}")
        print(f"CANDIDATES: {result['candidate_count']}")
        print(f"GENRE CHANGES: {result['genre_changes']}")
        print(f"GROUPING CHANGES: {result['grouping_changes']}")
        print(f"BACKUP SIZE: {result['backup_bytes'] / 1024**3:.3f} GiB")
        print(f"SMART CRATE CHANGES: {smart_count}")
        print(f"MASTER SHA: {result['master_sha']}")
        print("READY FOR HUMAN APPROVAL" if result["ready_for_human_approval"] else "NOT READY FOR HUMAN APPROVAL")
        return 0 if result["ready_for_human_approval"] else 2
    except metadata_batch.BatchRefused as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
