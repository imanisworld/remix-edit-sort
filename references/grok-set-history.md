# Grok set-history — make past sets available

Status: source-only, 2026-10-14. No music moves. No Serato/Rekordbox writes.
No parser for binary History databases.

## Goal for the set bot

Grok needs **official History exports** from Serato or rekordbox so it can see:

1. **What actually worked in a room** — tracks that were played in a real set (later joined to confirmed event/set windows).
2. **How you like to sequence** — order, transitions, and session shape across past sets.
3. **What you have already played too much** — repeat density across staged histories.

Crates, playlists, collection XML, `PlayCount`, and `LastPlayed` are **not** set history.

## What exists on the Mac today (2026-10-13/14 check)

| Source | Present? | Usable for Grok set bot? |
| --- | --- | --- |
| Serato `~/Music/_Serato_/History/Sessions/*.session` | Yes (~475) | **No** — binary; do not parse |
| Serato `history.database` | Yes | **No** — do not open |
| Serato `History Export` CSV/TXT/m3u | **Missing** | Required next step |
| rekordbox History `.txt` / `.m3u8` | **Not staged** | Required next step |
| Collection XML | May exist | Library file only — not a play log |

Until official exports are staged, readiness stays `WAITING_FOR_OFFICIAL_EXPORTS`.

## Operator export checklist (Mac)

### Serato DJ Pro

1. Open **History**.
2. Show columns: **Start Time**, **End Time**, **Playtime**, **Deck**, **Location**.
3. Pick a real past set (include a midnight-crossing set if you have one).
4. Prefer also including one **Insert Track** or **Mark as Played** row so we can see how edits look in the file.
5. Export **CSV** and **TXT** (and m3u if offered) into Serato's **History Export** folder, or into a private folder such as:
   `~/Documents/DJ-library-project-20260825/history-exports/inbox/`
6. Do **not** commit the files. Do **not** hand Grok the `.session` files.

Official Serato History docs: https://support.serato.com/hc/en-us/articles/223455687-History

### rekordbox

1. Open **Histories** (PERFORMANCE / LINK history — not the collection).
2. Copy or export one History to a playlist if needed, then export **`.txt`** and **`.m3u8`**.
3. Drop those files in the same private inbox folder.
4. Do **not** treat collection XML as the play log.

## Stage for Grok (read-only)

Private staging directory (outside Music, outside Git):

```bash
STAGE="$HOME/Documents/DJ-library-project-20260825/history-exports-for-grok"
mkdir -p "$STAGE" \
  "$HOME/Documents/DJ-library-project-20260825/history-exports/inbox"
```

Inventory what the Mac already has:

```bash
python3 scripts/dj_history_export_stage.py inventory \
  --search "$HOME/Documents/DJ-library-project-20260825/history-exports/inbox" \
  --json-out "$STAGE/last-inventory.json"
```

After you export CSV/TXT/m3u8 into the inbox (or Serato History Export):

```bash
python3 scripts/dj_history_export_stage.py stage \
  --staging-dir "$STAGE" \
  --search "$HOME/Documents/DJ-library-project-20260825/history-exports/inbox"
```

That writes:

- `$STAGE/GROK-SET-HISTORY.md` — Grok's entry brief
- `$STAGE/manifest.json` — machine-readable inventory + staged hashes
- `$STAGE/exports/*` — copies of official export files only

## What Grok may do with a staged pack

- Read `GROK-SET-HISTORY.md` and `manifest.json` first.
- Inspect only files under `exports/`.
- Summarize sequencing and repeat plays from those official exports.
- Flag titles that appear too often across staged sessions.

## What Grok must not do

- Open `.session`, `history.database`, `master.sqlite`, or diagnostic logs.
- Invent plays from event start times, crates, or PlayCount.
- Modify music, Serato, or rekordbox.
- Commit exports into Git.

## Related remote work (unmerged)

- PR #42 — read-only Serato/Rekordbox source drift
- PR #43 — DST-safe set-window time preview (synthetic)
- PR #44 — Mac DJ Library Review launcher (can fingerprint history exports)
- PR #46 — exact-hash event-play candidate join (synthetic; no real History parser yet)

Real vendor row parsing still waits for these staged samples. Staging is the unblocker for the set bot.
