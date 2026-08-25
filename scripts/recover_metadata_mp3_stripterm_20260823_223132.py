#!/usr/bin/env python3
"""One-time, batch-bound continuation for metadata-mp3-stripterm-20260823-223132 only.

S001-S087 of this batch (87 scopes) applied and verified cleanly. The APPLY
process then began S088: it acquired the R&b folder lock at 01:13:04 and was
mid-backup (copy finished, hashing in progress) when the T5 EVO drive dropped
off USB during a sleep/wake power-cycle at 06:54:47 on 2026-08-24 (kernel log
confirmed). The process died on OSError [Errno 5] before S088 ever reached
BACKED_UP, and before the failure handler could record batch-level BLOCKED --
the state file itself lives on the failed drive. Durable state is therefore
stale-but-consistent: batch APPLIED, S088-S133 all PREFLIGHT_PASS/NOT_STARTED,
one unverified partial backup copy on disk for S088 (no backup manifest, no
evidence dir), and one time-stale R&b folder lock from the dead run.

This script's job, per explicit operator direction on 2026-08-24 evening:
record the missing BLOCKED honestly first, then resume S088-S133 (46 scopes)
without reprocessing S001-S087, discarding S088's unverified partial backup
and taking a fresh proven one. The stale lock is reclaimed through
folder_lock.py's normal time-based staleness path (last renewal 01:15:36 is
far past the 6h window) -- never deleted by hand.

Carried over from the operator's standing rule on the 181741 recovery: a
post-apply Grouping mismatch is auto-held, without stopping to ask, ONLY when
the mutation engine's own journal record proves it is exactly the known
Serato-encoding-caution skip -- tag_skipped, tag_changed false, before/after
sha256 identical, clean journal. Any other failure class (Genre, ID3 version,
audio payload, Serato frames, an unrecognized Grouping mismatch, or a
dirty/incomplete journal) stops the whole run immediately.

There is no generic "resume from scope N" mechanism in this codebase by
design. Every identifying value below is a hardcoded constant bound to this
one batch; nothing here is reusable for any other recovery.

Usage:
    python3 scripts/recover_metadata_mp3_stripterm_20260823_223132.py record-blocked
    python3 scripts/recover_metadata_mp3_stripterm_20260823_223132.py create-approval \\
        --approved-by "name"
    python3 scripts/recover_metadata_mp3_stripterm_20260823_223132.py apply
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "engine-user"

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import apply_approved_batch
import apply_new_music_intake_plan as intake
import backup_manifest
import folder_lock
import lock_guard
import metadata_batch
import mutation_engine

BATCH_ID = "metadata-mp3-stripterm-20260823-223132"
ORIGINAL_MASTER_SHA = "41f339ec0d0b545ec3084cc4cd4824807df75f152f807fc62e4eb6760f49622c"
COMPLETED_SCOPE_IDS: tuple[str, ...] = tuple(f"S{n:03d}" for n in range(1, 88))    # S001-S087
RESUME_SCOPE_IDS: tuple[str, ...] = tuple(f"S{n:03d}" for n in range(88, 134))     # S088-S133
INTERRUPTED_SCOPE_ID = "S088"  # first resume scope; its partial backup is discarded
# The one lock the dead run left behind. Anything else holding a lock refuses.
STALE_LOCK_FOLDER = "R&b"
STALE_LOCK_SESSION = f"batch-{BATCH_ID}-{'S088'}"
BLOCKED_REASON = (
    "T5 EVO drive dropped off USB during sleep/wake power-cycle at 2026-08-24T06:54 "
    "while S088's backup was being hashed (kernel log confirmed); APPLY process died "
    "on OSError [Errno 5] before it could record BLOCKED. Recorded retroactively as "
    "the honest terminal record of the interrupted run; recovery is a separate, "
    "operator-approved, batch-bound continuation."
)
SAFE_SKIP_REASON = "Serato encoding-only change on write; skipped out of caution"

assert set(RESUME_SCOPE_IDS).isdisjoint(COMPLETED_SCOPE_IDS)
assert INTERRUPTED_SCOPE_ID in RESUME_SCOPE_IDS


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


def _require_pending(state: dict, scope_id: str) -> dict:
    entry = state.get("scopes", {}).get(scope_id, {})
    if entry.get("state") != "PREFLIGHT_PASS" or entry.get("execution") != "NOT_STARTED":
        raise metadata_batch.BatchRefused(
            f"{scope_id} is not PREFLIGHT_PASS/NOT_STARTED: "
            f"{entry.get('state')}/{entry.get('execution')}"
        )
    return entry


def _require_interrupted_scope_evidence(master: dict, t5_volume: Path) -> dict:
    """Prove S088 is exactly the mid-backup interruption this script was
    written for: partial backup copy present, no backup manifest, no
    evidence dir. Refuses on anything else."""
    scope = _scope_by_id(master, INTERRUPTED_SCOPE_ID)
    backup_root = metadata_batch.require_t5_path(Path(scope["backup_root"]), t5_volume)
    if not backup_root.is_dir():
        raise metadata_batch.BatchRefused(
            f"{INTERRUPTED_SCOPE_ID} partial backup dir is missing; expected the interrupted copy at {backup_root}"
        )
    if Path(scope["backup_manifest"]).exists():
        raise metadata_batch.BatchRefused(
            f"{INTERRUPTED_SCOPE_ID} has a backup manifest -- state does not match the "
            "known mid-backup interruption; refusing to discard anything"
        )
    if Path(scope["evidence_root"]).exists():
        raise metadata_batch.BatchRefused(
            f"{INTERRUPTED_SCOPE_ID} has an evidence dir -- state does not match the "
            "known mid-backup interruption; refusing to discard anything"
        )
    file_count = sum(len(files) for _root, _dirs, files in os.walk(backup_root))
    return {
        "scope_id": INTERRUPTED_SCOPE_ID,
        "stale_partial_backup_root": str(backup_root),
        "stale_partial_backup_file_count": file_count,
    }


def _validate_recovery_locks(resume_master: dict) -> None:
    """Same strictness as apply_approved_batch._validate_locks, with exactly
    one documented exception: the dead run's own S088 lock, which must be
    provably stale by folder_lock's time-based rule and is then reclaimed
    only through folder_lock's normal acquire path -- never deleted here."""
    checked = set()
    for scope in resume_master["scopes"]:
        folder = scope["lock_folder"]
        if folder in checked:
            continue
        checked.add(folder)
        try:
            lock = folder_lock.read_lock(folder)
        except folder_lock.CorruptLock as exc:
            raise metadata_batch.BatchRefused(f"corrupt mutation lock blocks recovery: {exc}") from exc
        if lock is None:
            continue
        if folder != STALE_LOCK_FOLDER or lock.get("session") != STALE_LOCK_SESSION:
            raise metadata_batch.BatchRefused(
                f"existing mutation lock blocks recovery (only the dead run's own stale "
                f"{STALE_LOCK_FOLDER!r} lock is tolerated): {folder}: {lock}"
            )
        staleness = folder_lock.evaluate_staleness(lock, 6.0)
        if not staleness.get("stale"):
            raise metadata_batch.BatchRefused(
                f"the dead run's {STALE_LOCK_FOLDER!r} lock is not yet stale by folder_lock's "
                f"own rule; refusing to race a possibly-live holder: {staleness}"
            )


