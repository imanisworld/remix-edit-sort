"""Filesystem behaviors that differ between Linux CI and the real library's APFS.

CI runs on Linux (ext4/overlayfs): case-SENSITIVE, and it stores whatever
Unicode bytes it is given. The production DJ library is on macOS/APFS:
case-INSENSITIVE but case-preserving, and historically prone to normalization
differences between NFC and NFD forms of the same filename.

That gap is a real risk for a tool that renames files. A case-only rename
("bt005.mp3" -> "BT005.mp3") is a no-op path on Linux and a two-step
temp-file dance on APFS. A naive `if dest.exists()` collision check returns
True for a file colliding only with itself on APFS, and the old
standardize_names.py responded by appending " (2)" to a file that was never
colliding.

These tests do two things:

  1. Test the pure decision logic (does this rename collide? is it case-only?
     do these two names normalize to the same string?) against explicit
     inputs, so the ANSWERS are pinned regardless of which filesystem the test
     runs on. This part is meaningful in Linux CI.

  2. Detect the host filesystem's actual behavior and assert the real
     end-to-end outcome only where that behavior matches production.

What CI cannot prove is that a real APFS volume behaves as modelled. See
`test_mac_canary_gate_is_documented` and MAC-CANARY.md: a Mac canary run is a
required gate before the first production apply.
"""

import os
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
for path in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import backup_manifest  # noqa: E402
import fixtures  # noqa: E402


def filesystem_is_case_insensitive(directory: Path) -> bool:
    probe = directory / "CaseProbe.tmp"
    probe.write_text("x")
    try:
        return (directory / "caseprobe.tmp").exists()
    finally:
        probe.unlink()


# ---------------------------------------------------------------------------
# Pure decision logic -- filesystem-independent, meaningful on Linux CI.
# ---------------------------------------------------------------------------

def _fold(path: str) -> str:
    """Case-fold a path the way a case-insensitive volume compares it.

    Deliberately NOT `os.path.normcase`: on every POSIX platform -- macOS
    included -- normcase is the identity function. It only lowercases on
    Windows. That is a genuine trap, and the old standardize_names.py fell
    into it: its case-only-rename detection used normcase and therefore never
    fired on the Mac it was written for. Explicit casefold() is what actually
    models APFS comparison.
    """
    return os.path.abspath(path).casefold()


def is_case_only_rename(source: str, destination: str) -> bool:
    """True when two paths differ only by letter case."""
    return source != destination and _fold(source) == _fold(destination)


def collides_case_insensitively(destination: str, existing: list[str]) -> bool:
    """Would this destination collide on a case-insensitive volume?

    This is the check an apply engine must use when planning against APFS,
    because two plan items whose destinations differ only by case will land on
    the SAME file there even though a case-sensitive check says they are
    distinct.
    """
    folded = _fold(destination)
    return any(_fold(other) == folded for other in existing)


def normalizes_equal(a: str, b: str) -> bool:
    """True when two names are the same string under Unicode NFC.

    macOS has historically stored filenames in a decomposed form, so "é"
    typed as U+00E9 and as "e" + U+0301 name the same file even though the
    byte strings differ. A plan built from one form and applied against the
    other must not conclude the file is missing.
    """
    return unicodedata.normalize("NFC", a) == unicodedata.normalize("NFC", b)


class CaseLogicTests(unittest.TestCase):
    def test_case_only_renames_are_recognized(self):
        self.assertTrue(is_case_only_rename("/lib/bt005.mp3", "/lib/BT005.mp3"))
        self.assertTrue(is_case_only_rename("/lib/Song (clean).mp3", "/lib/Song (Clean).mp3"))

    def test_a_genuine_rename_is_not_a_case_only_rename(self):
        self.assertFalse(is_case_only_rename("/lib/a.mp3", "/lib/b.mp3"))
        self.assertFalse(is_case_only_rename("/lib/Song (Clean).mp3", "/lib/Song (Dirty).mp3"))

    def test_identical_paths_are_not_a_rename_at_all(self):
        self.assertFalse(is_case_only_rename("/lib/a.mp3", "/lib/a.mp3"))

    def test_case_insensitive_collision_detection(self):
        existing = ["/lib/Track.mp3"]
        self.assertTrue(collides_case_insensitively("/lib/track.mp3", existing))
        self.assertTrue(collides_case_insensitively("/lib/TRACK.MP3", existing))
        self.assertFalse(collides_case_insensitively("/lib/Track 2.mp3", existing))

    def test_two_plan_items_differing_only_by_case_are_a_collision(self):
        """On APFS these two land on one file. A plan containing both is unsafe
        and must be caught before any mutation."""
        destinations = ["/lib/Hip Hop/Song (Clean).mp3", "/lib/Hip Hop/song (clean).mp3"]
        seen: list[str] = []
        collisions = []
        for destination in destinations:
            if collides_case_insensitively(destination, seen):
                collisions.append(destination)
            seen.append(destination)
        self.assertEqual(len(collisions), 1)

    def test_the_apply_engine_folds_case_when_detecting_destination_collisions(self):
        """apply_version_genre_plan.py's validator must use a casefolded key."""
        source = (SCRIPTS_DIR / "apply_version_genre_plan.py").read_text(encoding="utf-8")
        self.assertIn("destination_key = str(destination).casefold()", source)


