"""Synthetic tests for CLAP experiment compatibility helpers.

No test imports Torch/Transformers, accesses the network, or touches DJ files.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import genre_clap_support as support  # noqa: E402


class _Tensor:
    def detach(self):
        return self


class _PooledOutput:
    def __init__(self):
        self.pooler_output = _Tensor()


class _ModernProcessor:
    def __init__(self):
        self.called = None

    def __call__(self, audio=None, sampling_rate=None, return_tensors=None):
        self.called = (audio, sampling_rate, return_tensors)
        return {"ok": True}


class _LegacyProcessor:
    def __init__(self):
        self.called = None

    def __call__(self, audios=None, sampling_rate=None, return_tensors=None):
        self.called = (audios, sampling_rate, return_tensors)
        return {"ok": True}


class _KwargLegacyProcessor:
    def __init__(self):
        self.called = None

    def __call__(self, **kwargs):
        if "audio" in kwargs:
            raise TypeError("got an unexpected keyword argument 'audio'")
        self.called = kwargs
        return {"ok": True}


class ClapSupportTests(unittest.TestCase):
    def test_processor_uses_modern_audio_keyword_when_advertised(self):
        processor = _ModernProcessor()
        result = support.processor_audio_inputs(processor, [1, 2], sample_rate=48000)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(processor.called, ([1, 2], 48000, "pt"))

    def test_processor_uses_legacy_audios_keyword_when_advertised(self):
        processor = _LegacyProcessor()
        support.processor_audio_inputs(processor, [3, 4], sample_rate=48000)
        self.assertEqual(processor.called, ([3, 4], 48000, "pt"))

    def test_processor_kwargs_wrapper_falls_back_only_for_unknown_audio_keyword(self):
        processor = _KwargLegacyProcessor()
        support.processor_audio_inputs(processor, [5], sample_rate=48000)
        self.assertEqual(processor.called["audios"], [5])

    def test_unwrap_accepts_old_tensor_return(self):
        tensor = _Tensor()
        self.assertIs(support.unwrap_audio_embedding(tensor), tensor)

    def test_unwrap_accepts_transformers5_pooler_output(self):
        output = _PooledOutput()
        self.assertIs(support.unwrap_audio_embedding(output), output.pooler_output)

    def test_unwrap_refuses_unknown_return_shape(self):
        with self.assertRaises(support.ClapSupportError):
            support.unwrap_audio_embedding(object())

    def test_cache_identity_changes_with_checkpoint_revision(self):
        first = support.cache_identity(
            feature_mode="clap",
            window_mode="early-middle-late",
            model_id="org/model",
            model_revision="abc123",
        )
        second = support.cache_identity(
            feature_mode="clap",
            window_mode="early-middle-late",
            model_id="org/model",
            model_revision="def456",
        )
        self.assertNotEqual(first, second)

    def test_cache_identity_changes_with_feature_implementation_version(self):
        first = support.cache_identity(
            feature_mode="clap",
            window_mode="middle",
            model_id="org/model",
            model_revision="abc123",
            feature_version="v1",
        )
        second = support.cache_identity(
            feature_mode="clap",
            window_mode="middle",
            model_id="org/model",
            model_revision="abc123",
            feature_version="v2",
        )
        self.assertNotEqual(first, second)

    def test_embedding_sanity_accepts_distinct_vectors(self):
        result = support.embedding_sanity([
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.5, 0.5, 0.7071],
        ])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["dimension"], 3)

    def test_embedding_sanity_rejects_collapsed_checkpoint(self):
        base = np.asarray([0.2, -0.3, 0.9], dtype=float)
        with self.assertRaisesRegex(support.ClapSupportError, "degenerate"):
            support.embedding_sanity([base, base.copy(), base.copy()])

    def test_embedding_sanity_rejects_zero_and_nonfinite_vectors(self):
        with self.assertRaisesRegex(support.ClapSupportError, "zero-norm"):
            support.embedding_sanity([[0.0, 0.0], [1.0, 0.0]])
        with self.assertRaisesRegex(support.ClapSupportError, "non-finite"):
            support.embedding_sanity([[1.0, 0.0], [float("nan"), 1.0]])


if __name__ == "__main__":
    unittest.main()
