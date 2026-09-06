"""Unit tests for the read-only genre representation experiment harness.

These tests use only synthetic arrays/temp files. They do not touch the music
library, Serato, the network, Hugging Face, or external drives.
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import genre_learner  # noqa: E402
import genre_learner_experiments as experiments  # noqa: E402


class GenreLearnerExperimentTests(unittest.TestCase):
    def synthetic_audio(self, seconds=12):
        sample_count = genre_learner.SR * seconds
        t = np.arange(sample_count, dtype=np.float32) / genre_learner.SR
        # A deterministic mixture gives all feature families non-trivial input.
        return (
            0.45 * np.sin(2 * math.pi * 110.0 * t)
            + 0.25 * np.sin(2 * math.pi * 440.0 * t)
            + 0.15 * np.sin(2 * math.pi * 1760.0 * t)
        ).astype(np.float32)

    def test_enhanced_dsp_extends_current_baseline_vector(self):
        audio = self.synthetic_audio()
        baseline = genre_learner.features(audio)
        enhanced = experiments.enhanced_dsp_features(audio)
        self.assertIsNotNone(baseline)
        self.assertIsNotNone(enhanced)
        self.assertGreater(len(enhanced), len(baseline))
        self.assertEqual(enhanced[: len(baseline)], baseline)
        self.assertTrue(np.isfinite(np.asarray(enhanced, dtype=float)).all())

    def test_feature_spec_keeps_current_baseline_middle_definition(self):
        spec = experiments.feature_spec(
            "baseline", "middle", experiments.CLAP_DEFAULT_MODEL
        )
        self.assertEqual(spec["segment_seconds"], genre_learner.SEG)
        self.assertEqual(spec["sample_rate"], genre_learner.SR)
        self.assertEqual(spec["window_centers"], [0.5])
        self.assertEqual(spec["aggregation"], "single_window")

    def test_multi_window_uses_early_middle_late_centers_without_extra_label_weight(self):
        spec = experiments.feature_spec(
            "enhanced-dsp", "early-middle-late", experiments.CLAP_DEFAULT_MODEL
        )
        self.assertEqual(spec["window_centers"], [0.2, 0.5, 0.8])
        self.assertEqual(spec["segment_seconds"], experiments.MULTI_DSP_SEG_SECONDS)
        self.assertEqual(spec["aggregation"], "feature_mean")
        self.assertEqual(
            experiments.aggregate_vectors([[1.0, 4.0], [2.0, 5.0], [3.0, 6.0]]),
            [2.0, 5.0],
        )

    def test_clap_spec_is_explicit_and_uses_music_checkpoint(self):
        spec = experiments.feature_spec(
            "clap", "early-middle-late", experiments.CLAP_DEFAULT_MODEL
        )
        self.assertEqual(spec["sample_rate"], experiments.CLAP_SR)
        self.assertEqual(spec["segment_seconds"], experiments.CLAP_SEG_SECONDS)
        self.assertEqual(spec["clap_model"], "laion/larger_clap_music")

    def test_cache_key_separates_representation_window_and_file_revision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "audio.mp3"
            path.write_bytes(b"one")
            baseline = experiments.experiment_cache_key(
                path,
                experiments.feature_spec(
                    "baseline", "middle", experiments.CLAP_DEFAULT_MODEL
                ),
            )
            enhanced = experiments.experiment_cache_key(
                path,
                experiments.feature_spec(
                    "enhanced-dsp", "middle", experiments.CLAP_DEFAULT_MODEL
                ),
            )
            multi = experiments.experiment_cache_key(
                path,
                experiments.feature_spec(
                    "enhanced-dsp", "early-middle-late", experiments.CLAP_DEFAULT_MODEL
                ),
            )
            self.assertNotEqual(baseline, enhanced)
            self.assertNotEqual(enhanced, multi)

            path.write_bytes(b"two-two")
            revised = experiments.experiment_cache_key(
                path,
                experiments.feature_spec(
                    "baseline", "middle", experiments.CLAP_DEFAULT_MODEL
                ),
            )
            self.assertNotEqual(baseline, revised)

    def test_fixed_holdout_file_is_required_to_contain_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "holdout.json"
            path.write_text(json.dumps({"holdout": []}), encoding="utf-8")
            with self.assertRaises(experiments.ExperimentError):
                experiments._load_fixed_holdout(path)

    def test_clap_dependencies_are_lazy_not_imported_for_dsp_experiments(self):
        # The module must remain importable in CI without torch/transformers.
        self.assertIn("clap", experiments.FEATURE_MODES)
        self.assertEqual(experiments._CLAP_RUNTIME, {})


if __name__ == "__main__":
    unittest.main()
