from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
TESTS = Path(__file__).resolve().parent
for path in (SCRIPTS, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import rekordbox_bridge_verify  # noqa: E402
import test_rekordbox_bridge_verify as bridge_fixture  # noqa: E402


class RekordboxBridgeSeratoPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = bridge_fixture.RekordboxBridgeVerifyTests(
            methodName="test_passes_with_new_track_ids_dynamic_metadata_and_extra_fresh_content"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture._write_fresh(self.fixture._fresh_root())

    def verify(self, *, enabled: bool):
        return rekordbox_bridge_verify.verify(
            self.fixture.bridge,
            self.fixture.fresh,
            self.fixture.preview_path,
            verify_serato_performance=enabled,
        )

    def test_default_behavior_does_not_run_or_change_report_shape(self):
        with mock.patch.object(
            rekordbox_bridge_verify.serato_rekordbox_performance_verify,
            "verify_tracks",
        ) as performance:
            report = self.verify(enabled=False)
        performance.assert_not_called()
        self.assertEqual(report["verdict"], "PASS")
        self.assertNotIn("serato_performance", report)
        self.assertNotIn("serato_performance_fail_findings", report["summary"])

    def test_opt_in_runs_only_for_required_bridge_tracks_that_resolved_once(self):
        performance_report = {
            "verdict": "PASS",
            "summary": {"fail_findings": 0},
            "tracks": [],
        }
        with mock.patch.object(
            rekordbox_bridge_verify.serato_rekordbox_performance_verify,
            "verify_tracks",
            return_value=performance_report,
        ) as performance:
            report = self.verify(enabled=True)

        self.assertEqual(report["verdict"], "PASS")
        self.assertIs(report["serato_performance"], performance_report)
        self.assertEqual(report["summary"]["serato_performance_fail_findings"], 0)
        performance.assert_called_once()
        tracks = performance.call_args.args[0]
        self.assertEqual(len(tracks), 2)
        self.assertEqual(
            {Path(track["absolute_path"]).name for track in tracks},
            {"One.mp3", "Two.mp3"},
        )

    def test_opt_in_performance_failure_fails_bridge_verdict(self):
        performance_report = {
            "verdict": "FAIL",
            "summary": {"fail_findings": 2},
            "tracks": [{"status": "FAIL"}],
        }
        with mock.patch.object(
            rekordbox_bridge_verify.serato_rekordbox_performance_verify,
            "verify_tracks",
            return_value=performance_report,
        ):
            report = self.verify(enabled=True)

        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(report["summary"]["serato_performance_fail_findings"], 2)
        self.assertEqual(report["summary"]["fail_findings"], 2)

    def test_no_serato_data_is_reported_but_does_not_turn_clean_bridge_into_failure(self):
        performance_report = {
            "verdict": "NO_DATA",
            "summary": {"fail_findings": 0},
            "tracks": [{"status": "NO_DATA"}],
        }
        with mock.patch.object(
            rekordbox_bridge_verify.serato_rekordbox_performance_verify,
            "verify_tracks",
            return_value=performance_report,
        ):
            report = self.verify(enabled=True)

        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["serato_performance"]["verdict"], "NO_DATA")
        self.assertEqual(report["summary"]["serato_performance_fail_findings"], 0)

    def test_cli_flag_is_explicit_opt_in(self):
        args = rekordbox_bridge_verify.build_parser().parse_args(
            [
                "--bridge-xml", "bridge.xml",
                "--fresh-xml", "fresh.xml",
                "--preview", "preview.json",
            ]
        )
        self.assertFalse(args.verify_serato_performance)
        args = rekordbox_bridge_verify.build_parser().parse_args(
            [
                "--bridge-xml", "bridge.xml",
                "--fresh-xml", "fresh.xml",
                "--preview", "preview.json",
                "--verify-serato-performance",
            ]
        )
        self.assertTrue(args.verify_serato_performance)


if __name__ == "__main__":
    unittest.main()
