#!/usr/bin/env python3
"""Build a PREVIEW-ONLY Genre refinement plan: Pass A (restore pre-overwrite
evidence) + Pass B (explicit filename subgenres), computed independently and
reconciled without a precedence rule between them.

Writes reports only. Never moves, renames, deletes, or retags a file.
Never acquires a folder_lock -- read-only plan construction doesn't need one.

--------------------------------------------------------------------------
PASS A -- restore pre-overwrite evidence
--------------------------------------------------------------------------
Six folders in this library had every track's Genre tag forced to one fixed
value via `--assume-genre` (or the older one-off scripts' equivalent) during
the original version+genre sort, discarding whatever tag was there before,
regardless of its content:

    Hip Hop   -> "Hip-Hop"   (5,888 tracks; the two build_version_genre_plan.py
                              undo logs use "assumed" confidence explicitly)
    Dub       -> "Dub"       (1 track)
    Rave      -> "Rave"      (2 tracks; proven by a 'white, edit' -> 'Rave'
                              forced overwrite that no per-track classifier
                              would ever produce)
    Ambient   -> "Ambient"   (1 track)
    Wedding   -> "Latin"     (1 track; proven by a blank -> 'Latin' forced
                              write -- per-track classification never invents
                              a non-blank genre from a blank tag)
    metro     -> "Jungle"    (1 track, now living under white club/REMIXES &
                              EDITS/Jungle/ after another session folded metro
                              into white club; "jungle" is not in
                              version-genre-rules.json's tag_aliases, so a
                              per-track classifier could not have produced
                              "Jungle" from it either)

The other six folders (Events, RAP RN, 90S HIPHOP R&B CLEAN DIRTY,
music to move, Nigeria, belarusian_electronic) used per-track tag/keyword
classification from the start -- proven by finding at least one item in each
folder's original plan.json where the proposed genre is None/unresolved
(assume_genre always returns a fixed non-None value unconditionally; a
folder that ever left a track unresolved could not have used it). Pass A is
not applicable to these; only Pass B runs there.

For each Pass-A-eligible track: recover its pre-overwrite Genre from the
original sort's undo log (the "old_genre" field, written before the
assumed value replaced it), then resolve THAT value through the exact same
tag_aliases lookup already trusted elsewhere in this project (isolated by
calling classify_genre() with an empty filename, so the filename_keywords
branch can never fire here -- Pass A tests the tag, not the name). Propose a
change only when the recovered value is non-blank, resolves with real "tag"
confidence, and differs from the track's current live Genre. Never invent a
new alias: an old value that doesn't resolve is recorded as REVIEW with its
raw text and frequency, not guessed.

--------------------------------------------------------------------------
PASS B -- explicit filename subgenres
--------------------------------------------------------------------------
Across all 12 folders, re-run ONLY the existing filename_keywords rules from
references/version-genre-rules.json against every track's filename,
regardless of its current Genre. A rule fires only on an explicit substring
match (e.g. "ghettotech" literally in the filename) -- no synonyms, no new
categories, nothing implicit. Isolated by calling classify_genre() with an
empty existing-genre value, so the tag_aliases branch can never fire here.

--------------------------------------------------------------------------
COMBINING -- no precedence rule
--------------------------------------------------------------------------
Pass A and Pass B are computed completely independently for every track.
combined_status is one of:
    A_ONLY          -- only Pass A proposed a change
    B_ONLY          -- only Pass B proposed a change
    A_AND_B_SAME    -- both proposed, and they agree
    A_B_CONFLICT    -- both proposed, and they DISAGREE (proposed_genre left
                       blank -- this script does not choose between them)
    REVIEW          -- Pass A found non-blank evidence it could not resolve,
                       and Pass B found nothing
    NO_CHANGE       -- neither pass found anything actionable
"""

from __future__ import annotations

# Preview-only: builds a plan for human review. Never mutates library media.
SAFETY_CLASSIFICATION = "preview-only"

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_version_genre_plan import classify_genre, load_genre_rules  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
SURVEY = HERE / "handoff" / "session-2026-08-15" / "survey"
YEAR_BPM_SURVEY = SURVEY / "year-bpm"

