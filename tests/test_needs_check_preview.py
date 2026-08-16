import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


version_checker = load("check_version_evidence", "check_version_evidence.py")
genre_checker = load("check_genre_evidence", "check_genre_evidence.py")
version_genre = load("build_version_genre_plan", "build_version_genre_plan.py")


class VersionEvidenceTests(unittest.TestCase):
    def row(self, filename: str, title: str = "") -> dict[str, str]:
        return {
            "full_path": f"/tmp/_NEEDS CHECK/{filename}",
            "filename": filename,
            "sha256": "a" * 64,
            "artist": "Artist",
            "title": title,
            "genre": "Hip-Hop",
            "version_words": "",
        }

    def test_explicit_clean_label_confirms_clean(self):
        result = version_checker.evaluate_version_row(self.row("Song [Clean].mp3"))
        self.assertEqual(result["clean_dirty_status"], "CONFIRMED_CLEAN")
        self.assertEqual(result["proposed_clean_dirty"], "CLEAN")

    def test_explicit_dirty_label_confirms_dirty(self):
        result = version_checker.evaluate_version_row(self.row("Song (Dirty).mp3"))
        self.assertEqual(result["clean_dirty_status"], "CONFIRMED_DIRTY")
        self.assertEqual(result["proposed_clean_dirty"], "DIRTY")

    def test_title_word_dirty_is_not_proof(self):
        result = version_checker.evaluate_version_row(self.row("Talking Dirty.mp3", "Talking Dirty"))
        self.assertEqual(result["clean_dirty_status"], "NEEDS_LISTEN")
        self.assertEqual(result["proposed_clean_dirty"], "")

    def test_transcript_explicit_terms_only_make_likely_dirty(self):
        transcript = {"explicit_terms_detected": ["term"], "confidence": "high"}
        result = version_checker.evaluate_version_row(self.row("Unknown.mp3"), transcript)
        self.assertEqual(result["clean_dirty_status"], "LIKELY_DIRTY")
        self.assertEqual(result["candidate_label"], "DIRTY")
        self.assertEqual(result["proposed_clean_dirty"], "")

    def test_no_detected_profanity_does_not_prove_clean(self):
        transcript = {"explicit_terms_detected": [], "confidence": "high"}
        result = version_checker.evaluate_version_row(self.row("Unknown.mp3"), transcript)
        self.assertEqual(result["clean_dirty_status"], "NEEDS_LISTEN")
        self.assertEqual(result["proposed_clean_dirty"], "")

    def test_clean_label_conflicting_with_transcript_is_review(self):
        transcript = {"explicit_terms_detected": ["term"]}
        result = version_checker.evaluate_version_row(self.row("Song [Clean].mp3"), transcript)
        self.assertEqual(result["clean_dirty_status"], "CONFLICT_CLEAN_LABEL_AUDIO_EXPLICIT")
        self.assertEqual(result["proposed_clean_dirty"], "")


class GenreEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.rules = version_genre.load_genre_rules(None)

    def row(self, filename: str, genre: str) -> dict[str, str]:
        return {
            "full_path": f"/tmp/_GENRE NEEDS CHECK/{filename}",
            "filename": filename,
            "sha256": "b" * 64,
            "artist": "Artist",
            "title": "Title",
            "genre": genre,
        }

    def test_tag_alias_reuses_trusted_mapping(self):
        result = genre_checker.evaluate_genre_row(self.row("song.mp3", "R&B"), self.rules)
        self.assertEqual(result["genre_status"], "ALREADY_MATCHES")
        self.assertEqual(result["proposed_genre"], "R&B")
        self.assertEqual(result["genre_confidence"], "tag")

    def test_filename_keyword_can_resolve_blank_tag(self):
        result = genre_checker.evaluate_genre_row(self.row("Artist - Track Ghettotech Edit.mp3", ""), self.rules)
        self.assertEqual(result["genre_status"], "HIGH_CONFIDENCE_PROPOSAL")
        self.assertEqual(result["proposed_genre"], "Ghettotech")
        self.assertEqual(result["genre_confidence"], "high")

    def test_ambiguous_compound_tag_remains_review(self):
        result = genre_checker.evaluate_genre_row(self.row("song.mp3", "Hip Hop, pop"), self.rules)
        self.assertEqual(result["genre_status"], "REVIEW")
        self.assertEqual(result["proposed_genre"], "")

    def test_needs_check_path_detection(self):
        self.assertTrue(genre_checker.is_needs_check_path("/x/_GENRE NEEDS CHECK/song.mp3"))
        self.assertFalse(genre_checker.is_needs_check_path("/x/Hip-Hop/song.mp3"))


if __name__ == "__main__":
    unittest.main()
