# Library-wide duplicate sweep — read-only report

Scope: entire `/Users/djb.a.e/Music/DJ-Music/Music` (25,620 audio files, 196GB), except `Imani Drops` (excluded per standing out-of-scope rule).

**Nothing was deleted, moved, or modified. This is a read-only report.**

## Method

Fresh SHA-256 whole-file hash for every audio file, computed now rather than reused from earlier per-folder inventories — several folders (Hip Hop, R&b, House, Pop, oLD Head Party, Lounge, GarageJungle) had genre tags written after their original inventories were taken, which would have made cached hashes stale for this purpose.

**Caveat:** `inventory_library.py`'s own consistency check flagged the scan as `stable=False` — its before/after file listing didn't match exactly, most likely because another session moved a small number of files during the ~5 minute scan window (this library has multiple concurrent sessions writing to it). Checked: zero audio files show a content/mtime change inside the scan window, and the run completed cleanly with all 25,620 rows captured (no crash, no partial data). The hash data itself looks reliable, but flagging this transparently rather than claiming perfect certainty. A rerun would resolve any doubt if wanted.

## Headline numbers

- **122 exact-duplicate groups**, **128 redundant file instances**, **~1.06GB** total redundant space.
- **76 groups are cross-folder** (same track sitting in two different top-level folders) — this is the part nothing had checked before.
- **46 groups are same-folder** — of these, **43 are inside `Events`**, which is the already-known, deliberately-kept situation from earlier in this project (Events duplicates are intentionally retained per the project's standing rule). The other 3 are new: one each in `House`, `R&b`, and `music to move`.

## Cross-folder pattern (the new finding)

Two clusters account for the large majority of cross-folder duplicates:

1. **`edit` vs. properly-filed genre folders — 47 of 76 groups.** `edit` (House ×25, R&b ×15, alternative ×5, Club RN ×1, New Orleans Bounce ×1) has files that are byte-identical to copies already correctly sitting in their real genre folder. This strongly suggests `edit` is functioning as a drop/staging folder whose contents get copied elsewhere, leaving the originals behind rather than being cleared out.
2. **`oLD Head Party` vs. genre-specific folders — 15 of 76 groups.** (`Reggae` ×10, `Hip Hop` ×2, `Dancehall` ×1, `R&b` ×1, `Soca` ×1) — makes sense given oLD Head Party is a broad "old-school party" crate that naturally overlaps tracks already filed by genre elsewhere.

Remaining 14 groups are scattered pairs: `Pop`↔`R&b` (4), `Baile`↔`Lounge` (3), `Music`↔`midaiganes` (2), and one each of `Hip Hop`↔`R&b`, `House`↔`Lounge`, `Lounge`↔`R&b`, `Music`↔`enomiya`.

## One discrepancy worth flagging

`music to move/Music/TMHoarder/.../1-01. The Way (2).flac` and `music to move/_NEEDS CHECK/Hip-Hop/1-01. The Way.flac` — independently re-verified just now with a direct `shasum` outside the CSV pipeline: **these two files are byte-for-byte identical** (same SHA-256, same 28,444,260-byte size). This is the same track a peer session earlier reported as "confirmed NOT duplicates (different SHA-256/masters)." That claim may have been accurate for a different snapshot in time (e.g. before some earlier move/merge), but my fresh, direct check right now says they're identical. Flagging the contradiction rather than picking one claim to trust.

## What's not in this report

- No recommendation on what to delete — per the project's standing rule, duplicates are surfaced, never auto-removed. Every group and its exact paths are in `duplicate-groups.json` alongside this report for review.
- `Imani Drops` was not scanned as part of this analysis.
- Full per-file inventory (all 25,620 rows, one hash per file) is in `full-inventory/inventory.csv` if useful for other checks later.
