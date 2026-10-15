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
        importlib.reload(experiments_module)
        self.module = importlib.reload(hardened)

    def test_default_is_three_seed_winning_checkpoint_and_pinned_revision(self):
        self.assertEqual(self.module.DEFAULT_CLAP_MODEL, "laion/larger_clap_general")
        self.assertEqual(
            self.module.DEFAULT_CLAP_REVISION,
            "ada0c23a36c4e8582805bb38fec3905903f18b41",
        )
        self.assertEqual(self.module.PREFERRED_WINDOW_MODE, "early-middle-late")

    def test_known_default_injects_pinned_revision(self):
        args = self.module._preparse(["--feature-mode", "clap"])
        self.assertEqual(args.clap_model, self.module.DEFAULT_CLAP_MODEL)
        self.assertEqual(args.clap_revision, self.module.DEFAULT_CLAP_REVISION)

    def test_known_fallback_injects_its_pinned_revision(self):
        args = self.module._preparse([
            "--feature-mode", "clap",
            "--clap-model", self.module.FALLBACK_CLAP_MODEL,
        ])
        self.assertEqual(args.clap_revision, self.module.FALLBACK_CLAP_REVISION)

    def test_known_checkpoint_refuses_alternate_revision(self):
        with self.assertRaisesRegex(self.module.HardenedClapError, "pinned to"):
            self.module._preparse([
                "--feature-mode", "clap",
                "--clap-revision", "wrong",
            ])

    def test_unapproved_model_requires_explicit_revision(self):
        with self.assertRaisesRegex(self.module.HardenedClapError, "--clap-revision"):
            self.module._preparse([
                "--feature-mode", "clap",
                "--clap-model", "org/other-model",
            ])

    def test_unapproved_model_accepts_explicit_revision(self):
        args = self.module._preparse([
            "--feature-mode", "clap",
            "--clap-model", "org/other-model",
            "--clap-revision", "abc123",
        ])
        self.assertEqual(args.clap_model, "org/other-model")
        self.assertEqual(args.clap_revision, "abc123")

    def test_degenerate_checkpoint_revision_is_refused(self):
        with self.assertRaisesRegex(self.module.HardenedClapError, "degenerate"):
            self.module._preparse([
                "--feature-mode", "clap",
                "--clap-model", "laion/larger_clap_music",
                "--clap-revision", "a0b4534a14f58e20944452dff00a22a06ce629d1",
            ])

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
            ["--feature-mode", "clap", "--window-mode", "early-middle-late"],
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
        self.assertEqual(self.module._ensure_preferred_window(argv, "clap"), argv)
        non_clap = ["--feature-mode", "enhanced-dsp"]
        self.assertEqual(
            self.module._ensure_preferred_window(non_clap, "enhanced-dsp"),
            non_clap,
        )

    def test_selected_model_is_injected_when_not_explicit(self):
        argv = ["--feature-mode", "clap"]
        self.assertEqual(
            self.module._ensure_selected_model(argv, self.module.DEFAULT_CLAP_MODEL),
            ["--feature-mode", "clap", "--clap-model", self.module.DEFAULT_CLAP_MODEL],
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
            first_spec = self.module.experiments.feature_spec("clap", "middle", "org/model")
            first = self.module.experiments.experiment_cache_key(path, first_spec)

            importlib.reload(experiments_module)
            self.module = importlib.reload(hardened)
            self.module._install_hardening(
                model_id="org/model", revision="def456", device_request="cpu"
            )
            second_spec = self.module.experiments.feature_spec("clap", "middle", "org/model")
            second = self.module.experiments.experiment_cache_key(path, second_spec)
            self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
