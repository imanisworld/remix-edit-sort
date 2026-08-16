import struct
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_serato_crate_paths as checker  # noqa: E402


def tlv(tag: str, value: bytes) -> bytes:
    return tag.encode("ascii") + struct.pack(">I", len(value)) + value


def text(s: str) -> bytes:
    return s.encode("utf-16-be")


def otrk(fields: bytes) -> bytes:
    return tlv("otrk", fields)


class TlvParsingTests(unittest.TestCase):
    def test_parses_flat_records(self):
        blob = tlv("vrsn", text("1.0")) + tlv("pfil", text("a/b.mp3"))
        records = checker.parse_tlv(blob)
        self.assertEqual([tag for tag, _ in records], ["vrsn", "pfil"])
        self.assertEqual(checker.decode_text(dict(records)["pfil"]), "a/b.mp3")

    def test_torn_trailing_record_is_dropped_not_misread(self):
        blob = tlv("vrsn", text("1.0")) + b"pfil\x00\x00\x00"  # header claims a length it can't back
        records = checker.parse_tlv(blob)
        self.assertEqual([tag for tag, _ in records], ["vrsn"])


class DatabaseAndCrateParsingTests(unittest.TestCase):
    def test_parses_database_track_fields(self):
        track = tlv("pfil", text("Users/x/Music/Song.mp3")) + tlv("tsng", text("Song")) + tlv("tart", text("Artist"))
        db = tlv("vrsn", text("2.0")) + otrk(track)

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "database V2"
            db_path.write_bytes(db)
            tracks = checker.parse_database(db_path)

        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["path"], "Users/x/Music/Song.mp3")
        self.assertEqual(tracks[0]["title"], "Song")
        self.assertEqual(tracks[0]["artist"], "Artist")

    def test_parses_crate_track_paths(self):
        crate = tlv("vrsn", text("1.0")) + otrk(tlv("ptrk", text("Users/x/Music/A.mp3"))) \
            + otrk(tlv("ptrk", text("Users/x/Music/B.mp3")))
        with tempfile.TemporaryDirectory() as tmp:
            crate_path = Path(tmp) / "Test.crate"
            crate_path.write_bytes(crate)
            paths = checker.parse_crate(crate_path)
        self.assertEqual(paths, ["Users/x/Music/A.mp3", "Users/x/Music/B.mp3"])

    def test_resolve_prepends_leading_slash(self):
        self.assertEqual(checker.resolve("Users/x/Music/Song.mp3"), Path("/Users/x/Music/Song.mp3"))


class AuditCategorizationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.serato_dir = self.tmp / "_Serato_"
        (self.serato_dir / "Subcrates").mkdir(parents=True)
        self.library = self.tmp / "Music"
        self.library.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def write_database(self, paths: list[str]):
        body = tlv("vrsn", text("2.0"))
        for p in paths:
            body += otrk(tlv("pfil", text(p)) + tlv("tsng", text("T")))
        (self.serato_dir / "database V2").write_bytes(body)

    def write_crate(self, name: str, paths: list[str]):
        body = tlv("vrsn", text("1.0"))
        for p in paths:
            body += otrk(tlv("ptrk", text(p)))
        (self.serato_dir / "Subcrates" / f"{name}.crate").write_bytes(body)

    def test_a_file_that_exists_at_its_recorded_path_resolves(self):
        (self.library / "House").mkdir()
        (self.library / "House" / "Track.mp3").write_bytes(b"x")
        self.write_database([f"{self.library}/House/Track.mp3".lstrip("/")])

        report = checker.audit(self.serato_dir, self.library)
        self.assertEqual(report["resolved_count"], 1)
        self.assertEqual(report["missing_count"], 0)

    def test_a_moved_file_is_found_by_filename_as_moved_likely(self):
        (self.library / "Pop").mkdir()
        (self.library / "Pop" / "Track.mp3").write_bytes(b"x")
        # Serato still thinks it's in a folder that no longer has it.
        old_path = f"{self.library}/House/Track.mp3".lstrip("/")
        self.write_database([old_path])

        report = checker.audit(self.serato_dir, self.library)
        self.assertEqual(report["resolved_count"], 0)
        self.assertEqual(report["moved_likely_count"], 1)
        self.assertTrue(report["moved_likely"][0]["candidate_current_path"].endswith("Pop/Track.mp3"))

    def test_same_filename_in_two_places_is_ambiguous_not_guessed(self):
        (self.library / "Pop").mkdir()
        (self.library / "Rock").mkdir()
        (self.library / "Pop" / "Track.mp3").write_bytes(b"x")
        (self.library / "Rock" / "Track.mp3").write_bytes(b"y")
        old_path = f"{self.library}/House/Track.mp3".lstrip("/")
        self.write_database([old_path])

        report = checker.audit(self.serato_dir, self.library)
        self.assertEqual(report["moved_ambiguous_count"], 1)
        self.assertEqual(len(report["moved_ambiguous"][0]["candidates"]), 2)

    def test_a_file_found_nowhere_is_reported_missing_not_guessed(self):
        old_path = f"{self.library}/House/Gone.mp3".lstrip("/")
        self.write_database([old_path])

        report = checker.audit(self.serato_dir, self.library)
        self.assertEqual(report["missing_count"], 1)
        self.assertNotIn("candidate_current_path", report["missing"][0])

    def test_crate_only_reference_is_still_checked(self):
        """A path that's in a crate but was somehow never in the database (or
        the database parse missed it) must still be audited, not silently
        skipped because in_database is False."""
        old_path = f"{self.library}/House/Gone.mp3".lstrip("/")
        self.write_database([])
        self.write_crate("MyCrate", [old_path])

        report = checker.audit(self.serato_dir, self.library)
        self.assertEqual(report["missing_count"], 1)
        self.assertEqual(report["missing"][0]["crates"], ["MyCrate"])
        self.assertFalse(report["missing"][0]["in_database"])

    def test_no_serato_data_touched(self):
        """The checker must never write into the Serato directory it reads."""
        (self.library / "House").mkdir()
        (self.library / "House" / "Track.mp3").write_bytes(b"x")
        self.write_database([f"{self.library}/House/Track.mp3".lstrip("/")])
        before = (self.serato_dir / "database V2").read_bytes()

        checker.audit(self.serato_dir, self.library)

        after = (self.serato_dir / "database V2").read_bytes()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
