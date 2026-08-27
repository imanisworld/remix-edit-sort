# ADR-002: Import Rekordbox through its supported collection XML boundary

**Status:** Accepted
**Date:** 2026-08-27
**Deciders:** Repository maintainer and library owner

## Context

The local Rekordbox library is held in an encrypted `master.db`, while Rekordbox provides a
documented collection XML export containing track paths, metadata, playlists, position marks,
and tempo records. The DJ Library Tool needs this relationship data for eventual gig and USB
readiness checks without depending on private database internals or risking changes to
Rekordbox, audio files, or performance media.

The persistent SQLite library index already represents the current filesystem. Serato uses
separately versioned additive tables in that same database. Rekordbox evidence needs equivalent
queryability while retaining its own parser, schema lifecycle, and trust decisions.

## Decision

Accept only a user-created Rekordbox collection XML export. Never inspect or write
`master.db`. Store each successful XML import as an immutable snapshot in additive,
independently versioned `rekordbox_*` tables beside the library index.

Retain collection attributes, playlist hierarchy and references, position marks, and tempo
records. Match decoded local `file:` locations to the current library index by normalized exact
path. Only an exact path receives `file_id`. A unique filename-only candidate is
`MOVED_LIKELY`, multiple candidates are `AMBIGUOUS`, and no candidate is `MISSING`.

Fingerprint and read the XML before and after strict parsing. Reject DTD/entity declarations,
non-local locations, duplicate track IDs, inconsistent declared counts, unsupported structure,
symlink sources, and source changes. Commit one complete snapshot in a single SQLite
transaction; a failed import cannot replace prior trusted rows.

## Options considered

### Supported Rekordbox collection XML

| Dimension | Assessment |
| --- | --- |
| Complexity | Low to medium |
| Compatibility | Documented interchange boundary |
| Safety | High; source and DJ software remain read-only |
| Fidelity | Tracks, metadata, playlists, cues, and tempo records |

### Read encrypted `master.db`

| Dimension | Assessment |
| --- | --- |
| Complexity | High and version-dependent |
| Compatibility | Private implementation detail |
| Safety | Poor; encourages direct application-state coupling |
| Fidelity | Potentially high but unsupported |

### Write a Pioneer/Rekordbox USB database directly

| Dimension | Assessment |
| --- | --- |
| Complexity | High and hardware-dependent |
| Compatibility | Private device formats and changing generations |
| Safety | Unacceptable for this milestone |
| Fidelity | Not relevant until export verification is designed |

## Trade-off analysis

XML requires a manual supported export, so it cannot silently follow every Rekordbox change.
That is an acceptable cost for a stable and inspectable boundary. Snapshot history exposes
staleness explicitly and allows later comparisons without pretending filename similarity proves
identity.

Additive tables keep joins local and preserve the base library schema, but the SQLite file now
contains private playlists, paths, and DJ metadata and must remain outside Git. Cue and tempo
attributes are stored both in useful normalized columns and complete JSON attributes so a later
adapter can evolve without discarding evidence.

## Consequences

- The application can compare the current filesystem, Serato relationships, and an exported
  Rekordbox snapshot without reading any private Rekordbox database.
- A missing or outdated export remains visible; it is never papered over by an inferred link.
- The importer does not produce a Rekordbox XML, modify playlists, launch Rekordbox, or touch a
  performance device.
- A future “Prepare USB for Gig” workflow should let Rekordbox perform its supported device
  export, then verify the result read-only against this index.
- Hardware compatibility and post-export verification remain separate milestones.

## Action items

1. Export a complete collection XML from Rekordbox and import the first live snapshot.
2. Compare Serato crate membership with Rekordbox playlists in the application layer.
3. Design read-only device inventory and post-export verification for a user-selected USB.
