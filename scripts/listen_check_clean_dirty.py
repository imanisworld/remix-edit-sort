#!/usr/bin/env python3
"""Read-only listening check for a controlled batch of evidence-free tracks.

Applies the operator's explicit rule for this batch: transcribe each track
with faster-whisper and stop at the first confirmed explicit term (Dirty);
if no term is found through the full transcript, classify Clean.

This deliberately diverges from transcribe_explicit_evidence.py's stricter
"a transcript miss never proves Clean" design, by explicit human-operator
instruction for this specific batch. A Whisper miss is evidence, not proof
(quiet/mumbled/background vocals, non-English lyrics, or ad-libs can still
be missed) -- callers should treat the Clean classification here as "no
detected evidence", not as a guarantee.

Never edits audio or tags. Writes a JSON report only.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import argparse
import json
from pathlib import Path

import transcribe_explicit_evidence as tee


def classify(path: Path, terms: list[str], model) -> dict:
    segments, info = model.transcribe(str(path), vad_filter=True)
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        hits = tee.find_terms(text, terms)
        if hits:
            return {
                "classification": "Dirty",
                "stopped_early": True,
                "matched_term": hits[0],
                "matched_segment": text,
                "matched_at_seconds": segment.start,
                "language": getattr(info, "language", None),
            }
    return {
        "classification": "Clean",
        "stopped_early": False,
        "note": "no explicit terms detected across the full transcript",
        "language": getattr(info, "language", None),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths-file", required=True, type=Path, help="JSON list of file paths to check")
    parser.add_argument("--terms-file", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model", default="small.en")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--compute-type", default="default")
    args = parser.parse_args()

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SystemExit(
            "faster-whisper is optional and not installed. Install locally with: "
            "python3 -m pip install faster-whisper"
        ) from exc

    terms = tee.load_terms(args.terms_file)
    if not terms:
        raise SystemExit("terms file contains no usable entries")
    paths = json.loads(args.paths_file.read_text(encoding="utf-8"))

    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    results = []
    for index, raw_path in enumerate(paths, 1):
        path = Path(raw_path)
        if not path.is_file():
            results.append({"path": str(path), "error": "source missing"})
            print(f"[{index}/{len(paths)}] ERROR missing {path.name}", flush=True)
            continue
        try:
            result = classify(path, terms, model)
        except Exception as exc:
            results.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
            print(f"[{index}/{len(paths)}] ERROR {type(exc).__name__} {path.name}", flush=True)
            continue
        result["path"] = str(path)
        result["sha256"] = tee.sha256_file(path)
        results.append(result)
        print(f"[{index}/{len(paths)}] {result['classification']:5s} {path.name}", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    clean = sum(1 for r in results if r.get("classification") == "Clean")
    dirty = sum(1 for r in results if r.get("classification") == "Dirty")
    errors = sum(1 for r in results if "error" in r)
    payload = {
        "mode": "read-only-listening-check",
        "source_mutation": False,
        "rule": "first confirmed explicit term -> Dirty (stop listening); "
                "no explicit terms through full transcript -> Clean",
        "safety_note": "Clean here means 'no explicit term detected by Whisper', not a certainty.",
        "terms_file": str(args.terms_file),
        "model": args.model,
        "clean_count": clean,
        "dirty_count": dirty,
        "error_count": errors,
        "results": results,
    }
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"CLEAN": clean, "DIRTY": dirty, "ERRORS": errors, "output": str(args.out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