# folder label -> Pass A undo log(s) (None if not assumed-genre / Pass A N/A)
PASS_A_UNDO_LOGS = {
    "Hip Hop": [
        SURVEY / "hip-hop" / "hip-hop-undo-20260815-183117.jsonl",
        SURVEY / "hip-hop" / "hip-hop-remainder-undo-20260815-183812.jsonl",
    ],
    "Dub": [SURVEY / "dub" / "dub-undo-20260815-165621.jsonl"],
    "Rave": [SURVEY / "rave" / "rave-undo-20260815-165945.jsonl"],
    "Ambient": [SURVEY / "ambient" / "ambient-undo-20260815-165036.jsonl"],
    "Wedding": [SURVEY / "wedding" / "wedding-undo-20260815-165156.jsonl"],
    "metro": [SURVEY / "metro" / "metro-undo-20260815-165507.jsonl"],
}

# folder label -> fresh inventory.csv from the Year/BPM pass (this same session,
# ~30 min old at the time this script was authored -- reused as the Pass B
# filename/current-genre listing to avoid rescanning ~10k files; the actual
# proposal candidates are still freshly re-verified below, live, before
# appearing in the plan.
PASS_B_INVENTORIES = {
    "Hip Hop": YEAR_BPM_SURVEY / "hip-hop" / "inventory.csv",
    "Events": YEAR_BPM_SURVEY / "events" / "inventory.csv",
    "RAP RN": YEAR_BPM_SURVEY / "rap-rn" / "inventory.csv",
    "90S HIPHOP R&B CLEAN DIRTY": YEAR_BPM_SURVEY / "90s-hiphop-r-b-clean-dirty" / "inventory.csv",
    "Ambient": YEAR_BPM_SURVEY / "ambient" / "inventory.csv",
    "Wedding": YEAR_BPM_SURVEY / "wedding" / "inventory.csv",
    "belarusian_electronic": YEAR_BPM_SURVEY / "belarusian_electronic" / "inventory.csv",
    "Dub": YEAR_BPM_SURVEY / "dub" / "inventory.csv",
    "Rave": YEAR_BPM_SURVEY / "rave" / "inventory.csv",
    "Nigeria": YEAR_BPM_SURVEY / "nigeria" / "inventory.csv",
    "music to move": YEAR_BPM_SURVEY / "music-to-move" / "inventory.csv",
}
# metro has no per-folder inventory (single track, checked directly, folded
# into white club by another session).
METRO_PATH = (
    "/Users/djb.a.e/Music/DJ-Music/Music/white club/REMIXES & EDITS/Jungle/"
    "Way 2 Sexy, Future, Young Thug, Sørensen Funky - Drake (Sørensen Funky Remix).mp3"
)

