"""Synthetic, read-only validation of DJ set dates, DST and play-evidence gates."""
from __future__ import annotations

import sys
import unittest
from datetime import timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dj_event_time as dj  # noqa: E402

ZONE = "America/Indiana/Indianapolis"
HASH = "c" * 64


def event(start="2026-11-21T22:00:00", end="2026-11-22T02:00:00", **changes):
    return {"event_id": "event-21", "event_timezone": ZONE,
            "set_time_verified": True, "set_start_local": start,
            "set_end_local": end, **changes}


def play(at="2026-11-22T00:45:00-05:00", **changes):
    return {"play_id": "session1-row4", "played_at": at,
            "played_status": "PLAYED", "identity_status": "EXACT_INDEXED_FILE",
            "file_id": 12, "session_id": "session1",
            "source_artifact_sha256": HASH, **changes}


class DJEventTimeTests(unittest.TestCase):
    def test_midnight_creates_next_date_without_changing_performance_date(self):
        result = dj.preview([event()], [play()])
        row = result["plays"][0]
        self.assertEqual(row["state"], "CANDIDATE_ONLY")
        self.assertEqual(row["candidate_event_ids"], ["event-21"])
        self.assertEqual(row["played_local_date"], "2026-11-22")
        self.assertEqual(row["performance_date"], "2026-11-21")
        self.assertFalse(result["proof_of_play"])

    def test_midnight_rollover_must_be_explicit(self):
        result = dj.preview([event(end="2026-11-21T02:00:00")], [play()])
        self.assertEqual(result["verified_set_count"], 0)
        self.assertIn("after", result["events_requiring_review"][0]["reason"])
        self.assertEqual(result["plays"][0]["state"], "REVIEW")

    def test_spring_gap_is_rejected(self):
        with self.assertRaisesRegex(dj.ClockEvidenceError, "nonexistent"):
            dj.resolve_wall_clock("2026-03-08T02:30:00", ZONE)

    def test_ambiguous_fall_back_requires_fold(self):
        with self.assertRaisesRegex(dj.ClockEvidenceError, "ambiguous"):
            dj.resolve_wall_clock("2026-11-01T01:30:00", ZONE)
        first = dj.resolve_wall_clock("2026-11-01T01:30:00", ZONE, 0)
        second = dj.resolve_wall_clock("2026-11-01T01:30:00", ZONE, 1)
        self.assertEqual((second.astimezone(timezone.utc) -
                          first.astimezone(timezone.utc)).total_seconds(), 3600)

    def test_fall_back_can_end_with_earlier_display_clock(self):
        window = dj.event_window(event(
            "2026-11-01T01:45:00", "2026-11-01T01:15:00",
            set_start_fold=0, set_end_fold=1))
        self.assertEqual((window["end_utc"] - window["start_utc"]).total_seconds(), 1800)

    def test_spring_forward_uses_actual_duration(self):
        window = dj.event_window(event("2026-03-08T01:30:00", "2026-03-08T03:30:00"))
        self.assertEqual((window["end_utc"] - window["start_utc"]).total_seconds(), 3600)

    def test_public_schedule_is_not_confirmed_dj_set(self):
        result = dj.preview([event(set_time_verified=False)], [play()])
        self.assertEqual(result["verified_set_count"], 0)
        self.assertEqual(result["plays"][0]["candidate_event_ids"], [])

    def test_no_offset_on_history_row_requires_review(self):
        row = dj.preview([event()], [play(at="2026-11-22T00:45:00")])["plays"][0]
        self.assertEqual(row["state"], "REVIEW")
        self.assertIn("offset", row["reason"])

    def test_multiple_event_overlaps_are_never_selected(self):
        row = dj.preview([event(), event(event_id="other")], [play()])["plays"][0]
        self.assertEqual(row["state"], "REVIEW")
        self.assertEqual(row["candidate_event_ids"], ["event-21", "other"])

    def test_unplayed_or_uncertain_status_never_qualifies(self):
        for state in ("UNPLAYED", "UNKNOWN", "MANUALLY_INSERTED", None):
            with self.subTest(state=state):
                row = dj.preview([event()], [play(played_status=state)])["plays"][0]
                self.assertEqual(row["state"], "REVIEW")

    def test_exact_file_identity_and_source_provenance_required(self):
        cases = (
            {"identity_status": "FILENAME_ONLY"},
            {"file_id": 0},
            {"file_id": True},
            {"source_artifact_sha256": ""},
            {"source_artifact_sha256": "z" * 64},
            {"session_id": ""},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                row = dj.preview([event()], [play(**changes)])["plays"][0]
                self.assertEqual(row["state"], "REVIEW")

    def test_end_is_exclusive_and_unreasonably_long_set_is_rejected(self):
        row = dj.preview([event()], [play(at="2026-11-22T02:00:00-05:00")])["plays"][0]
        self.assertEqual(row["state"], "REVIEW")
        with self.assertRaisesRegex(dj.ClockEvidenceError, "longer"):
            dj.event_window(event(end="2026-11-23T23:00:00"))

    def test_missing_timezone_abbreviation_and_invalid_fold_fail(self):
        with self.assertRaisesRegex(dj.ClockEvidenceError, "IANA"):
            dj.event_window(event(event_timezone="EST"))
        with self.assertRaisesRegex(dj.ClockEvidenceError, "fold"):
            dj.resolve_wall_clock("2026-10-13T22:00:00", ZONE, 1)

    def test_duplicate_ids_fail_closed(self):
        with self.assertRaisesRegex(dj.ClockEvidenceError, "duplicate event"):
            dj.preview([event(), event()], [play()])
        with self.assertRaisesRegex(dj.ClockEvidenceError, "duplicate play"):
            dj.preview([event()], [play(), play()])


if __name__ == "__main__":
    unittest.main()
