#!/usr/bin/env python3
"""Generate read-only transcript evidence for unresolved Clean/Dirty review.

Requires the optional `faster-whisper` package and a user-supplied terms file.
It never edits audio and never declares a track Clean. Its output is evidence
for check_version_evidence.py, which treats detected terms as LIKELY_DIRTY and
requires review before any label can be applied.

By default the raw transcript is NOT written to disk; pass --include-transcript
only when you explicitly want it in the local output artifact.
"""

from __future__ import annotations

# Read-only: inspects the library and writes reports/derived data only.
SAFETY_CLASSIFICATION = "read-only"

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_terms(path: Path) -> list[str]:
    terms = []
    seen = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        term = raw.strip()
        if not term or term.startswith("#"):
            continue
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            terms.append(term)
    return terms


def find_terms(text: str, terms: list[str]) -> list[str]:
    detected = []
    folded = text.casefold()
    for term in terms:
        pattern = r"(?<!\w)" + re.escape(term.casefold()).replace(r"\ ", r"\s+") + r"(?!\w)"
        if re.search(pattern, folded, flags=re.UNICODE):
            detected.append(term)
    return detected


def is_needs_check_path(path: str) -> bool:
    return any(part.casefold() == "_needs check" for part in Path(path).parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path, help="inventory.csv from inventory_library.py")
    parser.add_argument("--terms-file", required=True, type=Path, help="Local reviewed explicit-language terms, one per line")
    parser.add_argument("--out", required=True, type=Path, help="Output transcript-evidence.json")
    parser.add_argument("--model", default="small.en", help="faster-whisper model name (default: small.en)")
    parser.add_argument("--device", default="auto", help="faster-whisper device (default: auto)")
    parser.add_argument("--compute-type", default="default", help="faster-whisper compute type")
    parser.add_argument("--only-needs-check", action="store_true")
    parser.add_argument("--include-transcript", action="store_true", help="Opt in to storing raw transcript text")
    args = parser.parse_args()

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SystemExit(
            "faster-whisper is optional and not installed. Install locally with: "
            "python3 -m pip install faster-whisper"
        ) from exc

    terms = load_terms(args.terms_file)
    if not terms:
        raise SystemExit("terms file contains no usable entries")

    with args.inventory.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if args.only_needs_check:
        rows = [row for row in rows if is_needs_check_path(row.get("full_path", ""))]

    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    records = []
    errors = []

    for row in rows:
        path = Path(row.get("full_path", ""))
        if not path.is_file():
            errors.append({"full_path": str(path), "error": "source missing"})
            continue
        try:
            segments, info = model.transcribe(str(path), vad_filter=True)
            transcript = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
            detected = find_terms(transcript, terms)
            record = {
                "full_path": str(path),
                "sha256": row.get("sha256") or sha256_file(path),
                "explicit_terms_detected": detected,
                "source": f"faster-whisper:{args.model}",
                "confidence": "audio-review",
                "language": getattr(info, "language", None),
                "language_probability": getattr(info, "language_probability", None),
            }
            if args.include_transcript:
                record["transcript"] = transcript
            records.append(record)
        except Exception as exc:
            errors.append({"full_path": str(path), "error": f"{type(exc).__name__}: {exc}"})

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": "read-only-evidence",
        "source_mutation": False,
        "model": args.model,
        "raw_transcripts_included": args.include_transcript,
        "safety_note": "No detected explicit terms is NOT proof that a track is Clean.",
        "records": records,
        "errors": errors,
    }
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"records": len(records), "errors": len(errors), "output": str(args.out)}, indent=2))


if __name__ == "__main__":
    main()
