"""Plan-only date-added restore + approved ID3v2.2 Grouping upgrade. Synthetic only."""
import sys, tempfile, unicodedata, unittest
from pathlib import Path
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import build_date_added_restore_plan as da  # noqa: E402
import apply_grouping_plan as g  # noqa: E402

DAY = 86400


class ProposeTests(unittest.TestCase):
    def test_never_later(self):
        self.assertIsNone(da.propose(1000 * DAY, 2000 * DAY, [])[0])

    def test_requires_gap(self):
        self.assertIsNone(da.propose(1000 * DAY, 980 * DAY, [])[0])
        self.assertEqual(da.propose(1000 * DAY, 900 * DAY, [])[0], 900 * DAY)

    def test_earliest_evidence_wins(self):
        p, src = da.propose(1000 * DAY, 900 * DAY, [(800 * DAY, "Serato DB copy x")])
        self.assertEqual((p, src), (800 * DAY, "Serato DB copy x"))

    def test_skips(self):
        self.assertEqual(da.skip_reason("streaming://apple_music/1"), "streaming (no file)")
        self.assertEqual(da.skip_reason(":/effects/x"), "Serato effect/plugin")
        self.assertIsNone(da.skip_reason("Users/x/Music/a.mp3"))

    def test_resolve_nfd_and_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            name = unicodedata.normalize("NFD", "Beyoncé.mp3")
            (Path(tmp) / name).write_bytes(b"x")
            got = da.resolve(7, unicodedata.normalize("NFC", "Beyoncé.mp3"), {7: tmp})
            self.assertIsNotNone(got)

    def test_copied_caveat(self):
        self.assertTrue(da.caveats(Path("/a/b.mp3"), 100 * DAY, 10 * DAY))
        self.assertTrue(da.caveats(Path("/x/backup/b.mp3"), None, None))


class V22UpgradeTests(unittest.TestCase):
    item = {"path": "a.mp3", "id3_version": 2, "id3_write_version": 3}

    def test_needs_approval(self):
        with self.assertRaises(RuntimeError):
            g.write_version({"rules": {}}, self.item)

    def test_approved(self):
        self.assertEqual(g.write_version({"rules": {"id3_v22_upgrade_approved": True}}, self.item), 3)

    def test_only_from_v22(self):
        with self.assertRaises(RuntimeError):
            g.write_version({"rules": {"id3_v22_upgrade_approved": True}}, {**self.item, "id3_version": 3})

    def test_default_keeps_version(self):
        self.assertEqual(g.write_version({"rules": {}}, {"path": "a", "id3_version": 4}), 4)


if __name__ == "__main__":
    unittest.main()
