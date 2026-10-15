"""Synthetic event-to-playback matching. Identity is whole-file SHA-256 only."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dj_event_playback_match as match  # noqa: E402

ZONE = "America/Indiana/Indianapolis"
TRACK = "ab" * 32
OTHER = "cd" * 32
SOURCE = "ef" * 32


def event(**changes):
    base = {
        "event_id": "event-21",
        "event_timezone": ZONE,
        "set_time_verified": True,
        "set_start_local": "2026-11-21T22:00:00",
        "set_end_local": "2026-11-22T02:00:00",
    }
    base.update(changes)
    return base


def play(**changes):
    base = {
        "play_id": "session1-row4",
        "played_at": "2026-11-22T00:45:00-05:00",
        "played_status": "PLAYED",
        "identity_status": "EXACT_INDEXED_FILE",
        "file_id": 12,
        "session_id": "session1",
        "source_artifact_sha256": SOURCE,
        "track_sha256": TRACK,
        "title": "Shared Title",
    }
    base.update(changes)
    return base


def library(*files):
    return {"files": list(files)}


def audio(file_id=12, sha256=TRACK, title="Shared Title", artist="Ada"):
    return {"file_id": file_id, "sha256": sha256, "title": title, "artist": artist}


def provenance(**changes):
    artifact = {
        "sha256": SOURCE,
        "session_id": "session1",
        "source": "normalized-history-export",
        "verified": True,
    }
    artifact.update(changes)
    return {"artifacts": [artifact]}


class EventPlaybackMatchTests(unittest.TestCase):
    def test_exact_hash_inside_one_verified_window_is_candidate_only(self):
        result = match.preview(
            [event()], [play()], library(audio()), provenance()
        )
        row = result["plays"][0]
        self.assertEqual(row["state"], "CANDIDATE_ONLY")
        self.assertEqual(row["candidate_event_ids"], ["event-21"])
        self.assertEqual(row["file_id"], 12)
        self.assertEqual(row["track_sha256"], TRACK)
        self.assertFalse(result["proof_of_play"])
        self.assertEqual(result["mode"], "preview-only")

    def test_duplicate_titles_do_not_identify_a_track(self):
        files = library(
            audio(file_id=12, sha256=TRACK, title="Shared Title"),
            audio(file_id=13, sha256=OTHER, title="Shared Title", artist="Bea"),
        )
        missing_hash = match.preview(
            [event()], [play(track_sha256=None)], files, provenance()
        )["plays"][0]
        self.assertEqual(missing_hash["state"], "REVIEW")
        self.assertIsNone(missing_hash["file_id"])

        title_only = match.preview(
            [event()],
            [play(identity_status="TITLE_ONLY", file_id=12, track_sha256=TRACK)],
            files,
            provenance(),
        )["plays"][0]
        self.assertEqual(title_only["state"], "REVIEW")

        exact = match.preview(
            [event()], [play()], files, provenance()
        )["plays"][0]
        self.assertEqual(exact["state"], "CANDIDATE_ONLY")
        self.assertEqual(exact["file_id"], 12)

    def test_unmatched_or_contradictory_identity_stays_unresolved(self):
        files = library(audio())
        contradictory = match.preview(
            [event()], [play(file_id=12, track_sha256=OTHER)], files, provenance()
        )["plays"][0]
        self.assertEqual(contradictory["state"], "REVIEW")
        self.assertIsNone(contradictory["file_id"])
        unknown = match.preview(
            [event()], [play(file_id=99, track_sha256="12" * 32)], files, provenance()
        )["plays"][0]
        self.assertEqual(unknown["state"], "REVIEW")
        self.assertIsNone(unknown["file_id"])

    def test_one_hash_on_two_file_ids_stays_unresolved(self):
        files = library(audio(file_id=12), audio(file_id=14, artist="Copy"))
        row = match.preview([event()], [play()], files, provenance())["plays"][0]
        self.assertEqual(row["state"], "REVIEW")
        self.assertIsNone(row["file_id"])
        self.assertIn("multiple", row["reason"])

    def test_source_provenance_must_name_a_verified_artifact(self):
        files = library(audio())
        for changed in (
            {"verified": False},
            {"session_id": "other-session"},
            {"sha256": "11" * 32},
            {"source": ""},
        ):
            with self.subTest(changed=changed):
                row = match.preview(
                    [event()], [play()], files, provenance(**changed)
                )["plays"][0]
                self.assertEqual(row["state"], "REVIEW")
                self.assertIsNone(row["file_id"])

    def test_ambiguous_time_is_not_resolved_by_an_exact_hash(self):
        overlap = match.preview(
            [event(), event(event_id="other")],
            [play()],
            library(audio()),
            provenance(),
        )["plays"][0]
        self.assertEqual(overlap["state"], "REVIEW")
        self.assertEqual(overlap["candidate_event_ids"], ["event-21", "other"])

        unverified = match.preview(
            [event(set_time_verified=False)],
            [play()],
            library(audio()),
            provenance(),
        )
        self.assertEqual(unverified["verified_set_count"], 0)
        self.assertEqual(unverified["plays"][0]["state"], "REVIEW")

    def test_spring_forward_gap_stays_unresolved(self):
        result = match.preview(
            [event(
                set_start_local="2026-03-08T02:30:00",
                set_end_local="2026-03-08T04:00:00",
            )],
            [play(played_at="2026-03-08T03:15:00-04:00")],
            library(audio()),
            provenance(),
        )
        self.assertEqual(result["verified_set_count"], 0)
        self.assertEqual(result["plays"][0]["state"], "REVIEW")
        self.assertTrue(result["events_requiring_review"])

    def test_cli_reads_synthetic_fixtures_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {}
            for name, payload in {
                "events": [event()],
                "plays": [play()],
                "library": library(audio()),
                "provenance": provenance(),
            }.items():
                path = root / f"{name}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                paths[name] = path
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = match.main([
                    "--events", str(paths["events"]),
                    "--plays", str(paths["plays"]),
                    "--library", str(paths["library"]),
                    "--provenance", str(paths["provenance"]),
                ])
            self.assertEqual(code, 0)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["plays"][0]["state"], "CANDIDATE_ONLY")
            self.assertEqual(sorted(path.name for path in root.iterdir()), [
                "events.json", "library.json", "plays.json", "provenance.json",
            ])


if __name__ == "__main__":
    unittest.main()
