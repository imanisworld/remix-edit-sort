from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rekordbox_unresolved_review  # noqa: E402


def candidate(
    file_id: int,
    path: str,
    *,
    score: int = 80,
    evidence: list[str] | None = None,
    conflicts: list[str] | None = None,
    differences: list[str] | None = None,
    material_change: bool = False,
) -> dict[str, object]:
    return {
        "file_id": file_id,
        "path": path,
        "score": score,
        "evidence": list(evidence or []),
        "conflicts": list(conflicts or []),
        "differences": list(differences or []),
        "material_change": material_change,
    }


def track(
    track_id: str,
    status: str,
    *,
    candidates: list[dict[str, object]] | None = None,
    proposed_file_id: int | None = None,
    proposed_path: str | None = None,
) -> dict[str, object]:
    resolved = status in {"UNCHANGED", "STRONG_CANDIDATE"}
    return {
        "rekordbox_track_id": track_id,
        "old_path": f"/old/{track_id}.mp3",
        "name": f"Track {track_id}",
        "artist": "Artist",
        "status": status,
        "proposed_file_id": proposed_file_id,
        "proposed_path": proposed_path,
        "bridge_eligible": resolved,
        "review_required": not resolved,
        "top_candidates": list(candidates or []),
    }


def preview_fixture() -> dict[str, object]:
    tracks = [
        track(
            "1",
            "UNCHANGED",
            candidates=[candidate(1, "/music/1.mp3", score=250)],
            proposed_file_id=1,
            proposed_path="/music/1.mp3",
        ),
        track(
            "2",
            "STRONG_CANDIDATE",
            candidates=[
                candidate(
                    2,
                    "/music/2.mp3",
                    score=150,
                    evidence=["sha256_move_lineage"],
                )
            ],
            proposed_file_id=2,
            proposed_path="/music/2.mp3",
        ),
        track(
            "3",
            "AMBIGUOUS",
            candidates=[
                candidate(
                    3,
                    "/music/3a.mp3",
                    score=70,
                    evidence=["filename_exact"],
                ),
                candidate(
                    4,
                    "/music/3b.mp3",
                    score=66,
                    evidence=["current_file_has_exact_serato_reference"],
                ),
            ],
            proposed_file_id=3,
            proposed_path="/music/3a.mp3",
        ),
        track(
            "4",
            "CHANGED",
            candidates=[
                candidate(
                    5,
                    "/music/4.mp3",
                    score=130,
                    evidence=["historical_sha256_move_lineage"],
                    conflicts=["changed_after_sha256_move", "byte_size_changed"],
                    differences=["genre_changed"],
                    material_change=True,
                )
            ],
            proposed_file_id=5,
            proposed_path="/music/4.mp3",
        ),
        track("5", "MISSING"),
    ]
    return {
        "tool": "rekordbox_relocation_preview",
        "tool_version": "1.0.1",
        "preview_schema_version": 1,
        "safety": {
            "classification": "read-only",
            "sqlite_mode": "read-only + query_only",
            "output": "stdout only",
            "writes_rekordbox": False,
            "writes_serato": False,
            "writes_audio": False,
            "writes_library_index": False,
            "automatic_approval": False,
        },
        "library": {"scan_id": 9, "file_count": 5},
        "rekordbox": {"import_id": 2, "collection_tracks": 5},
        "serato": {"available": True, "import_id": 4},
        "summary": {
            "total": 5,
            "unchanged": 1,
            "strong_candidate": 1,
            "ambiguous": 1,
            "changed": 1,
            "missing": 1,
            "bridge_eligible": 2,
            "review_required": 3,
        },
        "tracks": tracks,
    }


class RekordboxUnresolvedReviewTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_builds_only_unresolved_review_queues_with_no_auto_decisions(self):
        preview = preview_fixture()
        packet = rekordbox_unresolved_review.build_review_packet(
            preview, source_preview_sha256="a" * 64
        )

        self.assertEqual(packet["summary"]["unresolved_total"], 3)
        self.assertEqual(packet["summary"]["already_resolved"], 2)
        self.assertEqual(packet["summary"]["automatic_decisions"], 0)
        self.assertEqual(len(packet["queues"]["ambiguous"]), 1)
        self.assertEqual(len(packet["queues"]["changed"]), 1)
        self.assertEqual(len(packet["queues"]["missing"]), 1)

        ids = {
            item["rekordbox_track_id"]
            for queue in packet["queues"].values()
            for item in queue
        }
        self.assertEqual(ids, {"3", "4", "5"})
        for queue in packet["queues"].values():
            for item in queue:
                self.assertIsNone(item["decision"])
                self.assertFalse(item["automatic_decision"])
                self.assertEqual(item["decision_evidence"], [])

    def test_candidate_evidence_is_preserved_and_summarized_not_reinterpreted(self):
        packet = rekordbox_unresolved_review.build_review_packet(
            preview_fixture(), source_preview_sha256="b" * 64
        )
        changed = packet["queues"]["changed"][0]

        self.assertEqual(
            changed["top_candidates"][0]["evidence"],
            ["historical_sha256_move_lineage"],
        )
        self.assertEqual(
            changed["candidate_signals"]["conflict_terms"],
            ["byte_size_changed", "changed_after_sha256_move"],
        )
        self.assertTrue(changed["candidate_signals"]["material_change_present"])
        self.assertTrue(changed["candidate_signals"]["has_sha256_move_lineage"])

        ambiguous = packet["queues"]["ambiguous"][0]
        self.assertEqual(ambiguous["candidate_signals"]["top_score_gap"], 4)
        self.assertEqual(ambiguous["candidate_signals"]["candidate_count"], 2)

    def test_decision_options_force_explicit_human_review(self):
        packet = rekordbox_unresolved_review.build_review_packet(
            preview_fixture(), source_preview_sha256="c" * 64
        )
        ambiguous = packet["queues"]["ambiguous"][0]
        changed = packet["queues"]["changed"][0]
        missing = packet["queues"]["missing"][0]

        self.assertIn("APPROVE_CURRENT_FILE", ambiguous["allowed_decisions"])
        self.assertIn(
            "MARK_INTENTIONAL_VERSION_CONFLICT",
            ambiguous["allowed_decisions"],
        )
        self.assertIn(
            "APPROVE_CHANGED_CURRENT_FILE",
            changed["allowed_decisions"],
        )
        self.assertIn("MARK_GENUINELY_ABSENT", missing["allowed_decisions"])
        self.assertIn("MARK_STALE_REKORDBOX_RECORD", missing["allowed_decisions"])

    def test_rejects_unresolved_track_marked_bridge_eligible(self):
        preview = preview_fixture()
        preview["tracks"][2]["bridge_eligible"] = True
        with self.assertRaisesRegex(
            RuntimeError, "unresolved but bridge_eligible=true"
        ):
            rekordbox_unresolved_review.build_review_packet(
                preview, source_preview_sha256="d" * 64
            )

    def test_rejects_tampered_summary(self):
        preview = preview_fixture()
        preview["summary"]["missing"] = 99
        with self.assertRaisesRegex(RuntimeError, "summary mismatch for missing"):
            rekordbox_unresolved_review.build_review_packet(
                preview, source_preview_sha256="e" * 64
            )

    def test_rejects_duplicate_rekordbox_track_ids(self):
        preview = preview_fixture()
        duplicate = copy.deepcopy(preview["tracks"][4])
        duplicate["rekordbox_track_id"] = "4"
        preview["tracks"].append(duplicate)
        preview["summary"]["total"] += 1
        preview["summary"]["missing"] += 1
        preview["summary"]["review_required"] += 1

        with self.assertRaisesRegex(RuntimeError, "duplicate Rekordbox track id"):
            rekordbox_unresolved_review.build_review_packet(
                preview, source_preview_sha256="f" * 64
            )

    def test_rejects_unsafe_source_preview_flags(self):
        preview = preview_fixture()
        preview["safety"]["automatic_approval"] = True
        with self.assertRaisesRegex(RuntimeError, "unsafe preview flag set"):
            rekordbox_unresolved_review.build_review_packet(
                preview, source_preview_sha256="0" * 64
            )

    def test_input_file_is_read_only_and_sha_bound(self):
        path = self.tmp / "preview.json"
        raw = json.dumps(
            preview_fixture(), indent=2, ensure_ascii=False
        ).encode("utf-8")
        path.write_bytes(raw)
        before = path.read_bytes()

        loaded_raw, preview = rekordbox_unresolved_review._load_preview(path)
        packet = rekordbox_unresolved_review.build_review_packet(
            preview,
            source_preview_sha256=hashlib.sha256(loaded_raw).hexdigest(),
        )

        after = path.read_bytes()
        self.assertEqual(before, after)
        self.assertEqual(
            packet["source"]["preview_sha256"],
            hashlib.sha256(raw).hexdigest(),
        )

    def test_symlink_preview_is_refused(self):
        path = self.tmp / "preview.json"
        path.write_text(json.dumps(preview_fixture()), encoding="utf-8")
        alias = self.tmp / "preview-alias.json"
        alias.symlink_to(path)

        with self.assertRaisesRegex(RuntimeError, "must not be a symlink"):
            rekordbox_unresolved_review._load_preview(alias)

    def test_missing_track_cannot_carry_proposed_target(self):
        preview = preview_fixture()
        preview["tracks"][4]["proposed_file_id"] = 77
        preview["tracks"][4]["proposed_path"] = "/music/not-missing.mp3"
        with self.assertRaisesRegex(
            RuntimeError, "MISSING but still has a proposed target"
        ):
            rekordbox_unresolved_review.build_review_packet(
                preview, source_preview_sha256="1" * 64
            )


if __name__ == "__main__":
    unittest.main()
