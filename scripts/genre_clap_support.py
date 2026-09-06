#!/usr/bin/env python3
"""Version-tolerant, testable helpers for genre-learner CLAP experiments.

This module is deliberately dependency-light: importing it does not import
Torch, Transformers, access the network, inspect the music library, or mutate
Serato/audio. Runtime objects are passed in by the experiment harness.
"""
from __future__ import annotations

import inspect
from typing import Any, Iterable

SAFETY_CLASSIFICATION = "preview-only"
FEATURE_IMPLEMENTATION_VERSION = "clap-support-v1"


class ClapSupportError(RuntimeError):
    pass


def processor_audio_inputs(processor: Any, audio: Any, *, sample_rate: int, return_tensors: str = "pt"):
    """Call a CLAP processor across Transformers 4/5 audio keyword variants.

    Transformers releases have exposed the audio argument as either ``audios``
    or ``audio``. Prefer the parameter explicitly advertised by the installed
    processor instead of coupling the learner to one library version.
    """
    try:
        parameters = inspect.signature(processor.__call__).parameters
    except (TypeError, ValueError):
        parameters = {}

    kwargs = {"sampling_rate": sample_rate, "return_tensors": return_tensors}
    if "audio" in parameters:
        return processor(audio=audio, **kwargs)
    if "audios" in parameters:
        return processor(audios=audio, **kwargs)

    # Some callable wrappers expose only **kwargs. Try the current API first,
    # and fall back only when the failure is specifically an unknown keyword.
    try:
        return processor(audio=audio, **kwargs)
    except TypeError as exc:
        text = str(exc).casefold()
        if "audio" not in text or not any(token in text for token in ("unexpected", "keyword", "argument")):
            raise
        return processor(audios=audio, **kwargs)


def unwrap_audio_embedding(output: Any):
    """Return the embedding tensor from old tensor or newer pooled outputs."""
    if output is None:
        raise ClapSupportError("CLAP audio feature call returned None")
    if hasattr(output, "detach"):
        return output
    pooled = getattr(output, "pooler_output", None)
    if pooled is not None and hasattr(pooled, "detach"):
        return pooled
    if isinstance(output, (tuple, list)):
        for value in output:
            if hasattr(value, "detach"):
                return value
    raise ClapSupportError(
        f"unsupported CLAP audio feature return type: {type(output).__name__}"
    )


def cache_identity(
    *,
    feature_mode: str,
    window_mode: str,
    model_id: str | None = None,
    model_revision: str | None = None,
    feature_version: str = FEATURE_IMPLEMENTATION_VERSION,
) -> str:
    """Stable identity for feature-cache compatibility.

    A model revision is part of the identity so changed Hub weights cannot
    silently reuse embeddings created from an older checkpoint revision.
    """
    model = model_id or "none"
    revision = model_revision or "none"
    return f"{feature_version}:{feature_mode}:{window_mode}:{model}@{revision}"


def runtime_versions(torch_module: Any = None, transformers_module: Any = None) -> dict[str, str | None]:
    return {
        "torch": getattr(torch_module, "__version__", None) if torch_module is not None else None,
        "transformers": getattr(transformers_module, "__version__", None) if transformers_module is not None else None,
    }


def embedding_sanity(vectors: Iterable[Iterable[float]], *, max_cosine: float = 0.99995) -> dict:
    """Fail closed when supposedly different audio collapses to one embedding.

    This is intended for a tiny preflight set before a full-library extraction.
    It does not judge genre quality; it only detects pathological zero/nonfinite
    or near-identical checkpoint output like the observed CLAP checkpoint
    collapse.
    """
    try:
        import numpy as np
    except ImportError as exc:
        raise ClapSupportError("embedding sanity check requires numpy") from exc

    rows = [np.asarray(list(vector), dtype=float).reshape(-1) for vector in vectors]
    if len(rows) < 2:
        raise ClapSupportError("embedding sanity check requires at least two vectors")
    dimensions = {len(row) for row in rows}
    if len(dimensions) != 1 or 0 in dimensions:
        raise ClapSupportError(f"embedding dimensions are inconsistent: {sorted(dimensions)}")
    matrix = np.vstack(rows)
    if not np.isfinite(matrix).all():
        raise ClapSupportError("embedding contains non-finite values")
    norms = np.linalg.norm(matrix, axis=1)
    if (norms <= 1e-12).any():
        raise ClapSupportError("embedding contains a zero-norm vector")
    normalized = matrix / norms[:, None]
    similarities = normalized @ normalized.T
    upper = similarities[np.triu_indices(len(rows), k=1)]
    maximum = float(upper.max()) if len(upper) else 1.0
    minimum = float(upper.min()) if len(upper) else 1.0
    mean = float(upper.mean()) if len(upper) else 1.0
    if len(upper) and bool((upper >= max_cosine).all()):
        raise ClapSupportError(
            f"checkpoint embeddings are degenerate: all pairwise cosine similarities >= {max_cosine}"
        )
    return {
        "status": "PASS",
        "count": len(rows),
        "dimension": int(matrix.shape[1]),
        "pairwise_cosine_min": minimum,
        "pairwise_cosine_mean": mean,
        "pairwise_cosine_max": maximum,
        "threshold": max_cosine,
    }
