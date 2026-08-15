import importlib.util
import math
import struct
import tempfile
import unittest
import wave
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_verified_audit.py"
SPEC = importlib.util.spec_from_file_location("run_verified_audit", MODULE_PATH)
verified = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(verified)


def write_test_wav(path: Path) -> None:
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        frames = []
        for index in range(2205):
            value = int(10000 * math.sin(2 * math.pi * 440 * index / 44100))
            frames.append(struct.pack("<h", value))
        handle.writeframes(b"".join(frames))


class VerifiedAuditTests(unittest.TestCase):
    def test_compare_manifests_detects_added_removed_and_modified(self):
        before = {"a": {"size": 1, "mtime_ns": 1, "sha256": "a"}, "b": {"size": 1, "mtime_ns": 1, "sha256": "b"}}
        after = {"a": {"size": 2, "mtime_ns": 2, "sha256": "x"}, "c": {"size": 1, "mtime_ns": 1, "sha256": "c"}}
        changes = verified.compare_manifests(before, after)
        self.assertEqual({"modified", "removed", "added"}, {item["change"] for item in changes})

    def test_fingerprint_is_stable_without_writes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            track = root / "track.wav"
            write_test_wav(track)
            before = verified.fingerprint_roots([root])
            after = verified.fingerprint_roots([root])
            self.assertEqual(before, after)

    def test_verified_run_fails_closed_if_runner_mutates_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "music"
            root.mkdir()
            track = root / "track.wav"
            write_test_wav(track)
            fake_audit = base / "audit_library.py"
            fake_audit.write_text("# fixture\n", encoding="utf-8")

            def mutating_runner(command, check=False):
                track.write_bytes(track.read_bytes() + b"x")
                return SimpleNamespace(returncode=0)

            code, _run_dir, manifest = verified.run_verified_audit(
                [root], base / "out", fake_audit, runner=mutating_runner
            )
            self.assertEqual(2, code)
            self.assertFalse(manifest["source_verified_unchanged"])
            self.assertEqual("FAIL", manifest["result"])

    def test_run_id_is_utc_and_deterministic_for_given_time(self):
        now = datetime(2026, 8, 15, 23, 45, 1, 123456, tzinfo=timezone.utc)
        self.assertEqual("20260815T234501.123456Z", verified.make_run_id(now))


if __name__ == "__main__":
    unittest.main()
