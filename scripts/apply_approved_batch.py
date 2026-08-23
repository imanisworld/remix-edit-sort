#!/usr/bin/env python3
"""Apply exactly one durably approved metadata batch, scope by scope.

This is a minimal orchestrator around ``apply_new_music_intake_plan.py`` and
the shared mutation engine.  It never invents or regenerates a plan.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "engine-user"

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import apply_new_music_intake_plan as intake
import backup_manifest
import folder_lock
import lock_guard
import metadata_batch
import mutation_engine


def _walk(root: Path) -> dict[str, Path]:
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name != "__pycache__"]
        for name in filenames:
            if name.startswith("._") or name in {".DS_Store", "Icon\r", "Icon?"}:
                continue
            path = Path(dirpath) / name
            if path.is_file() and not path.is_symlink():
                found[str(path.relative_to(root))] = path
    return found


def _approval(master: dict, state: dict, handoff_root: Path) -> dict:
    approval_path = state.get("approval_path")
    if not approval_path:
        raise metadata_batch.BatchRefused("durable approval record is absent")
    path = metadata_batch.require_handoff_path(Path(approval_path), handoff_root)
    approval = metadata_batch.read_json(path)
    required = {
        "batch_id": master["batch_id"],
        "master_sha": master["master_sha"],
        "repo_commit": master["repo_commit"],
        "candidate_count": master["candidate_count"],
    }
    for field, expected in required.items():
        if approval.get(field) != expected:
            raise metadata_batch.BatchRefused(
                f"approval {field} does not bind approved master: {approval.get(field)!r} != {expected!r}"
            )
    expected_plans = {
        scope["scope_id"]: scope["plan_sha256"] for scope in master["scopes"]
    }
    if approval.get("plan_sha256") != expected_plans:
        raise metadata_batch.BatchRefused(
            "durable approval does not explicitly bind every approved per-scope plan SHA"
        )
    if approval.get("safety_file_sha256") != master.get("safety_file_sha256"):
        raise metadata_batch.BatchRefused(
            "durable approval does not bind the approved gated safety-file contents"
        )
    if not str(approval.get("approved_by", "")).strip():
        raise metadata_batch.BatchRefused("durable approval has no explicit approver")
    return approval


def _validate_live(master: dict) -> None:
    for row in master["candidates"]:
        path = Path(row["path"])
        if not path.is_file():
            raise metadata_batch.BatchRefused(f"approved live path is missing: {path}")
        current = {
            "sha256": intake.sha256_file(path),
            "genre": intake.read_genre(path),
            "grouping": intake.read_grouping(path)[0],
            "id3_major": intake.read_grouping(path)[1],
            "audio_payload_sha256": intake.sha256_bytes(intake.audio_payload(path)),
            "serato_frames_sha256": intake.serato_digest(path),
        }
        expected = {
            "sha256": row["sha256"], "genre": row["genre"], "grouping": row["grouping"],
            "id3_major": row["id3_major"],
            "audio_payload_sha256": row["audio_payload_sha256"],
            "serato_frames_sha256": row["serato_frames_sha256"],
        }
        for field, value in expected.items():
            if current[field] != value:
                raise metadata_batch.BatchRefused(f"live {field} drift for {path}")
        if path.suffix.casefold() != ".mp3" or current["id3_major"] not in {3, 4}:
            raise metadata_batch.BatchRefused(f"approved item left MP3 ID3v2.3/v2.4 lane: {path}")


def _validate_plans(master: dict, handoff_root: Path) -> None:
    preview = master.get("preview")
    if not isinstance(preview, dict):
        raise metadata_batch.BatchRefused("approved master has no preview hash bindings")
    for kind in ("md", "csv"):
        preview_path = metadata_batch.require_handoff_path(
            Path(preview.get(f"{kind}_path", "")), handoff_root
        )
        if not preview_path.is_file() or metadata_batch.sha256_file(preview_path) != preview.get(f"{kind}_sha256"):
            raise metadata_batch.BatchRefused(f"approved {kind.upper()} preview hash mismatch: {preview_path}")
    union = []
    for scope in master["scopes"]:
        plan_path = metadata_batch.require_handoff_path(Path(scope["plan_path"]), handoff_root)
        if metadata_batch.sha256_file(plan_path) != scope["plan_sha256"]:
            raise metadata_batch.BatchRefused(f"approved plan hash mismatch: {plan_path}")
        validated, _ = intake.load_and_validate(plan_path, Path(scope["source_root"]))
        if len(validated) != scope["item_count"]:
            raise metadata_batch.BatchRefused(f"real validator count mismatch: {scope['scope_id']}")
        union.extend(row["path"] for row in validated)
    approved = [row["path"] for row in master["candidates"]]
    if len(union) != len(set(union)) or sorted(union) != sorted(approved):
        raise metadata_batch.BatchRefused("per-scope plan union differs from approved candidate set")


def _validate_t5(master: dict, t5_volume: Path, mount_checker) -> None:
    if not t5_volume.is_dir() or not mount_checker(str(t5_volume)):
        raise metadata_batch.BatchRefused(f"required T5 volume is not mounted: {t5_volume}")
    for scope in master["scopes"]:
        for field in ("backup_root", "backup_manifest", "evidence_root"):
            metadata_batch.require_t5_path(Path(scope[field]), t5_volume)


def _validate_locks(master: dict) -> None:
    checked = set()
    for scope in master["scopes"]:
        folder = scope["lock_folder"]
        if folder in checked:
            continue
        checked.add(folder)
        try:
            lock = folder_lock.read_lock(folder)
        except folder_lock.CorruptLock as exc:
            raise metadata_batch.BatchRefused(f"corrupt mutation lock blocks batch: {exc}") from exc
        if lock is not None:
            raise metadata_batch.BatchRefused(
                f"existing mutation lock blocks batch (stale locks are not auto-reclaimed): {folder}: {lock}"
            )


def preflight(master_sha: str, handoff_root: Path = metadata_batch.HANDOFF_ROOT,
              t5_volume: Path = metadata_batch.T5_VOLUME, mount_checker=os.path.ismount) -> tuple[dict, dict]:
    handoff_root = Path(handoff_root).resolve()
    state = metadata_batch.read_json(handoff_root / "current-batch.json")
    if state.get("state") != "HUMAN_APPROVED":
        raise metadata_batch.BatchRefused(
            f"apply requires HUMAN_APPROVED durable state, found {state.get('state')}"
        )
    master_path = metadata_batch.require_handoff_path(Path(state["master_path"]), handoff_root)
    master = metadata_batch.verify_master(master_path, master_sha)
    if state.get("master_sha") != master_sha:
        raise metadata_batch.BatchRefused("current-batch master SHA differs from requested SHA")
    approval = _approval(master, state, handoff_root)
    if metadata_batch.repo_head() != master["repo_commit"]:
        raise metadata_batch.BatchRefused("repo HEAD differs from approved commit")
    current_safety = metadata_batch.safety_file_hashes()
    approved_safety = master.get("safety_file_sha256")
    if not isinstance(approved_safety, dict) or current_safety != approved_safety:
        changed = sorted(
            relative for relative in set(current_safety).union(approved_safety or {})
            if current_safety.get(relative) != (approved_safety or {}).get(relative)
        )
        raise metadata_batch.BatchRefused(
            f"gated safety files changed after approval: {changed}"
        )
    _validate_plans(master, handoff_root)
    _validate_live(master)
    _validate_t5(master, Path(t5_volume).resolve(), mount_checker)
    _validate_locks(master)
    return master, approval


def _lock_command(command: str, folder: str, session: str, master: dict) -> dict:
    cmd = [
        sys.executable, str(metadata_batch.REPO_ROOT / "scripts/folder_lock.py"), command,
        "--folder", folder, "--session", session,
    ]
    if command == "acquire":
        cmd += [
            "--agent", "metadata-batch", "--repo-sha", master["repo_commit"],
            "--note", f"approved metadata batch {master['batch_id']}",
        ]
    result = subprocess.run(cmd, text=True, capture_output=True)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise metadata_batch.BatchRefused(
            f"lock {command} returned non-JSON output: {result.stdout} {result.stderr}"
        ) from exc
    if result.returncode != 0:
        raise metadata_batch.BatchRefused(f"lock {command} refused for {folder}: {payload}")
    expected_flag = "acquired" if command == "acquire" else "released"
    if not payload.get(expected_flag):
        raise metadata_batch.BatchRefused(
            f"lock {command} did not confirm {expected_flag} for {folder}: {payload}"
        )
    return payload


def _verify_scope(master: dict, scope: dict, result: dict) -> dict:
    scope_rows = [row for row in master["candidates"] if row["scope_id"] == scope["scope_id"]]
    candidate_paths = {Path(row["path"]).resolve() for row in scope_rows}
    for row in scope_rows:
        path = Path(row["path"])
        if intake.read_genre(path) != row["proposed_genre"]:
            raise metadata_batch.BatchRefused(f"post-apply Genre mismatch: {path}")
        grouping, major = intake.read_grouping(path)
        proposed_parts = [part.strip() for part in row["proposed_grouping"].split(";") if part.strip()]
        if grouping != proposed_parts:
            raise metadata_batch.BatchRefused(f"post-apply Grouping mismatch: {path}")
        if major != row["id3_major"]:
            raise metadata_batch.BatchRefused(f"post-apply ID3 version changed: {path}")
        if intake.sha256_bytes(intake.audio_payload(path)) != row["audio_payload_sha256"]:
            raise metadata_batch.BatchRefused(f"post-apply audio payload changed: {path}")
        if intake.serato_digest(path) != row["serato_frames_sha256"]:
            raise metadata_batch.BatchRefused(f"post-apply Serato frames changed: {path}")

    source_root, backup_root = Path(scope["source_root"]), Path(scope["backup_root"])
    source_files, backup_files = _walk(source_root), _walk(backup_root)
    if set(source_files) != set(backup_files):
        raise metadata_batch.BatchRefused(f"unauthorized missing/extra file in scope {scope['scope_id']}")
    unauthorized = []
    for relative, source in source_files.items():
        if source.resolve() not in candidate_paths:
            if metadata_batch.sha256_file(source) != metadata_batch.sha256_file(backup_files[relative]):
                unauthorized.append(str(source))
    if unauthorized:
        raise metadata_batch.BatchRefused(f"unauthorized non-candidate changes: {unauthorized}")

    journal = metadata_batch.require_t5_path(Path(result["recovery_journal"]), metadata_batch.T5_VOLUME)
    recovery = mutation_engine.recover(journal)
    if recovery["rollback_failures"]:
        raise metadata_batch.RollbackFailed(
            f"ROLLBACK_FAILED in {scope['scope_id']}: {recovery['in_flight']}"
        )
    if not recovery["clean"] or not recovery["runs"] or not all(
        run.get("complete") for run in recovery["runs"].values()
    ):
        raise metadata_batch.BatchRefused(f"recovery journal is not clean and complete: {recovery}")
    return {"files": len(scope_rows), "unauthorized_changes": 0, "recovery": recovery}


def inspect_failed_run(evidence_root: Path, t5_volume: Path) -> None:
    """Classify a failed child apply from its durable recovery journals."""
    evidence_root = metadata_batch.require_t5_path(evidence_root, t5_volume)
    journals = sorted(evidence_root.glob("*-recovery-*.jsonl"))
    if not journals:
        raise metadata_batch.BatchRefused(
            f"apply failed before producing a recovery journal: {evidence_root}"
        )
    reports = [mutation_engine.recover(path) for path in journals]
    failed_rollbacks = [report for report in reports if report.get("rollback_failures")]
    if failed_rollbacks:
        raise metadata_batch.RollbackFailed(
            f"ROLLBACK_FAILED; manual restore required: {failed_rollbacks}"
        )
    interrupted = [
        report for report in reports
        if not report.get("clean")
        or not report.get("runs")
        or not all(run.get("complete") for run in report["runs"].values())
    ]
    if interrupted:
        raise metadata_batch.BatchRefused(
            f"interrupted apply; recovery inspection required: {interrupted}"
        )
    raise metadata_batch.BatchRefused(
        f"child apply failed despite clean journals; inspect stdout/stderr: {evidence_root}"
    )


def _write_spotcheck(master: dict, handoff_root: Path) -> Path:
    path = Path(handoff_root) / "batches" / master["batch_id"] / "SERATO-SPOTCHECK.md"
    lines = [
        f"# Serato spot-check — {master['batch_id']}", "",
        "Open Serato manually. This pipeline does not automate its UI.", "",
        "Check these changed tracks and the listed expected rule-level effects:", "",
    ]
    rows = [row for row in master["candidates"] if row["smart_crate_effects"]]
    if not rows:
        rows = master["candidates"][:min(10, len(master["candidates"]))]
    for row in rows:
        effects = "; ".join(
            f"{item['crate']}: {item['field']} {item['effect']} ({item['operator']} {item['value']!r})"
            for item in row["smart_crate_effects"]
        ) or "no predicted Genre/Grouping Smart Crate membership change"
        lines.append(f"- `{row['path']}` — {effects}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def apply_batch(master_sha: str, handoff_root: Path = metadata_batch.HANDOFF_ROOT,
                t5_volume: Path = metadata_batch.T5_VOLUME, mount_checker=os.path.ismount) -> dict:
    handoff_root, t5_volume = Path(handoff_root).resolve(), Path(t5_volume).resolve()
    state_path = handoff_root / "current-batch.json"
    active_scope_id = None
    try:
        master, _ = preflight(master_sha, handoff_root, t5_volume, mount_checker)
        scope_states = {
            scope["scope_id"]: {"state": "PREFLIGHT_PASS", "history": [
                {"state": "PREFLIGHT_PASS", "at": metadata_batch.now()}
            ]} for scope in master["scopes"]
        }
        metadata_batch.transition(state_path, "PLANNED", handoff_root=handoff_root,
                                  updates={"scopes": scope_states})
        metadata_batch.transition(state_path, "PREFLIGHT_PASS", handoff_root=handoff_root)
        batch_phase = "PREFLIGHT_PASS"
        verified = 0
        locks_released = True
        evidence_roots = []
        for scope in master["scopes"]:
            scope_id = scope["scope_id"]
            active_scope_id = scope_id
            session = f"batch-{master['batch_id']}-{scope_id}"
            acquired = False
            scope_failure = None
            try:
                # Refuse a mount disappearance or lock race immediately before this scope.
                _validate_t5({"scopes": [scope]}, t5_volume, mount_checker)
                acquired_payload = _lock_command("acquire", scope["lock_folder"], session, master)
                acquired = True
                heartbeat = lock_guard.LockHeartbeat(scope["lock_folder"], session)
                heartbeat.pin_run_id(acquired_payload["lock"])
                manifest = metadata_batch.create_verified_backup(
                    Path(scope["source_root"]), Path(scope["backup_root"]),
                    Path(scope["backup_manifest"]), t5_volume=t5_volume,
                    heartbeat=heartbeat,
                )
                if manifest["status"] != backup_manifest.STATUS_PASS:
                    raise metadata_batch.BatchRefused(f"backup manifest did not PASS: {scope_id}")
                metadata_batch.update_scope(
                    state_path, scope_id, "BACKED_UP",
                    details={"backup_manifest_sha256": manifest["manifest_sha256"]},
                    handoff_root=handoff_root,
                )
                if batch_phase == "PREFLIGHT_PASS":
                    metadata_batch.transition(state_path, "BACKED_UP", handoff_root=handoff_root)
                    batch_phase = "BACKED_UP"

                evidence_root = metadata_batch.require_t5_path(Path(scope["evidence_root"]), t5_volume)
                evidence_root.mkdir(parents=True, exist_ok=False)
                cmd = [
                    sys.executable, str(metadata_batch.REPO_ROOT / "scripts/apply_new_music_intake_plan.py"),
                    "--root", scope["source_root"], "--plan", scope["plan_path"],
                    "--backup-manifest", scope["backup_manifest"], "--out-dir", str(evidence_root),
                    "--label", f"metadata-{master['batch_id']}-{scope_id}", "--apply",
                    "--lock-folder", scope["lock_folder"], "--session", session,
                ]
                run = subprocess.run(cmd, text=True, capture_output=True)
                if run.returncode != 0:
                    inspect_failed_run(evidence_root, t5_volume)
                result = json.loads(run.stdout)
                metadata_batch.update_scope(state_path, scope_id, "APPLIED",
                                            details={"result_path": result.get("result_path")},
                                            handoff_root=handoff_root)
                if batch_phase == "BACKED_UP":
                    metadata_batch.transition(state_path, "APPLIED", handoff_root=handoff_root)
                    batch_phase = "APPLIED"
                proof = _verify_scope(master, scope, result)
                metadata_batch.update_scope(state_path, scope_id, "VERIFIED", details=proof,
                                            handoff_root=handoff_root)
                verified += proof["files"]
                evidence_roots.append(str(evidence_root))
            except BaseException as exc:
                scope_failure = exc
                raise
            finally:
                if acquired:
                    try:
                        released = _lock_command("release", scope["lock_folder"], session, master)
                        locks_released = locks_released and bool(released.get("released"))
                    except Exception:
                        locks_released = False
                        # Never mask the more severe fact that rollback failed.
                        if scope_failure is None:
                            raise
        metadata_batch.transition(state_path, "VERIFIED", handoff_root=handoff_root)
        spotcheck = _write_spotcheck(master, handoff_root)
        summary = {
            "BATCH RESULT": "PASS",
            "FILES VERIFIED": verified,
            "AUDIO PAYLOAD": "PASS",
            "SERATO FRAMES": "PASS",
            "ID3 PRESERVATION": "PASS",
            "UNAUTHORIZED CHANGES": 0,
            "RECOVERY STATUS": "CLEAN",
            "T5 EVIDENCE PATH": evidence_roots,
            "LOCKS RELEASED": locks_released,
            "SERATO SPOTCHECK LIST": str(spotcheck),
            "READY FOR SERATO SPOT-CHECK": bool(locks_released),
        }
        metadata_batch.atomic_write_json(
            Path(state_path.parent) / "batches" / master["batch_id"] / "apply-summary.json",
            summary, handoff_root=handoff_root,
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
                    details={"failure": str(exc)}, handoff_root=handoff_root,
                )
            state = metadata_batch.read_json(state_path)
            if state.get("state") not in {"ROLLBACK_FAILED", "COMPLETE"}:
                metadata_batch.transition(
                    state_path, "ROLLBACK_FAILED", reason=str(exc), handoff_root=handoff_root
                )
        except Exception:
            pass
        raise
    except BaseException as exc:
        try:
            state = metadata_batch.read_json(state_path)
            if state.get("state") not in {"BLOCKED", "COMPLETE"}:
                metadata_batch.transition(state_path, "BLOCKED", reason=str(exc), handoff_root=handoff_root)
        except Exception:
            pass
        raise


def record_spotcheck(master_sha: str, result: str, checked_by: str,
                     handoff_root: Path = metadata_batch.HANDOFF_ROOT) -> dict:
    handoff_root = Path(handoff_root).resolve()
    state_path = handoff_root / "current-batch.json"
    state = metadata_batch.read_json(state_path)
    if state.get("state") != "VERIFIED":
        raise metadata_batch.BatchRefused(f"spot-check requires VERIFIED, found {state.get('state')}")
    if not checked_by.strip():
        raise metadata_batch.BatchRefused("--checked-by must be explicit and non-blank")
    master_path = metadata_batch.require_handoff_path(Path(state["master_path"]), handoff_root)
    master = metadata_batch.verify_master(master_path, master_sha)
    record = {
        "batch_id": master["batch_id"], "master_sha": master_sha,
        "result": result, "checked_by": checked_by.strip(), "checked_at": metadata_batch.now(),
    }
    path = Path(state["batch_dir"]) / "serato-spotcheck.json"
    metadata_batch.atomic_write_json(path, record, handoff_root=handoff_root)
    if result == "FAIL":
        metadata_batch.transition(state_path, "BLOCKED", reason="human Serato spot-check failed",
                                  handoff_root=handoff_root)
    else:
        metadata_batch.transition(state_path, "SERATO_SPOTCHECK", handoff_root=handoff_root)
        metadata_batch.transition(state_path, "COMPLETE", handoff_root=handoff_root)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master-sha", required=True)
    parser.add_argument("--record-serato-spotcheck", choices=("PASS", "FAIL"))
    parser.add_argument("--checked-by")
    args = parser.parse_args()
    try:
        if args.record_serato_spotcheck:
            if not args.checked_by:
                raise metadata_batch.BatchRefused("--checked-by is required for a spot-check record")
            result = record_spotcheck(args.master_sha, args.record_serato_spotcheck,
                                      args.checked_by, metadata_batch.HANDOFF_ROOT)
        else:
            if args.checked_by:
                raise metadata_batch.BatchRefused("--checked-by is only valid with --record-serato-spotcheck")
            result = apply_batch(args.master_sha, metadata_batch.HANDOFF_ROOT)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (metadata_batch.BatchRefused, backup_manifest.BackupGateError,
            mutation_engine.MutationRefused, json.JSONDecodeError) as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
