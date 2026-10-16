"""upgrade_id3v22_tags.py: preview, locked + backed-up apply, fail-closed checks, undo.

Every file is synthetic, every library is temporary, and every apply uses a
real lock plus a genuinely verified backup manifest.
"""

import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
for entry in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import apply_grouping_plan  # noqa: E402
import fixtures  # noqa: E402
import mutation_engine  # noqa: E402
import upgrade_id3v22_tags as upgrade  # noqa: E402
from mutagen.id3 import ID3  # noqa: E402

FOLDER_LOCK = SCRIPTS_DIR / "folder_lock.py"
UPGRADE = SCRIPTS_DIR / "upgrade_id3v22_tags.py"


def run(*args):
    return subprocess.run(
        [sys.executable, *[str(arg) for arg in args]],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )


def raw_v22_mp3(path: Path, frames, seed: int = 0) -> Path:
    """A v2.2 file from exact (frame id, body bytes) pairs, for cases
    write_id3v22_mp3 cannot express (multi-value text, unknown frames)."""
    body = b"".join(
        frame_id.encode("ascii") + len(data).to_bytes(3, "big") + data
        for frame_id, data in frames
    ) + b"\x00" * 16
    header = b"ID3" + bytes((2, 0, 0)) + fixtures._synchsafe(len(body))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + body + fixtures.mp3_bytes(seed))
    return path


