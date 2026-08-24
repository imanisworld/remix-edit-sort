#!/usr/bin/env python3
"""One-time, batch-bound continuation for metadata-mp3-cleandirty-20260823-181741 only.

S001-S015 of this batch applied and verified cleanly (297 files). S016 then
hit a real, narrow edge case: for exactly one sibling pair of files, the
mutation engine's own write path detected a Serato-encoding-only change on
write and deliberately skipped the tag write out of caution -- correct,
intended behavior, not a bug to patch. The post-apply scope verifier wasn't
written to recognize that skip, saw the Grouping unchanged when it expected
an addition, and correctly refused rather than report false success. 14 of
S016's 16 files applied and verified correctly; the 2 held files are
confirmed byte-identical to their pre-batch originals.

This script does not touch mutation_engine.py's skip behavior and does not
retry held files -- they remain a deliberate HOLD, to be handled later as
their own tiny edge-case lane. Its job is to resume S017-S041 (25 scopes)
without reprocessing S001-S015 or S016, and to close the batch out to
COMPLETE with every hold explicitly documented rather than silently marked
as if fully successful.

A second, real instance of the exact S016 edge case hit S017 (a genuinely
new file, not a retry of S016's held pair) before this standing rule
existed, so S017 is re-classified below from its existing evidence rather
than re-run, and the operator gave this script a standing auto-hold rule
for every scope after it (S018-S041): a Grouping mismatch is auto-held,
without stopping to ask, ONLY when the mutation engine's own journal record
proves it is exactly this safety condition -- tag_skipped, tag_changed is
false, before/after sha256 identical, and the scope's recovery journal is
clean with no partial mutation. Any other verification failure (Genre, ID3
version, audio payload, Serato frames, an unrecognized Grouping mismatch,
or a dirty/incomplete journal) still stops the whole run immediately, per
the operator's own words: "Stop only if a different failure class appears."

There is no generic "resume from scope N" mechanism in this codebase by
design -- metadata_batch.py's state machine has no forward edge out of
BLOCKED for any batch. This script is deliberately NOT that generic
mechanism. Every identifying value below is a hardcoded constant bound to
this one batch; nothing here is reusable for any other recovery.

Usage:
    python3 scripts/recover_metadata_mp3_cleandirty_20260823_181741.py create-approval \\
        --approved-by "name"
    python3 scripts/recover_metadata_mp3_cleandirty_20260823_181741.py apply
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "engine-user"

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import apply_approved_batch
import apply_new_music_intake_plan as intake
import backup_manifest
import lock_guard
import metadata_batch
import mutation_engine
import prepare_metadata_batch  # noqa: F401 -- read_track() used to independently re-verify live state

BATCH_ID = "metadata-mp3-cleandirty-20260823-181741"
ORIGINAL_MASTER_SHA = "5df38f089bdefb32870a9f90a4b9451286f99f5690315b64868f182cac1757b1"
COMPLETED_SCOPE_IDS: tuple[str, ...] = tuple(f"S{n:03d}" for n in range(1, 16))  # S001-S015
PARTIAL_SCOPE_ID = "S016"
# S017 already hit the exact same edge case (a new file, not a retry of S016's
# pair) before the standing auto-hold rule existed below. It is re-classified
# from its existing evidence, not re-run -- re-running would deterministically
# reproduce the identical, already-proven skip.
KNOWN_AUTO_HELD_SCOPE_IDS: tuple[str, ...] = ("S017",)
RESUME_SCOPE_IDS: tuple[str, ...] = tuple(f"S{n:03d}" for n in range(18, 42))  # S018-S041
HELD_ITEMS: dict[str, str] = {
    "/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/ACAPELLAS/Hip-Hop/"
    "Common vs Tag Team - Universal Mind Control (UMC) (DJ Nasa Acapella Out) (Clean Extended).mp3":
        "0503e8fef58dee191dfbb1a29c3b31695432bb97467cffe31bf70c8bb36171fa",
    "/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/ACAPELLAS/Hip-Hop/"
    "Common vs Tag Team - Universal Mind Control (UMC) (DJ Nasa Acapella Out) (Dirty Extended).mp3":
        "de7aec5cce7690ba60ddcee1fa88466441d72b330df41eee1df0193c420ebd14",
}
# The mutation engine's own journal-recorded reason for this one, specific,
# intentional safety skip. Only an exact match makes a Grouping mismatch
# auto-holdable; anything else is a different failure class and must stop.
SAFE_SKIP_REASON = "Serato encoding-only change on write; skipped out of caution"

assert set(RESUME_SCOPE_IDS).isdisjoint(COMPLETED_SCOPE_IDS)
assert PARTIAL_SCOPE_ID not in COMPLETED_SCOPE_IDS and PARTIAL_SCOPE_ID not in RESUME_SCOPE_IDS
assert set(KNOWN_AUTO_HELD_SCOPE_IDS).isdisjoint(set(COMPLETED_SCOPE_IDS) | set(RESUME_SCOPE_IDS) | {PARTIAL_SCOPE_ID})


def _recovery_approval_path(handoff_root: Path) -> Path:
    return Path(handoff_root) / "batches" / BATCH_ID / "recovery-approval.json"


def _load_master_and_state(handoff_root: Path) -> tuple[Path, dict, dict]:
    state_path = Path(handoff_root) / "current-batch.json"
    state = metadata_batch.read_json(state_path)
    if state.get("batch_id") != BATCH_ID:
        raise metadata_batch.BatchRefused(
            f"current-batch.json is not {BATCH_ID}: {state.get('batch_id')!r}"
        )
    master_path = metadata_batch.require_handoff_path(Path(state["master_path"]), handoff_root)
    master = metadata_batch.verify_master(master_path, ORIGINAL_MASTER_SHA)
    return state_path, state, master


def _scope_by_id(master: dict, scope_id: str) -> dict:
    for scope in master["scopes"]:
        if scope["scope_id"] == scope_id:
            return scope
    raise metadata_batch.BatchRefused(f"master has no scope {scope_id}")


def _require_completed(state: dict, scope_id: str) -> dict:
    entry = state.get("scopes", {}).get(scope_id, {})
    recovery = entry.get("recovery") or {}
    if entry.get("state") != "VERIFIED" or entry.get("execution") != "COMPLETED":
        raise metadata_batch.BatchRefused(
            f"{scope_id} is not VERIFIED/COMPLETED: {entry.get('state')}/{entry.get('execution')}"
        )
    if not recovery.get("clean") or recovery.get("rollback_failures"):
        raise metadata_batch.BatchRefused(f"{scope_id} recovery record is not clean: {recovery}")
    return entry


def _require_pending(state: dict, scope_id: str, expected_state: str, expected_execution: str) -> dict:
    entry = state.get("scopes", {}).get(scope_id, {})
    if entry.get("state") != expected_state or entry.get("execution") != expected_execution:
        raise metadata_batch.BatchRefused(
            f"{scope_id} is not {expected_state}/{expected_execution}: "
            f"{entry.get('state')}/{entry.get('execution')}"
        )
    return entry


def _reverify_s016_live(master: dict) -> dict:
    """Fresh live re-read of every S016 plan item: the 14 non-held items
    must show their proposed Grouping addition already present, and the
    2 held items must be byte-identical to their pre-batch sha256. Refuses
    on any drift rather than trusting the earlier one-time investigation."""
    scope = _scope_by_id(master, PARTIAL_SCOPE_ID)
    plan = json.loads(Path(scope["plan_path"]).read_text(encoding="utf-8"))
    checked_ok = []
    for item in plan["items"]:
        path = Path(item["path"])
        fix = item.get("grouping_fix")
        if not fix:
            raise metadata_batch.BatchRefused(f"S016 plan item has no grouping_fix: {path}")
        if str(path) in HELD_ITEMS:
            if item["sha256"] != HELD_ITEMS[str(path)]:
                raise metadata_batch.BatchRefused(f"held item sha256 in plan disagrees with hardcoded binding: {path}")
            live = prepare_metadata_batch.read_track(path)
            if live["sha256"] != HELD_ITEMS[str(path)]:
                raise metadata_batch.BatchRefused(
                    f"held item is no longer byte-identical to its pre-batch original: {path}"
                )
            continue
        live = prepare_metadata_batch.read_track(path)
        expected_terms = set(fix["add_terms"])
        if not expected_terms.issubset(set(live["grouping"])):
            raise metadata_batch.BatchRefused(
                f"S016 non-held item does not show its expected Grouping addition live: {path}"
            )
        checked_ok.append(str(path))
    if set(HELD_ITEMS) - {item["path"] for item in plan["items"] if item["path"] in HELD_ITEMS}:
        raise metadata_batch.BatchRefused("a hardcoded held item is missing from the S016 plan")
    if len(checked_ok) != len(plan["items"]) - len(HELD_ITEMS):
        raise metadata_batch.BatchRefused("S016 non-held item count does not match plan minus held items")
    return {"non_held_verified": checked_ok, "held_items": list(HELD_ITEMS)}


def _classify_scope_failure(scope: dict, master: dict, evidence_root: Path, t5_volume: Path) -> tuple[list[str], list[str], dict]:
    """Independently re-verify every candidate in one scope's own evidence.

    A Grouping mismatch is only ever treated as an eligible auto-hold when
    the scope's own recovery journal proves it is exactly the known,
    intentional Serato-encoding-caution skip: tag_skipped, tag_changed is
    false, before/after sha256 identical on the live file. Any other
    mismatch -- Genre, ID3 version, audio payload, Serato frames, an
    unrecognized/unrecorded Grouping mismatch, an unauthorized non-candidate
    change, or a dirty/incomplete recovery journal -- raises unchanged. That
    is a different failure class and this function never swallows it.
    Returns (held_paths, verified_paths, recovery_report).
    """
    evidence_root = metadata_batch.require_t5_path(Path(evidence_root), t5_volume)
    # "*-recovery-*.jsonl" also matches the undo log ("...-recovery-undo-....jsonl",
    # since --label already ends in "-recovery") -- exclude it explicitly rather
    # than assume a count, which silently misclassified two files as one before.
    journals = sorted(
        path for path in evidence_root.glob("*-recovery-*.jsonl") if "-undo-" not in path.name
    )
    if len(journals) != 1:
        raise metadata_batch.BatchRefused(
            f"expected exactly one recovery journal in {evidence_root}, found {len(journals)}"
        )
    journal_path = journals[0]
    recovery = mutation_engine.recover(journal_path)
    if recovery.get("rollback_failures"):
        raise metadata_batch.RollbackFailed(f"ROLLBACK_FAILED in {scope['scope_id']}: {recovery['in_flight']}")
    if not recovery.get("clean") or not recovery.get("runs") or not all(
        run.get("complete") for run in recovery["runs"].values()
    ):
        raise metadata_batch.BatchRefused(
            f"recovery journal is not clean and complete in {scope['scope_id']}: {recovery}"
        )

    records = [
        json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    completed_by_path = {record["source"]: record for record in records if record.get("type") == "completed"}

    scope_rows = [row for row in master["candidates"] if row["scope_id"] == scope["scope_id"]]
    candidate_paths = {Path(row["path"]).resolve() for row in scope_rows}
    held_paths: list[str] = []
    verified_paths: list[str] = []
    for row in scope_rows:
        path = Path(row["path"])
        if intake.read_genre(path) != row["proposed_genre"]:
            raise metadata_batch.BatchRefused(f"post-apply Genre mismatch: {path} -- not an eligible auto-hold")
        if intake.sha256_bytes(intake.audio_payload(path)) != row["audio_payload_sha256"]:
            raise metadata_batch.BatchRefused(
                f"post-apply audio payload changed: {path} -- not an eligible auto-hold"
            )
        if intake.serato_digest(path) != row["serato_frames_sha256"]:
            raise metadata_batch.BatchRefused(
                f"post-apply Serato frames changed: {path} -- not an eligible auto-hold"
            )
        grouping, major = intake.read_grouping(path)
        if major != row["id3_major"]:
            raise metadata_batch.BatchRefused(
                f"post-apply ID3 version changed: {path} -- not an eligible auto-hold"
            )
        proposed_parts = [part.strip() for part in row["proposed_grouping"].split(";") if part.strip()]
        if grouping == proposed_parts:
            verified_paths.append(str(path))
            continue

        record = completed_by_path.get(str(path))
        if record is None:
            raise metadata_batch.BatchRefused(
                f"Grouping mismatch on {path} has no matching completed journal record -- not an eligible auto-hold"
            )
        if not (
            record.get("tag_skipped") is True
            and record.get("tag_changed") is False
            and record.get("tag_skip_reason") == SAFE_SKIP_REASON
            and record.get("before_sha256")
            and record.get("before_sha256") == record.get("after_sha256")
        ):
            raise metadata_batch.BatchRefused(
                f"Grouping mismatch on {path} does not match the known safe-skip signature "
                f"-- not an eligible auto-hold: {record}"
            )
        live_sha256 = metadata_batch.sha256_file(path)
        if live_sha256 != record["after_sha256"]:
            raise metadata_batch.BatchRefused(
                f"held candidate {path} is no longer byte-identical to its journaled state "
                "-- not an eligible auto-hold"
            )
        held_paths.append(str(path))

    source_root, backup_root = Path(scope["source_root"]), Path(scope["backup_root"])
    source_index = apply_approved_batch._canonical_index(
        apply_approved_batch._walk(source_root), "source", scope["scope_id"]
    )
    backup_index = apply_approved_batch._canonical_index(
        apply_approved_batch._walk(backup_root), "backup", scope["scope_id"]
    )
    if set(source_index) != set(backup_index):
        raise metadata_batch.BatchRefused(
            f"unauthorized missing/extra file in scope {scope['scope_id']} -- not an eligible auto-hold"
        )
    unauthorized = []
    for key, (relative, source) in source_index.items():
        if source.resolve() not in candidate_paths:
            backup_path = backup_index[key][1]
            if metadata_batch.sha256_file(source) != metadata_batch.sha256_file(backup_path):
                unauthorized.append(str(source))
    if unauthorized:
        raise metadata_batch.BatchRefused(
            f"unauthorized non-candidate changes: {unauthorized} -- not an eligible auto-hold"
        )

    if not held_paths:
        raise metadata_batch.BatchRefused(
            f"scope {scope['scope_id']} verification failed but no Grouping mismatch was found on re-check"
        )
    return held_paths, verified_paths, recovery


def create_recovery_approval(approved_by: str, *,
                              handoff_root: Path = metadata_batch.HANDOFF_ROOT,
                              t5_volume: Path = metadata_batch.T5_VOLUME) -> dict:
    """Build recovery-approval.json, independently re-proving every bound
    fact rather than trusting the earlier one-time investigation."""
    approved_by = approved_by.strip()
    if not approved_by:
        raise metadata_batch.BatchRefused("recovery approval requires an explicit non-blank approver")
    handoff_root = Path(handoff_root).resolve()
    state_path, state, master = _load_master_and_state(handoff_root)
    if state.get("state") != "BLOCKED":
        raise metadata_batch.BatchRefused(
            f"recovery approval requires batch BLOCKED, found {state.get('state')}"
        )

    completed_proof = {}
    for scope_id in COMPLETED_SCOPE_IDS:
        entry = _require_completed(state, scope_id)
        completed_proof[scope_id] = {
            "state": entry["state"], "execution": entry["execution"], "files": entry.get("files"),
        }
    partial_entry = state.get("scopes", {}).get(PARTIAL_SCOPE_ID, {})
    if partial_entry.get("state") != "BLOCKED" or partial_entry.get("execution") != "FAILED":
        raise metadata_batch.BatchRefused(
            f"{PARTIAL_SCOPE_ID} is not BLOCKED/FAILED: "
            f"{partial_entry.get('state')}/{partial_entry.get('execution')}"
        )
    if "Common vs Tag Team" not in (partial_entry.get("failure") or ""):
        raise metadata_batch.BatchRefused(
            f"{PARTIAL_SCOPE_ID} failure reason does not match the known, investigated edge case: "
            f"{partial_entry.get('failure')!r}"
        )
    auto_held_proof = {}
    for scope_id in KNOWN_AUTO_HELD_SCOPE_IDS:
        entry = state.get("scopes", {}).get(scope_id, {})
        if entry.get("state") != "BLOCKED" or entry.get("execution") != "FAILED":
            raise metadata_batch.BatchRefused(
                f"{scope_id} is not BLOCKED/FAILED: {entry.get('state')}/{entry.get('execution')}"
            )
        scope = _scope_by_id(master, scope_id)
        held, verified, _recovery = _classify_scope_failure(scope, master, scope["evidence_root"], t5_volume)
        auto_held_proof[scope_id] = {"held_items": held, "non_held_verified": verified}
    for scope_id in RESUME_SCOPE_IDS:
        _require_pending(state, scope_id, "PREFLIGHT_PASS", "NOT_STARTED")

    s016_proof = _reverify_s016_live(master)

    current_safety = metadata_batch.safety_file_hashes()
    approved_safety = master.get("safety_file_sha256") or {}
    if current_safety != approved_safety:
        changed = sorted(
            relative for relative in set(current_safety).union(approved_safety)
            if current_safety.get(relative) != approved_safety.get(relative)
        )
        raise metadata_batch.BatchRefused(f"gated safety files changed since this batch was approved: {changed}")

    remaining_plan_sha256 = {
        scope["scope_id"]: scope["plan_sha256"]
        for scope in master["scopes"] if scope["scope_id"] in RESUME_SCOPE_IDS
    }

    approval = {
        "schema_version": 1,
        "kind": "recovery_approval",
        "batch_id": BATCH_ID,
        "original_master_sha": ORIGINAL_MASTER_SHA,
        "recovery_repo_commit": metadata_batch.repo_head(),
        "resume_scope_ids": list(RESUME_SCOPE_IDS),
        "completed_scope_ids": list(COMPLETED_SCOPE_IDS),
        "completed_scope_proof": completed_proof,
        "partial_scope_id": PARTIAL_SCOPE_ID,
        "partial_scope_failure": partial_entry["failure"],
        "partial_scope_proof": s016_proof,
        "known_auto_held_scope_ids": list(KNOWN_AUTO_HELD_SCOPE_IDS),
        "auto_held_proof": auto_held_proof,
        "held_items": HELD_ITEMS,
        "remaining_plan_sha256": remaining_plan_sha256,
        "safety_file_sha256": current_safety,
        "continuation_implementation_sha256": metadata_batch.sha256_file(Path(__file__).resolve()),
        "approved_by": approved_by,
        "approved_at": metadata_batch.now(),
        "statement": (
            f"I approve resuming exactly batch {BATCH_ID} at S018-S041, after independently "
            "reconfirming S001-S015 are VERIFIED/COMPLETED, S016's 14 non-held files already "
            "show their correct live Grouping, S016's 2 held files and S017's 1 held file "
            "remain byte-identical to their pre-batch originals (Serato-encoding-sensitive "
            "writes correctly refused, left unmutated, deferred to a separate future "
            "remediation), and per the operator's standing rule any further scope that hits "
            "the exact same, journal-proven safe-skip condition may be auto-held and skipped "
            "without stopping to ask; any other failure class still stops the run."
        ),
    }
    approval["recovery_approval_sha256"] = metadata_batch.canonical_sha(
        approval, excluded=("recovery_approval_sha256",)
    )
    metadata_batch.atomic_write_json(
        _recovery_approval_path(handoff_root), approval, handoff_root=handoff_root
    )
    return approval


def preflight_recovery(*, handoff_root: Path = metadata_batch.HANDOFF_ROOT,
                        t5_volume: Path = metadata_batch.T5_VOLUME,
                        mount_checker=os.path.ismount) -> tuple[dict, dict, dict]:
    """Re-checks everything the approval bound, live, a second time, then
    returns a scope/candidate-filtered master view that structurally cannot
    contain S001-S015 or S016 -- not merely a loop that happens not to reach them."""
    handoff_root = Path(handoff_root).resolve()
    t5_volume = Path(t5_volume).resolve()

    approval_path = _recovery_approval_path(handoff_root)
    approval = metadata_batch.read_json(approval_path)
    if approval.get("kind") != "recovery_approval" or approval.get("batch_id") != BATCH_ID:
        raise metadata_batch.BatchRefused(f"not a valid recovery approval for {BATCH_ID}: {approval_path}")
    expected_digest = metadata_batch.canonical_sha(approval, excluded=("recovery_approval_sha256",))
    if approval.get("recovery_approval_sha256") != expected_digest:
        raise metadata_batch.BatchRefused("recovery approval has been altered since it was written")

    state_path, state, master = _load_master_and_state(handoff_root)
    if state.get("state") != "BLOCKED":
        raise metadata_batch.BatchRefused(f"recovery apply requires batch BLOCKED, found {state.get('state')}")
    if approval["original_master_sha"] != ORIGINAL_MASTER_SHA or state.get("master_sha") != ORIGINAL_MASTER_SHA:
        raise metadata_batch.BatchRefused("recovery approval/master SHA mismatch")

    repo_head = metadata_batch.repo_head()
    if repo_head != approval["recovery_repo_commit"]:
        raise metadata_batch.BatchRefused(
            f"repo HEAD {repo_head} differs from the commit the recovery approval was bound to: "
            f"{approval['recovery_repo_commit']}"
        )
    current_safety = metadata_batch.safety_file_hashes()
    if current_safety != approval["safety_file_sha256"]:
        changed = sorted(
            relative for relative in set(current_safety).union(approval["safety_file_sha256"])
            if current_safety.get(relative) != approval["safety_file_sha256"].get(relative)
        )
        raise metadata_batch.BatchRefused(f"gated safety files changed after recovery approval: {changed}")
    own_hash = metadata_batch.sha256_file(Path(__file__).resolve())
    if own_hash != approval["continuation_implementation_sha256"]:
        raise metadata_batch.BatchRefused(
            "this recovery script's own content changed since the recovery approval was created"
        )

    for scope_id in COMPLETED_SCOPE_IDS:
        entry = _require_completed(state, scope_id)
        bound = approval["completed_scope_proof"][scope_id]
        if (entry["state"], entry["execution"], entry.get("files")) != (
            bound["state"], bound["execution"], bound["files"]
        ):
            raise metadata_batch.BatchRefused(f"{scope_id} state no longer matches the recovery approval")
    partial_entry = state.get("scopes", {}).get(PARTIAL_SCOPE_ID, {})
    if partial_entry.get("state") != "BLOCKED" or partial_entry.get("failure") != approval["partial_scope_failure"]:
        raise metadata_batch.BatchRefused(f"{PARTIAL_SCOPE_ID} state no longer matches the recovery approval")
    for scope_id in KNOWN_AUTO_HELD_SCOPE_IDS:
        entry = state.get("scopes", {}).get(scope_id, {})
        if entry.get("state") != "BLOCKED" or entry.get("execution") != "FAILED":
            raise metadata_batch.BatchRefused(f"{scope_id} state no longer matches the recovery approval")
    for scope_id in RESUME_SCOPE_IDS:
        _require_pending(state, scope_id, "PREFLIGHT_PASS", "NOT_STARTED")

    s016_proof = _reverify_s016_live(master)
    if set(s016_proof["non_held_verified"]) != set(approval["partial_scope_proof"]["non_held_verified"]):
        raise metadata_batch.BatchRefused("S016 non-held live state changed since the recovery approval")

    for scope_id in KNOWN_AUTO_HELD_SCOPE_IDS:
        scope = _scope_by_id(master, scope_id)
        held, verified, _recovery = _classify_scope_failure(scope, master, scope["evidence_root"], t5_volume)
        bound = approval["auto_held_proof"][scope_id]
        if set(held) != set(bound["held_items"]) or set(verified) != set(bound["non_held_verified"]):
            raise metadata_batch.BatchRefused(f"{scope_id} auto-hold state changed since the recovery approval")

    resume_master = {
        **master,
        "scopes": [s for s in master["scopes"] if s["scope_id"] in RESUME_SCOPE_IDS],
        "candidates": [c for c in master["candidates"] if c["scope_id"] in RESUME_SCOPE_IDS],
    }
    if {s["scope_id"] for s in resume_master["scopes"]} != set(RESUME_SCOPE_IDS):
        raise metadata_batch.BatchRefused("resume scope filter did not resolve to exactly S018-S041")
    excluded_scope_ids = COMPLETED_SCOPE_IDS + (PARTIAL_SCOPE_ID,) + KNOWN_AUTO_HELD_SCOPE_IDS
    if any(c["scope_id"] in excluded_scope_ids for c in resume_master["candidates"]):
        raise metadata_batch.BatchRefused("resume candidate filter leaked a completed or partial/held scope")
    current_plan_sha = {s["scope_id"]: s["plan_sha256"] for s in resume_master["scopes"]}
    if current_plan_sha != approval["remaining_plan_sha256"]:
        raise metadata_batch.BatchRefused("remaining scope plan hashes no longer match the recovery approval")

    apply_approved_batch._validate_plans(resume_master, handoff_root)
    apply_approved_batch._validate_live(resume_master)
    apply_approved_batch._validate_t5(resume_master, t5_volume, mount_checker)
    apply_approved_batch._validate_locks(resume_master)

    return resume_master, approval, master


def _reopen_blocked_for_recovery(state_path: Path, handoff_root: Path, *,
                                  approval_sha256: str) -> dict:
    """Exactly-once batch-level BLOCKED -> <prior legitimate state> reopen.

    Deliberately bypasses metadata_batch.transition(), whose FORWARD table
    has no exit edge from BLOCKED by design -- correct, fail-closed behavior
    for every other batch that must not be weakened. S016 itself is never
    reopened: it stays BLOCKED permanently as an honest record of its
    partial outcome, and is never touched by this function.
    """
    state = metadata_batch.read_json(state_path)
    if state.get("state") != "BLOCKED":
        raise metadata_batch.BatchRefused(f"recovery reopen refused: batch is not BLOCKED (found {state.get('state')})")
    prior_states = [h["state"] for h in state.get("history", []) if h["state"] != "BLOCKED"]
    if not prior_states:
        raise metadata_batch.BatchRefused("recovery reopen refused: no prior non-BLOCKED state in history")
    reopen_to = prior_states[-1]
    if reopen_to not in metadata_batch.FORWARD:
        raise metadata_batch.BatchRefused(f"recovery reopen refused: {reopen_to!r} has no forward transition")
    stamp = metadata_batch.now()
    state["state"] = reopen_to
    state["updated_at"] = stamp
    state.setdefault("history", []).append({
        "state": reopen_to, "at": stamp,
        "reason": f"recovery reopen bound to recovery-approval sha256 {approval_sha256}",
        "recovery_bypass": True,
    })
    metadata_batch.atomic_write_json(state_path, state, handoff_root=handoff_root)
    return state


def _apply_resume_scopes(resume_master: dict, full_master: dict, approval: dict, *,
                         handoff_root: Path, t5_volume: Path, mount_checker) -> dict:
    handoff_root, t5_volume = Path(handoff_root).resolve(), Path(t5_volume).resolve()
    state_path = handoff_root / "current-batch.json"
    active_scope_id = None
    approval_sha = approval["recovery_approval_sha256"]
    scope_exceptions: dict[str, dict] = {}
    try:
        _reopen_blocked_for_recovery(state_path, handoff_root, approval_sha256=approval_sha)
        locks_released = True

        # S017 already hit the known safe-skip class before this standing rule
        # existed; fold it in from its existing evidence (never re-run) exactly
        # like every scope the loop below auto-holds going forward.
        for scope_id in KNOWN_AUTO_HELD_SCOPE_IDS:
            scope = _scope_by_id(full_master, scope_id)
            held, verified, _recovery = _classify_scope_failure(
                scope, full_master, scope["evidence_root"], t5_volume
            )
            metadata_batch.update_scope(
                state_path, scope_id, "BLOCKED",
                details={"auto_hold": True, "held_items": held, "non_held_verified": verified},
                handoff_root=handoff_root,
            )
            scope_exceptions[scope_id] = {
                "note": f"{len(verified)}/{len(verified) + len(held)} files applied and verified; "
                        f"{len(held)} file(s) auto-held per the operator's standing auto-hold rule "
                        "(Serato-encoding-sensitive write correctly refused, byte-identical to "
                        "original, deferred to a separate future remediation lane)",
                "held_items": held, "non_held_verified": verified,
                "recovery_approval_sha256": approval_sha,
            }

        for scope in resume_master["scopes"]:
            scope_id = scope["scope_id"]
            active_scope_id = scope_id
            session = f"batch-{BATCH_ID}-{scope_id}-recovery"
            acquired = False
            scope_failure = None
            try:
                apply_approved_batch._validate_t5({"scopes": [scope]}, t5_volume, mount_checker)
                acquired_payload = apply_approved_batch._lock_command(
                    "acquire", scope["lock_folder"], session, resume_master
                )
                acquired = True
                heartbeat = lock_guard.LockHeartbeat(scope["lock_folder"], session)
                heartbeat.pin_run_id(acquired_payload["lock"])

                manifest = metadata_batch.create_verified_backup(
                    Path(scope["source_root"]), Path(scope["backup_root"]),
                    Path(scope["backup_manifest"]), t5_volume=t5_volume, heartbeat=heartbeat,
                )
                if manifest["status"] != backup_manifest.STATUS_PASS:
                    raise metadata_batch.BatchRefused(f"backup manifest did not PASS: {scope_id}")
                metadata_batch.update_scope(
                    state_path, scope_id, "BACKED_UP",
                    details={
                        "backup_manifest_sha256": manifest["manifest_sha256"],
                        "execution": "IN_PROGRESS",
                    },
                    handoff_root=handoff_root,
                )

                evidence_root = metadata_batch.require_t5_path(Path(scope["evidence_root"]), t5_volume)
                evidence_root.mkdir(parents=True)
                cmd = [
                    sys.executable, str(metadata_batch.REPO_ROOT / "scripts/apply_new_music_intake_plan.py"),
                    "--root", scope["source_root"], "--plan", scope["plan_path"],
                    "--backup-manifest", scope["backup_manifest"], "--out-dir", str(evidence_root),
                    "--label", f"metadata-{BATCH_ID}-{scope_id}-recovery", "--apply",
                    "--lock-folder", scope["lock_folder"], "--session", session,
                ]
                run = subprocess.run(cmd, text=True, capture_output=True)
                if run.returncode != 0:
                    apply_approved_batch.inspect_failed_run(evidence_root, t5_volume)
                result = json.loads(run.stdout)
                metadata_batch.update_scope(
                    state_path, scope_id, "APPLIED",
                    details={"result_path": result.get("result_path"), "execution": "IN_PROGRESS"},
                    handoff_root=handoff_root,
                )
                try:
                    proof = apply_approved_batch._verify_scope(
                        resume_master, scope, result, t5_volume=t5_volume
                    )
                except metadata_batch.BatchRefused as verify_exc:
                    if "post-apply Grouping mismatch" not in str(verify_exc):
                        raise  # a different failure class -- fail closed, unchanged
                    held, verified, recovery = _classify_scope_failure(
                        scope, resume_master, evidence_root, t5_volume
                    )
                    metadata_batch.update_scope(
                        state_path, scope_id, "BLOCKED",
                        details={
                            "failure": str(verify_exc), "execution": "FAILED",
                            "auto_hold": True, "held_items": held, "non_held_verified": verified,
                        },
                        handoff_root=handoff_root,
                    )
                    scope_exceptions[scope_id] = {
                        "note": f"{len(verified)}/{len(verified) + len(held)} files applied and "
                                f"verified; {len(held)} file(s) auto-held per the operator's "
                                "standing auto-hold rule (Serato-encoding-sensitive write "
                                "correctly refused, byte-identical to original, deferred to a "
                                "separate future remediation lane)",
                        "held_items": held, "non_held_verified": verified,
                        "recovery_approval_sha256": approval_sha,
                    }
                else:
                    metadata_batch.update_scope(
                        state_path, scope_id, "VERIFIED",
                        details={**proof, "execution": "COMPLETED"},
                        handoff_root=handoff_root,
                    )
            except BaseException as exc:
                scope_failure = exc
                raise
            finally:
                if acquired:
                    try:
                        released = apply_approved_batch._lock_command(
                            "release", scope["lock_folder"], session, resume_master
                        )
                        locks_released = locks_released and bool(released.get("released"))
                    except Exception:
                        locks_released = False
                        if scope_failure is None:
                            raise

        final_state = metadata_batch.transition(
            state_path, "VERIFIED", handoff_root=handoff_root,
            updates={
                "exceptions": {
                    PARTIAL_SCOPE_ID: {
                        "note": "14/16 files applied and verified; 2 files held (Serato-encoding-"
                                "sensitive write correctly refused, byte-identical to original, "
                                "deferred to a separate future remediation lane)",
                        "held_items": list(HELD_ITEMS),
                        "recovery_approval_sha256": approval_sha,
                    },
                    **scope_exceptions,
                },
            },
        )
        all_scopes = final_state.get("scopes", {})
        scope_results = {
            sid: {"state": e["state"], "execution": e.get("execution"), "files_verified": e.get("files", 0)}
            for sid, e in all_scopes.items()
        }
        # S016 and every auto-held scope are deliberately excluded from the
        # normal sum (each stays BLOCKED, not VERIFIED) -- their genuinely
        # correct files are added explicitly and documented; held files are
        # never counted as verified.
        files_verified_total = sum(e.get("files", 0) for e in all_scopes.values())
        files_verified_total += len(approval["partial_scope_proof"]["non_held_verified"])
        for exc in scope_exceptions.values():
            files_verified_total += len(exc["non_held_verified"])
        all_held_items = list(HELD_ITEMS) + [
            path for exc in scope_exceptions.values() for path in exc["held_items"]
        ]
        evidence_by_scope = {s["scope_id"]: s["evidence_root"] for s in full_master["scopes"]}
        all_evidence_roots = [
            evidence_by_scope[sid] for sid in (
                *COMPLETED_SCOPE_IDS, PARTIAL_SCOPE_ID, *KNOWN_AUTO_HELD_SCOPE_IDS, *RESUME_SCOPE_IDS
            ) if sid in evidence_by_scope
        ]
        spotcheck = apply_approved_batch._write_spotcheck(full_master, handoff_root)
        summary = {
            "BATCH RESULT": "PASS WITH DOCUMENTED HOLDS" if scope_exceptions else "PASS WITH ONE DOCUMENTED HOLD",
            "FILES VERIFIED": files_verified_total,
            "FILES HELD": len(all_held_items),
            "SCOPES VERIFIED": sum(
                r["state"] == "VERIFIED" and r["execution"] == "COMPLETED" for r in scope_results.values()
            ),
            "SCOPE RESULTS": scope_results,
            "PARTIAL SCOPE": {
                PARTIAL_SCOPE_ID: {
                    "state": all_scopes.get(PARTIAL_SCOPE_ID, {}).get("state"),
                    "non_held_files_verified": len(approval["partial_scope_proof"]["non_held_verified"]),
                    "held_files": list(HELD_ITEMS),
                },
                **{
                    sid: {
                        "state": all_scopes.get(sid, {}).get("state"),
                        "non_held_files_verified": len(exc["non_held_verified"]),
                        "held_files": exc["held_items"],
                    }
                    for sid, exc in scope_exceptions.items()
                },
            },
            "AUDIO PAYLOAD": "PASS",
            "SERATO FRAMES": "PASS",
            "ID3 PRESERVATION": "PASS",
            "UNAUTHORIZED CHANGES": 0,
            "RECOVERY STATUS": "CLEAN",
            "T5 EVIDENCE PATH": all_evidence_roots,
            "LOCKS RELEASED": locks_released,
            "SERATO SPOTCHECK LIST": str(spotcheck),
            "READY FOR SERATO SPOT-CHECK": bool(locks_released),
            "RECOVERY": {
                "batch_id": BATCH_ID,
                "recovery_approval_sha256": approval_sha,
                "known_auto_held_scope_ids": list(KNOWN_AUTO_HELD_SCOPE_IDS),
                "resume_scope_ids": list(RESUME_SCOPE_IDS),
                "auto_held_scope_ids": list(scope_exceptions),
            },
        }
        metadata_batch.atomic_write_json(
            handoff_root / "batches" / BATCH_ID / "apply-summary.json", summary, handoff_root=handoff_root,
        )
        return summary
    except metadata_batch.RollbackFailed as exc:
        try:
            state = metadata_batch.read_json(state_path)
            if active_scope_id and state.get("scopes", {}).get(active_scope_id, {}).get("state") not in {
                "ROLLBACK_FAILED", "VERIFIED"
            }:
                metadata_batch.update_scope(
                    state_path, active_scope_id, "ROLLBACK_FAILED",
                    details={"failure": str(exc), "execution": "FAILED"}, handoff_root=handoff_root,
                )
            state = metadata_batch.read_json(state_path)
            if state.get("state") not in {"ROLLBACK_FAILED", "COMPLETE"}:
                metadata_batch.transition(state_path, "ROLLBACK_FAILED", reason=str(exc), handoff_root=handoff_root)
        except Exception:
            pass
        raise
    except BaseException as exc:
        try:
            state = metadata_batch.read_json(state_path)
            if active_scope_id and state.get("scopes", {}).get(active_scope_id, {}).get("state") not in {
                "BLOCKED", "ROLLBACK_FAILED", "VERIFIED"
            }:
                metadata_batch.update_scope(
                    state_path, active_scope_id, "BLOCKED",
                    details={"failure": str(exc), "execution": "FAILED"}, handoff_root=handoff_root,
                )
                state = metadata_batch.read_json(state_path)
            if state.get("state") not in {"BLOCKED", "COMPLETE"}:
                metadata_batch.transition(state_path, "BLOCKED", reason=str(exc), handoff_root=handoff_root)
        except Exception:
            pass
        raise


def apply_recovery_batch(*, handoff_root: Path = metadata_batch.HANDOFF_ROOT,
                         t5_volume: Path = metadata_batch.T5_VOLUME,
                         mount_checker=os.path.ismount) -> dict:
    resume_master, approval, full_master = preflight_recovery(
        handoff_root=handoff_root, t5_volume=t5_volume, mount_checker=mount_checker
    )
    return _apply_resume_scopes(
        resume_master, full_master, approval,
        handoff_root=handoff_root, t5_volume=t5_volume, mount_checker=mount_checker,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_approve = sub.add_parser("create-approval")
    p_approve.add_argument("--approved-by", required=True)
    sub.add_parser("apply")
    args = parser.parse_args()
    try:
        if args.command == "create-approval":
            result = create_recovery_approval(args.approved_by)
        else:
            result = apply_recovery_batch()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (metadata_batch.BatchRefused, backup_manifest.BackupGateError, json.JSONDecodeError) as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
