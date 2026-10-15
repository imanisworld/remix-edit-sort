"""Synthetic tests for Grok History-export staging; no real DJ library used."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dj_history_export_stage as stage  # noqa: E402


class HistoryExportStageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.history = self.root / "History"
        self.export = self.root / "History Export"
        self.private = self.root / "private-inbox"
        self.staging = self.root / "grok-staging"
        self.history.mkdir()
        self.export.mkdir()
        self.private.mkdir()
        (self.history / "Sessions").mkdir()
        (self.history / "Sessions" / "101.session").write_bytes(b"binary-session")
        (self.history / "history.database").write_bytes(b"db")
        (self.export / "Friday.csv").write_text("name,start\nA,1\n", encoding="utf-8")
        (self.export / "Friday.txt").write_text("A\n", encoding="utf-8")
        (self.private / "HISTORY 2026-10-01.txt").write_text("track\n", encoding="utf-8")
        (self.private / "HISTORY 2026-10-01.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
        (self.private / "collection.xml").write_text("<DJ_PLAYLISTS/>", encoding="utf-8")

    def test_inventory_finds_exports_and_blocks_sessions(self):
        report = stage.inventory(
            serato_history=self.history,
            serato_history_export=self.export,
            rekordbox_export_dirs=[self.private],
        )
        self.assertEqual(report["readiness"], "READY_FOR_GROK_STAGING")
        self.assertEqual(report["serato_session_file_count"], 1)
        self.assertTrue(report["blocked_binary_present"])
        names = {Path(item["absolute_path"]).name for item in report["official_export_candidates"]}
        self.assertEqual(
            names,
            {
                "Friday.csv",
                "Friday.txt",
                "HISTORY 2026-10-01.txt",
                "HISTORY 2026-10-01.m3u8",
            },
        )
        self.assertNotIn("collection.xml", names)
        self.assertNotIn("101.session", names)
        self.assertFalse(report["safety"]["opens_session_or_sqlite"])
        self.assertFalse(report["safety"]["parses_vendor_rows"])

    def test_stage_copies_only_official_exports(self):
        before_session = (self.history / "Sessions" / "101.session").read_bytes()
        manifest = stage.stage(
            staging_dir=self.staging,
            serato_history=self.history,
            serato_history_export=self.export,
            rekordbox_export_dirs=[self.private],
            music_roots=[self.root / "Music-does-not-exist"],
        )
        self.assertEqual(manifest["readiness"], "RAW_EXPORTS_STAGED_FOR_GROK")
        self.assertEqual(manifest["staged_count"], 4)
        exports = list((self.staging / "exports").iterdir())
        self.assertEqual(len(exports), 4)
        self.assertTrue((self.staging / "manifest.json").is_file())
        self.assertTrue((self.staging / "GROK-SET-HISTORY.md").is_file())
        self.assertEqual(
            (self.history / "Sessions" / "101.session").read_bytes(),
            before_session,
        )
        loaded = json.loads((self.staging / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(loaded["staged_count"], 4)
        for item in loaded["staged_exports"]:
            self.assertEqual(item["set_bot_ready"], "RAW_EXPORT_STAGED_UNPARSED")
            self.assertFalse(item["parsed"])

    def test_waiting_when_only_binaries_exist(self):
        empty_export = self.root / "empty-export"
        empty_export.mkdir()
        report = stage.inventory(
            serato_history=self.history,
            serato_history_export=empty_export,
            rekordbox_export_dirs=[],
        )
        self.assertEqual(report["readiness"], "WAITING_FOR_OFFICIAL_EXPORTS")
        self.assertEqual(report["official_export_candidate_count"], 0)

    def test_refuses_staging_inside_repo(self):
        repo = Path(__file__).resolve().parents[1]
        with self.assertRaises(stage.StageRefused):
            stage.stage(
                staging_dir=repo / "local-history-stage",
                serato_history=self.history,
                serato_history_export=self.export,
            )

    def test_refuses_staging_under_music_root(self):
        music = self.root / "Music"
        music.mkdir()
        bad = music / "grok-stage"
        with self.assertRaises(stage.StageRefused):
            stage.stage(
                staging_dir=bad,
                serato_history=self.history,
                serato_history_export=self.export,
                music_roots=[music],
            )

    def test_cli_inventory_json(self):
        out = self.root / "inventory.json"
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            code = stage.main([
                "inventory",
                "--serato-history", str(self.history),
                "--serato-history-export", str(self.export),
                "--rekordbox-export-dir", str(self.private),
                "--json-out", str(out),
            ])
        self.assertEqual(code, 0)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["tool"], "dj_history_export_stage")
        self.assertGreaterEqual(data["official_export_candidate_count"], 4)


if __name__ == "__main__":
    unittest.main()
