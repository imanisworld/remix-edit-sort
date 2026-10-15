#!/usr/bin/env python3
"""Taxonomy-preserving genre learner for the DJ library.

Read-only with respect to the music library and Serato. Outputs only reports,
feature caches and predictions under --out-dir.

Canonical Genres are never folded into parent Genres to satisfy sample-count
requirements. Sparse Genres may use explicitly accepted external references;
final evaluation remains Brooke/user-library holdout only.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "preview-only"

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import wave
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RULES = REPO_ROOT / "references/genre-learner-rules.json"
DEFAULT_DOCS = Path("/Users/djb.a.e/Documents/DJ-library-project-20260825")
DEFAULT_LIBRARY = Path("/Users/djb.a.e/Music/DJ-Music/Music")
DEFAULT_SERATO_DB = Path("/Users/djb.a.e/Music/_Serato_/database V2")
DEFAULT_SERATO_PARSER = Path("/Volumes/T5 EVO/DJ/Tools/remix-edit-sort/scripts")

SR = 22050
SEG = 60
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".flac", ".wav", ".aif", ".aiff"}


class LearnerError(RuntimeError):
    pass


def _np():
    try:
        import numpy as np
    except ImportError as exc:
        raise LearnerError("genre learner requires numpy") from exc
    return np


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_rules(path: Path) -> dict:
    rules = json.loads(Path(path).read_text(encoding="utf-8"))
    canonical = rules.get("canonical_genres")
    if not isinstance(canonical, list) or not canonical:
        raise LearnerError(f"rules contain no canonical_genres: {path}")
    if len(canonical) != len(set(canonical)):
        raise LearnerError("canonical_genres contains duplicates")
    if not rules.get("training", {}).get("never_fold_to_parent_for_sample_count"):
        raise LearnerError("rules must explicitly forbid sample-count taxonomy folding")
    alias_index(rules)
    context_index(rules)
    compound_index(rules)
    genre_to_family(rules)
    return rules


def alias_index(rules: dict) -> dict[str, str]:
    index = {}
    for genre in rules["canonical_genres"]:
        key = norm(genre)
        if key in index and index[key] != genre:
            raise LearnerError(f"canonical normalization collision: {genre!r} / {index[key]!r}")
        index[key] = genre
    for raw, genre in rules.get("aliases", {}).items():
        if genre not in rules["canonical_genres"]:
            raise LearnerError(f"alias {raw!r} targets noncanonical Genre {genre!r}")
        key = norm(raw)
        if key in index and index[key] != genre:
            raise LearnerError(f"alias normalization collision: {raw!r}")
        index[key] = genre
    return index


def context_index(rules: dict) -> dict[str, str]:
    """Normalized Genre-field context token -> canonical Grouping term."""
    index = {}
    for raw in rules.get("context_tokens_not_genre", []):
        key = norm(raw)
        if key:
            index[key] = str(raw).strip()
    for raw, grouping in rules.get("context_aliases", {}).items():
        key = norm(raw)
        value = str(grouping).strip()
        if not key or not value:
            raise LearnerError("context aliases must have non-empty keys and values")
        # Explicit aliases deliberately canonicalize legacy spellings from the
        # compatibility list (for example Old Head Party -> Old Head).
        index[key] = value
    return index


def compound_index(rules: dict) -> dict[str, dict]:
    """Validate explicit tokens that yield both Genre and Grouping values."""
    index = {}
    for raw, mapping in rules.get("compound_aliases", {}).items():
        if not isinstance(mapping, dict):
            raise LearnerError(f"compound alias {raw!r} must be an object")
        genres = mapping.get("genres", [])
        grouping = mapping.get("grouping", [])
        if not isinstance(genres, list) or not isinstance(grouping, list):
            raise LearnerError(f"compound alias {raw!r} values must be lists")
        unknown = [genre for genre in genres if genre not in rules["canonical_genres"]]
        if unknown:
            raise LearnerError(f"compound alias {raw!r} targets noncanonical Genre {unknown!r}")
        key = norm(raw)
        value = {
            "genres": list(dict.fromkeys(genres)),
            "grouping": list(dict.fromkeys(str(term).strip() for term in grouping if str(term).strip())),
        }
        if not key or (not value["genres"] and not value["grouping"]):
            raise LearnerError(f"compound alias {raw!r} has no mapping")
        if key in index and index[key] != value:
            raise LearnerError(f"compound normalization collision: {raw!r}")
        index[key] = value
    return index


def genre_to_family(rules: dict) -> dict[str, str]:
    mapping = {}
    for family, genres in rules.get("families", {}).items():
        for genre in genres:
            if genre not in rules["canonical_genres"]:
                raise LearnerError(f"family {family!r} names noncanonical Genre {genre!r}")
            if genre in mapping:
                raise LearnerError(f"Genre {genre!r} belongs to multiple learner families")
            mapping[genre] = family
    for genre in rules["canonical_genres"]:
        mapping.setdefault(genre, f"single::{genre}")
    return mapping


PROTECTED_AMPERSAND_PHRASES = ("r&b", "drum & bass", "funk & soul", "juke & footwork")
_PLACEHOLDER = re.compile(r"__PROTECT_(\d+)__")


def _split_genre_string(raw: str) -> list[str]:
    """Split a multi-genre string on separators without breaking protected
    ampersand phrases. Placeholders are restored inside every token (e.g.
    "Alternative R&B" or "Nu R&B"), preserving the original casing, so a
    placeholder can never leak into a label or an unknown-token report."""
    saved: list[str] = []

    def _stash(match: re.Match) -> str:
        saved.append(match.group(0))
        return f"__PROTECT_{len(saved) - 1}__"

    text = raw
    for phrase in PROTECTED_AMPERSAND_PHRASES:
        text = re.sub(re.escape(phrase), _stash, text, flags=re.I)
    parts = re.split(r"\s*(?:;|,|/|\+|\||\s&\s)\s*", text)

    def _restore(part: str) -> str:
        return _PLACEHOLDER.sub(lambda match: saved[int(match.group(1))], part).strip()

    restored = [_restore(part) for part in parts]
    return [part for part in restored if part]


def classify_raw_genre(raw: str, rules: dict) -> dict:
    """Interpret metadata without inventing or folding a Genre."""
    raw = (raw or "").strip()
    if not raw:
        return {"status": "blank", "labels": [], "grouping": [], "unknown": [], "raw": raw}

    legacy = {
        norm(value): value for value in rules.get("legacy_compounds_requiring_audio_review", [])
    }
    if norm(raw) in legacy:
        return {
            "status": "legacy_compound_review",
            "labels": [],
            "grouping": [],
            "unknown": [raw],
            "raw": raw,
        }

    aliases = alias_index(rules)
    contexts = context_index(rules)
    compounds = compound_index(rules)
    whole_compound = compounds.get(norm(raw))
    if whole_compound:
        labels = whole_compound["genres"]
        grouping = whole_compound["grouping"]
        return {
            "status": "single" if len(labels) == 1 else "multi_label" if labels else "context_only",
            "labels": labels, "grouping": grouping, "unknown": [], "raw": raw,
        }
    whole_context = contexts.get(norm(raw))
    if whole_context:
        return {
            "status": "context_only", "labels": [], "grouping": [whole_context],
            "unknown": [], "raw": raw,
        }
    whole = aliases.get(norm(raw))
    if whole:
        return {
            "status": "single", "labels": [whole], "grouping": [],
            "unknown": [], "raw": raw,
        }

    modifier_grouping = {
        "edit": "Edit", "remix": "Remix", "flip": "Remix", "bootleg": "Remix",
    }
    labels, grouping, unknown = [], [], []
    for token in _split_genre_string(raw):
        key = norm(token)
        if not key:
            continue
        compound = compounds.get(key)
        if compound:
            labels.extend(value for value in compound["genres"] if value not in labels)
            grouping.extend(value for value in compound["grouping"] if value not in grouping)
            continue
        context = contexts.get(key)
        if context:
            if context not in grouping:
                grouping.append(context)
            continue
        if key in legacy:
            unknown.append(token)
            continue
        genre = aliases.get(key)
        if genre:
            if genre not in labels:
                labels.append(genre)
            continue
        match = re.fullmatch(r"(.+?)\s+(edit|remix|flip|bootleg)", token, flags=re.I)
        if match:
            stem_key = norm(match.group(1))
            modifier = modifier_grouping[match.group(2).casefold()]
            stem_genre = aliases.get(stem_key)
            stem_context = contexts.get(stem_key)
            if stem_genre:
                if stem_genre not in labels:
                    labels.append(stem_genre)
                if modifier not in grouping:
                    grouping.append(modifier)
                continue
            if stem_context:
                for value in (stem_context, modifier):
                    if value not in grouping:
                        grouping.append(value)
                continue
            # An explicit version is still useful even when the musical
            # style needs review: "Afrobeat Bootleg" proves a version marker,
            # not that historical Afrobeat equals contemporary Afrobeats.
            unknown.append(match.group(1))
            if modifier not in grouping:
                grouping.append(modifier)
            continue
        unknown.append(token)

    if unknown and labels:
        status = "mixed_unknown"
    elif unknown:
        status = "unknown"
    elif len(labels) > 1:
        status = "multi_label"
    elif len(labels) == 1:
        status = "single"
    else:
        status = "context_only"
    return {
        "status": status, "labels": labels, "grouping": grouping,
        "unknown": unknown, "raw": raw,
    }


def training_status(counts: dict[str, int], rules: dict) -> dict[str, str]:
    target = int(rules["training"]["target_examples_per_genre"])
    return {
        genre: ("trainable" if int(counts.get(genre, 0)) >= target else "not_yet_trainable")
        for genre in rules["canonical_genres"]
    }


def parse_serato_database(database: Path, parser_path: Path) -> list[dict]:
    parser_path = Path(parser_path)
    if str(parser_path) not in sys.path:
        sys.path.insert(0, str(parser_path))
    try:
        import check_serato_crate_paths as cs  # type: ignore
    except ImportError as exc:
        raise LearnerError(f"cannot import Serato parser from {parser_path}") from exc
    return cs.parse_database(Path(database))


def library_rows(rows: list[dict], library: Path) -> list[dict]:
    library_root = Path(library).resolve()
    result = []
    for row in rows:
        raw_path = str(row.get("path") or "")
        path = Path(raw_path if raw_path.startswith("/") else "/" + raw_path)
        resolved = path.resolve()
        try:
            resolved.relative_to(library_root)
        except ValueError:
            continue
        if "needs check" in str(resolved).casefold():
            continue
        result.append({**row, "file": str(resolved)})
    return result


def build_census(rows: list[dict], rules: dict) -> dict:
    raw_counts = Counter()
    canonical_counts = Counter()
    status_counts = Counter()
    unknown_counts = Counter()
    examples = defaultdict(list)
    for row in rows:
        raw = row.get("genre") or ""
        raw_counts[raw] += 1
        parsed = classify_raw_genre(raw, rules)
        status_counts[parsed["status"]] += 1
        if parsed["status"] == "single":
            genre = parsed["labels"][0]
            canonical_counts[genre] += 1
            if len(examples[genre]) < 5:
                examples[genre].append(row.get("file"))
        for unknown in parsed["unknown"]:
            unknown_counts[unknown] += 1
    return {
        "raw_genre_counts": dict(raw_counts.most_common()),
        "canonical_single_label_counts": {
            genre: canonical_counts.get(genre, 0) for genre in rules["canonical_genres"]
        },
        "parse_status_counts": dict(status_counts),
        "unknown_token_counts": dict(unknown_counts.most_common()),
        "examples": dict(examples),
    }


def decode(path: Path):
    """afconvert -> mono 22.05k s16 WAV; return middle SEG seconds."""
    np = _np()
    with tempfile.TemporaryDirectory(prefix="genre-learner-") as temp_dir:
        temp = Path(temp_dir) / "segment.wav"
        try:
            result = subprocess.run(
                [
                    "/usr/bin/afconvert", "-f", "WAVE", "-d", f"LEI16@{SR}",
                    "-c", "1", str(path), str(temp),
                ],
                capture_output=True,
                timeout=180,
            )
            if result.returncode != 0:
                return None, f"afconvert exit {result.returncode}"
            with wave.open(str(temp), "rb") as wav:
                n = wav.getnframes()
                segment = SEG * SR
                start = max(0, (n - segment) // 2)
                wav.setpos(start)
                data = wav.readframes(min(segment, n - start))
            x = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            if len(x) < SR * 10:
                return None, "decoded segment shorter than 10 seconds"
            return x, None
        except subprocess.TimeoutExpired:
            return None, "afconvert timeout"
        except Exception as exc:  # noqa: BLE001 - recorded as evidence
            return None, f"{type(exc).__name__}: {exc}"


def features(x):
    np = _np()
    n_fft, hop = 2048, 512
    win = np.hanning(n_fft)
    frames = 1 + (len(x) - n_fft) // hop
    if frames < 50:
        return None
    idx = np.arange(n_fft)[None, :] + hop * np.arange(frames)[:, None]
    spectrum = np.abs(np.fft.rfft(x[idx] * win, axis=1))
    freqs = np.fft.rfftfreq(n_fft, 1 / SR)
    power = spectrum ** 2
    total = power.sum(axis=1) + 1e-10

    edges = np.geomspace(30, 10000, 25)
    bands = np.stack([
        power[:, (freqs >= lo) & (freqs < hi)].sum(axis=1)
        for lo, hi in zip(edges[:-1], edges[1:])
    ], axis=1)
    logb = np.log10(bands + 1e-9)
    centroid = (spectrum * freqs).sum(axis=1) / (spectrum.sum(axis=1) + 1e-9)
    cumulative = np.cumsum(power, axis=1)
    rolloff = freqs[np.argmax(cumulative >= 0.85 * total[:, None], axis=1)]
    flux = np.sqrt(((np.diff(spectrum, axis=0)) ** 2).sum(axis=1))
    zcr = np.mean(np.abs(np.diff(np.sign(x))) > 0)
    rms = np.sqrt(np.mean(x ** 2))
    peak = np.percentile(np.abs(x), 99.9)

    onset = np.maximum(0, np.diff(logb, axis=0)).sum(axis=1)
    onset -= onset.mean()
    ac = np.correlate(onset, onset, "full")[len(onset) - 1:]
    ac /= ac[0] + 1e-9
    fps = SR / hop
    lo_lag, hi_lag = int(fps * 60 / 200), int(fps * 60 / 60)
    lag = lo_lag + int(np.argmax(ac[lo_lag:hi_lag]))
    bpm = 60.0 * fps / lag

    values = [
        bpm,
        float(ac[lag]),
        float(ac[min(2 * lag, len(ac) - 1)]),
        zcr,
        rms,
        peak,
        float(centroid.mean()),
        float(centroid.std()),
        float(rolloff.mean()),
        float(rolloff.std()),
        float(flux.mean()),
        float(flux.std()),
        float(power[:, freqs < 150].sum() / power.sum()),
        float(power[:, (freqs >= 150) & (freqs < 2000)].sum() / power.sum()),
        float(power[:, freqs >= 4000].sum() / power.sum()),
    ]
    values += list(logb.mean(axis=0))
    values += list(logb.std(axis=0))
    return [round(float(value), 5) for value in values]


def load_feature_cache(path: Path) -> dict[str, dict]:
    done = {}
    if not Path(path).is_file():
        return done
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            done[row["key"]] = row
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return done


def append_feature(path: Path, row: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def feature_for_path(path: Path, cache_path: Path, cache: dict[str, dict]):
    key = f"file:{Path(path).resolve()}"
    cached = cache.get(key)
    if cached is not None:
        return cached.get("feat"), cached.get("error")
    x, error = decode(Path(path))
    feat = features(x) if x is not None else None
    if x is not None and feat is None:
        error = "feature extraction returned None"
    row = {"key": key, "path": str(Path(path).resolve()), "feat": feat, "error": error}
    append_feature(cache_path, row)
    cache[key] = row
    return feat, error


def _row_identity(row: dict) -> str | None:
    artist = norm(str(row.get("artist") or ""))
    title = norm(str(row.get("title") or ""))
    return f"{artist}|{title}" if artist and title else None


def single_label_examples(rows: list[dict], rules: dict) -> dict[str, list[dict]]:
    grouped = defaultdict(list)
    for row in rows:
        parsed = classify_raw_genre(row.get("genre") or "", rules)
        if parsed["status"] != "single":
            continue
        path = Path(row["file"])
        if path.suffix.casefold() not in AUDIO_EXTENSIONS or not path.is_file():
            continue
        genre = parsed["labels"][0]
        grouped[genre].append({
            "path": str(path),
            "genre": genre,
            "artist": row.get("artist"),
            "title": row.get("title"),
            "source_type": "library",
        })
    return grouped


def split_library_examples(
    grouped: dict[str, list[dict]],
    rules: dict,
    seed: int = 42,
    fixed_holdout: set[str] | None = None,
    cap_overrides: dict[str, int] | None = None,
):
    """Split library examples into train/holdout per Genre.

    With ``fixed_holdout`` (a set of file paths), the holdout is exactly those
    paths and never re-drawn, so later runs (bigger caps, external references)
    are scored against the same held-out library tracks. Training then draws
    from the remaining pool only. ``cap_overrides`` raises/lowers the per-Genre
    training cap for named Genres without touching the others.
    """
    np = _np()
    cap = int(rules["training"]["cap_brooke_examples_per_genre"])
    cap_overrides = cap_overrides or {}
    hold_fraction = float(rules["training"]["heldout_fraction"])
    min_eval = int(rules["training"]["minimum_brooke_examples_for_stratified_eval"])
    rng = np.random.default_rng(seed)
    train, holdout, per_genre = [], [], {}
    for genre in rules["canonical_genres"]:
        available = list(grouped.get(genre, []))
        available.sort(key=lambda row: row["path"])
        genre_cap = int(cap_overrides.get(genre, cap))
        if fixed_holdout is not None:
            genre_hold = [row for row in available if row["path"] in fixed_holdout]
            pool = [row for row in available if row["path"] not in fixed_holdout]
            if len(pool) > genre_cap:
                indices = rng.choice(len(pool), genre_cap, replace=False)
                pool = [pool[int(index)] for index in sorted(indices)]
            train.extend(pool)
            holdout.extend(genre_hold)
            per_genre[genre] = {
                "available": len(available),
                "selected": len(pool) + len(genre_hold),
                "train": len(pool),
                "holdout": len(genre_hold),
                "holdout_source": "fixed",
                "cap": genre_cap,
            }
            continue
        rows = available
        if len(rows) > genre_cap:
            indices = rng.choice(len(rows), genre_cap, replace=False)
            rows = [rows[int(index)] for index in sorted(indices)]
        if len(rows) >= min_eval:
            order = rng.permutation(len(rows))
            hold_count = max(1, int(round(len(rows) * hold_fraction)))
            hold_idx = set(int(index) for index in order[:hold_count])
            genre_hold = [row for index, row in enumerate(rows) if index in hold_idx]
            genre_train = [row for index, row in enumerate(rows) if index not in hold_idx]
        else:
            genre_train, genre_hold = rows, []
        train.extend(genre_train)
        holdout.extend(genre_hold)
        per_genre[genre] = {
            "available": len(available),
            "selected": len(rows),
            "train": len(genre_train),
            "holdout": len(genre_hold),
        }
    return train, holdout, per_genre


def load_external_manifest(path: Path | None, rules: dict, holdout: list[dict]):
    if path is None or not Path(path).exists():
        return [], []
    path = Path(path)
    if path.suffix.casefold() == ".jsonl":
        rows = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
        rows = value if isinstance(value, list) else value.get("examples", [])

    canonical = set(rules["canonical_genres"])
    holdout_ids = {_row_identity(row) for row in holdout} - {None}
    accepted, rejected = [], []
    for index, row in enumerate(rows, start=1):
        reason = None
        genre = row.get("genre")
        if genre not in canonical:
            reason = "noncanonical genre"
        elif row.get("accepted") is not True:
            reason = "not explicitly accepted"
        elif not row.get("source") or not row.get("source_url"):
            reason = "missing provenance"
        elif str(row.get("label_confidence", "")).casefold() not in {"high", "reviewed"}:
            reason = "label confidence is not high/reviewed"
        elif _row_identity(row) in holdout_ids:
            reason = "artist/title overlaps library holdout"
        elif not isinstance(row.get("feat"), list):
            local = Path(str(row.get("path") or ""))
            if not local.is_file():
                reason = "no feature vector and local audio path is missing"
        if reason:
            rejected.append({"index": index, "reason": reason, "row": row})
        else:
            accepted.append({**row, "source_type": "external"})
    return accepted, rejected


def supplement_training(library_train: list[dict], external: list[dict], rules: dict):
    target = int(rules["training"]["target_examples_per_genre"])
    library_by_genre = defaultdict(list)
    external_by_genre = defaultdict(list)
    for row in library_train:
        library_by_genre[row["genre"]].append(row)
    for row in external:
        external_by_genre[row["genre"]].append(row)

    combined, report = [], {}
    for genre in rules["canonical_genres"]:
        library_rows_for_genre = library_by_genre[genre]
        external_rows_for_genre = external_by_genre[genre]
        needed = max(0, target - len(library_rows_for_genre))
        chosen_external = external_rows_for_genre[:needed]
        combined.extend(library_rows_for_genre)
        combined.extend(chosen_external)
        total = len(library_rows_for_genre) + len(chosen_external)
        report[genre] = {
            "library_train": len(library_rows_for_genre),
            "external_available": len(external_rows_for_genre),
            "external_used": len(chosen_external),
            "total_training": total,
            "target": target,
            "still_needed": max(0, target - total),
            "status": "trainable" if total >= target else "not_yet_trainable",
        }
    return combined, report


def build_external_needs(report: dict) -> list[dict]:
    return [
        {"genre": genre, "needed": row["still_needed"], "library_train": row["library_train"]}
        for genre, row in report.items() if row["still_needed"] > 0
    ]


def materialize_features(examples, cache_path: Path, cache: dict, failures: list[dict]):
    usable = []
    for index, row in enumerate(examples, start=1):
        if isinstance(row.get("feat"), list):
            feat, error = row["feat"], None
        else:
            feat, error = feature_for_path(Path(row["path"]), cache_path, cache)
        if feat is None:
            failures.append({"path": row.get("path"), "genre": row.get("genre"), "error": error})
            continue
        usable.append({**row, "feat": feat})
        if index % 25 == 0:
            print(f"features {index}/{len(examples)} failures={len(failures)}", flush=True)
    return usable


def _fit_classifier(X, y):
    np = _np()
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
    except ImportError as exc:
        raise LearnerError("genre learner training requires scikit-learn") from exc
    model = HistGradientBoostingClassifier(max_iter=400, random_state=7)
    model.fit(np.asarray(X), np.asarray(y))
    return model


def train_hierarchy(examples, trainable_genres: set[str], rules: dict):
    family_map = genre_to_family(rules)
    usable = [row for row in examples if row["genre"] in trainable_genres]
    if not usable:
        raise LearnerError("no trainable examples after sample gate")
    family_labels = [family_map[row["genre"]] for row in usable]
    families = sorted(set(family_labels))
    family_model = None
    if len(families) > 1:
        family_model = _fit_classifier([row["feat"] for row in usable], family_labels)

    family_models, family_genres = {}, {}
    for family in families:
        subset = [row for row in usable if family_map[row["genre"]] == family]
        genres = sorted(set(row["genre"] for row in subset))
        family_genres[family] = genres
        if len(genres) > 1:
            family_models[family] = _fit_classifier(
                [row["feat"] for row in subset],
                [row["genre"] for row in subset],
            )
    return {
        "family_model": family_model,
        "families": families,
        "family_models": family_models,
        "family_genres": family_genres,
    }


def predict_scores(bundle: dict, feat) -> dict[str, float]:
    family_model = bundle["family_model"]
    if family_model is None:
        family_probs = {bundle["families"][0]: 1.0}
    else:
        probabilities = family_model.predict_proba([feat])[0]
        family_probs = {
            str(label): float(probabilities[index])
            for index, label in enumerate(family_model.classes_)
        }

    scores = {}
    for family, family_probability in family_probs.items():
        subtype = bundle["family_models"].get(family)
        genres = bundle["family_genres"][family]
        if subtype is None:
            scores[genres[0]] = scores.get(genres[0], 0.0) + family_probability
            continue
        probabilities = subtype.predict_proba([feat])[0]
        for index, genre in enumerate(subtype.classes_):
            scores[str(genre)] = (
                scores.get(str(genre), 0.0)
                + family_probability * float(probabilities[index])
            )
    total = sum(scores.values()) or 1.0
    return {genre: value / total for genre, value in scores.items()}


def top_predictions(bundle: dict, feat, limit: int = 3) -> list[dict]:
    return [
        {"genre": genre, "confidence": round(score, 4)}
        for genre, score in sorted(
            predict_scores(bundle, feat).items(), key=lambda item: -item[1]
        )[:limit]
    ]


def evaluate(bundle: dict, holdout, trainable_genres: set[str], out_dir: Path) -> dict:
    try:
        from sklearn.metrics import (
            accuracy_score,
            balanced_accuracy_score,
            classification_report,
            confusion_matrix,
        )
    except ImportError as exc:
        raise LearnerError("genre learner evaluation requires scikit-learn") from exc

    rows = [
        row for row in holdout
        if row["genre"] in trainable_genres and row.get("feat") is not None
    ]
    if not rows:
        return {"status": "NO_LIBRARY_HOLDOUT_FOR_TRAINABLE_GENRES", "count": 0}
    truth = [row["genre"] for row in rows]
    predicted = [top_predictions(bundle, row["feat"], 1)[0]["genre"] for row in rows]
    labels = sorted(set(truth) | set(predicted))
    report = classification_report(
        truth, predicted, labels=labels, zero_division=0, output_dict=True
    )
    matrix = confusion_matrix(truth, predicted, labels=labels)
    with (out_dir / "confusion-matrix.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["actual\\predicted", *labels])
        for label, values in zip(labels, matrix):
            writer.writerow([label, *[int(value) for value in values]])
    return {
        "status": "PASS",
        "count": len(rows),
        "accuracy": float(accuracy_score(truth, predicted)),
        "balanced_accuracy": float(balanced_accuracy_score(truth, predicted)),
        "classification_report": report,
    }


def confidence_summary(predictions: list[dict]) -> dict:
    np = _np()
    values = np.asarray([
        row["top"][0]["confidence"] for row in predictions if row.get("top")
    ], dtype=float)
    if not len(values):
        return {"count": 0}
    return {
        "count": int(len(values)),
        "min": float(values.min()),
        "p10": float(np.percentile(values, 10)),
        "p25": float(np.percentile(values, 25)),
        "median": float(np.median(values)),
        "p75": float(np.percentile(values, 75)),
        "p90": float(np.percentile(values, 90)),
        "max": float(values.max()),
    }


def build_targets(rows: list[dict], library: Path, rules: dict) -> dict:
    """Read-only: a fresh prediction-target list from the CURRENT library.

    Targets are files with no trustworthy Genre: anything under a
    "needs check" folder, Serato rows whose Genre is blank, rows whose Genre
    holds no canonical label at all, and legacy compound rows. Folders named
    in rules["target_excluded_folders"] (the user's own productions) are
    skipped. Nothing is written except the report the caller chooses.
    """
    library_root = Path(library).resolve()
    excluded = {norm(value) for value in rules.get("target_excluded_folders", [])}

    def _excluded(path: Path) -> bool:
        try:
            parts = path.resolve().relative_to(library_root).parts
        except ValueError:
            return True
        return any(norm(part) in excluded for part in parts)

    targets: dict[str, dict] = {}

    def _add(path: Path, reason: str, raw_genre: str = "") -> None:
        if path.suffix.casefold() not in AUDIO_EXTENSIONS or not path.is_file():
            return
        if _excluded(path):
            return
        key = str(path.resolve())
        entry = targets.setdefault(key, {"file": key, "reasons": [], "raw_genre": raw_genre})
        if reason not in entry["reasons"]:
            entry["reasons"].append(reason)

    for path in sorted(library_root.rglob("*")):
        if "needs check" in str(path).casefold():
            _add(path, "needs_check_folder")
    for row in rows:
        raw = (row.get("genre") or "").strip()
        parsed = classify_raw_genre(raw, rules)
        path = Path(row["file"])
        if parsed["status"] == "blank":
            _add(path, "blank_genre", raw)
        elif parsed["status"] == "unknown":
            _add(path, "no_canonical_genre_label", raw)
        elif parsed["status"] == "legacy_compound_review":
            _add(path, "legacy_compound", raw)
    ordered = sorted(targets.values(), key=lambda entry: entry["file"])
    counts = Counter(reason for entry in ordered for reason in entry["reasons"])
    return {
        "mode": "read-only-targets",
        "library": str(library_root),
        "excluded_folders": sorted(rules.get("target_excluded_folders", [])),
        "count": len(ordered),
        "reason_counts": dict(counts),
        "targets": ordered,
    }


def load_targets_file(path: Path) -> list[str]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = value.get("targets", value) if isinstance(value, dict) else value
    files = [entry["file"] if isinstance(entry, dict) else str(entry) for entry in entries]
    return sorted(file for file in files if Path(file).is_file())


def load_prediction_targets(docs: Path) -> list[str]:
    targets = set()
    lookup = docs / "needscheck-genre-lookup.json"
    if lookup.is_file():
        value = json.loads(lookup.read_text(encoding="utf-8"))
        targets.update(row["file"] for row in value.get("misses", []) if row.get("file"))
    flac = docs / "needscheck-nogenre-flac.json"
    if flac.is_file():
        value = json.loads(flac.read_text(encoding="utf-8"))
        if isinstance(value, list):
            targets.update(str(path) for path in value)
    tier = docs / "genre-tier-cases.json"
    if tier.is_file():
        for row in json.loads(tier.read_text(encoding="utf-8")):
            if isinstance(row, list) and len(row) >= 2 and row[0] == "conflict":
                targets.add(row[1])
    return sorted(path for path in targets if Path(path).is_file())


def filter_prediction_targets_against_training(
    prediction_targets: list[str],
    training_rows: list[dict],
    library_rows_for_identity: list[dict],
) -> tuple[list[str], list[dict]]:
    """Exclude targets that overlap external examples actually used to train.

    Exact local-path overlap is always excluded. Artist/title identity is also
    excluded when the external example has provenance for the same recording,
    preventing a differently located copy/version of that identity from being
    counted as an independent prediction.
    """
    external_training = [
        row for row in training_rows if row.get("source_type") == "external"
    ]
    training_paths = {
        str(Path(str(row["path"])).resolve())
        for row in external_training if row.get("path")
    }
    training_ids = {_row_identity(row) for row in external_training} - {None}

    library_ids = {}
    for row in library_rows_for_identity:
        if not row.get("file"):
            continue
        identity = _row_identity(row)
        if identity:
            library_ids[str(Path(str(row["file"])).resolve())] = identity

    kept, excluded = [], []
    for path_text in prediction_targets:
        resolved = str(Path(path_text).resolve())
        reasons = []
        if resolved in training_paths:
            reasons.append("same_local_path_as_external_training")
        identity = library_ids.get(resolved)
        if identity and identity in training_ids:
            reasons.append("same_artist_title_as_external_training")
        if reasons:
            excluded.append({"file": path_text, "reasons": reasons})
        else:
            kept.append(path_text)
    return kept, excluded


def write_json(path: Path, value) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def command_census(args) -> int:
    rules = load_rules(args.rules)
    rows = library_rows(
        parse_serato_database(args.serato_db, args.serato_parser), args.library
    )
    census = build_census(rows, rules)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.out_dir / "genre-census.json", census)
    print(json.dumps({
        "rows": len(rows),
        "single_label": census["parse_status_counts"].get("single", 0),
        "unknown": census["parse_status_counts"].get("unknown", 0),
        "output": str(args.out_dir / "genre-census.json"),
    }, indent=2))
    return 0


def command_run(args) -> int:
    rules = load_rules(args.rules)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = args.out_dir / "genre-features.jsonl"
    cache = load_feature_cache(cache_path)

    rows = library_rows(
        parse_serato_database(args.serato_db, args.serato_parser), args.library
    )
    census = build_census(rows, rules)
    write_json(args.out_dir / "genre-census.json", census)

    grouped = single_label_examples(rows, rules)
    fixed_holdout = None
    if args.holdout_file:
        holdout_value = json.loads(Path(args.holdout_file).read_text(encoding="utf-8"))
        holdout_rows = holdout_value.get("holdout", holdout_value)
        fixed_holdout = {
            row["path"] if isinstance(row, dict) else str(row) for row in holdout_rows
        }
    cap_overrides = parse_cap_overrides(args.cap_override, rules)
    library_train, library_holdout, split_report = split_library_examples(
        grouped, rules, fixed_holdout=fixed_holdout, cap_overrides=cap_overrides
    )
    write_json(args.out_dir / "genre-holdout.json", {
        "note": "held-out library tracks used for evaluation; pass as --holdout-file to freeze",
        "source": "fixed" if fixed_holdout is not None else "seeded split",
        "count": len(library_holdout),
        "holdout": [{"path": row["path"], "genre": row["genre"]} for row in library_holdout],
    })
    external, external_rejected = load_external_manifest(
        args.external_manifest, rules, library_holdout
    )
    training_rows, sample_report = supplement_training(library_train, external, rules)
    write_json(args.out_dir / "genre-training-counts.json", {
        "split": split_report,
        "training": sample_report,
        "external_rejected": external_rejected,
    })
    write_json(
        args.out_dir / "genre-external-needs.json",
        build_external_needs(sample_report),
    )

    failures = []
    training_with_features = materialize_features(
        training_rows, cache_path, cache, failures
    )
    holdout_with_features = materialize_features(
        library_holdout, cache_path, cache, failures
    )
    write_json(args.out_dir / "genre-feature-failures.json", failures)

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
    write_json(args.out_dir / "genre-not-yet-trainable.json", unsupported)
    if not trainable:
        raise LearnerError(
            "no Genre reached the training target; add accepted external references"
        )

    eval_bundle = train_hierarchy(training_with_features, trainable, rules)
    evaluation = evaluate(
        eval_bundle, holdout_with_features, trainable, args.out_dir
    )
    write_json(args.out_dir / "genre-learner-eval.json", evaluation)

    # Only after the honest library holdout score is recorded may the final
    # prediction model use the held-out library examples too.
    final_library = library_train + library_holdout
    final_training, final_sample_report = supplement_training(
        final_library, external, rules
    )
    final_with_features = materialize_features(
        final_training, cache_path, cache, failures
    )
    final_counts = Counter(row["genre"] for row in final_with_features)
    final_trainable = {
        genre for genre in rules["canonical_genres"]
        if final_counts[genre] >= target
    }
    final_bundle = train_hierarchy(final_with_features, final_trainable, rules)

    if args.targets_file:
        requested_prediction_targets = load_targets_file(args.targets_file)
    else:
        requested_prediction_targets = load_prediction_targets(args.docs)
    prediction_targets, prediction_exclusions = filter_prediction_targets_against_training(
        requested_prediction_targets, final_with_features, rows
    )
    write_json(args.out_dir / "genre-prediction-exclusions.json", {
        "requested": len(requested_prediction_targets),
        "excluded_training_overlap": len(prediction_exclusions),
        "remaining": len(prediction_targets),
        "excluded": prediction_exclusions,
    })

    predictions = []
    for path_text in prediction_targets:
        feat, error = feature_for_path(Path(path_text), cache_path, cache)
        if feat is None:
            failures.append({"path": path_text, "genre": None, "error": error})
            continue
        predictions.append({
            "file": path_text,
            "top": top_predictions(final_bundle, feat, 3),
        })
    predictions.sort(
        key=lambda row: -(row["top"][0]["confidence"] if row["top"] else 0.0)
    )
    write_json(args.out_dir / "genre-learner-predictions.json", predictions)
    write_json(
        args.out_dir / "genre-confidence-distribution.json",
        confidence_summary(predictions),
    )
    write_json(args.out_dir / "genre-feature-failures.json", failures)
    write_json(args.out_dir / "genre-model-metadata.json", {
        "rules": str(args.rules),
        "training_target": target,
        "trainable_genres_eval": sorted(trainable),
        "trainable_genres_final": sorted(final_trainable),
        "external_manifest": str(args.external_manifest) if args.external_manifest else None,
        "holdout_file": str(args.holdout_file) if args.holdout_file else None,
        "targets_file": str(args.targets_file) if args.targets_file else None,
        "cap_overrides": cap_overrides,
        "prediction_target_count_requested": len(requested_prediction_targets),
        "prediction_target_count": len(prediction_targets),
        "prediction_targets_excluded_training_overlap": len(prediction_exclusions),
        "external_examples_accepted": len(external),
        "library_holdout_count": len(library_holdout),
        "final_training": final_sample_report,
        "taxonomy_folded_for_sample_count": False,
    })

    print(json.dumps({
        "library_rows": len(rows),
        "trainable_genres": len(trainable),
        "not_yet_trainable": len(unsupported),
        "external_examples": len(external),
        "holdout_evaluation": evaluation,
        "prediction_targets_excluded_training_overlap": len(prediction_exclusions),
        "predictions": len(predictions),
        "confidence": confidence_summary(predictions),
        "failures": len(failures),
        "out_dir": str(args.out_dir),
    }, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    root.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    root.add_argument("--serato-db", type=Path, default=DEFAULT_SERATO_DB)
    root.add_argument("--serato-parser", type=Path, default=DEFAULT_SERATO_PARSER)
    root.add_argument("--out-dir", type=Path, default=DEFAULT_DOCS / "genre-learner")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("census")
    sub.add_parser("targets", help="read-only: fresh prediction targets from the current library")
    run = sub.add_parser("run")
    run.add_argument("--docs", type=Path, default=DEFAULT_DOCS)
    run.add_argument("--external-manifest", type=Path)
    run.add_argument("--targets-file", type=Path, help="genre-targets.json from the targets command")
    run.add_argument("--holdout-file", type=Path, help="genre-holdout.json from a previous run; freezes the held-out set")
    run.add_argument("--cap-override", default="", help="per-Genre training cap overrides, e.g. 'R&B=800,Pop=800'")
    return root


def parse_cap_overrides(text: str, rules: dict) -> dict[str, int]:
    overrides: dict[str, int] = {}
    canonical = set(rules["canonical_genres"])
    for chunk in (text or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        genre, _, value = chunk.rpartition("=")
        genre = genre.strip()
        if genre not in canonical:
            raise LearnerError(f"cap override names noncanonical Genre {genre!r}")
        overrides[genre] = int(value)
    return overrides


def command_targets(args) -> int:
    rules = load_rules(args.rules)
    rows = library_rows(
        parse_serato_database(args.serato_db, args.serato_parser), args.library
    )
    report = build_targets(rows, args.library, rules)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.out_dir / "genre-targets.json", report)
    print(json.dumps({
        "targets": report["count"],
        "reason_counts": report["reason_counts"],
        "output": str(args.out_dir / "genre-targets.json"),
    }, indent=2))
    return 0


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "census":
            return command_census(args)
        if args.command == "targets":
            return command_targets(args)
        return command_run(args)
    except LearnerError as exc:
        print(
            json.dumps({"status": "REFUSED", "reason": str(exc)}, indent=2),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