def _classify_scope_failure(scope: dict, master: dict, evidence_root: Path, t5_volume: Path) -> tuple[list[str], list[str], dict]:
    """Independently re-verify every candidate in one scope's own evidence.
    Identical discipline to the committed 181741 recovery: only the exact
    journal-proven Serato-encoding-caution skip is auto-holdable; every
    other failure class raises unchanged."""
    evidence_root = metadata_batch.require_t5_path(Path(evidence_root), t5_volume)
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


def record_blocked(*, handoff_root: Path = metadata_batch.HANDOFF_ROOT) -> dict:
    """Retroactively record the batch-level BLOCKED the crashed process could
    not write (the state file lives on the drive that failed). Any-state ->
    BLOCKED is a legal transition; nothing else is changed."""
    handoff_root = Path(handoff_root).resolve()
    state_path, state, _master = _load_master_and_state(handoff_root)
    if state.get("state") == "BLOCKED":
        return state  # already recorded
    if state.get("state") != "APPLIED":
        raise metadata_batch.BatchRefused(
            f"record-blocked expects the interrupted batch to be APPLIED, found {state.get('state')}"
        )
    for scope_id in COMPLETED_SCOPE_IDS:
        _require_completed(state, scope_id)
    for scope_id in RESUME_SCOPE_IDS:
        _require_pending(state, scope_id)
    return metadata_batch.transition(
        state_path, "BLOCKED", reason=BLOCKED_REASON, handoff_root=handoff_root
    )


