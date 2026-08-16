# `handoff/` is historical evidence. It is never production executable code.

Everything under `handoff/` is a record of what past runs did: plans,
inventories, previews, result JSON, undo manifests, completion reports, and
the one-off scripts that were written for a single folder and then finished.

**No file here may be run.** All 22 Python files that used to live here are
now stored as `*.py.archived`. Their contents are unchanged and fully
readable — `git log --follow` still traces each one's history — but they can
no longer be imported or executed by `python3 <path>`, and a repo-wide check
(`scripts/mutation_surface.py`, enforced in CI) fails if any `*.py` file
reappears under `handoff/`.

## Why

Most of these files are `apply_*.py` mutators. They were written before the
repository had a shared mutation engine, a mandatory folder lock, a verified
backup manifest, or a write-ahead recovery journal. Each one carries its own
hand-rolled, now-divergent copy of the safety logic, plus hardcoded absolute
paths into one specific person's music library and hardcoded session names.

They succeeded historically. That is not evidence that they are safe to run
again:

* they hardcode paths and expected file counts from a library state that no
  longer exists, so re-running one now would operate on the wrong files;
* they predate the lock enforcement, so they can run concurrently with
  another agent session mid-apply;
* several force `v2_version=3`, downgrading ID3v2.4 and destroying ID3v2.2
  blocks;
* none of them require a verified backup manifest;
* none of them write recovery intent before mutating, so a crash leaves no
  record of the in-flight item.

An agent scanning this repo for "how did the last session apply a plan?" must
find evidence, not a runnable shortcut. Archiving is what makes that
distinction structural instead of a comment nobody reads.

## What to use instead

| Task | Approved tool |
| --- | --- |
| File relocation / rename + Genre | `scripts/apply_version_genre_plan.py` |
| Grouping backfill | `scripts/apply_grouping_plan.py` |
| Artist / Title backfill | `scripts/apply_artist_title_plan.py` |
| Year / BPM backfill | `scripts/apply_year_bpm_plan.py` |
| Genre refinement | `scripts/apply_genre_refinement_plan.py` |
| Needs-check genre recheck | `scripts/apply_needs_check_recheck_genre_plan.py` |
| Deleting a verified exact duplicate | `scripts/delete_verified_duplicates.py` (manual, SHA-256 only) |

All of them route every write through `scripts/mutation_engine.py` and refuse
to run without an owned folder lock and a PASS backup manifest.

## Reading an archived file

    less handoff/session-2026-08-15/survey/dub/apply_dub_plan.py.archived

If you genuinely need logic from one of these, port it into `scripts/` as an
engine user, generalize the hardcoded paths, and add tests. Do not restore the
`.py` extension.