class UpgradeId3v22Tests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.root = self.tmp / "library"
        self.root.mkdir()
        self.lock_folder = f"TEST-FIXTURE-id3-upgrade-{uuid.uuid4().hex}"
        self.session = "id3-upgrade-test"
        acquired = run(
            FOLDER_LOCK, "acquire", "--folder", self.lock_folder,
            "--session", self.session, "--agent", "test",
        )
        self.assertEqual(acquired.returncode, 0, acquired.stdout + acquired.stderr)

    def tearDown(self):
        run(
            FOLDER_LOCK, "release", "--folder", self.lock_folder,
            "--session", self.session, "--force", "--reason", "test teardown",
        )
        self._tmp.cleanup()

    def locked_args(self):
        return ("--lock-folder", self.lock_folder, "--session", self.session)

    def manifest(self):
        backup = fixtures.mirror_backup(self.root, self.tmp / "backup")
        return fixtures.passing_manifest(self.root, backup, self.tmp / "manifest.json")

    def tems(self, name="Free Fall", seed=0):
        return fixtures.write_id3v22_mp3(
            self.root / "Afrobeats" / f"Tems - {name}.mp3", seed=seed,
            genre="Afrobeats", title=name, artist="Tems", grouping="Existing",
            year="2024", comment="Keep This Comment",
            picture_data=b"synthetic-png", geob_data=b"synthetic-serato-payload",
        )

    def preview(self, *paths):
        plan_path = self.tmp / "plans" / "id3-upgrade-plan.json"
        args = [UPGRADE, "--root", self.root, "--out", plan_path]
        for path in paths:
            args += ["--file", path]
        return run(*args), plan_path

    def apply(self, plan_path, *extra):
        return run(
            UPGRADE, "--root", self.root, "--plan", plan_path,
            "--backup-manifest", self.manifest(), "--apply", *self.locked_args(), *extra,
        )

    def test_upgrades_v22_to_v23_and_keeps_every_frame_audio_and_serato(self):
        paths = [self.tems("Free Fall", 1), self.tems("Forever", 2), self.tems("Unfortunate", 3)]
        originals = {p: p.read_bytes() for p in paths}
        before = {p: upgrade.capture_state(p) for p in paths}

        previewed, plan_path = self.preview(*paths)
        self.assertEqual(previewed.returncode, 0, previewed.stdout + previewed.stderr)
        self.assertEqual({p: p.read_bytes() for p in paths}, originals, "preview must not write")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["counts"], {"files": 3, "ready": 3, "blocked": 0})
        self.assertEqual(plan["items"][0]["text_frames"]["TPE1"], [["Tems"]])

        result = self.apply(plan_path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["id3_upgraded"], 3)

        for path in paths:
            self.assertEqual(ID3(path).version, (2, 3, 0))
            after = upgrade.capture_state(path)
            self.assertEqual(after["text_frames"], before[path]["text_frames"])
            self.assertEqual(after["frames"], before[path]["frames"])
            self.assertEqual(after["audio_payload_sha256"], before[path]["audio_payload_sha256"])
            self.assertEqual(after["serato_payload_sha256"], before[path]["serato_payload_sha256"])
            tags = ID3(path)
            self.assertEqual(tags.getall("GEOB")[0].data, b"synthetic-serato-payload")
            self.assertEqual(tags.getall("APIC")[0].data, b"synthetic-png")
            # The year stays a v2.3 TYER frame, not an off-spec TDRC.
            frame_ids = [name for name, _ in upgrade.raw_frames(path.read_bytes())[1]]
            self.assertIn("TYER", frame_ids)
            self.assertNotIn("TDRC", frame_ids)
            # The Grouping tool now sees a writable v2.3 tag.
            self.assertEqual(apply_grouping_plan.read_grouping(path), (["Existing"], 3))

        self.assertTrue(mutation_engine.recover(Path(payload["recovery_journal"]))["clean"])

        undone = run(UPGRADE, "--undo", payload["undo_log"], *self.locked_args())
        self.assertEqual(undone.returncode, 0, undone.stdout + undone.stderr)
        self.assertEqual(json.loads(undone.stdout)["restored"], 3)
        self.assertEqual({p: p.read_bytes() for p in paths}, originals)

    def test_numeric_genre_and_utf16_text_survive_unchanged(self):
        title = "Frée Fäll — Ł"
        path = raw_v22_mp3(self.root / "Afrobeats" / "utf16.mp3", [
            ("TT2", b"\x01" + title.encode("utf-16")),
            ("TCO", b"\x00(17)"),
            ("TXX", b"\x00SERATO_NOTE\x00kept"),
        ])
        before = upgrade.raw_text_values(path.read_bytes())
        previewed, plan_path = self.preview(path)
        self.assertEqual(previewed.returncode, 0, previewed.stdout + previewed.stderr)
        result = self.apply(plan_path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        after = upgrade.raw_text_values(path.read_bytes())
        self.assertEqual(after, before)
        self.assertEqual(after["TCON"], [["(17)"]])
        self.assertEqual(after["TIT2"], [[title]])

    def test_multi_value_text_that_v23_would_join_is_blocked(self):
        path = raw_v22_mp3(self.root / "Afrobeats" / "multi.mp3", [
            ("TT2", b"\x00Title"),
            ("TP1", b"\x00Tems\x00Wizkid"),
        ])
        original = path.read_bytes()
        previewed, plan_path = self.preview(path)
        self.assertEqual(previewed.returncode, 1, previewed.stdout + previewed.stderr)
        self.assertIn("text frame TPE1 would change", previewed.stdout)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["items"][0]["status"], "blocked")

        result = self.apply(plan_path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("blocked in preview", result.stderr)
        self.assertEqual(path.read_bytes(), original)

    def test_unknown_v22_frame_is_blocked(self):
        path = raw_v22_mp3(self.root / "Afrobeats" / "unknown.mp3", [
            ("TT2", b"\x00Title"),
            ("ZZZ", b"opaque"),
        ])
        previewed, plan_path = self.preview(path)
        self.assertEqual(previewed.returncode, 1)
        item = json.loads(plan_path.read_text(encoding="utf-8"))["items"][0]
        self.assertEqual(item["status"], "blocked")
        self.assertIn("ZZZ", " ".join(item["problems"]))

    def test_v23_file_and_file_outside_root_are_blocked(self):
        v23 = fixtures.make_library(self.root, [
            {"relative": "Afrobeats/new.mp3", "title": "x", "v2_version": 3},
        ])["Afrobeats/new.mp3"]
        outside = fixtures.write_id3v22_mp3(self.tmp / "elsewhere" / "a.mp3", title="x")
        previewed, plan_path = self.preview(v23, outside)
        self.assertEqual(previewed.returncode, 1)
        items = json.loads(plan_path.read_text(encoding="utf-8"))["items"]
        self.assertIn("not ID3v2.2", " ".join(items[0]["problems"]))
        self.assertIn("outside --root", items[1]["problems"])

    def test_preview_refuses_to_write_its_plan_inside_the_library(self):
        path = self.tems()
        result = run(UPGRADE, "--root", self.root, "--file", path,
                     "--out", self.root / "plan.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "plan.json").exists())

    def test_apply_requires_lock_and_pass_manifest(self):
        path = self.tems()
        original = path.read_bytes()
        _, plan_path = self.preview(path)
        no_manifest = run(UPGRADE, "--root", self.root, "--plan", plan_path,
                          "--apply", *self.locked_args())
        self.assertNotEqual(no_manifest.returncode, 0)
        self.assertIn("--backup-manifest is required", no_manifest.stderr)
        no_lock = run(UPGRADE, "--root", self.root, "--plan", plan_path,
                      "--backup-manifest", self.manifest(), "--apply",
                      "--lock-folder", self.lock_folder, "--session", "someone-else")
        self.assertNotEqual(no_lock.returncode, 0)
        self.assertEqual(path.read_bytes(), original)

    def test_file_changed_since_preview_is_refused(self):
        path = self.tems()
        _, plan_path = self.preview(path)
        path.write_bytes(path.read_bytes() + b"\x00")
        changed = path.read_bytes()
        result = self.apply(plan_path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("changed since preview", result.stderr)
        self.assertEqual(path.read_bytes(), changed)

    def test_verification_failure_during_apply_rolls_back(self):
        path = self.tems()
        original = path.read_bytes()
        _, plan_path = self.preview(path)
        manifest_path = self.manifest()
        heartbeat, manifest = mutation_engine.run_preflight(
            lock_folder=self.lock_folder, session=self.session,
            manifest_path=manifest_path, source_root=self.root,
        )
        real_upgrade = upgrade.upgrade_tag

        def lossy_upgrade(target):
            real_upgrade(target)
            tags = ID3(target, translate=False)
            tags["TIT2"].text = ["Changed"]
            tags.save(target, v2_version=3)

        with mock.patch.object(upgrade, "upgrade_tag", lossy_upgrade):
            with self.assertRaisesRegex(
                mutation_engine.MutationVerificationError, "text frame TIT2 would change"
            ):
                upgrade.apply(plan_path, manifest, self.root, self.tmp / "out", "test", heartbeat)
        self.assertEqual(path.read_bytes(), original)
        journal = next((self.tmp / "out").glob("*-recovery-*.jsonl"))
        events = [json.loads(line)["type"] for line in journal.read_text().splitlines()]
        self.assertIn("rolled_back", events)

    def test_undo_refuses_a_file_changed_after_the_upgrade(self):
        path = self.tems()
        _, plan_path = self.preview(path)
        result = self.apply(plan_path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        # A later Grouping write must not be erased by undoing the upgrade.
        apply_grouping_plan.write_grouping(path, "Existing; Energy: Starter", 3)
        later = path.read_bytes()
        undone = run(UPGRADE, "--undo", payload["undo_log"], *self.locked_args())
        self.assertNotEqual(undone.returncode, 0)
        self.assertIn("changed since preview", undone.stderr)
        self.assertEqual(path.read_bytes(), later)


if __name__ == "__main__":
    unittest.main()
