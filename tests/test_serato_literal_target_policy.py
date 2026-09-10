import sys
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import rewrite_serato_paths as rewrite  # noqa: E402


class SeratoLiteralTargetPolicyTests(unittest.TestCase):
    def test_target_check_uses_engine_literal_path_primitive(self):
        resolved = Path("/Volumes/T5 EVO/DJ-Music/Music/House/Ghettotech/track.mp3")
        with (
            mock.patch.object(
                rewrite.check_serato_crate_paths, "resolve", return_value=resolved
            ) as resolve,
            mock.patch.object(
                rewrite.mutation_engine, "literal_path_is_file", return_value=False
            ) as literal,
        ):
            self.assertFalse(
                rewrite._literal_stored_path_is_file(
                    "Volumes/T5 EVO/DJ-Music/Music/House/Ghettotech/track.mp3"
                )
            )
        resolve.assert_called_once()
        literal.assert_called_once_with(resolved)

    def test_rewrite_safety_checks_do_not_use_alias_tolerant_is_file(self):
        source = (SCRIPTS_DIR / "rewrite_serato_paths.py").read_text(encoding="utf-8")
        self.assertNotIn(
            'check_serato_crate_paths.resolve(change["to"]).is_file()', source
        )
        self.assertNotIn("check_serato_crate_paths.resolve(path).is_file()", source)
        self.assertGreaterEqual(source.count("_literal_stored_path_is_file("), 4)


if __name__ == "__main__":
    unittest.main()
