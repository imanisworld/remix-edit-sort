from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import usb_readonly_inventory  # noqa: E402


class UsbReadonlyInventoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.volume = self.tmp / "DJ USB"
        self.volume.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _inventory(self) -> dict:
        with mock.patch("usb_readonly_inventory.os.path.ismount", return_value=True), mock.patch(
            "usb_readonly_inventory._diskutil_info",
            return_value={
                "available": True,
                "mount_point": str(self.volume.resolve()),
                "filesystem_type": "msdos",
                "filesystem_name": "ExFAT",
                "writable": True,
                "internal": False,
            },
        ):
            return usb_readonly_inventory.inventory(self.volume)

    def test_explicit_non_mount_path_is_refused(self):
        with mock.patch("usb_readonly_inventory.os.path.ismount", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "not a mount point"):
                usb_readonly_inventory.inventory(self.volume)

    def test_system_root_is_refused_even_if_it_is_a_mount(self):
        with mock.patch("usb_readonly_inventory.os.path.ismount", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "system root"):
                usb_readonly_inventory._explicit_mountpoint(Path("/"))

    def test_inventory_is_read_only_counts_audio_and_hashes_database_marker(self):
        music = self.volume / "Contents"
        music.mkdir()
        track = music / "Track.mp3"
        track.write_bytes(b"audio")
        pioneer = self.volume / "PIONEER"
        pioneer.mkdir()
        database = pioneer / "export.pdb"
        database.write_bytes(b"database")
        note = self.volume / "notes.txt"
        note.write_text("hello", encoding="utf-8")

        watched = [track, database, note]
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in watched}
        report = self._inventory()

        self.assertEqual(report["health"]["status"], "PASS")
        self.assertEqual(report["inventory"]["audio_file_count"], 1)
        self.assertEqual(report["inventory"]["audio_by_extension"], {".mp3": 1})
        markers = report["inventory"]["rekordbox_markers"]
        export_marker = next(item for item in markers if item["path"] == "PIONEER/export.pdb")
        self.assertEqual(len(export_marker["sha256"]), 64)
        self.assertTrue(report["rekordbox_presence"]["markers_found"])
        self.assertFalse(report["safety"]["writes_target_volume"])
        self.assertFalse(report["safety"]["auto_selects_volume"])

        for path, (payload, mtime_ns) in before.items():
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(path.stat().st_mtime_ns, mtime_ns)

    def test_symlinks_are_skipped_and_never_followed(self):
        outside = self.tmp / "outside.mp3"
        outside.write_bytes(b"outside")
        link = self.volume / "linked.mp3"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("host does not permit symlinks")

        report = self._inventory()

        self.assertEqual(report["inventory"]["symlinks_skipped"], 1)
        self.assertEqual(report["inventory"]["audio_file_count"], 0)
        self.assertEqual(report["health"]["status"], "PASS")
        self.assertTrue(any("symlink" in reason for reason in report["health"]["reasons"]))
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_unreadable_subtree_is_fail_closed_in_report(self):
        bad = self.volume / "Bad"
        bad.mkdir()
        good = self.volume / "Good.mp3"
        good.write_bytes(b"good")
        real_scandir = os.scandir

        def guarded(path):
            if Path(path).name == "Bad":
                raise PermissionError("denied for test")
            return real_scandir(path)

        with mock.patch("usb_readonly_inventory.os.path.ismount", return_value=True), mock.patch(
            "usb_readonly_inventory._diskutil_info", return_value={"available": False}
        ), mock.patch("usb_readonly_inventory.os.scandir", side_effect=guarded):
            report = usb_readonly_inventory.inventory(self.volume)

        self.assertEqual(report["health"]["status"], "FAIL")
        self.assertEqual(report["inventory"]["traversal_errors"], 1)
        self.assertIn("could not be inventoried", report["health"]["reasons"][0])

    def test_volume_identity_change_fails_health(self):
        original = usb_readonly_inventory._root_identity(self.volume)
        changed = dict(original)
        changed["st_ino"] += 1
        with mock.patch("usb_readonly_inventory.os.path.ismount", return_value=True), mock.patch(
            "usb_readonly_inventory._diskutil_info", return_value={"available": False}
        ), mock.patch(
            "usb_readonly_inventory._root_identity", side_effect=[original, changed]
        ):
            report = usb_readonly_inventory.inventory(self.volume)

        self.assertEqual(report["health"]["status"], "FAIL")
        self.assertIn("identity changed", report["health"]["reasons"][0])

    def test_non_macos_diskutil_state_is_explicitly_unavailable(self):
        with mock.patch("usb_readonly_inventory.platform.system", return_value="Linux"):
            result = usb_readonly_inventory._diskutil_info(self.volume)
        self.assertFalse(result["available"])
        self.assertIn("macOS", result["reason"])

    def test_cli_requires_an_explicit_volume_argument(self):
        parser = usb_readonly_inventory.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([])

    def test_marker_name_alone_does_not_claim_a_valid_export(self):
        (self.volume / "PIONEER").mkdir()
        report = self._inventory()
        self.assertTrue(report["rekordbox_presence"]["markers_found"])
        self.assertIn("does not by itself prove", report["rekordbox_presence"]["interpretation"])


if __name__ == "__main__":
    unittest.main()
