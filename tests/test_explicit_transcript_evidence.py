import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "transcribe_explicit_evidence.py"
SPEC = importlib.util.spec_from_file_location("transcribe_explicit_evidence", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


class ExplicitTranscriptEvidenceTests(unittest.TestCase):
    def test_word_boundaries_prevent_substring_false_positive(self):
        self.assertEqual(module.find_terms("classic bassline", ["ass"]), [])

    def test_casefolded_term_match(self):
        self.assertEqual(module.find_terms("THIS WORD appears", ["word"]), ["word"])

    def test_phrase_match_allows_whitespace(self):
        self.assertEqual(module.find_terms("alpha   beta", ["alpha beta"]), ["alpha beta"])

    def test_needs_check_path_detection(self):
        self.assertTrue(module.is_needs_check_path("/Music/Hip Hop/_NEEDS CHECK/a.mp3"))
        self.assertFalse(module.is_needs_check_path("/Music/Hip Hop/CLEAN/a.mp3"))


if __name__ == "__main__":
    unittest.main()
