#!/usr/bin/env python3
"""
HARD-DISABLED LEGACY MUTATOR. Reports on a plan; cannot apply or undo anything.

Step 4 of the original Genre/Remixer pipeline for flat SoundCloud/DJ-pool
downloads. It moved files and wrote ID3 tags.

## Why it is disabled rather than merely deprecated

It was already flagged deprecated and gated behind `--legacy-confirm` plus a
folder lock. A safety audit found that insufficient: a `--legacy-confirm`
flag is one typed word away from a full production mutation run that still
lacks everything the approved engine provides.

Specifically, this script:

  * forced `v2_version=3` on every file it tagged, downgrading ID3v2.4 tags
    and destroying ID3v2.2 blocks;
  * had no per-file audio-payload or Serato-frame verification, so a write
    that corrupted embedded Serato cue/beatgrid data would pass unnoticed;
  * required no verified backup of any kind;
  * wrote its undo log only at the END of the run, so a crash mid-run left no
    record at all of what had already moved;
  * did not revert tags on `--undo` ("ID3 tags are NOT reverted");
  * deleted directories and `.DS_Store` files via prune_empty_dirs() as a
    side effect of a move;
  * resolved destination collisions itself by appending " (2)", silently
    renaming what may be two genuinely different recordings.

Every one of those is fixed in the approved family, which shares one engine:
apply_version_genre_plan.py, apply_grouping_plan.py, apply_artist_title_plan.py,
apply_year_bpm_plan.py. There is no case where running this instead is correct,
so the mutation code is removed rather than left behind a flag.

The read-only reporting half is kept: `--report` summarizes what a plan
contains, which is still useful for reading old plans.

Usage:
    python3 apply_plan.py --plan outputs/plan.json --report
"""

from __future__ import annotations

# Hard-disabled legacy mutator. Kept as history; cannot run.
SAFETY_CLASSIFICATION = "disabled-legacy"

import argparse
import collections
import json
import sys

# Read by scripts/mutation_surface.py: this module must have no live path to a
# filesystem or tag mutation.
MUTATION_DISABLED = True

_REPLACEMENTS = (
    "scripts/apply_version_genre_plan.py   (file relocation + Genre)\n"
    "  scripts/apply_grouping_plan.py        (Grouping)\n"
    "  scripts/apply_artist_title_plan.py    (Artist/Title)\n"
    "  scripts/apply_year_bpm_plan.py        (Year/BPM)"
)


def _refuse_to_run(flag: str) -> None:
    """Every former mutation entry point lands here. Never returns."""
    raise SystemExit(
        f"apply_plan.py {flag} is permanently disabled.\n"
        "\n"
        "This legacy engine forced ID3v2.3, verified no audio payload or Serato\n"
        "frames, required no verified backup, wrote its undo log only after the\n"
        "whole run, never reverted tags on undo, and deleted directories as a\n"
        "side effect. It cannot be made safe by a confirmation flag.\n"
        "\n"
        "Use the approved engine family instead:\n"
        f"  {_REPLACEMENTS}\n"
        "\n"
        "All of them require an owned folder lock, a PASS backup manifest, a\n"
        "write-ahead recovery journal, and per-item verification.\n"
    )


def report(plan_path: str) -> int:
    """Read-only summary of a plan file. Touches nothing."""
    with open(plan_path, encoding="utf-8") as handle:
        plan = json.load(handle)
    items = plan.get("items", [])
    buckets = collections.Counter(item.get("bucket") for item in items)
    print(json.dumps({
        "mode": "report-only",
        "plan": plan_path,
        "items": len(items),
        "buckets": dict(buckets),
        "needs_review": sum(1 for item in items if item.get("needs_review")),
        "changes_made": 0,
        "note": "apply_plan.py cannot mutate; use the approved engine family.",
    }, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--plan", default="outputs/plan.json")
    ap.add_argument("--report", action="store_true", help="read-only plan summary")
    for dead in ("--apply", "--undo", "--copy", "--tags-only", "--legacy-confirm"):
        ap.add_argument(dead, action="store_true", help="DISABLED -- refuses to run")
    ap.add_argument("--skip-review", action="store_true", help="DISABLED -- refuses to run")
    ap.add_argument("--lock-folder")
    ap.add_argument("--session")
    args = ap.parse_args()

    for flag, value in (("--apply", args.apply), ("--undo", args.undo),
                        ("--copy", args.copy), ("--tags-only", args.tags_only),
                        ("--legacy-confirm", args.legacy_confirm)):
        if value:
            _refuse_to_run(flag)

    return report(args.plan)


if __name__ == "__main__":
    sys.exit(main())
