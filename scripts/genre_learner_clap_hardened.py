#!/usr/bin/env python3
"""Hardened CLAP entrypoint for the read-only genre representation experiment.

This wrapper integrates the compatibility/provenance safeguards from
``genre_clap_support`` without changing the production genre learner. Known
approved CLAP checkpoints are pinned to exact Hugging Face revisions, a small
synthetic-audio embedding sanity check runs before library audio is touched,
and the actual evaluation delegates to ``genre_learner_experiments``.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "preview-only"

import argparse
import math
import sys
from pathlib import Path

import genre_clap_support as support
import genre_learner_experiments as experiments

DEFAULT_CLAP_MODEL = "laion/larger_clap_general"
DEFAULT_CLAP_REVISION = "ada0c23a36c4e8582805bb38fec3905903f18b41"
FALLBACK_CLAP_MODEL = "laion/larger_clap_music_and_speech"
FALLBACK_CLAP_REVISION = "195c3a3e68faebb3e2088b9a79e79b43ddbda76b"
EXCLUDED_CLAP_REVISIONS = {
    ("laion/larger_clap_music", "a0b4534a14f58e20944452dff00a22a06ce629d1"),
}
PREFERRED_WINDOW_MODE = "early-middle-late"
_PREFLIGHT_DONE: set[tuple[str, str, str]] = set()
_RUNTIME: dict[tuple[str, str, str], tuple[object, object, object, str]] = {}


class HardenedClapError(experiments.ExperimentError):
    pass


def _approved_revision(model_id: str) -> str | None:
    if model_id == DEFAULT_CLAP_MODEL:
        return DEFAULT_CLAP_REVISION
    if model_id == FALLBACK_CLAP_MODEL:
        return FALLBACK_CLAP_REVISION
    return None


def _preparse(argv: list[str]):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--feature-mode", choices=experiments.FEATURE_MODES)
    parser.add_argument("--clap-model", default=DEFAULT_CLAP_MODEL)
    parser.add_argument("--clap-revision")
    parser.add_argument("--clap-device", default="auto")
    known, _unknown = parser.parse_known_args(argv)

    if known.feature_mode != "clap":
        return known

    approved_revision = _approved_revision(known.clap_model)
    if known.clap_revision is None and approved_revision:
        known.clap_revision = approved_revision
    elif known.clap_revision is None:
        raise HardenedClapError(
            "unapproved CLAP checkpoints require --clap-revision so weights are reproducible"
        )

    if (known.clap_model, known.clap_revision) in EXCLUDED_CLAP_REVISIONS:
        raise HardenedClapError(
            f"CLAP checkpoint is excluded as degenerate: {known.clap_model}@{known.clap_revision}"
        )

    if approved_revision and known.clap_revision != approved_revision:
        raise HardenedClapError(
            f"approved checkpoint {known.clap_model} is pinned to {approved_revision}; "
            f"refusing alternate revision {known.clap_revision}"
        )
    return known


def _strip_revision_arg(argv: list[str]) -> list[str]:
    result: list[str] = []
    skip = False
    for index, value in enumerate(argv):
        if skip:
            skip = False
            continue
        if value == "--clap-revision":
            if index + 1 >= len(argv):
                raise HardenedClapError("--clap-revision requires a value")
            skip = True
            continue
        if value.startswith("--clap-revision="):
            continue
        result.append(value)
    return result


def _ensure_preferred_window(argv: list[str], feature_mode: str | None) -> list[str]:
    """Default CLAP to the proven three-window representation."""
    result = list(argv)
    if feature_mode != "clap":
        return result
    if any(
        value == "--window-mode" or value.startswith("--window-mode=")
        for value in result
    ):
        return result
    return [*result, "--window-mode", PREFERRED_WINDOW_MODE]


def _ensure_selected_model(argv: list[str], model_id: str) -> list[str]:
    result = list(argv)
    if any(
        value == "--clap-model" or value.startswith("--clap-model=")
        for value in result
    ):
        return result
    return [*result, "--clap-model", model_id]


def _synthetic_preflight_audio():
    np = experiments._np()
    count = experiments.CLAP_SR * experiments.CLAP_SEG_SECONDS
    t = np.arange(count, dtype=np.float32) / experiments.CLAP_SR
    first = (0.4 * np.sin(2.0 * math.pi * 220.0 * t)).astype(np.float32)
    second = (0.4 * np.sin(2.0 * math.pi * 1760.0 * t)).astype(np.float32)
    third = np.zeros(count, dtype=np.float32)
    third[:: max(1, experiments.CLAP_SR // 8)] = 0.7
    return [first, second, third]


def _embed_windows(*, torch, processor, model, device: str, windows: list):
    vectors = []
    for window in windows:
        inputs = support.processor_audio_inputs(
            processor,
            window,
            sample_rate=experiments.CLAP_SR,
            return_tensors="pt",
        )
        inputs = {name: tensor.to(device) for name, tensor in inputs.items()}
        with torch.no_grad():
            output = model.get_audio_features(**inputs)
        tensor = support.unwrap_audio_embedding(output)
        vector = tensor.detach().cpu().numpy().reshape(-1).astype(float)
        vectors.append(vector)
    return vectors


def _runtime(model_id: str, revision: str, device_request: str):
    key = (model_id, revision, device_request)
    if key in _RUNTIME:
        return _RUNTIME[key]
    try:
        import torch
        import transformers
        from transformers import ClapModel, ClapProcessor
    except ImportError as exc:
        raise HardenedClapError(
            "CLAP experiment requires torch and transformers on the experiment host"
        ) from exc

    device = experiments._resolve_device(device_request, torch)
    processor = ClapProcessor.from_pretrained(model_id, revision=revision)
    model = ClapModel.from_pretrained(model_id, revision=revision)
    model.eval()
    model.to(device)

    if key not in _PREFLIGHT_DONE:
        vectors = _embed_windows(
            torch=torch,
            processor=processor,
            model=model,
            device=device,
            windows=_synthetic_preflight_audio(),
        )
        try:
            support.embedding_sanity(vectors)
        except support.ClapSupportError as exc:
            raise HardenedClapError(
                f"CLAP checkpoint failed embedding sanity preflight: {model_id}@{revision}: {exc}"
            ) from exc
        _PREFLIGHT_DONE.add(key)

    runtime = (torch, processor, model, device)
    _RUNTIME[key] = runtime
    return runtime


def _install_hardening(*, model_id: str, revision: str, device_request: str) -> None:
    original_feature_spec = experiments.feature_spec

    def hardened_feature_spec(feature_mode: str, window_mode: str, clap_model: str) -> dict:
        spec = original_feature_spec(feature_mode, window_mode, clap_model)
        spec["feature_implementation_version"] = support.FEATURE_IMPLEMENTATION_VERSION
        if feature_mode == "clap":
            spec["clap_revision"] = revision
            torch_module = sys.modules.get("torch")
            transformers_module = sys.modules.get("transformers")
            spec["runtime_versions"] = support.runtime_versions(
                torch_module, transformers_module
            )
        return spec

    def hardened_cache_key(path: Path, spec: dict) -> str:
        resolved = Path(path).resolve()
        stat = resolved.stat()
        identity = support.cache_identity(
            feature_mode=spec["feature_mode"],
            window_mode=spec["window_mode"],
            model_id=spec.get("clap_model"),
            model_revision=spec.get("clap_revision"),
            feature_version=spec.get(
                "feature_implementation_version", support.FEATURE_IMPLEMENTATION_VERSION
            ),
        )
        return f"experiment:{identity}:{resolved}:{stat.st_size}:{stat.st_mtime_ns}"

    def hardened_clap_features(windows: list, *, model_id: str, device_request: str):
        np = experiments._np()
        torch, processor, model, device = _runtime(
            model_id, revision, device_request
        )
        raw = _embed_windows(
            torch=torch,
            processor=processor,
            model=model,
            device=device,
            windows=windows,
        )
        embeddings = []
        for vector in raw:
            norm = float(np.linalg.norm(vector))
            if norm > 0:
                vector = vector / norm
            embeddings.append(vector)
        if not embeddings:
            return None, device
        vector = np.asarray(embeddings).mean(axis=0)
        norm = float(np.linalg.norm(vector))
        if norm > 0:
            vector /= norm
        return [round(float(value), 7) for value in vector], device

    experiments.CLAP_DEFAULT_MODEL = model_id
    experiments.feature_spec = hardened_feature_spec
    experiments.experiment_cache_key = hardened_cache_key
    experiments.clap_features = hardened_clap_features


def main() -> int:
    try:
        known = _preparse(sys.argv[1:])
        if known.feature_mode == "clap":
            _install_hardening(
                model_id=known.clap_model,
                revision=known.clap_revision,
                device_request=known.clap_device,
            )
        delegated = _strip_revision_arg(sys.argv[1:])
        delegated = _ensure_preferred_window(delegated, known.feature_mode)
        if known.feature_mode == "clap":
            delegated = _ensure_selected_model(delegated, known.clap_model)
        sys.argv = [sys.argv[0], *delegated]
        return experiments.main()
    except experiments.ExperimentError as exc:
        import json

        print(json.dumps({"status": "REFUSED", "reason": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
