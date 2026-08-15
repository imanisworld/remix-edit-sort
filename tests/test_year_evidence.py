import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_year_evidence.py"
SPEC = importlib.util.spec_from_file_location("validate_year_evidence", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


class YearEvidenceTests(unittest.TestCase):
    def test_confirmed_web_evidence_requires_source(self):
        payload = {"schema_version": 1, "entries": [{"key": "x", "year": 2024, "evidence_grade": "confirmed_primary", "sources": []}]}
        self.assertTrue(module.validate(payload))

    def test_unconfirmable_may_have_no_source_and_no_year(self):
        payload = {"schema_version": 1, "entries": [{"key": "dead-silence-pack", "year": None, "evidence_grade": "unconfirmable", "sources": []}]}
        self.assertEqual([], module.validate(payload))

    def test_valid_confirmed_record(self):
        payload = {"schema_version": 1, "entries": [{"key": "artist--title", "year": 2024, "evidence_grade": "confirmed_primary", "sources": [{"url": "https://example.com/release"}]}]}
        self.assertEqual([], module.validate(payload))


if __name__ == "__main__":
    unittest.main()