def create_recovery_approval(approved_by: str, *,
                              handoff_root: Path = metadata_batch.HANDOFF_ROOT,
                              t5_volume: Path = metadata_batch.T5_VOLUME) -> dict:
    approved_by = approved_by.strip()
    if not approved_by:
        raise metadata_batch.BatchRefused("recovery approval requires an explicit non-blank approver")
    handoff_root = Path(handoff_root).resolve()
    state_path, state, master = _load_master_and_state(handoff_root)
    if state.get("state") != "BLOCKED":
        raise metadata_batch.BatchRefused(
            f"recovery approval requires batch BLOCKED, found {state.get('state')}"
        )
    if BLOCKED_REASON not in (state.get("reason") or ""):
        raise metadata_batch.BatchRefused(
            "batch BLOCKED reason does not match this recovery's documented interruption"
        )

    completed_proof = {}
    for scope_id in COMPLETED_SCOPE_IDS:
        entry = _require_completed(state, scope_id)
        completed_proof[scope_id] = {
            "state": entry["state"], "execution": entry["execution"], "files": entry.get("files"),
        }
    for scope_id in RESUME_SCOPE_IDS:
        _require_pending(state, scope_id)

    interrupted_proof = _require_interrupted_scope_evidence(master, Path(t5_volume).resolve())

    current_safety = metadata_batch.safety_file_hashes()
    approved_safety = master.get("safety_file_sha256") or {}
    if current_safety != approved_safety:
        changed = sorted(
            relative for relative in set(current_safety).union(approved_safety)
            if current_safety.get(relative) != approved_safety.get(relative)
        )
        raise metadata_batch.BatchRefused(f"gated safety files changed since this batch was approved: {changed}")
    repo_head = metadata_batch.repo_head()
    if repo_head != master["repo_commit"]:
        raise metadata_batch.BatchRefused(
            f"repo HEAD {repo_head} differs from the commit this batch was prepared at: {master['repo_commit']}"
        )

    remaining_plan_sha256 = {
        scope["scope_id"]: scope["plan_sha256"]
        for scope in master["scopes"] if scope["scope_id"] in RESUME_SCOPE_IDS
    }

    approval = {
        "schema_version": 1,
        "kind": "recovery_approval",
        "batch_id": BATCH_ID,
        "original_master_sha": ORIGINAL_MASTER_SHA,
        "recovery_repo_commit": repo_head,
        "resume_scope_ids": list(RESUME_SCOPE_IDS),
        "completed_scope_ids": list(COMPLETED_SCOPE_IDS),
        "completed_scope_proof": completed_proof,
        "interrupted_scope_proof": interrupted_proof,
        "blocked_reason": BLOCKED_REASON,
        "remaining_plan_sha256": remaining_plan_sha256,
        "safety_file_sha256": current_safety,
        "continuation_implementation_sha256": metadata_batch.sha256_file(Path(__file__).resolve()),
        "approved_by": approved_by,
        "approved_at": metadata_batch.now(),
        "statement": (
            f"I approve resuming exactly batch {BATCH_ID} at S088-S133, after independently "
            "reconfirming S001-S087 are VERIFIED/COMPLETED with clean recovery records, "
            "S088-S133 are untouched (PREFLIGHT_PASS/NOT_STARTED), and S088's on-disk state "
            "is exactly the known mid-backup interruption (partial copy, no manifest, no "
            "evidence). S088's unverified partial backup copy is discarded and re-taken "
            "fresh; the stale R&b lock from the dead run is reclaimed only through "
            "folder_lock.py's time-based staleness path. Per the operator's standing rule "
            "carried over from the 181741 recovery, a scope hitting the exact journal-proven "
            "Serato-encoding-caution safe-skip may be auto-held and skipped without stopping "
            "to ask; any other failure class stops the run."
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
    for scope_id in RESUME_SCOPE_IDS:
        _require_pending(state, scope_id)

    interrupted_proof = _require_interrupted_scope_evidence(master, t5_volume)
    if interrupted_proof["stale_partial_backup_root"] != approval["interrupted_scope_proof"]["stale_partial_backup_root"]:
        raise metadata_batch.BatchRefused("S088 partial backup path no longer matches the recovery approval")

    resume_master = {
        **master,
        "scopes": [s for s in master["scopes"] if s["scope_id"] in RESUME_SCOPE_IDS],
        "candidates": [c for c in master["candidates"] if c["scope_id"] in RESUME_SCOPE_IDS],
    }
    if {s["scope_id"] for s in resume_master["scopes"]} != set(RESUME_SCOPE_IDS):
        raise metadata_batch.BatchRefused("resume scope filter did not resolve to exactly S088-S133")
    if any(c["scope_id"] in COMPLETED_SCOPE_IDS for c in resume_master["candidates"]):
        raise metadata_batch.BatchRefused("resume candidate filter leaked a completed scope")
    current_plan_sha = {s["scope_id"]: s["plan_sha256"] for s in resume_master["scopes"]}
    if current_plan_sha != approval["remaining_plan_sha256"]:
        raise metadata_batch.BatchRefused("remaining scope plan hashes no longer match the recovery approval")

    apply_approved_batch._validate_plans(resume_master, handoff_root)
    apply_approved_batch._validate_live(resume_master)
    apply_approved_batch._validate_t5(resume_master, t5_volume, mount_checker)
    _validate_recovery_locks(resume_master)

    return resume_master, approval, master


def _reopen_blocked_for_recovery(state_path: Path, handoff_root: Path, *,
                                  approval_sha256: str) -> dict:
    """Exactly-once batch-level BLOCKED -> APPLIED reopen, same deliberate
    bypass as the committed 181741 recovery; refuses in any other state."""
    state = metadata_batch.read_json(state_path)
    if state.get("state") != "BLOCKED":
        raise metadata_batch.BatchRefused(f"recovery reopen refused: batch is not BLOCKED (found {state.get('state')})")
    prior_states = [h["state"] for h in state.get("history", []) if h["state"] != "BLOCKED"]
    if not prior_states or prior_states[-1] != "APPLIED":
        raise metadata_batch.BatchRefused(
            f"recovery reopen refused: prior state is not APPLIED: {prior_states[-1:]}"
        )
    stamp = metadata_batch.now()
    state["state"] = "APPLIED"
    state["updated_at"] = stamp
    state.setdefault("history", []).append({
        "state": "APPLIED", "at": stamp,
        "reason": f"recovery reopen bound to recovery-approval sha256 {approval_sha256}",
        "recovery_bypass": True,
    })
    metadata_batch.atomic_write_json(state_path, state, handoff_root=handoff_root)
    return state


def _discard_stale_partial_backup(master: dict, approval: dict, t5_volume: Path) -> None:
    """Remove exactly the bound S088 partial backup dir, nothing else."""
    proof = _require_interrupted_scope_evidence(master, t5_volume)
    bound = approval["interrupted_scope_proof"]["stale_partial_backup_root"]
    if proof["stale_partial_backup_root"] != bound:
        raise metadata_batch.BatchRefused("refusing discard: partial backup path does not match approval binding")
    target = metadata_batch.require_t5_path(Path(bound), t5_volume)
    if BATCH_ID not in str(target) or target.name != "source":
        raise metadata_batch.BatchRefused(f"refusing discard: {target} is not this batch's scope backup dir")
    shutil.rmtree(target)


def _apply_resume_scopes(resume_master: dict, full_master: dict, approval: dict, *,
                         handoff_root: Path, t5_volume: Path, mount_checker) -> dict:
    handoff_root, t5_volume = Path(handoff_root).resolve(), Path(t5_volume).resolve()
    state_path = handoff_root / "current-batch.json"
    active_scope_id = None
    approval_sha = approval["recovery_approval_sha256"]
    scope_exceptions: dict[str, dict] = {}
    try:
        _discard_stale_partial_backup(full_master, approval, t5_volume)
        _reopen_blocked_for_recovery(state_path, handoff_root, approval_sha256=approval_sha)
        locks_released = True

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
            updates={"exceptions": scope_exceptions} if scope_exceptions else None,
        )
        all_scopes = final_state.get("scopes", {})
        scope_results = {
            sid: {"state": e["state"], "execution": e.get("execution"), "files_verified": e.get("files", 0)}
            for sid, e in all_scopes.items()
        }
        files_verified_total = sum(e.get("files", 0) for e in all_scopes.values())
        for exc in scope_exceptions.values():
            files_verified_total += len(exc["non_held_verified"])
        all_held_items = [
            path for exc in scope_exceptions.values() for path in exc["held_items"]
        ]
        evidence_by_scope = {s["scope_id"]: s["evidence_root"] for s in full_master["scopes"]}
        all_evidence_roots = [
            evidence_by_scope[sid] for sid in (*COMPLETED_SCOPE_IDS, *RESUME_SCOPE_IDS)
            if sid in evidence_by_scope
        ]
        spotcheck = apply_approved_batch._write_spotcheck(full_master, handoff_root)
        summary = {
            "BATCH RESULT": "PASS WITH DOCUMENTED HOLDS" if scope_exceptions else "PASS",
            "FILES VERIFIED": files_verified_total,
            "FILES HELD": len(all_held_items),
            "SCOPES VERIFIED": sum(
                r["state"] == "VERIFIED" and r["execution"] == "COMPLETED" for r in scope_results.values()
            ),
            "SCOPE RESULTS": scope_results,
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
                "resume_scope_ids": list(RESUME_SCOPE_IDS),
                "auto_held_scope_ids": list(scope_exceptions),
                "interruption": BLOCKED_REASON,
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
    sub.add_parser("record-blocked")
    p_approve = sub.add_parser("create-approval")
    p_approve.add_argument("--approved-by", required=True)
    sub.add_parser("apply")
    args = parser.parse_args()
    try:
        if args.command == "record-blocked":
            result = record_blocked()
        elif args.command == "create-approval":
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
