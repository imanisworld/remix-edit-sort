#!/usr/bin/env python3
"""Controlled audio-representation experiments for ``genre_learner.py``.

This is deliberately separate from the production learner path. It never
changes music files, tags, folders, Serato data, or the canonical taxonomy.
It evaluates alternate audio representations against an explicitly frozen
library holdout so a better representation can be proven before adoption.

Experiment lanes:
- baseline: the current learner's exact middle-window DSP vector
- enhanced-dsp: baseline DSP plus MFCC-like cepstra, chroma, spectral contrast,
  and spectral bandwidth, implemented with NumPy only
- clap: frozen LAION CLAP music audio embeddings via Hugging Face Transformers

Window lanes:
- middle: one representative middle window
- early-middle-late: three windows centered at 20%, 50%, and 80%, aggregated
  at feature level. No track receives more label weight because it has more
  windows.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "preview-only"

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import wave
from collections import Counter
from pathlib import Path

import genre_learner as learner

CLAP_DEFAULT_MODEL = "laion/larger_clap_music"
CLAP_SR = 48000
CLAP_SEG_SECONDS = 10
MULTI_DSP_SEG_SECONDS = 20
WINDOW_CENTERS = {
    "middle": (0.5,),
    "early-middle-late": (0.2, 0.5, 0.8),
}
FEATURE_MODES = ("baseline", "enhanced-dsp", "clap")
WINDOW_MODES = tuple(WINDOW_CENTERS)
_CLAP_RUNTIME: dict[tuple[str, str], tuple[object, object, object, str]] = {}


class ExperimentError(learner.LearnerError):
    pass


def _np():
    return learner._np()


def _decode_windows(
    path: Path,
    *,
    sample_rate: int,
    segment_seconds: int,
    centers: tuple[float, ...],
) -> tuple[list, str | None]:
    """Decode once with afconvert and return deterministic centered windows."""
    np = _np()
    with tempfile.TemporaryDirectory(prefix="genre-experiment-") as temp_dir:
        temp = Path(temp_dir) / "decoded.wav"
        try:
            result = subprocess.run(
                [
                    "/usr/bin/afconvert",
                    "-f",
                    "WAVE",
                    "-d",
                    f"LEI16@{sample_rate}",
                    "-c",
                    "1",
                    str(path),
                    str(temp),
                ],
                capture_output=True,
                timeout=180,
            )
            if result.returncode != 0:
                return [], f"afconvert exit {result.returncode}"
            with wave.open(str(temp), "rb") as wav:
                frame_count = wav.getnframes()
                wanted = segment_seconds * sample_rate
                if frame_count < sample_rate * 10:
                    return [], "decoded audio shorter than 10 seconds"
                windows = []
                for center in centers:
                    midpoint = int(round(center * max(frame_count - 1, 0)))
                    start = max(0, min(frame_count - wanted, midpoint - wanted // 2))
                    wav.setpos(start)
                    data = wav.readframes(min(wanted, frame_count - start))
                    x = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                    if len(x) < sample_rate * 10:
                        return [], f"window at {center:.1f} shorter than 10 seconds"
                    windows.append(x)
            return windows, None
        except subprocess.TimeoutExpired:
            return [], "afconvert timeout"
        except Exception as exc:  # noqa: BLE001 - surfaced as experiment evidence
            return [], f"{type(exc).__name__}: {exc}"


def _stft_power(x, *, sample_rate: int = learner.SR):
    np = _np()
    n_fft, hop = 2048, 512
    frames = 1 + (len(x) - n_fft) // hop
    if frames < 50:
        return None
    indices = np.arange(n_fft)[None, :] + hop * np.arange(frames)[:, None]
    window = np.hanning(n_fft)
    spectrum = np.abs(np.fft.rfft(x[indices] * window, axis=1))
    power = spectrum ** 2
    freqs = np.fft.rfftfreq(n_fft, 1 / sample_rate)
    return spectrum, power, freqs


def _mel_filterbank(freqs, *, n_filters: int = 40, f_min: float = 20.0, f_max: float | None = None):
    np = _np()
    if f_max is None:
        f_max = float(freqs[-1])

    def hz_to_mel(hz):
        return 2595.0 * np.log10(1.0 + hz / 700.0)

    def mel_to_hz(mel):
        return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

    mel_points = np.linspace(hz_to_mel(f_min), hz_to_mel(f_max), n_filters + 2)
    hz_points = mel_to_hz(mel_points)
    bank = np.zeros((n_filters, len(freqs)), dtype=float)
    for index in range(n_filters):
        left, center, right = hz_points[index:index + 3]
        if center <= left or right <= center:
            continue
        left_mask = (freqs >= left) & (freqs <= center)
        right_mask = (freqs > center) & (freqs <= right)
        bank[index, left_mask] = (freqs[left_mask] - left) / (center - left)
        bank[index, right_mask] = (right - freqs[right_mask]) / (right - center)
    return bank


def _mfcc_summary(power, freqs, *, n_mfcc: int = 20):
    """Return mean/std of 20 DCT-II log-mel cepstral coefficients."""
    np = _np()
    bank = _mel_filterbank(freqs)
    mel_energy = np.maximum(power @ bank.T, 1e-12)
    log_mel = np.log(mel_energy)
    n_filters = log_mel.shape[1]
    k = np.arange(n_mfcc)[:, None]
    n = np.arange(n_filters)[None, :]
    dct = np.cos(math.pi / n_filters * (n + 0.5) * k)
    dct[0] *= 1.0 / math.sqrt(2.0)
    dct *= math.sqrt(2.0 / n_filters)
    coeffs = log_mel @ dct.T
    return [*coeffs.mean(axis=0), *coeffs.std(axis=0)]


def _chroma_summary(power, freqs):
    np = _np()
    chroma = np.zeros((power.shape[0], 12), dtype=float)
    valid = freqs >= 40.0
    valid_freqs = freqs[valid]
    if not len(valid_freqs):
        return [0.0] * 24
    midi = np.rint(69.0 + 12.0 * np.log2(valid_freqs / 440.0)).astype(int)
    classes = np.mod(midi, 12)
    valid_power = power[:, valid]
    for pitch_class in range(12):
        chroma[:, pitch_class] = valid_power[:, classes == pitch_class].sum(axis=1)
    chroma /= chroma.sum(axis=1, keepdims=True) + 1e-12
    return [*chroma.mean(axis=0), *chroma.std(axis=0)]


def _spectral_contrast_summary(power, freqs):
    np = _np()
    edges = [0.0, 200.0, 400.0, 800.0, 1600.0, 3200.0, 6400.0, float(freqs[-1]) + 1.0]
    log_power = 10.0 * np.log10(power + 1e-12)
    means, stds = [], []
    for low, high in zip(edges[:-1], edges[1:]):
        mask = (freqs >= low) & (freqs < high)
        if not mask.any():
            contrast = np.zeros(power.shape[0])
        else:
            band = log_power[:, mask]
            contrast = np.percentile(band, 90, axis=1) - np.percentile(band, 10, axis=1)
        means.append(float(contrast.mean()))
        stds.append(float(contrast.std()))
    return [*means, *stds]


def _spectral_bandwidth_summary(power, freqs):
    np = _np()
    total = power.sum(axis=1) + 1e-12
    centroid = (power * freqs).sum(axis=1) / total
    bandwidth = np.sqrt((power * (freqs[None, :] - centroid[:, None]) ** 2).sum(axis=1) / total)
    return [float(bandwidth.mean()), float(bandwidth.std())]


def enhanced_dsp_features(x):
    """Current DSP vector plus four standard music-information feature families."""
    base = learner.features(x)
    if base is None:
        return None
    stft = _stft_power(x)
    if stft is None:
        return None
    _spectrum, power, freqs = stft
    extras = []
    extras.extend(_mfcc_summary(power, freqs))
    extras.extend(_chroma_summary(power, freqs))
    extras.extend(_spectral_contrast_summary(power, freqs))
    extras.extend(_spectral_bandwidth_summary(power, freqs))
    return [*base, *[round(float(value), 5) for value in extras]]


def aggregate_vectors(vectors: list[list[float]]) -> list[float] | None:
    np = _np()
    if not vectors:
        return None
    lengths = {len(vector) for vector in vectors}
    if len(lengths) != 1:
        raise ExperimentError(f"cannot aggregate feature vectors with dimensions {sorted(lengths)}")
    return [round(float(value), 6) for value in np.asarray(vectors, dtype=float).mean(axis=0)]


def _resolve_device(requested: str, torch) -> str:
    if requested != "auto":
        return requested
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _clap_runtime(model_id: str, device_request: str):
    key = (model_id, device_request)
    if key in _CLAP_RUNTIME:
        return _CLAP_RUNTIME[key]
    try:
        import torch
        from transformers import ClapModel, ClapProcessor
    except ImportError as exc:
        raise ExperimentError(
            "CLAP experiment requires torch and transformers; install them only on the experiment host"
        ) from exc
    device = _resolve_device(device_request, torch)
    processor = ClapProcessor.from_pretrained(model_id)
    model = ClapModel.from_pretrained(model_id)
    model.eval()
    model.to(device)
    runtime = (torch, processor, model, device)
    _CLAP_RUNTIME[key] = runtime
    return runtime


def clap_features(windows: list, *, model_id: str, device_request: str) -> tuple[list[float] | None, str]:
    np = _np()
    torch, processor, model, device = _clap_runtime(model_id, device_request)
    embeddings = []
    for window in windows:
        inputs = processor(audios=window, sampling_rate=CLAP_SR, return_tensors="pt")
        inputs = {name: tensor.to(device) for name, tensor in inputs.items()}
        with torch.no_grad():
            embedding = model.get_audio_features(**inputs)
        vector = embedding.detach().cpu().numpy().reshape(-1).astype(float)
        norm = float(np.linalg.norm(vector))
        if norm > 0:
            vector /= norm
        embeddings.append(vector)
    if not embeddings:
        return None, device
    vector = np.asarray(embeddings).mean(axis=0)
    norm = float(np.linalg.norm(vector))
    if norm > 0:
        vector /= norm
    return [round(float(value), 7) for value in vector], device


def feature_spec(feature_mode: str, window_mode: str, clap_model: str) -> dict:
    centers = WINDOW_CENTERS[window_mode]
    if feature_mode == "clap":
        segment_seconds = CLAP_SEG_SECONDS
        sample_rate = CLAP_SR
    elif window_mode == "middle":
        segment_seconds = learner.SEG
        sample_rate = learner.SR
    else:
        segment_seconds = MULTI_DSP_SEG_SECONDS
        sample_rate = learner.SR
    return {
        "feature_mode": feature_mode,
        "window_mode": window_mode,
        "window_centers": list(centers),
        "segment_seconds": segment_seconds,
        "sample_rate": sample_rate,
        "clap_model": clap_model if feature_mode == "clap" else None,
        "aggregation": "feature_mean" if len(centers) > 1 else "single_window",
    }


def experiment_cache_key(path: Path, spec: dict) -> str:
    resolved = Path(path).resolve()
    stat = resolved.stat()
    model = spec.get("clap_model") or "none"
    return (
        f"experiment:{spec['feature_mode']}:{spec['window_mode']}:{model}:"
        f"{resolved}:{stat.st_size}:{stat.st_mtime_ns}"
    )


def feature_for_path(
    path: Path,
    cache_path: Path,
    cache: dict[str, dict],
    *,
    feature_mode: str,
    window_mode: str,
    clap_model: str,
    clap_device: str,
):
    spec = feature_spec(feature_mode, window_mode, clap_model)
    key = experiment_cache_key(path, spec)
    cached = cache.get(key)
    if cached is not None:
        return cached.get("feat"), cached.get("error"), cached.get("runtime_device")

    runtime_device = None
    if feature_mode == "baseline" and window_mode == "middle":
        x, error = learner.decode(path)
        feat = learner.features(x) if x is not None else None
    else:
        windows, error = _decode_windows(
            path,
            sample_rate=spec["sample_rate"],
            segment_seconds=spec["segment_seconds"],
            centers=tuple(spec["window_centers"]),
        )
        feat = None
        if not error:
            if feature_mode == "baseline":
                feat = aggregate_vectors([learner.features(window) for window in windows if learner.features(window) is not None])
            elif feature_mode == "enhanced-dsp":
                vectors = []
                for window in windows:
                    vector = enhanced_dsp_features(window)
                    if vector is not None:
                        vectors.append(vector)
                feat = aggregate_vectors(vectors)
            elif feature_mode == "clap":
                feat, runtime_device = clap_features(
                    windows,
                    model_id=clap_model,
                    device_request=clap_device,
                )
            else:  # defensive; argparse constrains this
                raise ExperimentError(f"unsupported feature mode {feature_mode!r}")

    if feat is None and not error:
        error = "feature extraction returned None"
    row = {
        "key": key,
        "path": str(Path(path).resolve()),
        "spec": spec,
        "feat": feat,
        "error": error,
        "runtime_device": runtime_device,
    }
    learner.append_feature(cache_path, row)
    cache[key] = row
    return feat, error, runtime_device


def materialize_features(
    examples: list[dict],
    cache_path: Path,
    cache: dict[str, dict],
    failures: list[dict],
    *,
    feature_mode: str,
    window_mode: str,
    clap_model: str,
    clap_device: str,
):
    usable = []
    runtime_devices = set()
    for index, row in enumerate(examples, start=1):
        path_text = str(row.get("path") or "")
        if not path_text or not Path(path_text).is_file():
            failures.append({
                "path": path_text or None,
                "genre": row.get("genre"),
                "error": "experimental representations require a local audio path",
            })
            continue
        feat, error, runtime_device = feature_for_path(
            Path(path_text),
            cache_path,
            cache,
            feature_mode=feature_mode,
            window_mode=window_mode,
            clap_model=clap_model,
            clap_device=clap_device,
        )
        if runtime_device:
            runtime_devices.add(runtime_device)
        if feat is None:
            failures.append({"path": path_text, "genre": row.get("genre"), "error": error})
            continue
        usable.append({**row, "feat": feat})
        if index % 25 == 0:
            print(f"experiment features {index}/{len(examples)} failures={len(failures)}", flush=True)
    return usable, sorted(runtime_devices)


def _load_fixed_holdout(path: Path) -> set[str]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = value.get("holdout", value) if isinstance(value, dict) else value
    holdout = {
        row["path"] if isinstance(row, dict) else str(row)
        for row in rows
    }
    if not holdout:
        raise ExperimentError("--holdout-file contains no holdout paths")
    return holdout


def run_experiment(args) -> int:
    rules = learner.load_rules(args.rules)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = args.out_dir / "genre-experiment-features.jsonl"
    cache = learner.load_feature_cache(cache_path)

    rows = learner.library_rows(
        learner.parse_serato_database(args.serato_db, args.serato_parser), args.library
    )
    grouped = learner.single_label_examples(rows, rules)
    fixed_holdout = _load_fixed_holdout(args.holdout_file)
    cap_overrides = learner.parse_cap_overrides(args.cap_override, rules)
    library_train, library_holdout, split_report = learner.split_library_examples(
        grouped,
        rules,
        seed=args.seed,
        fixed_holdout=fixed_holdout,
        cap_overrides=cap_overrides,
    )
    actual_holdout_paths = {row["path"] for row in library_holdout}
    missing_holdout = sorted(fixed_holdout - actual_holdout_paths)
    if missing_holdout:
        raise ExperimentError(
            f"frozen holdout no longer resolves to current eligible library rows: {len(missing_holdout)} missing"
        )

    external, external_rejected = learner.load_external_manifest(
        args.external_manifest, rules, library_holdout
    )
    training_rows, sample_report = learner.supplement_training(library_train, external, rules)

    failures = []
    training_with_features, train_devices = materialize_features(
        training_rows,
        cache_path,
        cache,
        failures,
        feature_mode=args.feature_mode,
        window_mode=args.window_mode,
        clap_model=args.clap_model,
        clap_device=args.clap_device,
    )
    holdout_with_features, holdout_devices = materialize_features(
        library_holdout,
        cache_path,
        cache,
        failures,
        feature_mode=args.feature_mode,
        window_mode=args.window_mode,
        clap_model=args.clap_model,
        clap_device=args.clap_device,
    )

    target = int(rules["training"]["target_examples_per_genre"])
    usable_counts = Counter(row["genre"] for row in training_with_features)
    trainable = {
        genre for genre in rules["canonical_genres"]
        if usable_counts[genre] >= target
    }
    unsupported = [
        {"genre": genre, "usable_training": usable_counts[genre], "target": target}
        for genre in rules["canonical_genres"] if genre not in trainable
    ]
    if not trainable:
        raise ExperimentError("no Genre reached the training target in experiment")

    bundle = learner.train_hierarchy(training_with_features, trainable, rules)
    evaluation = learner.evaluate(bundle, holdout_with_features, trainable, args.out_dir)
    feature_dimension = (
        len(training_with_features[0]["feat"]) if training_with_features else None
    )
    spec = feature_spec(args.feature_mode, args.window_mode, args.clap_model)
    metadata = {
        "mode": "read-only-controlled-experiment",
        "spec": spec,
        "feature_dimension": feature_dimension,
        "runtime_devices": sorted(set(train_devices + holdout_devices)),
        "frozen_holdout_requested": len(fixed_holdout),
        "frozen_holdout_resolved": len(library_holdout),
        "library_training_rows": len(library_train),
        "training_rows_with_features": len(training_with_features),
        "holdout_rows_with_features": len(holdout_with_features),
        "training_target": target,
        "trainable_genres": sorted(trainable),
        "not_yet_trainable": unsupported,
        "external_manifest": str(args.external_manifest) if args.external_manifest else None,
        "external_examples_accepted": len(external),
        "external_rejected": external_rejected,
        "cap_overrides": cap_overrides,
        "seed": args.seed,
        "taxonomy_folded_for_sample_count": False,
        "library_or_serato_mutation": False,
    }
    learner.write_json(args.out_dir / "genre-experiment-metadata.json", metadata)
    learner.write_json(args.out_dir / "genre-experiment-eval.json", evaluation)
    learner.write_json(args.out_dir / "genre-experiment-training-counts.json", {
        "split": split_report,
        "training": sample_report,
    })
    learner.write_json(args.out_dir / "genre-experiment-feature-failures.json", failures)

    print(json.dumps({
        "feature_mode": args.feature_mode,
        "window_mode": args.window_mode,
        "feature_dimension": feature_dimension,
        "holdout": len(library_holdout),
        "trainable_genres": len(trainable),
        "evaluation": evaluation,
        "failures": len(failures),
        "runtime_devices": metadata["runtime_devices"],
        "out_dir": str(args.out_dir),
    }, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--rules", type=Path, default=learner.DEFAULT_RULES)
    root.add_argument("--library", type=Path, default=learner.DEFAULT_LIBRARY)
    root.add_argument("--serato-db", type=Path, default=learner.DEFAULT_SERATO_DB)
    root.add_argument("--serato-parser", type=Path, default=learner.DEFAULT_SERATO_PARSER)
    root.add_argument("--out-dir", type=Path, required=True)
    root.add_argument("--holdout-file", type=Path, required=True)
    root.add_argument("--external-manifest", type=Path)
    root.add_argument("--feature-mode", choices=FEATURE_MODES, required=True)
    root.add_argument("--window-mode", choices=WINDOW_MODES, default="middle")
    root.add_argument("--clap-model", default=CLAP_DEFAULT_MODEL)
    root.add_argument("--clap-device", default="auto")
    root.add_argument("--cap-override", default="")
    root.add_argument("--seed", type=int, default=42)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return run_experiment(args)
    except learner.LearnerError as exc:
        print(json.dumps({"status": "REFUSED", "reason": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