class UnicodeNormalizationTests(unittest.TestCase):
    # Built from escapes so the two really are different byte strings,
    # rather than relying on this source file surviving a normalizing
    # editor or a filesystem round-trip.
    _NAME = "Beyonc\u00e9 - D\u00e9j\u00e0 Vu.mp3"
    NFC = unicodedata.normalize("NFC", _NAME)
    NFD = unicodedata.normalize("NFD", _NAME)

    def test_the_two_forms_are_different_byte_strings(self):
        self.assertNotEqual(self.NFD, self.NFC)

    def test_but_they_normalize_to_the_same_name(self):
        self.assertTrue(normalizes_equal(self.NFD, self.NFC))

    def test_unrelated_names_do_not_normalize_equal(self):
        self.assertFalse(normalizes_equal("Song A.mp3", "Song B.mp3"))

    def test_backup_manifest_handles_unicode_paths_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            root = tmp / "lib"
            fixtures.make_library(root, [
                {"relative": self.NFC, "genre": "Pop"},
                {"relative": "日本語 - トラック (Clean).mp3", "genre": "Pop"},
                {"relative": "Ø Åström — Naïve.mp3", "genre": "Pop"},
            ])
            backup = fixtures.mirror_backup(root, tmp / "backup")
            manifest_path = fixtures.passing_manifest(root, backup, tmp / "m.json")
            manifest = backup_manifest.require_pass(manifest_path, root)
            self.assertEqual(manifest["file_count"], 3)


class HostFilesystemTests(unittest.TestCase):
    """Real end-to-end behavior, asserted only where the host matches production."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.case_insensitive = filesystem_is_case_insensitive(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_case_only_rename_lands_on_this_host(self):
        """Two-step rename through a temp name works on BOTH kinds of volume,
        which is why it is the safe way to do a case-only rename."""
        source = self.tmp / "bt005.mp3"
        fixtures.write_mp3(source, seed=1)
        digest = backup_manifest.sha256_file(source)
        destination = self.tmp / "BT005.mp3"

        temporary = source.with_name(source.name + ".__case_tmp")
        os.rename(source, temporary)
        os.rename(temporary, destination)

        self.assertTrue(destination.is_file())
        self.assertEqual(backup_manifest.sha256_file(destination), digest)
        names = [p.name for p in self.tmp.iterdir()]
        self.assertIn("BT005.mp3", names, "the rename did not preserve the new casing")

    def test_case_insensitive_hosts_report_a_case_variant_as_existing(self):
        if not self.case_insensitive:
            self.skipTest("host filesystem is case-sensitive (Linux CI); "
                          "see MAC-CANARY.md -- this behavior needs the Mac canary")
        path = self.tmp / "Track.mp3"
        fixtures.write_mp3(path, seed=2)
        self.assertTrue((self.tmp / "track.mp3").exists())
        self.assertTrue((self.tmp / "TRACK.MP3").exists())

    def test_case_sensitive_hosts_keep_variants_distinct(self):
        if self.case_insensitive:
            self.skipTest("host filesystem is case-insensitive (macOS/APFS)")
        fixtures.write_mp3(self.tmp / "Track.mp3", seed=2)
        self.assertFalse((self.tmp / "track.mp3").exists())


class MacCanaryGateTests(unittest.TestCase):
    def test_mac_canary_gate_is_documented(self):
        """CI cannot prove APFS behavior, so the requirement must be written
        down where an operator will actually see it before an apply."""
        canary = REPO_ROOT / "MAC-CANARY.md"
        self.assertTrue(canary.is_file(),
                        "MAC-CANARY.md must exist: Linux CI cannot prove APFS behavior")
        text = canary.read_text(encoding="utf-8")
        for required in ("case-insensitive", "case-only rename", "Unicode", "before"):
            self.assertIn(required, text)

    def test_the_canary_requirement_is_stated_in_the_main_docs(self):
        for name in ("README.md", "CLAUDE.md"):
            with self.subTest(doc=name):
                text = (REPO_ROOT / name).read_text(encoding="utf-8")
                self.assertIn("MAC-CANARY", text)


if __name__ == "__main__":
    unittest.main()
