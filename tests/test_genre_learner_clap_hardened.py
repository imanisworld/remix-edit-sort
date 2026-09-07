"""Synthetic tests for the hardened CLAP genre experiment entrypoint."""
from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import genre_learner_experiments as experiments_module  # noqa: E402
import genre_learner_clap_hardened as hardened  # noqa: E402


class HardenedClapEntrypointTests(unittest.TestCase):
    def setUp(self):
        # Reload both modules so runtime monkeypatches cannot leak between tests.
        importlib.reload(experiments_module)
        self.module = importlib.reload(hardened)

    def test_provisional_default_is_proven_three_seed_checkpoint(self):
        self.assertEqual(
            self.module.PROVISIONAL_CLAP_MODEL,
            "laion/larger_clap_music_and_speech",
        )
        self.assertEqual(self.module.PREFERRED_WINDOW_MODE, "early-middle-late")

    def test_clap_run_requires_pinned_revision(self):
        with self.assertRaisesRegex(self.module.HardenedClapError, "--clap-revision"):
            self.module._preparse(["--feature-mode", "clap"])

    def test_clap_run_accepts_explicit_model_and_revision(self):
        args = self.module._preparse([
            "--feature-mode", "clap",
            "--clap-model", "laion/larger_clap_general",
            "--clap-revision", "abc123",
        ])
        self.assertEqual(args.clap_model, "laion/larger_clap_general")
        self.assertEqual(args.clap_revision, "abc123")

    def test_non_clap_run_does_not_require_revision(self):
        args = self.module._preparse(["--feature-mode", "enhanced-dsp"])
        self.assertEqual(args.feature_mode, "enhanced-dsp")
        self.assertIsNone(args.clap_revision)

    def test_strip_revision_argument_preserves_other_args(self):
        argv = [
            "--feature-mode", "clap",
            "--clap-revision", "abc123",
            "--window-mode", "early-middle-late",
        ]
        self.assertEqual(
            self.module._strip_revision_arg(argv),
            [
                "--feature-mode", "clap",
                "--window-mode", "early-middle-late",
            ],
        )
        self.assertEqual(
            self.module._strip_revision_arg([
                "--feature-mode", "clap", "--clap-revision=def456"
            ]),
            ["--feature-mode", "clap"],
        )

    def test_clap_defaults_to_proven_three_window_mode(self):
        argv = ["--feature-mode", "clap"]
        self.assertEqual(
            self.module._ensure_preferred_window(argv, "clap"),
            ["--feature-mode", "clap", "--window-mode", "early-middle-late"],
        )

    def test_explicit_window_mode_is_never_overridden(self):
        argv = ["--feature-mode", "clap", "--window-mode", "middle"]
        self.assertEqual(
            self.module._ensure_preferred_window(argv, "clap"), argv
        )
        non_clap = ["--feature-mode", "enhanced-dsp"]
        self.assertEqual(
            self.module._ensure_preferred_window(non_clap, "enhanced-dsp"),
            non_clap,
        )

    def test_synthetic_preflight_uses_three_distinct_ten_second_signals(self):
        rows = self.module._synthetic_preflight_audio()
        self.assertEqual(len(rows), 3)
        expected = self.module.experiments.CLAP_SR * self.module.experiments.CLAP_SEG_SECONDS
        self.assertTrue(all(len(row) == expected for row in rows))
        self.assertFalse(np.array_equal(rows[0], rows[1]))
        self.assertFalse(np.array_equal(rows[1], rows[2]))

    def test_install_hardening_adds_revision_and_feature_version_to_spec(self):
        self.module._install_hardening(
            model_id="org/model",
            revision="abc123",
            device_request="cpu",
        )
        spec = self.module.experiments.feature_spec(
            "clap", "early-middle-late", "org/model"
        )
        self.assertEqual(spec["clap_revision"], "abc123")
        self.assertEqual(
            spec["feature_implementation_version"],
            self.module.support.FEATURE_IMPLEMENTATION_VERSION,
        )

    def test_hardened_cache_key_changes_when_revision_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "audio.mp3"
            path.write_bytes(b"audio")

            self.module._install_hardening(
                model_id="org/model", revision="abc123", device_request="cpu"
            )
            first_spec = self.module.experiments.feature_spec(
                "clap", "middle", "org/model"
            )
            first = self.module.experiments.experiment_cache_key(path, first_spec)

            importlib.reload(experiments_module)
            self.module = importlib.reload(hardened)
            self.module._install_hardening(
                model_id="org/model", revision="def456", device_request="cpu"
            )
            second_spec = self.module.experiments.feature_spec(
                "clap", "middle", "org/model"
            )
            second = self.module.experiments.experiment_cache_key(path, second_spec)
            self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
