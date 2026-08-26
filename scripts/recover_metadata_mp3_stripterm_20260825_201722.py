#!/usr/bin/env python3
"""One-time, batch-bound reopen for metadata-mp3-stripterm-20260825-201722.

The batch was HUMAN_APPROVED and went BLOCKED at the apply-level lock
validation because a concurrent operator-approved fill-empty session held
folder locks. The refusal happened BEFORE any scope work: no backup was
created, no lock was taken, no tag was written, scopes == {}. This script
performs the explicitly-approved, exactly-once BLOCKED -> HUMAN_APPROVED
reopen so the untouched approved batch can run once the peer locks clear.

Commands:
  create-approval --approved-by "djb.a.e (human operator)"
  reopen

Both refuse unless every precondition still holds. The reopen refuses if
any folder lock exists, if the batch mutated anything, or if it already ran.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "preview-only"

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metadata_batch  # noqa: E402

BATCH_ID = "metadata-mp3-stripterm-20260825-201722"
MASTER_SHA = "3b3a59d06dccc8ad83c422b4f0d768f0ed4d9983ed6d91d051449221c96aefc6"
BLOCK_REASON_PREFIX = "existing mutation lock blocks batch"
BLOCKING_SESSION = "e112d639-fillempty-20260825"

HANDOFF = metadata_batch.HANDOFF_ROOT
STATE_PATH = HANDOFF / "current-batch.json"
BATCH_DIR = HANDOFF / "batches" / BATCH_ID
APPROVAL_PATH = BATCH_DIR / "recovery-approval.json"
MARKER_PATH = BATCH_DIR / "recovery-reopen-done.json"
LOCKS_DIR = metadata_batch.REPO_ROOT / ".locks"
T5_BACKUP_DIR = metadata_batch.T5_BACKUP_ROOT / BATCH_ID


def _self_sha() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _refuse(message: str) -> None:
    raise metadata_batch.BatchRefused(f"reopen refused: {message}")


def _validate_blocked_untouched() -> dict:
    state = metadata_batch.read_json(STATE_PATH)
    if state.get("batch_id") != BATCH_ID:
        _refuse(f"current batch is {state.get('batch_id')!r}, not {BATCH_ID}")
    if state.get("state") != "BLOCKED":
        _refuse(f"state is {state.get('state')!r}, not BLOCKED")
    if not str(state.get("reason") or "").startswith(BLOCK_REASON_PREFIX):
        _refuse(f"BLOCKED for an unexpected reason: {state.get('reason')!r}")
    if BLOCKING_SESSION not in str(state.get("reason")):
        _refuse("BLOCKED reason does not name the known fill-empty peer session")
    if state.get("scopes"):
        _refuse("scopes are not empty; the batch is not provably untouched")
    states_seen = [entry.get("state") for entry in state.get("history", [])]
    if states_seen != ["DISCOVERED", "PREVIEWED", "HUMAN_APPROVED", "BLOCKED"]:
        _refuse(f"unexpected history {states_seen}; reopen is bound to a pre-apply block")
    if T5_BACKUP_DIR.exists():
        _refuse(f"backup dir exists for this batch ({T5_BACKUP_DIR}); not a pre-backup block")
    master = metadata_batch.verify_master(BATCH_DIR / "master-manifest.json", MASTER_SHA)
    approval = metadata_batch.read_json(BATCH_DIR / "approval.json")
    if approval.get("master_sha") != MASTER_SHA:
        _refuse("approval.json does not bind the expected master sha")
    if approval.get("approved_by") != "djb.a.e (human operator)":
        _refuse(f"unexpected approver {approval.get('approved_by')!r}")
    return {"state": state, "master": master, "approval": approval}


def create_approval(approved_by: str) -> None:
    if approved_by.strip() != "djb.a.e (human operator)":
        _refuse("recovery approval must be recorded for the human operator")
    _validate_blocked_untouched()
    if APPROVAL_PATH.exists():
        _refuse(f"recovery approval already exists: {APPROVAL_PATH}")
    payload = {
        "schema_version": 1,
        "batch_id": BATCH_ID,
        "master_sha": MASTER_SHA,
        "approved_by": approved_by.strip(),
        "approved_at": metadata_batch.now(),
        "repo_commit": metadata_batch.repo_head(),
        "recovery_script_sha256": _self_sha(),
        "statement": (
            "I approve exactly one BLOCKED -> HUMAN_APPROVED reopen of this "
            "untouched batch, blocked pre-apply by a peer session's folder locks."
        ),
    }
    metadata_batch.atomic_write_json(APPROVAL_PATH, payload, handoff_root=HANDOFF)
    print(json.dumps({"RECOVERY_APPROVAL_RECORDED": True, **payload}, indent=2))


def reopen() -> None:
    context = _validate_blocked_untouched()
    if MARKER_PATH.exists():
        _refuse(f"this reopen already ran exactly once: {MARKER_PATH}")
    if not APPROVAL_PATH.exists():
        _refuse("no recovery-approval.json; run create-approval first")
    recovery = metadata_batch.read_json(APPROVAL_PATH)
    if recovery.get("master_sha") != MASTER_SHA or recovery.get("batch_id") != BATCH_ID:
        _refuse("recovery approval does not bind this batch")
    if recovery.get("recovery_script_sha256") != _self_sha():
        _refuse("this script's content differs from the approved recovery script")
    if recovery.get("repo_commit") != metadata_batch.repo_head():
        _refuse("repo HEAD moved since the recovery approval was recorded")
    live_locks = sorted(LOCKS_DIR.glob("*.lock.json"))
    if live_locks:
        _refuse(f"{len(live_locks)} folder lock(s) still exist; the block cause is not clear yet")
    state = context["state"]
    stamp = metadata_batch.now()
    state["state"] = "HUMAN_APPROVED"
    state["reason"] = None
    state["updated_at"] = stamp
    state["history"].append({
        "state": "HUMAN_APPROVED",
        "at": stamp,
        "reason": (
            "explicitly-approved exactly-once reopen after a pre-apply block on "
            f"peer session {BLOCKING_SESSION} locks; recovery-approval.json binds this script"
        ),
    })
    metadata_batch.atomic_write_json(STATE_PATH, state, handoff_root=HANDOFF)
    metadata_batch.atomic_write_json(MARKER_PATH, {
        "reopened_at": stamp,
        "recovery_script_sha256": _self_sha(),
        "recovery_approval_sha256": hashlib.sha256(APPROVAL_PATH.read_bytes()).hexdigest(),
    }, handoff_root=HANDOFF)
    print(json.dumps({"REOPENED": True, "batch_id": BATCH_ID, "state": "HUMAN_APPROVED"}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    approve_cmd = sub.add_parser("create-approval")
    approve_cmd.add_argument("--approved-by", required=True)
    sub.add_parser("reopen")
    args = parser.parse_args()
    try:
        if args.command == "create-approval":
            create_approval(args.approved_by)
        else:
            reopen()
    except metadata_batch.BatchRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
