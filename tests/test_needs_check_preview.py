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

    def assert_resolved(self, genre, expected):
        result = genre_checker.evaluate_genre_row(self.row("song.mp3", genre), self.rules)
        self.assertEqual(result["genre_resolution_status"], "RESOLVED")
        self.assertEqual(result["proposed_genre"], expected)

    def test_established_single_and_multi_genres(self):
        for raw, expected in [
            ("House; Techno", "House; Techno"),
            ("house, techno", "House; Techno"),
            ("Techno; Electronic", "Techno; Electronic"),
            ("Techno, Electronic", "Techno; Electronic"),
            ("R&B; Pop", "R&B; Pop"),
            ("R&B, pop", "R&B; Pop"),
            ("Pop; Pop Rock; Rock; Soft Rock", "Pop; Pop Rock; Rock; Soft Rock"),
            ("Trap", "Hip-Hop"),
            ("Southern Hip-Hop", "Hip-Hop"),
        ]:
            with self.subTest(raw=raw):
                self.assert_resolved(raw, expected)

    def test_unknown_token_mixed_with_known_genre_is_partial_and_not_proposed(self):
        result = genre_checker.evaluate_genre_row(self.row("song.mp3", "House; Mystery Style"), self.rules)
        self.assertEqual(result["genre_resolution_status"], "PARTIALLY_RESOLVED")
        self.assertEqual(result["recognized_genres"], "House")
        self.assertEqual(result["unknown_tokens"], "Mystery Style")
        self.assertEqual(result["proposed_genre"], "")

    def test_blank_and_other_remain_review(self):
        for raw, category in [("", "blank"), ("Other", "other")]:
            with self.subTest(raw=raw):
                result = genre_checker.evaluate_genre_row(self.row("song.mp3", raw), self.rules)
                self.assertEqual(result["genre_resolution_status"], "REVIEW")
                self.assertEqual(result["genre_category"], category)

    def test_post_canary_betty_is_partial_not_wholly_unknown(self):
        row = self.row("After The Pain (Intro Clean).mp3", "R&B, pop, old head party")
        row["grouping"] = "Clean; Old Head; Intro Edit"
        result = genre_checker.evaluate_genre_row(row, self.rules)
        self.assertEqual(result["genre_resolution_status"], "PARTIALLY_RESOLVED")
        self.assertEqual(result["recognized_genres"], "R&B; Pop")
        self.assertEqual(result["known_contexts"], "Old Head")
        self.assertEqual(result["grouping_resolution_status"], "RESOLVED")

    def test_post_canary_ballin_flossin(self):
        self.assert_resolved("Electronic; Footwork; Ghetto House; Juke", "Electronic; Footwork; Ghetto House; Juke")

    def test_post_canary_field_trip(self):
        row = self.row("FIELD TRIP.mp3", "Jungle; Ghetto Tech; Jersey Club")
        row["grouping"] = "Plex; Edit"
        result = genre_checker.evaluate_genre_row(row, self.rules)
        self.assertEqual(result["genre_resolution_status"], "RESOLVED")
        self.assertEqual(result["proposed_genre"], "Jungle; Ghettotech; Jersey Club")
        self.assertEqual(result["grouping_resolution_status"], "RESOLVED")

    def test_post_canary_beautiful_liar(self):
        row = self.row("Beautiful Liar (DJ Edit).mp3", "Baile")
        row["grouping"] = "Edit"
        result = genre_checker.evaluate_genre_row(row, self.rules)
        self.assertEqual(result["genre_resolution_status"], "RESOLVED")
        self.assertEqual(result["grouping_resolution_status"], "RESOLVED")

    def test_remaining_post_canary_states(self):
        cases = [
            ("Essence (Instrumental).mp3", "Dancehall", "Instrumental"),
            ("Jazzy Belle (Swift C's Remix) (Dirty Acapella).mp3", "Hip-Hop", "Remix; Edit; Dirty; Acapella"),
            ("Already Best Friends (Dennis Blaze Afro Reggae Remix) (Clean Extended).mp3", "Afrobeat", "Clean; Remix; Edit; Extended"),
            ("Freak Like Me (Bootleg Remix) Extended Mix.mp3", "Groovy Bounce", "Remix; Bootleg; Edit; Extended"),
        ]
        for filename, genre, grouping in cases:
            with self.subTest(filename=filename):
                row = self.row(filename, genre)
                row["grouping"] = grouping
                result = genre_checker.evaluate_genre_row(row, self.rules)
                self.assertEqual(result["genre_resolution_status"], "REVIEW" if genre == "Afrobeat" else "RESOLVED")
                self.assertEqual(result["grouping_resolution_status"], "RESOLVED")
                self.assertEqual(result["proposed_grouping"], grouping)

    def test_bracketed_radio_edit_flip_and_rework_write_their_exact_terms(self):
        # An explicit bracketed label is exact evidence for that exact term.
        # None of these is ever collapsed into the generic "Edit" term.
        for filename, term in [
            ("Song (Radio Edit).mp3", "Radio Edit"),
            ("Song (Flip).mp3", "Flip"),
            ("Song (ReWork).mp3", "ReWork"),
        ]:
            with self.subTest(filename=filename):
                analysis = version_genre.classify_grouping(filename, "", "", self.rules)
                self.assertEqual(analysis["resolution_status"], "RESOLVED")
                self.assertIn(term, analysis["add_terms"])
                self.assertNotIn("Edit", analysis["add_terms"])
                self.assertEqual(analysis["review_reasons"], [])

    def test_unbracketed_radio_edit_flip_and_rework_remain_reviewable(self):
        # A plain word match is not proof of a version label: "Flip" may be
        # an artist and unbracketed "radio edit" may be title text.
        for filename, reason in [
            ("My Radio Edit.mp3", "Radio Edit"),
            ("Flip Dinero - Song.mp3", "Flip"),
            ("Total Rework Of It.mp3", "ReWork"),
        ]:
            with self.subTest(filename=filename):
                analysis = version_genre.classify_grouping(filename, "", "", self.rules)
                self.assertEqual(analysis["resolution_status"], "REVIEW")
                self.assertNotIn("Edit", analysis["add_terms"])
                self.assertTrue(any(reason in item for item in analysis["review_reasons"]))

    def test_needs_check_path_detection(self):
        self.assertTrue(genre_checker.is_needs_check_path("/x/_GENRE NEEDS CHECK/song.mp3"))
        self.assertFalse(genre_checker.is_needs_check_path("/x/Hip-Hop/song.mp3"))


if __name__ == "__main__":
    unittest.main()
