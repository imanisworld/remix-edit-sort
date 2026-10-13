"""Source-only smoke tests spanning the proposed genre and DJ integration stacks."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_version_genre_plan as version_sort  # noqa: E402
import check_genre_naming as naming  # noqa: E402
import dj_event_time  # noqa: E402
import dj_library_review  # noqa: E402


class CombinedRemoteIntegrationTests(unittest.TestCase):
    def test_combined_vocabulary_and_both_sorters(self):
        refs = ROOT / "references"
        master = json.loads((refs / "genre-learner-rules.json").read_text(encoding="utf-8"))
        older = json.loads((refs / "genre-rules.json").read_text(encoding="utf-8"))
        version = json.loads((refs / "version-genre-rules.json").read_text(encoding="utf-8"))
        self.assertEqual(len(master["canonical_genres"]), 76)
        self.assertEqual(master["canonical_genres"], older["canonical_genres"])
        self.assertEqual(naming.noncanonical_outputs(refs), [])
        self.assertEqual(older["folder_names"], {})
        self.assertEqual(version["folder_names"], {})
        conflict = version_sort.analyze_genre(
            {"genre": "Miami Bass", "filename": "Artist - ATL Bass Edit.mp3"}, version
        )
        self.assertEqual(conflict["resolution_status"], "REVIEW")
        self.assertIsNone(conflict["proposed_genre"])

    def test_download_report_cannot_authorize_genre_or_physical_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            incoming = Path(tmp) / "incoming"
            incoming.mkdir()
            downloaded = incoming / "unknown DJ edit.mp3"
            original = b"synthetic sample audio header, no embedded tags"
            downloaded.write_bytes(original)
            review = dj_library_review.inspect_downloads(incoming, None)
            self.assertEqual(review["audio_files_scanned"], 1)
            self.assertEqual(review["items"][0]["status"], "NEEDS_REVIEW")
            self.assertEqual(
                review["items"][0]["library_duplicate_check"], "NOT_CHECKED_NO_INDEX"
            )
            self.assertEqual(downloaded.read_bytes(), original)
            self.assertEqual(sorted(path.name for path in incoming.iterdir()),
                             ["unknown DJ edit.mp3"])

    def test_overnight_candidate_never_claims_playback_proof(self):
        events = [{
            "event_id": "synthetic-event-1",
            "event_timezone": "America/Indiana/Indianapolis",
            "set_time_verified": True,
            "set_start_local": "2026-11-21T22:00:00",
            "set_end_local": "2026-11-22T02:00:00",
        }]
        plays = [{
            "play_id": "synthetic-row-1",
            "played_at": "2026-11-22T00:15:00-05:00",
            "played_status": "PLAYED",
            "identity_status": "EXACT_INDEXED_FILE",
            "file_id": 7,
            "session_id": "synthetic-session",
            "source_artifact_sha256": "a" * 64,
        }]
        check = dj_event_time.preview(events, plays)
        self.assertFalse(check["proof_of_play"])
        self.assertEqual(check["plays"][0]["state"], "CANDIDATE_ONLY")
        self.assertEqual(check["plays"][0]["played_local_date"], "2026-11-22")
        self.assertEqual(check["plays"][0]["performance_date"], "2026-11-21")


if __name__ == "__main__":
    unittest.main()
