"""Behavior tests for the Clean/Dirty lane, intake lane, exact version-label
automation, and the confirmed larger batch limit.

All media is synthetic and every writable root is a TemporaryDirectory path.
No test touches the real library, real Serato data, or the production
handoff state.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import json  # noqa: E402

import metadata_batch  # noqa: E402
import mutation_surface  # noqa: E402
import prepare_metadata_batch  # noqa: E402
from fixtures import make_library  # noqa: E402


class _LaneCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = self.root / "Music"
        self.library.mkdir()
        self.serato = self.root / "_Serato_"
        (self.serato / "SmartCrates").mkdir(parents=True)
        self.handoff = self.root / "repo-handoff"
        self.handoff.mkdir()


class CleanDirtyLaneTests(_LaneCase):
    def discover(self, maximum=150):
        return prepare_metadata_batch.discover_clean_dirty(
            self.library, self.serato, maximum
        )

    def test_folder_evidence_appends_and_preserves_unrelated_grouping(self):
        make_library(self.library, [{
            "relative": "Hip Hop/CLEAN/song.mp3", "genre": "Hip-Hop",
            "title": "Song", "grouping": "Event: Cookout; Old Head", "v2_version": 3,
        }])
        rows, exclusions = self.discover()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["grouping_add_terms"], ["Clean"])
        self.assertEqual(rows[0]["proposed_grouping"], "Event: Cookout; Old Head; Clean")
        self.assertEqual(rows[0]["grouping"], ["Event: Cookout", "Old Head"])
        self.assertEqual(rows[0]["proposed_genre"], rows[0]["genre"])
        self.assertEqual(rows[0]["lock_folder"], "Hip Hop")

    def test_bracketed_label_evidence_without_folder(self):
        make_library(self.library, [{
            "relative": "R&B/Slow Jams/song (Dirty).mp3", "genre": "R&B",
            "title": "Song (Dirty)", "grouping": "Holiday", "v2_version": 4,
        }])
        rows, _ = self.discover()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["grouping_add_terms"], ["Dirty"])
        self.assertEqual(rows[0]["proposed_grouping"], "Holiday; Dirty")

    def test_conflicting_folder_and_label_evidence_is_held(self):
        make_library(self.library, [{
            "relative": "Hip Hop/CLEAN/song (Dirty).mp3", "genre": "Hip-Hop",
            "title": "Song (Dirty)", "v2_version": 3,
        }])
        rows, exclusions = self.discover()
        self.assertEqual(rows, [])
        self.assertEqual(exclusions["conflicting Clean/Dirty evidence"], 1)

    def test_evidence_conflicting_with_existing_status_is_held(self):
        make_library(self.library, [{
            "relative": "Hip Hop/CLEAN/song.mp3", "genre": "Hip-Hop",
            "title": "Song", "grouping": "Dirty", "v2_version": 3,
        }])
        rows, exclusions = self.discover()
        self.assertEqual(rows, [])
        self.assertEqual(exclusions["conflicting Clean/Dirty evidence"], 1)

    def test_existing_status_is_not_duplicated(self):
        make_library(self.library, [{
            "relative": "Hip Hop/DIRTY/song.mp3", "genre": "Hip-Hop",
            "title": "Song", "grouping": "Dirty; Old Head", "v2_version": 3,
        }])
        rows, exclusions = self.discover()
        self.assertEqual(rows, [])
        self.assertEqual(exclusions["already carries its Clean/Dirty status"], 1)

    def test_needs_check_files_are_left_to_other_lanes(self):
        make_library(self.library, [{
            "relative": "Hip Hop/_GENRE NEEDS CHECK/song (Clean).mp3",
            "genre": "Hip-Hop", "title": "Song (Clean)", "v2_version": 3,
        }])
        rows, exclusions = self.discover()
        self.assertEqual(rows, [])
        self.assertEqual(exclusions["left to the needs-check lanes"], 1)

    def test_id3v22_is_skipped_not_upgraded(self):
        make_library(self.library, [{
            "relative": "Hip Hop/CLEAN/old.mp3", "genre": "Hip-Hop", "v2_version": 2,
        }])
        rows, exclusions = self.discover()
        self.assertEqual(rows, [])
        self.assertEqual(exclusions["ID3v2.2"], 1)

    def test_full_prepare_never_plans_a_genre_change(self):
        # "hip hop" is alias-mappable, but the Clean/Dirty lane must leave it.
        make_library(self.library, [{
            "relative": "Hip Hop/CLEAN/song.mp3", "genre": "hip hop",
            "title": "Song", "grouping": "Old Head", "v2_version": 3,
        }])
        master = prepare_metadata_batch.prepare(
            self.library, self.serato, self.handoff, 150, "mp3", lane="clean-dirty"
        )
        self.assertEqual(master["lane"], "clean-dirty")
        self.assertIn("cleandirty", master["batch_id"])
        self.assertEqual(master["candidate_count"], 1)
        self.assertEqual(master["genre_changes"], 0)
        self.assertEqual(master["grouping_changes"], 1)
        plan = json.loads(Path(master["scopes"][0]["plan_path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(plan["items"]), 1)
        self.assertNotIn("genre_fix", plan["items"][0])
        self.assertEqual(plan["items"][0]["grouping_fix"]["add_terms"], ["Clean"])
        self.assertEqual(plan["items"][0]["grouping_fix"]["existing"], ["Old Head"])
        state = metadata_batch.read_json(self.handoff / "current-batch.json")
        self.assertEqual(state["state"], "PREVIEWED")


class TranscriptEvidenceCleanDirtyTests(_LaneCase):
    """--transcript-evidence: confirmed Dirty hits are evidence; silence never proves Clean."""

    def _write_evidence(self, rows):
        evidence_path = self.root / "listen-check.json"
        evidence_path.write_text(json.dumps({"results": rows}), encoding="utf-8")
        return evidence_path

    def discover(self, evidence_path=None, maximum=150):
        transcript_dirty = (
            prepare_metadata_batch.load_transcript_dirty_evidence(evidence_path)
            if evidence_path is not None else None
        )
        return prepare_metadata_batch.discover_clean_dirty(
            self.library, self.serato, maximum, transcript_dirty_evidence=transcript_dirty
        )

    def test_confirmed_transcript_hit_makes_track_dirty_eligible(self):
        make_library(self.library, [{
            "relative": "Hip Hop/Random Set/song.mp3", "genre": "Hip-Hop",
            "title": "Song", "grouping": "Old Head", "v2_version": 3,
        }])
        path = str((self.library / "Hip Hop/Random Set/song.mp3").resolve())
        sha = prepare_metadata_batch.read_track(Path(path))["sha256"]
        evidence = self._write_evidence([
            {"path": path, "sha256": sha, "classification": "Dirty"},
        ])
        rows, _ = self.discover(evidence)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["grouping_add_terms"], ["Dirty"])
        self.assertEqual(rows[0]["proposed_grouping"], "Old Head; Dirty")
        self.assertIn("faster-whisper transcript", rows[0]["grouping_evidence"][0])

    def test_transcript_silence_alone_does_not_prove_clean(self):
        make_library(self.library, [{
            "relative": "Hip Hop/Random Set/song.mp3", "genre": "Hip-Hop",
            "title": "Song", "v2_version": 3,
        }])
        path = str((self.library / "Hip Hop/Random Set/song.mp3").resolve())
        sha = prepare_metadata_batch.read_track(Path(path))["sha256"]
        # A "Clean" classification in the evidence file must never be loaded
        # or trusted -- load_transcript_dirty_evidence only reads Dirty rows.
        evidence = self._write_evidence([
            {"path": path, "sha256": sha, "classification": "Clean"},
        ])
        transcript_dirty = prepare_metadata_batch.load_transcript_dirty_evidence(evidence)
        self.assertEqual(transcript_dirty, {})
        rows, exclusions = self.discover(evidence)
        self.assertEqual(rows, [])

    def test_independent_metadata_evidence_still_proves_clean(self):
        make_library(self.library, [{
            "relative": "Hip Hop/CLEAN/song.mp3", "genre": "Hip-Hop",
            "title": "Song", "grouping": "Old Head", "v2_version": 3,
        }])
        # No transcript evidence at all for this track -- folder evidence alone resolves it.
        rows, _ = self.discover(evidence_path=None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["grouping_add_terms"], ["Clean"])

    def test_unrelated_grouping_preserved_alongside_transcript_dirty(self):
        make_library(self.library, [{
            "relative": "Hip Hop/Random Set/song.mp3", "genre": "Hip-Hop",
            "title": "Song", "grouping": "Event: Cookout; Old Head", "v2_version": 3,
        }])
        path = str((self.library / "Hip Hop/Random Set/song.mp3").resolve())
        sha = prepare_metadata_batch.read_track(Path(path))["sha256"]
        evidence = self._write_evidence([
            {"path": path, "sha256": sha, "classification": "Dirty"},
        ])
        rows, _ = self.discover(evidence)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["grouping"], ["Event: Cookout", "Old Head"])
        self.assertEqual(rows[0]["proposed_grouping"], "Event: Cookout; Old Head; Dirty")

    def test_transcript_dirty_contradicting_existing_clean_status_is_held(self):
        make_library(self.library, [{
            "relative": "Hip Hop/Random Set/song.mp3", "genre": "Hip-Hop",
            "title": "Song", "grouping": "Clean", "v2_version": 3,
        }])
        path = str((self.library / "Hip Hop/Random Set/song.mp3").resolve())
        sha = prepare_metadata_batch.read_track(Path(path))["sha256"]
        evidence = self._write_evidence([
            {"path": path, "sha256": sha, "classification": "Dirty"},
        ])
        rows, exclusions = self.discover(evidence)
        self.assertEqual(rows, [])
        self.assertEqual(exclusions["conflicting Clean/Dirty evidence"], 1)

    def test_transcript_dirty_matching_existing_dirty_status_adds_nothing(self):
        make_library(self.library, [{
            "relative": "Hip Hop/Random Set/song.mp3", "genre": "Hip-Hop",
            "title": "Song", "grouping": "Dirty", "v2_version": 3,
        }])
        path = str((self.library / "Hip Hop/Random Set/song.mp3").resolve())
        sha = prepare_metadata_batch.read_track(Path(path))["sha256"]
        evidence = self._write_evidence([
            {"path": path, "sha256": sha, "classification": "Dirty"},
        ])
        rows, exclusions = self.discover(evidence)
        self.assertEqual(rows, [])
        self.assertEqual(exclusions["already carries its Clean/Dirty status"], 1)

    def test_stale_transcript_evidence_is_never_trusted(self):
        make_library(self.library, [{
            "relative": "Hip Hop/Random Set/song.mp3", "genre": "Hip-Hop",
            "title": "Song", "v2_version": 3,
        }])
        path = str((self.library / "Hip Hop/Random Set/song.mp3").resolve())
        evidence = self._write_evidence([
            {"path": path, "sha256": "0" * 64, "classification": "Dirty"},
        ])
        rows, exclusions = self.discover(evidence)
        self.assertEqual(rows, [])
        self.assertEqual(exclusions["transcript evidence stale (sha256 drift since transcription)"], 1)


class VersionLabelBatchTests(_LaneCase):
    def discover(self, maximum=150):
        return prepare_metadata_batch.discover(self.library, self.serato, maximum)

    def test_bracketed_labels_flow_as_their_exact_terms(self):
        make_library(self.library, [{
            "relative": "House/_GENRE NEEDS CHECK/a (Radio Edit).mp3",
            "genre": "House", "title": "A (Radio Edit)",
            "grouping": "Top 40", "v2_version": 3,
        }])
        rows, _ = self.discover()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["grouping_add_terms"], ["Radio Edit"])
        self.assertNotIn("Edit", rows[0]["grouping_add_terms"])
        self.assertEqual(rows[0]["proposed_grouping"], "Top 40; Radio Edit")

    def test_unbracketed_labels_stay_held(self):
        make_library(self.library, [
            {"relative": "House/_GENRE NEEDS CHECK/a.mp3", "genre": "house",
             "title": "My Radio Edit", "v2_version": 3},
            {"relative": "House/_GENRE NEEDS CHECK/b.mp3", "genre": "house",
             "title": "Flip Dinero Anthem", "v2_version": 3},
            {"relative": "House/_GENRE NEEDS CHECK/c.mp3", "genre": "house",
             "title": "Total Rework", "v2_version": 3},
        ])
        rows, exclusions = self.discover()
        self.assertEqual(rows, [])
        self.assertEqual(exclusions["Radio Edit"], 1)
        self.assertEqual(exclusions["Flip"], 1)
        self.assertEqual(exclusions["ReWork"], 1)

    def test_version_labels_never_touch_other_metadata_fields(self):
        make_library(self.library, [{
            "relative": "House/_GENRE NEEDS CHECK/a (Flip).mp3",
            "genre": "House", "title": "A (Flip)", "artist": "Some Artist",
            "grouping": "Holiday; Instrumental", "v2_version": 4,
        }])
        master = prepare_metadata_batch.prepare(
            self.library, self.serato, self.handoff, 150, "mp3"
        )
        plan = json.loads(Path(master["scopes"][0]["plan_path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(plan["items"]), 1)
        # The plan may carry only the two reviewed fix shapes; nothing that
        # could rewrite Artist/Title/Year or move the file.
        self.assertLessEqual(set(plan["items"][0]), {"path", "sha256", "genre_fix", "grouping_fix"})
        self.assertNotIn("genre_fix", plan["items"][0])
        fix = plan["items"][0]["grouping_fix"]
        self.assertEqual(fix["existing"], ["Holiday", "Instrumental"])
        self.assertEqual(fix["add_terms"], ["Flip"])

    def test_multi_value_grouping_remains_intact(self):
        make_library(self.library, [{
            "relative": "House/_GENRE NEEDS CHECK/a (Clean).mp3",
            "genre": "House", "title": "A (Clean)",
            "grouping": "Holiday; Instrumental", "v2_version": 3,
        }])
        rows, _ = self.discover()
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["proposed_grouping"], "Holiday; Instrumental; Clean"
        )


class GenreOnlyBatchTests(_LaneCase):
    """--genre-only: propose Genre changes, hold Grouping unchanged (0 additions)."""

    def discover(self, maximum=150, genre_only=True):
        return prepare_metadata_batch.discover(
            self.library, self.serato, maximum, genre_only=genre_only
        )

    def test_grouping_addition_is_suppressed_while_genre_change_survives(self):
        make_library(self.library, [{
            "relative": "House/_GENRE NEEDS CHECK/a (Flip).mp3",
            "genre": "house, techno", "title": "A (Flip)",
            "grouping": "Holiday", "v2_version": 3,
        }])
        rows, _ = self.discover()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["proposed_genre"], "House; Techno")
        self.assertEqual(rows[0]["grouping_add_terms"], [])
        self.assertEqual(rows[0]["proposed_grouping"], "Holiday")

    def test_grouping_only_candidate_is_excluded_not_written_as_noop(self):
        make_library(self.library, [{
            "relative": "House/_GENRE NEEDS CHECK/a (Clean).mp3",
            "genre": "House", "title": "A (Clean)",
            "grouping": "Holiday", "v2_version": 3,
        }])
        rows, exclusions = self.discover()
        self.assertEqual(rows, [])
        self.assertEqual(exclusions["already normalized / no approved change"], 1)

    def test_semantic_alias_genre_change_still_held(self):
        make_library(self.library, [{
            "relative": "House/_GENRE NEEDS CHECK/a.mp3",
            "genre": "Trap", "title": "A", "v2_version": 3,
        }])
        rows, exclusions = self.discover()
        self.assertEqual(rows, [])
        self.assertEqual(exclusions["semantic alias mapping held"], 1)

    def test_plan_item_carries_no_grouping_fix_key(self):
        make_library(self.library, [{
            "relative": "House/_GENRE NEEDS CHECK/a (Flip).mp3",
            "genre": "house, techno", "title": "A (Flip)",
            "grouping": "Holiday", "v2_version": 3,
        }])
        master = prepare_metadata_batch.prepare(
            self.library, self.serato, self.handoff, 150, "mp3", genre_only=True,
        )
        self.assertTrue(master["genre_only"])
        self.assertEqual(master["grouping_changes"], 0)
        plan = json.loads(Path(master["scopes"][0]["plan_path"]).read_text(encoding="utf-8"))
        self.assertNotIn("grouping_fix", plan["items"][0])
        self.assertEqual(plan["counts"]["grouping_fixes"], 0)
        self.assertIn("genre_fix", plan["items"][0])

    def test_genre_only_rejected_outside_genre_lane(self):
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "--genre-only is only valid"):
            prepare_metadata_batch.prepare(
                self.library, self.serato, self.handoff, 150, "mp3",
                lane="clean-dirty", genre_only=True,
            )


class BatchScalingTests(_LaneCase):
    def _seed_one_candidate(self):
        make_library(self.library, [{
            "relative": "House/_GENRE NEEDS CHECK/good.mp3",
            "genre": "house", "title": "Good", "v2_version": 3,
        }])

    def test_max_above_default_requires_matching_confirmation(self):
        self._seed_one_candidate()
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "explicit operator decision"):
            prepare_metadata_batch.prepare(
                self.library, self.serato, self.handoff, 500, "mp3"
            )
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "explicit operator decision"):
            prepare_metadata_batch.prepare(
                self.library, self.serato, self.handoff, 500, "mp3",
                confirmed_maximum=151,
            )
        # The refusal happens before any batch state is created.
        self.assertFalse((self.handoff / "current-batch.json").exists())

    def test_confirmation_without_a_large_max_is_refused(self):
        self._seed_one_candidate()
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "only valid with --max above"):
            prepare_metadata_batch.prepare(
                self.library, self.serato, self.handoff, 100, "mp3",
                confirmed_maximum=100,
            )

    def test_confirmed_larger_limit_is_accepted_and_recorded(self):
        self._seed_one_candidate()
        master = prepare_metadata_batch.prepare(
            self.library, self.serato, self.handoff, 500, "mp3",
            confirmed_maximum=500,
        )
        self.assertEqual(master["batch_limit"], 500)
        self.assertEqual(master["candidate_count"], 1)

    def test_capped_selection_is_deterministic(self):
        make_library(self.library, [
            {"relative": f"House/_GENRE NEEDS CHECK/track-{index}.mp3",
             "genre": "house", "title": f"Track {index}", "v2_version": 3}
            for index in range(3)
        ])
        first_rows, first_exclusions = prepare_metadata_batch.discover(
            self.library, self.serato, 2
        )
        second_rows, _ = prepare_metadata_batch.discover(self.library, self.serato, 2)
        self.assertEqual(
            [row["path"] for row in first_rows],
            [row["path"] for row in second_rows],
        )
        self.assertEqual(len(first_rows), 2)
        self.assertEqual(
            [Path(row["path"]).name for row in first_rows],
            ["track-0.mp3", "track-1.mp3"],
        )
        self.assertEqual(first_exclusions["eligible but deferred by batch cap"], 1)


class IntakeLaneTests(_LaneCase):
    def setUp(self):
        super().setUp()
        self.intake = self.library / "music to move"
        self.intake.mkdir()

    def discover(self, maximum=150):
        return prepare_metadata_batch.discover_intake(
            self.library, self.serato, self.intake, maximum
        )

    def test_unanimous_recognized_consensus_fills_placeholder_genre(self):
        make_library(self.library, [
            {"relative": "music to move/batch1/one.mp3", "genre": "House",
             "title": "One", "v2_version": 3},
            {"relative": "music to move/batch1/two.mp3", "genre": "House",
             "title": "Two", "v2_version": 3},
            {"relative": "music to move/batch1/blank.mp3", "title": "Blank",
             "v2_version": 3},
        ])
        rows, exclusions = self.discover()
        by_name = {Path(row["path"]).name: row for row in rows}
        self.assertIn("blank.mp3", by_name)
        self.assertEqual(by_name["blank.mp3"]["proposed_genre"], "House")
        self.assertIn("unanimous sibling Genre consensus", by_name["blank.mp3"]["genre_evidence"])
        # The already-tagged siblings have nothing to change.
        self.assertEqual(exclusions["already normalized / no approved change"], 2)

    def test_disagreeing_siblings_hold_the_placeholder(self):
        make_library(self.library, [
            {"relative": "music to move/batch1/one.mp3", "genre": "House",
             "title": "One", "v2_version": 3},
            {"relative": "music to move/batch1/two.mp3", "genre": "Jazz",
             "title": "Two", "v2_version": 3},
            {"relative": "music to move/batch1/blank.mp3", "title": "Blank",
             "v2_version": 3},
        ])
        rows, exclusions = self.discover()
        self.assertNotIn(
            "blank.mp3", {Path(row["path"]).name for row in rows}
        )
        self.assertEqual(
            exclusions["Genre placeholder without unanimous tagged-sibling consensus"], 1
        )

    def test_unrecognized_consensus_value_is_held(self):
        make_library(self.library, [
            {"relative": "music to move/batch1/one.mp3", "genre": "Zorbastep",
             "title": "One", "v2_version": 3},
            {"relative": "music to move/batch1/two.mp3", "genre": "Zorbastep",
             "title": "Two", "v2_version": 3},
            {"relative": "music to move/batch1/blank.mp3", "title": "Blank",
             "v2_version": 3},
        ])
        rows, exclusions = self.discover()
        self.assertNotIn("blank.mp3", {Path(row["path"]).name for row in rows})
        self.assertTrue(any(
            reason.startswith("sibling consensus Genre is not a recognized canonical value")
            for reason in exclusions
        ), exclusions)

    def test_grouping_additions_proceed_while_genre_stays_held(self):
        make_library(self.library, [{
            "relative": "music to move/batch1/song (Clean).mp3",
            "title": "Song (Clean)", "v2_version": 3,
        }])
        rows, _ = self.discover()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["grouping_add_terms"], ["Clean"])
        self.assertEqual(rows[0]["proposed_genre"], rows[0]["genre"])
        self.assertEqual(rows[0]["genre"], "")

    def test_intake_root_must_be_inside_the_library(self):
        outside = self.root / "downloads"
        outside.mkdir()
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "inside the library root"):
            prepare_metadata_batch.prepare(
                self.library, self.serato, self.handoff, 150, "mp3",
                lane="intake", intake_root=outside,
            )
        self.assertFalse((self.handoff / "current-batch.json").exists())

    def test_intake_root_is_required_for_the_lane_and_refused_elsewhere(self):
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "--intake-root is required"):
            prepare_metadata_batch.prepare(
                self.library, self.serato, self.handoff, 150, "mp3", lane="intake"
            )
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "only valid with --lane intake"):
            prepare_metadata_batch.prepare(
                self.library, self.serato, self.handoff, 150, "mp3",
                intake_root=self.intake,
            )

    def test_full_intake_prepare_binds_lane_and_validated_plans(self):
        make_library(self.library, [
            {"relative": "music to move/batch1/one.mp3", "genre": "House",
             "title": "One", "v2_version": 3},
            {"relative": "music to move/batch1/two.mp3", "genre": "House",
             "title": "Two", "v2_version": 3},
            {"relative": "music to move/batch1/blank (Dirty).mp3",
             "title": "Blank (Dirty)", "v2_version": 3},
        ])
        master = prepare_metadata_batch.prepare(
            self.library, self.serato, self.handoff, 150, "mp3",
            lane="intake", intake_root=self.intake,
        )
        self.assertEqual(master["lane"], "intake")
        self.assertIn("intake", master["batch_id"])
        self.assertEqual(master["intake_root"], str(self.intake.resolve()))
        self.assertEqual(master["candidate_count"], 1)
        plan = json.loads(Path(master["scopes"][0]["plan_path"]).read_text(encoding="utf-8"))
        item = plan["items"][0]
        self.assertEqual(item["genre_fix"], {"from": "", "to": "House"})
        self.assertEqual(item["grouping_fix"]["add_terms"], ["Dirty"])


class ScopeConstructionTests(_LaneCase):
    """Scope roots must be disjoint folder subtrees (regression for the
    nested S011/S012 scopes found in metadata-mp3-cleandirty-20260823-174725,
    where a candidate folder contained another candidate folder)."""

    def assert_disjoint(self, scopes):
        roots = [Path(scope["source_root"]).resolve() for scope in scopes]
        for i, left in enumerate(roots):
            for right in roots[i + 1:]:
                self.assertNotEqual(left, right)
                self.assertFalse(right.is_relative_to(left), (left, right))
                self.assertFalse(left.is_relative_to(right), (left, right))

    def test_parent_and_child_candidate_folders_coalesce_into_one_scope(self):
        make_library(self.library, [
            {"relative": "Events/Funk/DIRTY/Funk & Soul/one.mp3",
             "genre": "Funk", "title": "One", "v2_version": 3},
            {"relative": "Events/Funk/DIRTY/Funk & Soul/REMIXES/two.mp3",
             "genre": "Funk", "title": "Two", "v2_version": 3},
        ])
        master = prepare_metadata_batch.prepare(
            self.library, self.serato, self.handoff, 150, "mp3", lane="clean-dirty"
        )
        self.assertEqual(master["candidate_count"], 2)
        self.assertEqual(len(master["scopes"]), 1)
        self.assert_disjoint(master["scopes"])
        scope = master["scopes"][0]
        self.assertEqual(
            Path(scope["source_root"]).resolve(),
            (self.library / "Events/Funk/DIRTY/Funk & Soul").resolve(),
        )
        plan = json.loads(Path(scope["plan_path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(plan["items"]), 2)
        # The recorded candidate order must still follow the selection key.
        key = lambda row: (row["preference"], row["scope_root"].casefold(), row["path"].casefold())
        keys = [key(row) for row in master["candidates"]]
        self.assertEqual(keys, sorted(keys))

    def test_three_level_nesting_anchors_to_the_shallowest_folder(self):
        make_library(self.library, [
            {"relative": "Hip Hop/DIRTY/a.mp3", "genre": "Hip-Hop", "v2_version": 3},
            {"relative": "Hip Hop/DIRTY/SUB/b.mp3", "genre": "Hip-Hop", "v2_version": 3},
            {"relative": "Hip Hop/DIRTY/SUB/DEEPER/c.mp3", "genre": "Hip-Hop", "v2_version": 3},
        ])
        master = prepare_metadata_batch.prepare(
            self.library, self.serato, self.handoff, 150, "mp3", lane="clean-dirty"
        )
        self.assertEqual(master["candidate_count"], 3)
        self.assertEqual(len(master["scopes"]), 1)
        self.assert_disjoint(master["scopes"])
        self.assertEqual(
            Path(master["scopes"][0]["source_root"]).resolve(),
            (self.library / "Hip Hop/DIRTY").resolve(),
        )

    def test_genre_lane_nested_needs_check_folders_coalesce(self):
        make_library(self.library, [
            {"relative": "Juke/_GENRE NEEDS CHECK/a.mp3",
             "genre": "house", "v2_version": 3},
            {"relative": "Juke/_GENRE NEEDS CHECK/SUB/b.mp3",
             "genre": "house", "v2_version": 3},
        ])
        master = prepare_metadata_batch.prepare(
            self.library, self.serato, self.handoff, 150, "mp3", genre_only=True,
        )
        self.assertEqual(master["candidate_count"], 2)
        self.assertEqual(len(master["scopes"]), 1)
        self.assert_disjoint(master["scopes"])

    def test_disjoint_sibling_folders_stay_separate_scopes(self):
        make_library(self.library, [
            {"relative": "Hip Hop/DIRTY/a.mp3", "genre": "Hip-Hop", "v2_version": 3},
            {"relative": "R&B/DIRTY/b.mp3", "genre": "R&B", "v2_version": 3},
        ])
        master = prepare_metadata_batch.prepare(
            self.library, self.serato, self.handoff, 150, "mp3", lane="clean-dirty"
        )
        self.assertEqual(master["candidate_count"], 2)
        self.assertEqual(len(master["scopes"]), 2)
        self.assert_disjoint(master["scopes"])

    def test_fail_closed_check_refuses_surviving_overlap(self):
        with self.assertRaises(metadata_batch.BatchRefused):
            prepare_metadata_batch._refuse_overlapping_scope_roots(
                [str(self.library / "X"), str(self.library / "X" / "SUB")]
            )
        with self.assertRaises(metadata_batch.BatchRefused):
            prepare_metadata_batch._refuse_overlapping_scope_roots(
                [str(self.library / "X"), str(self.library / "X")]
            )
        # Disjoint siblings pass untouched.
        prepare_metadata_batch._refuse_overlapping_scope_roots(
            [str(self.library / "X"), str(self.library / "Y")]
        )


class LaneGovernanceTests(unittest.TestCase):
    def test_lane_discovery_added_no_mutation_primitives(self):
        report = mutation_surface.audit(REPO_ROOT)
        self.assertTrue(report["ok"], report["violations"])
        entry = next(
            module for module in report["modules"]
            if module["module"] == "scripts/prepare_metadata_batch.py"
        )
        self.assertEqual(entry["classification"], "preview-only")
        self.assertEqual(entry["primitive_count"], 0)
        self.assertEqual(entry["primitives"], [])


if __name__ == "__main__":
    unittest.main()