ALL_FOLDERS = [
    "music to move", "Events", "RAP RN", "90S HIPHOP R&B CLEAN DIRTY", "Ambient",
    "Wedding", "belarusian_electronic", "metro", "Dub", "Rave", "Nigeria", "Hip Hop",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_live_genre(path: Path) -> str:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return ""
    values = tags.getall("TCON")
    return str(values[0]).strip() if values else ""


def pass_a_resolve(old_genre: str, rules: dict) -> tuple[str | None, str, str]:
    """Isolated to the tag_aliases branch only: empty filename can never
    trigger filename_keywords, so this exercises exactly the same
    trusted lookup classify_genre() uses for a real per-track tag,
    applied here to a *recovered historical* tag instead of a live one."""
    return classify_genre({"filename": "", "genre": old_genre}, rules, None)


def pass_b_resolve(filename: str, rules: dict) -> tuple[str | None, str, str, str | None]:
    """Isolated to the filename_keywords branch only: empty existing-genre
    can never trigger the tag_aliases branch (blank routes straight to
    review, which we ignore here). Also returns the exact rule's `contains`
    key for provenance, which classify_genre() itself doesn't expose."""
    lower_name = filename.casefold()
    for rule in rules.get("filename_keywords", []):
        if rule["contains"] in lower_name:
            return rule["genre"], rule["evidence"], "high", rule["contains"]
    return None, "no filename_keywords rule matched", "none", None


def is_compoundish(raw: str) -> bool:
    """Reporting-only heuristic (never used to decide a change) to split the
    'unmapped old tags' review bucket into 'ambiguous/compound' vs 'a single
    clean value that simply isn't in the alias table yet', for the exec
    counts and section 4 of the output."""
    return any(sep in raw for sep in (",", "/", "&", ";", " and ", " + "))


def load_pass_a_candidates(folder: str, logs: list[Path]) -> list[dict]:
    seen_paths = set()
    candidates = []
    for log in logs:
        for line in log.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            dest = record["destination"]
            if folder == "metro":
                # another session folded metro into white club after this undo log
                # was written; the log's destination path is now stale.
                dest = METRO_PATH
            if dest in seen_paths:
                continue  # a track can appear in more than one historical undo log for the same folder
            seen_paths.add(dest)
            candidates.append(
                {
                    "folder": folder,
                    "path": dest,
                    "old_genre": record.get("old_genre", ""),
                    "undo_log": str(log),
                    "undo_index": record.get("index"),
                }
            )
    return candidates


def load_pass_b_rows(folder: str, inventory_csv: Path) -> list[dict]:
    with inventory_csv.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    return [
        {"folder": folder, "path": row["full_path"], "filename": row["filename"], "inventory_genre": row["genre"], "inventory_sha256": row["sha256"]}
        for row in rows
        if row["file_type"] == "mp3"  # Genre write path is mp3/flac; flac is not present in any of these 12 folders
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    rules = load_genre_rules(None)

    errors = []

    # ---- Pass A candidates ----
    pass_a_by_path: dict[str, dict] = {}
    for folder, logs in PASS_A_UNDO_LOGS.items():
        for cand in load_pass_a_candidates(folder, logs):
            path = Path(cand["path"])
            if not path.is_file():
                errors.append(f"Pass A: referenced path does not exist: {cand['path']} (folder {folder})")
                continue
            old_genre = cand["old_genre"].strip()
            resolved, evidence, confidence = pass_a_resolve(old_genre, rules)
            alias_key = old_genre.casefold() if old_genre else None
            cand.update(
                {
                    "pass_a_old_genre": old_genre,
                    "pass_a_resolved": resolved,
                    "pass_a_confidence": confidence,
                    "pass_a_alias_key": alias_key,
                    "pass_a_compoundish": is_compoundish(old_genre) if old_genre else False,
                }
            )
            pass_a_by_path[cand["path"]] = cand

    # ---- Pass B candidates ----
    pass_b_by_path: dict[str, dict] = {}
    for folder, inv_csv in PASS_B_INVENTORIES.items():
        if not inv_csv.is_file():
            errors.append(f"Pass B: missing inventory for folder {folder}: {inv_csv}")
            continue
        for row in load_pass_b_rows(folder, inv_csv):
            resolved, evidence, confidence, rule_key = pass_b_resolve(row["filename"], rules)
            if resolved is None:
                continue  # no signal at all -- not tracked as a candidate row
            path = Path(row["path"])
            if not path.is_file():
                errors.append(f"Pass B: referenced path does not exist: {row['path']} (folder {folder})")
                continue
            pass_b_by_path[row["path"]] = {
                **row,
                "pass_b_resolved": resolved,
                "pass_b_evidence": evidence,
                "pass_b_rule": rule_key,
            }
    # metro: single known file, checked directly (no per-folder inventory built for it)
    if Path(METRO_PATH).is_file():
        resolved, evidence, confidence, rule_key = pass_b_resolve(Path(METRO_PATH).name, rules)
        if resolved is not None:
            pass_b_by_path[METRO_PATH] = {
                "folder": "metro",
                "path": METRO_PATH,
                "filename": Path(METRO_PATH).name,
                "inventory_genre": None,
                "inventory_sha256": None,
                "pass_b_resolved": resolved,
                "pass_b_evidence": evidence,
                "pass_b_rule": rule_key,
            }
    else:
        errors.append(f"Pass B: metro's known file no longer exists at expected path: {METRO_PATH}")

    # ---- union of paths worth evaluating together, then live re-verification ----
    all_paths = sorted(set(pass_a_by_path) | set(pass_b_by_path))
    if len(all_paths) != len(set(all_paths)):
        errors.append("duplicate plan entries detected after union")

    items = []
    stats_a = {"evaluated": len(pass_a_by_path), "proposed": 0, "unchanged": 0, "ambiguous_compound": 0, "unmapped": 0, "blank_old_tag": 0}
    stats_b = {"evaluated": 0, "keyword_matches": len(pass_b_by_path), "proposed": 0, "unchanged": 0}
    combined_counts = {"A_ONLY": 0, "B_ONLY": 0, "A_AND_B_SAME": 0, "A_B_CONFLICT": 0, "REVIEW": 0, "NO_CHANGE": 0}
    unmapped_old_tags: dict[str, dict] = {}
    rule_evidence: dict[str, dict] = {}
    genre_labels_a: dict[str, int] = {}
    genre_labels_b: dict[str, int] = {}

    # Pass B "evaluated" = every mp3 across the 12 folders, regardless of match
    for folder, inv_csv in PASS_B_INVENTORIES.items():
        if inv_csv.is_file():
            stats_b["evaluated"] += sum(1 for _ in load_pass_b_rows(folder, inv_csv))
    stats_b["evaluated"] += 1  # metro

    for path_str in all_paths:
        path = Path(path_str)
        a = pass_a_by_path.get(path_str)
        b = pass_b_by_path.get(path_str)
        folder = (a or b)["folder"]

        # cheap tag-only read for every candidate -- classification doesn't need a hash
        try:
            current_genre_now = read_live_genre(path)
        except OSError as exc:
            errors.append(f"could not read tags for {path_str}: {exc}")
            continue

        # ---- Pass A outcome for this track ----
        pa_status, pa_value = "NOT_APPLICABLE", None
        if a:
            old_genre = a["pass_a_old_genre"]
            resolved = a["pass_a_resolved"]
            confidence = a["pass_a_confidence"]
            if not old_genre:
                pa_status = "NO_CHANGE"
                stats_a["unchanged"] += 1
                stats_a["blank_old_tag"] += 1
            elif confidence == "tag" and resolved == current_genre_now:
                pa_status = "NO_CHANGE"
                stats_a["unchanged"] += 1
            elif confidence == "tag" and resolved != current_genre_now:
                pa_status, pa_value = "PROPOSED", resolved
                stats_a["proposed"] += 1
                genre_labels_a[resolved] = genre_labels_a.get(resolved, 0) + 1
            else:
                pa_status = "REVIEW"
                if a["pass_a_compoundish"]:
                    stats_a["ambiguous_compound"] += 1
                else:
                    stats_a["unmapped"] += 1
                key = old_genre
                bucket = unmapped_old_tags.setdefault(key, {"count": 0, "examples": []})
                bucket["count"] += 1
                if len(bucket["examples"]) < 3:
                    bucket["examples"].append(f"{folder}/{path.name}")

        # ---- Pass B outcome for this track ----
        pb_status, pb_value = "NOT_APPLICABLE", None
        if b:
            pb_status, pb_value = "PROPOSED", b["pass_b_resolved"]
            # filename-rule evidence (section 5) tracks every rule HIT, regardless of
            # whether it would actually change anything -- this is how the user verifies
            # the keyword rules are behaving as intended, independent of proposal status.
            key = f"{b['pass_b_rule']} -> {b['pass_b_resolved']}"
            rev = rule_evidence.setdefault(key, {"rule": b["pass_b_rule"], "genre": b["pass_b_resolved"], "count": 0, "examples": []})
            rev["count"] += 1
            if len(rev["examples"]) < 5:
                rev["examples"].append(path.name)
            if pb_value == current_genre_now:
                pb_status = "NO_CHANGE"
                stats_b["unchanged"] += 1
            else:
                stats_b["proposed"] += 1
                genre_labels_b[pb_value] = genre_labels_b.get(pb_value, 0) + 1

        # ---- combine, no precedence rule ----
        a_proposed = pa_status == "PROPOSED"
        b_proposed = pb_status == "PROPOSED" and pb_value != current_genre_now
        if a_proposed and b_proposed:
            combined = "A_AND_B_SAME" if pa_value == pb_value else "A_B_CONFLICT"
        elif a_proposed:
            combined = "A_ONLY"
        elif b_proposed:
            combined = "B_ONLY"
        elif pa_status == "REVIEW":
            combined = "REVIEW"
        else:
            combined = "NO_CHANGE"
        combined_counts[combined] += 1

        if combined == "NO_CHANGE":
            continue  # not interesting enough for the per-track table

        # live re-verification, only for the subset that actually makes the table
        try:
            current_sha256 = sha256_file(path)
        except OSError as exc:
            errors.append(f"could not hash {path_str}: {exc}")
            continue
        inv_genre = b["inventory_genre"] if b else None
        inv_sha = b["inventory_sha256"] if b else None
        if inv_sha and inv_sha != current_sha256:
            errors.append(f"file changed since Year/BPM inventory snapshot, skipping until re-verified: {path_str}")
            continue
        if inv_genre is not None and inv_genre != current_genre_now:
            errors.append(f"Genre tag drifted since inventory snapshot: {path_str}: {inv_genre!r} != {current_genre_now!r}")
            continue

        proposed_genre = None
        if combined == "A_ONLY":
            proposed_genre = pa_value
        elif combined == "B_ONLY":
            proposed_genre = pb_value
        elif combined == "A_AND_B_SAME":
            proposed_genre = pa_value

        items.append(
            {
                "folder": folder,
                "path": path_str,
                "filename": path.name,
                "current_genre": current_genre_now,
                "pre_overwrite_genre": a["pass_a_old_genre"] if a else None,
                "pass_a_result": pa_value,
                "pass_a_evidence": (f"pre-overwrite genre tag {a['pass_a_old_genre']!r} resolved via tag_aliases[{a['pass_a_alias_key']!r}]" if a and pa_status == "PROPOSED" else (f"pre-overwrite genre tag {a['pass_a_old_genre']!r} did not resolve" if a and pa_status == "REVIEW" else None)),
                "pass_a_confidence": a["pass_a_confidence"] if a else None,
                "pass_a_status": pa_status,
                "filename_keyword": b["pass_b_rule"] if b else None,
                "pass_b_result": pb_value if b else None,
                "pass_b_rule": (f"filename_keywords[contains={b['pass_b_rule']!r}] -> {b['pass_b_resolved']}" if b else None),
                "pass_b_status": pb_status,
                "combined_status": combined,
                "proposed_genre": proposed_genre,
                "reason": {
                    "A_ONLY": "Pass A restored evidence; Pass B found no filename signal",
                    "B_ONLY": "Pass B matched an explicit filename rule; Pass A found no (or no differing) prior evidence",
                    "A_AND_B_SAME": "Pass A and Pass B independently agree",
                    "A_B_CONFLICT": "Pass A and Pass B disagree -- needs a human decision, no proposal applied",
                    "REVIEW": "Pass A found non-blank prior evidence that does not resolve through existing aliases",
                }.get(combined, ""),
                "provenance": {
                    "undo_log": a["undo_log"] if a else None,
                    "undo_index": a["undo_index"] if a else None,
                    "pass_a_alias_key": a["pass_a_alias_key"] if a else None,
                    "pass_b_filename_keyword": b["pass_b_rule"] if b else None,
                    "current_sha256": current_sha256,
                },
            }
        )

    # ---- validation ----
    proposed_with_blank_missing = [
        i for i in items if i["combined_status"] in ("REVIEW", "A_B_CONFLICT") and i["proposed_genre"] is not None
    ]
    if proposed_with_blank_missing:
        errors.append(f"{len(proposed_with_blank_missing)} REVIEW/A_B_CONFLICT items unexpectedly carry a proposed_genre")

    paths_seen = [i["path"] for i in items]
    if len(paths_seen) != len(set(paths_seen)):
        errors.append("duplicate plan entries detected in final item list")

    plan = {
        "mode": "preview-only",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rules": {
            "approval_required": True,
            "no_delete": True,
            "no_move": True,
            "no_rename": True,
            "no_grouping_change": True,
            "genre_metadata_only": True,
            "no_precedence_between_passes": True,
        },
        "pass_a_folders": list(PASS_A_UNDO_LOGS),
        "pass_b_folders": ALL_FOLDERS,
        "stats_pass_a": stats_a,
        "stats_pass_b": stats_b,
        "combined_counts": combined_counts,
        "total_unique_tracks_affected": sum(v for k, v in combined_counts.items() if k != "NO_CHANGE"),
        "label_breakdown_pass_a": dict(sorted(genre_labels_a.items(), key=lambda kv: -kv[1])),
        "label_breakdown_pass_b": dict(sorted(genre_labels_b.items(), key=lambda kv: -kv[1])),
        "unmapped_old_tags": dict(sorted(unmapped_old_tags.items(), key=lambda kv: -kv[1]["count"])),
        "filename_rule_evidence": dict(sorted(rule_evidence.items(), key=lambda kv: -kv[1]["count"])),
        "errors": errors,
        "items": items,
    }
    plan_path = out_dir / "genre-refinement-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    with (out_dir / "genre-refinement-full-table.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "folder", "path", "current_genre", "pre_overwrite_genre", "pass_a_result", "pass_a_evidence",
            "pass_a_confidence", "filename_keyword", "pass_b_result", "pass_b_rule", "combined_status",
            "proposed_genre", "reason",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for item in items:
            writer.writerow(item)

    print(
        json.dumps(
            {
                "plan": str(plan_path),
                "csv": str(out_dir / "genre-refinement-full-table.csv"),
                "stats_pass_a": stats_a,
                "stats_pass_b": stats_b,
                "combined_counts": combined_counts,
                "total_unique_tracks_affected": plan["total_unique_tracks_affected"],
                "errors": errors,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
