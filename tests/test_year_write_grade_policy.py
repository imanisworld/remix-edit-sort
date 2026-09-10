import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import apply_new_music_intake_plan as intake_apply  # noqa: E402


class YearWriteGradePolicyTests(unittest.TestCase):
    def test_inference_grade_cannot_authorize_year_write(self):
        problem = intake_apply.year_fix_error({
            "from": "",
            "to": "2013",
            "basis": "inferred from context",
            "grade": "inference",
        })
        self.assertIsNotNone(problem)
        self.assertIn("grade must authorize a write", problem)

    def test_only_evidence_grades_authorize_year_write(self):
        self.assertEqual(
            intake_apply.YEAR_GRADES,
            {"confirmed_primary", "confirmed_secondary", "local_evidence"},
        )


if __name__ == "__main__":
    unittest.main()
