# Artist identification proposals

`scripts/build_artist_identification_plan.py` audits blank Artist or Title
values and produces review artifacts. It is preview-only: it does not write
tags, rename or move files, or touch Serato.

## Workflow

```bash
python3 scripts/inventory_library.py \
  --root "<import folder>" \
  --out-dir "<work>/inventory"

python3 scripts/build_artist_identification_plan.py \
  --inventory "<work>/inventory/inventory.csv" \
  --out-dir "<work>/artist-identification"
```

Optional local Chromaprint corroboration:

```bash
python3 scripts/build_artist_identification_plan.py \
  --inventory "<work>/inventory/inventory.csv" \
  --out-dir "<work>/artist-identification" \
  --chromaprint
```

`--chromaprint` requires `fpcalc` at runtime. It is not installed or used by
CI. A missing binary or any failed probe is recorded as `FAILED`; all matches
from that incomplete run are discarded because a missing reference could hide
a conflict. Failure never becomes `NO_MATCH` and never increases confidence.
The tool sends nothing over the network.

Outputs:

- `artist-identification-plan.json` — complete machine-readable review data;
- `artist-identification-review.csv` — in-scope blank Artist/Title rows;
- `artist-identification-outside-scope.csv` — complete rows, unsupported
  eventual-apply formats, and excluded paths such as `Imani Drops`;
- `artist-identification-summary.json` — counts and optional-tool status;
- `ARTIST-IDENTIFICATION-PREVIEW.md` — short review summary.

## Evidence and status rules

- A strict, single `Artist - Title` filename split creates a candidate only.
  A punctuated leading track number (`01.`, `02_`, or `03-`) and terminal copy
  suffix are removed; a space-only numeric prefix is preserved so artists such
  as `21 Savage`, `112`, and `2 Chainz` are not damaged. Ambiguous multi-dash
  names stay on hold.
- An existing Artist or Title may anchor the missing field only when it agrees
  exactly after Unicode/case/whitespace normalization.
- An exact full-file SHA-256 group is reported, but cannot independently fill
  a blank embedded tag: identical bytes necessarily contain identical embedded
  tags. Different tag states under one hash are an inconsistent inventory and
  become `CONFLICT`.
- Optional Chromaprint may corroborate a candidate against a fully tagged row
  when similarity is at least 0.85 and duration differs by no more than three
  seconds. This is identification evidence, not duplicate proof, version
  proof, or permission to write.

Statuses:

- `CORROBORATED_FOR_REVIEW` — independent local audio evidence agrees; still
  requires human review.
- `NEEDS_CORROBORATION` — a filename candidate exists, but filename evidence
  alone is never approved.
- `HOLD_NO_CANDIDATE` — Artist and Title cannot both be proposed safely.
- `CONFLICT` — evidence disagrees or the inventory is internally impossible.

Confidence is explicit (`MEDIUM`, `LOW`, or `NONE`). No output row is approved
automatically.

## Why public metadata lookup is not automatic here

Free-text MusicBrainz search starts from the same filename candidate, so a
text match is not independent identity evidence. AcoustID provides a better
fingerprint-to-recording bridge but requires a registered application client
key. This PR therefore keeps the no-secret path local and optional instead of
fabricating confidence from a text search or making CI depend on a provider.
See `references/free-open-validation-roadmap.md` for the future HTTPS,
provider-state, cache, and credential requirements.

## Eventual approved apply

The proposal JSON uses mode `artist-identification-review` and is deliberately
rejected by `apply_artist_title_plan.py`. This PR adds no apply or promotion
command.

After a human reviews the CSV, a later, separately reviewed promotion step may
copy only accepted rows into the existing Artist/Title preview schema:

```json
{
  "mode": "preview-only",
  "rules": {"approval_required": true},
  "items": [{
    "path": "/absolute/path/to/file.mp3",
    "sha256": "<inventory SHA-256>",
    "current_artist": "",
    "current_title": "",
    "proposed_artist": "Reviewed Artist",
    "proposed_title": "Reviewed Title"
  }]
}
```

That eventual plan still receives no special authority. Applying it must use
the existing `apply_artist_title_plan.py` flow: fresh PASS backup manifest,
owned folder lock, per-item heartbeat, mutation-engine write-ahead journal,
hash pinning, audio/Serato verification, rollback, and undo log. Explicit user
approval remains mandatory before any write.
