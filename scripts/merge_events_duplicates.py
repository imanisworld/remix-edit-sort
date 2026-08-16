#!/usr/bin/env python3
"""
HARD-DISABLED one-off. Historical record of a completed 9-track Events merge.

This moved 9 duplicate tracks out of two junk-named Events folders ("August
30th Random DL" and "trendindg august 2025") into the real Hip Hop / R&b genre
folders. Every pair had been independently verified first: audio-payload
SHA-256 and Serato GEOB frame set/sizes matched exactly between both copies,
so which physical copy moved was arbitrary. The leftover copy in the other
junk folder was intentionally NOT deleted -- only the move was approved.

That run is done. This file is kept as evidence of what was moved where.

## Why it is disabled

A safety audit found three problems that make re-running it unsafe, quite
apart from the fact that re-running a completed one-off is meaningless:

  * it hardcoded `lock_guard.require_lock_or_exit(folder, "claude-metadata-pass")`
    -- someone else's session name. Any operator running this would either
    fail outright or, worse, satisfy the gate using a lock they do not own,
    which defeats the entire purpose of the lock;
  * it had no per-item heartbeat, so a long run could keep moving files after
    another session had reclaimed Events, Hip Hop or R&b;
  * it took its own ad-hoc backup copy inline instead of requiring a verified
    backup manifest, and wrote its recovery record only AFTER each rename, so
    a crash between the rename and the journal write left an untracked move.

Equivalent work now goes through `apply_version_genre_plan.py` (mutation class
`relocate`), which runs on the shared engine: owned lock, PASS backup manifest,
write-ahead intent journal, per-item verification, real undo.
"""

from __future__ import annotations

# Hard-disabled legacy mutator. Kept as history; cannot run.
SAFETY_CLASSIFICATION = "disabled-legacy"

import json
import sys

# Read by scripts/mutation_surface.py: no live mutation path may exist here.
MUTATION_DISABLED = True

# Kept verbatim as the historical record of what this run moved.
PAIRS = [
    ("Events/trendindg august 2025/CLEAN/Hip-Hop/Metro Boomin, Roscoe Dash & Quavo - Butterflies (Right Now) (Clean).mp3",
     "Hip Hop/Clean/Hip-Hop/Metro Boomin, Roscoe Dash & Quavo - Butterflies (Right Now) (Clean).mp3"),
    ("Events/trendindg august 2025/DIRTY/Hip-Hop/Metro Boomin, Roscoe Dash & Quavo - Butterflies (Right Now) (Intro Dirty).mp3",
     "Hip Hop/DIRTY/Hip-Hop/Metro Boomin, Roscoe Dash & Quavo - Butterflies (Right Now) (Intro Dirty).mp3"),
    ("Events/trendindg august 2025/DIRTY/Hip-Hop/Saweetie - pressure (Intro Dirty).mp3",
     "Hip Hop/DIRTY/Hip-Hop/Saweetie - pressure (Intro Dirty).mp3"),
    ("Events/trendindg august 2025/CLEAN/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Clean).mp3",
     "R&b/CLEAN/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Clean).mp3"),
    ("Events/trendindg august 2025/CLEAN/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Intro Clean).mp3",
     "R&b/CLEAN/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Intro Clean).mp3"),
    ("Events/trendindg august 2025/DIRTY/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Dirty).mp3",
     "R&b/DIRTY/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Dirty).mp3"),
    ("Events/trendindg august 2025/DIRTY/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Intro Dirty).mp3",
     "R&b/DIRTY/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Intro Dirty).mp3"),
    ("Events/trendindg august 2025/INSTRUMENTALS/R&B/INSTRUMENTALS/Al Green - Love And Happiness (Instrumental).mp3",
     "R&b/INSTRUMENTALS/R&B/Al Green - Love And Happiness (Instrumental).mp3"),
    ("Events/trendindg august 2025/INSTRUMENTALS/R&B/INSTRUMENTALS/Ciara ft Latto & Jazze Pha - This Right Here (Instrumental).mp3",
     "R&b/INSTRUMENTALS/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Instrumental).mp3"),
]

LEFTOVER_DUPLICATES = [
    "Events/August 30th Random DL/CLEAN/Hip-Hop/Metro Boomin, Roscoe Dash & Quavo - Butterflies (Right Now) (Clean).mp3",
    "Events/August 30th Random DL/DIRTY/Hip-Hop/Metro Boomin, Roscoe Dash & Quavo - Butterflies (Right Now) (Intro Dirty).mp3",
    "Events/August 30th Random DL/DIRTY/Hip-Hop/Saweetie - pressure (Intro Dirty).mp3",
    "Events/August 30th Random DL/CLEAN/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Clean).mp3",
    "Events/August 30th Random DL/CLEAN/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Intro Clean).mp3",
    "Events/August 30th Random DL/DIRTY/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Dirty).mp3",
    "Events/August 30th Random DL/DIRTY/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Intro Dirty).mp3",
    "Events/August 30th Random DL/DJ TOOLS/R&B/INSTRUMENTALS/Al Green - Love And Happiness (Instrumental).mp3",
    "Events/August 30th Random DL/DJ TOOLS/R&B/INSTRUMENTALS/Ciara ft Latto & Jazze Pha - This Right Here (Instrumental).mp3",
]


def _refuse_to_run() -> None:
    raise SystemExit(
        "merge_events_duplicates.py is a completed one-off and is permanently disabled.\n"
        "\n"
        "It hardcoded another session's lock name ('claude-metadata-pass'), had no\n"
        "per-item heartbeat, and journaled each move only after performing it.\n"
        "\n"
        "The 9 moves it describes were applied on 2026-08-15; see\n"
        "handoff/session-2026-08-15/survey/events-dedup-merge/ for the result and\n"
        "undo log. This file remains only as evidence of what moved where.\n"
        "\n"
        "For equivalent work use scripts/apply_version_genre_plan.py, which runs on\n"
        "the shared mutation engine (lock + PASS backup manifest + write-ahead\n"
        "journal + per-item verification).\n"
    )


def main() -> None:
    _refuse_to_run()


def record() -> dict:
    """Read-only accessor for the historical record."""
    return {
        "status": "completed 2026-08-15, script disabled",
        "moves": [{"source": src, "destination": dest} for src, dest in PAIRS],
        "leftover_duplicates_not_deleted": LEFTOVER_DUPLICATES,
    }


if __name__ == "__main__":
    if "--record" in sys.argv:
        print(json.dumps(record(), indent=2, ensure_ascii=False))
        sys.exit(0)
    main()
