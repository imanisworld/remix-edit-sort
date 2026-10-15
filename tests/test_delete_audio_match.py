"""Owner audio-match duplicate rule (2026-10-15) and its delete mode."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import audio_fingerprint  # noqa: E402
import audio_payload  # noqa: E402
import backup_manifest  # noqa: E402
import delete_verified_duplicates as tool  # noqa: E402
import duplicate_semantics as ds  # noqa: E402
from media_fixtures import write_tiny  # noqa: E402
from mutagen.flac import FLAC  # noqa: E402


class FakeHeartbeat:
    def beat(self, force=False):
        return {}


def probe_dict(**over):
    base = {"markers": [], "payload_sha256": "a", "duration": 200.0, "bitrate": 320000, "lossless": False}
    base.update(over)
    return base


class RuleTests(unittest.TestCase):
    def test_identical_payload_is_removable(self):
        r = ds.classify_audio_match(delete=probe_dict(bitrate=320000), keep=probe_dict(), similarity=1.0)
        self.assertTrue(ds.may_authorize_audio_match_deletion(r))

    def test_different_markers_never_removable(self):
        r = ds.classify_audio_match(delete=probe_dict(markers=["clean"]), keep=probe_dict(markers=["dirty"]), similarity=1.0)
        self.assertEqual(r["verdict"], ds.RELATED_VERSION)
        self.assertFalse(ds.may_authorize_audio_match_deletion(r))

    def test_low_similarity_is_review(self):
        r = ds.classify_audio_match(delete=probe_dict(payload_sha256="b", bitrate=128000), keep=probe_dict(), similarity=0.84)
        self.assertEqual(r["verdict"], ds.REVIEW)

    def test_duration_gap_is_review(self):
        r = ds.classify_audio_match(delete=probe_dict(payload_sha256="b", bitrate=128000, duration=196.5), keep=probe_dict(), similarity=0.95)
        self.assertEqual(r["verdict"], ds.REVIEW)

    def test_lower_bitrate_is_removable(self):
        r = ds.classify_audio_match(delete=probe_dict(payload_sha256="b", bitrate=192000, duration=201.0), keep=probe_dict(), similarity=0.9)
        self.assertTrue(ds.may_authorize_audio_match_deletion(r))

    def test_equal_or_higher_bitrate_not_removable(self):
        for br in (320000, 400000):
            r = ds.classify_audio_match(delete=probe_dict(payload_sha256="b", bitrate=br), keep=probe_dict(), similarity=0.99)
            self.assertFalse(ds.may_authorize_audio_match_deletion(r))

    def test_lossless_keeper_beats_mp3(self):
        r = ds.classify_audio_match(delete=probe_dict(payload_sha256="b", bitrate=320000),
                                    keep=probe_dict(lossless=True, bitrate=900000), similarity=0.9)
        self.assertTrue(ds.may_authorize_audio_match_deletion(r))
        r = ds.classify_audio_match(delete=probe_dict(payload_sha256="b", lossless=True),
                                    keep=probe_dict(bitrate=999999), similarity=0.9)
        self.assertFalse(ds.may_authorize_audio_match_deletion(r))

    def test_exact_mode_policy_unchanged(self):
        self.assertEqual(ds.DELETABLE_VERDICTS, frozenset({ds.EXACT_DUPLICATE}))
        self.assertFalse(ds.may_authorize_deletion(ds.AUDIO_EQUIVALENT))


class SimilarityTests(unittest.TestCase):
    def test_identical_and_shifted(self):
        fp = [(i * 2654435761) & 0xFFFFFFFF for i in range(300)]
        self.assertEqual(audio_fingerprint.similarity(fp, fp), 1.0)
        self.assertEqual(audio_fingerprint.similarity(fp[10:], fp), 1.0)

    def test_unrelated_is_low(self):
        a = [(i * 2654435761) & 0xFFFFFFFF for i in range(300)]
        b = [(i * 40503 + 7) * 2246822519 & 0xFFFFFFFF for i in range(300)]
        self.assertLess(audio_fingerprint.similarity(a, b, max_offset=5), 0.85)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.lib = self.tmp / "Music"
        self.keep = write_tiny(self.lib / "Pop" / "Song.flac", "flac")
        self.dele = write_tiny(self.lib / "Y2K" / "Song.flac", "flac")
        audio = FLAC(self.dele)
        audio["grouping"] = ["Source: Y2K"]
        audio.save()
        self.assertNotEqual(tool.sha256_file(self.keep), tool.sha256_file(self.dele))
        self.backup = self.tmp / "backup"
        (self.backup / "Y2K").mkdir(parents=True)
        shutil.copy2(self.dele, self.backup / "Y2K" / "Song.flac")
        self.manifest = backup_manifest.build_scoped_manifest(self.lib, self.backup, ["Y2K/Song.flac"])
        self.assertEqual(self.manifest["status"], "PASS", self.manifest["problems"])
        self.out = self.tmp / "out"
        self.out.mkdir()
        self.calls = 0

    def probe(self, path, markers_after=None):
        self.calls += 1
        markers = markers_after if (markers_after is not None and self.calls == 3) else []
        return {"markers": markers, "payload_sha256": audio_payload.payload_digest(path).sha256,
                "duration": 1.0, "bitrate": 900000, "lossless": True, "fingerprint": [1] * 60}

    def pairs(self, keep=None):
        path = self.tmp / "pairs.json"
        path.write_text(json.dumps([{"delete": str(self.dele), "keep": str(keep or self.keep),
                                      "sha256": tool.sha256_file(self.dele),
                                      "backup_relative": "Y2K/Song.flac"}]))
        return path

    def run_apply(self, pairs, probe):
        tool.apply(pairs, self.manifest, self.lib, self.backup, self.out, "t", FakeHeartbeat(),
                   mode="audio-match", probe=probe)

    def test_identical_audio_deletes_and_undo_restores(self):
        sha = tool.sha256_file(self.dele)
        self.run_apply(self.pairs(), self.probe)
        self.assertFalse(self.dele.exists())
        self.assertTrue(self.keep.exists())
        undo = next(self.out.glob("t-undo-*.jsonl"))
        record = json.loads(undo.read_text().splitlines()[0])
        self.assertEqual(record["rule"], ds.OWNER_AUDIO_MATCH_RULE)
        tool.undo(undo, "t", FakeHeartbeat())
        self.assertEqual(tool.sha256_file(self.dele), sha)

    def test_missing_keeper_refuses(self):
        with self.assertRaises(RuntimeError):
            self.run_apply(self.pairs(keep=self.lib / "Pop" / "Gone.flac"), self.probe)
        self.assertTrue(self.dele.exists())

    def test_rule_rechecked_at_write_time(self):
        probe = lambda p: self.probe(p, markers_after=["dirty"])  # noqa: E731
        with self.assertRaises(BaseException):
            self.run_apply(self.pairs(), probe)
        self.assertTrue(self.dele.exists())
        self.assertGreater(self.calls, 2)

    def test_keeper_cannot_also_be_deleted(self):
        path = self.tmp / "pairs.json"
        sha = tool.sha256_file(self.dele)
        path.write_text(json.dumps([
            {"delete": str(self.dele), "keep": str(self.keep), "sha256": sha, "backup_relative": "Y2K/Song.flac"},
            {"delete": str(self.keep), "keep": str(self.dele), "sha256": tool.sha256_file(self.keep), "backup_relative": "Pop/Song.flac"},
        ]))
        with self.assertRaises(RuntimeError):
            tool.load_and_validate_audio_match(path, self.probe)

    def test_exact_mode_still_refuses_differing_bytes(self):
        with self.assertRaises(RuntimeError):
            tool.apply(self.pairs(), self.manifest, self.lib, self.backup, self.out, "t", FakeHeartbeat())
        self.assertTrue(self.dele.exists())


if __name__ == "__main__":
    unittest.main()
