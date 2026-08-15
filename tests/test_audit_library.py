import hashlib
import importlib.util
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "audit_library.py"
SPEC = importlib.util.spec_from_file_location("audit_library", MODULE_PATH)
audit_library = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(audit_library)


def write_test_wav(path: Path) -> None:
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        frames = []
        for index in range(4410):
            value = int(10000 * math.sin(2 * math.pi * 440 * index / 44100))
            frames.append(struct.pack("<h", value))
        handle.writeframes(b"".join(frames))


def fingerprint(path: Path) -> tuple[int, str]:
    return (
        path.stat().st_mtime_ns,
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )


class AuditLibrarySafetyTests(unittest.TestCase):
    def test_scan_is_read_only_and_finds_exact_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.wav"
            second = root / "second.wav"
            write_test_wav(first)
            second.write_bytes(first.read_bytes())

            before = {path.name: fingerprint(path) for path in (first, second)}
            rows = [
                audit_library.scan_file(root, first, True, 192),
                audit_library.scan_file(root, second, True, 192),
            ]
            groups = audit_library.assign_exact_duplicate_groups(rows)
            after = {path.name: fingerprint(path) for path in (first, second)}

            self.assertEqual(before, after)
            self.assertEqual(1, len(groups))
            self.assertEqual(rows[0]["sha256"], rows[1]["sha256"])
            self.assertEqual(
                rows[0]["exact_duplicate_group"],
                rows[1]["exact_duplicate_group"],
            )

    def test_unreadable_audio_is_recorded_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bad = root / "broken.mp3"
            bad.write_bytes(b"not an audio file")
            before = fingerprint(bad)

            row = audit_library.scan_file(root, bad, True, 192)

            self.assertTrue(row["read_error"])
            self.assertEqual(before, fingerprint(bad))

    def test_metadata_group_is_only_a_review_candidate(self) -> None:
        rows = []
        for digest in ("a" * 64, "b" * 64):
            row = {column: "" for column in audit_library.INVENTORY_COLUMNS}
            row.update(
                {
                    "artist": "Beyoncé",
                    "title": "Cuff It",
                    "sha256": digest,
                    "metadata_identity": audit_library.metadata_identity(
                        "Beyoncé", "Cuff It"
                    ),
                }
            )
            rows.append(row)

        groups = audit_library.metadata_review_groups(rows)
        self.assertEqual([[0, 1]], groups)


if __name__ == "__main__":
    unittest.main()
