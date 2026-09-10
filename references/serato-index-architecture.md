# ADR-001: Store read-only Serato relationships beside the library index

**Status:** Accepted
**Date:** 2026-08-27
**Deciders:** Repository maintainer and library owner

## Context

The persistent SQLite index is the trusted current map of audio files. Serato holds a second
path-based view in `database V2` and its crate files. The application needs to relate those
records without making Serato a writable dependency, guessing after moves, or invalidating an
already populated library-index database.

## Decision

Use additive, independently versioned `serato_*` tables in the existing local SQLite database.
Import Serato sources as immutable snapshots. Match by normalized exact path first. Treat a
unique filename-only candidate as `MOVED_LIKELY`, never as an authoritative relink; preserve
multiple candidates as `AMBIGUOUS` and absent candidates as `MISSING`.

Fingerprint `database V2` and every real crate before and after strict structural parsing.
Commit the entire import in one SQLite transaction only when the source set is stable. Keep
historical relationship rows independent of foreign keys to the mutable current `files` table.

## Options considered

### Additive tables in the library database

| Dimension | Assessment |
| --- | --- |
| Complexity | Low |
| Queryability | High; application joins stay local |
| Safety | High; Serato remains read-only |
| Migration risk | Low; base schema v3 remains valid |

### Separate Serato SQLite database

| Dimension | Assessment |
| --- | --- |
| Complexity | Medium; cross-database joins and lifecycle coordination |
| Queryability | Lower |
| Safety | High |
| Migration risk | Low |

### Write corrected paths back to Serato

| Dimension | Assessment |
| --- | --- |
| Complexity | High |
| Queryability | Not applicable |
| Safety | Unacceptable for this milestone |
| Migration risk | High; could damage crates or DJ metadata |

## Trade-off analysis

Co-locating relationships makes later application queries straightforward and keeps one local
backup unit. Separate versioning avoids forcing a rebuild of the trusted audio index. Historical
rows intentionally do not enforce a foreign key to current files because a later library scan
may remove or replace a current file row while old Serato evidence must remain inspectable.

Filename matching is useful operational evidence but cannot prove identity. The importer stores
the hint while withholding an authoritative relationship unless the current indexed path is an
exact normalized match.

## Consequences

- Search and future UI work can show Serato presence and crate membership without rescanning the
  music tree.
- Failed, truncated, or changing Serato inputs cannot replace the latest trusted import.
- The SQLite file contains private Serato paths and crate names and must remain outside Git.
- Rekordbox support remains a separate adapter with its own source parser and schema version.
- This importer still provides no Serato write path. The later, separately approved repair design
  is implemented by `rewrite_serato_paths.py` and documented in
  `serato-path-rewrite-architecture.md`; it does not weaken this adapter's read-only boundary.

## Action items

1. Import and validate the live Serato snapshot read-only.
2. Expose unresolved and ambiguous relationship counts in the future UI.
3. Design the Rekordbox adapter independently after its live source format is inventoried.
