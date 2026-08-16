# `_NEEDS CHECK` preview tools

These tools are deliberately read-only. They produce evidence reports only; they do not move, rename, delete, retag, or rewrite audio.

## 1. Build or reuse an inventory

```bash
python3 scripts/inventory_library.py \
  --root "/path/to/folder" \
  --out-dir output/needs-check-inventory
```

## 2. Genre checker

Reuse the existing trusted genre classifier and `references/version-genre-rules.json`:

```bash
python3 scripts/check_genre_evidence.py \
  --inventory output/needs-check-inventory/inventory.csv \
  --out-dir output/genre-check \
  --only-needs-check
```

Outputs:

- `genre-evidence.csv`
- `genre-evidence-summary.json`
- `GENRE-EVIDENCE-PREVIEW.md`

`HIGH_CONFIDENCE_PROPOSAL` means an existing filename keyword or tag alias resolved through the same classifier used by the version+genre pipeline. Blank, compound, ambiguous, and unmapped tags remain `REVIEW`. This checker never adds aliases or new genre heuristics.

## 3. Clean/Dirty checker from existing labels

```bash
python3 scripts/check_version_evidence.py \
  --inventory output/needs-check-inventory/inventory.csv \
  --out-dir output/version-check \
  --only-needs-check
```

Explicit bracketed/structured Clean and Dirty labels reuse `classify_version()` from `build_version_genre_plan.py`.

Important rule: a track with no explicit Clean/Dirty evidence remains `NEEDS_LISTEN`.

## 4. Optional local audio evidence

The optional transcription helper requires `faster-whisper` and a **user-reviewed local terms file**. The repository does not define an explicit-language lexicon for you.

```bash
python3 -m pip install faster-whisper
python3 scripts/transcribe_explicit_evidence.py \
  --inventory output/needs-check-inventory/inventory.csv \
  --terms-file /path/to/local-explicit-terms.txt \
  --out output/version-check/transcript-evidence.json \
  --only-needs-check
```

Then join that evidence into the version checker:

```bash
python3 scripts/check_version_evidence.py \
  --inventory output/needs-check-inventory/inventory.csv \
  --transcript-evidence output/version-check/transcript-evidence.json \
  --out-dir output/version-check/combined \
  --only-needs-check
```

### Audio-evidence semantics

- explicit Clean label -> `CONFIRMED_CLEAN`, unless transcript evidence conflicts;
- explicit Dirty/Explicit Edit label -> `CONFIRMED_DIRTY`;
- no label + detected reviewed explicit-language term -> `LIKELY_DIRTY` only;
- no detected term -> `NEEDS_LISTEN`, **never automatically Clean**;
- explicit Clean label + detected explicit-language evidence -> `CONFLICT_CLEAN_LABEL_AUDIO_EXPLICIT`.

`LIKELY_DIRTY` and conflicts require human review. The checker leaves `proposed_clean_dirty` blank for those statuses.

By default raw transcript text is not written to disk. `--include-transcript` is opt-in.

## Safety

These tools do not require a mutation lock because they are preview-only. Any later application of approved labels must go through the repository's normal lock, backup, apply, verification, and undo controls.
