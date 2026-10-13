# DJ Library Review — manually run on your Mac

Status: source-only draft PR #44, stacked on PR #43 → #42.
The real Mac and live library have not been tested. This does not install a
background process, an additional database, or an automatic file mover.

## What it does

Run it when you download new tracks, finish tagging, or export updated DJ
data. It creates a local HTML report plus a machine-readable JSON report
OUTSIDE music folders.

1. Downloads or another explicitly selected inbox: enumerate supported audio,
   skipping symlinks, incomplete browser downloads and Apple sidecar files.
   Read current tags, calculate whole-file SHA-256, flag missing fields.
   Default scan cap is 500 audio files. Larger folders are flagged as
   incomplete; a --max-files budget can be set on the CLI.
2. Duplicate evidence: compare exact full-file SHA-256 in the inbox even when
   filenames differ. When you select the EXISTING DJ SQLite library index,
   use its candidate hashes and rehash the current indexed source bytes
   before confirming an exact copy. Different ID3 tags or artwork can change
   file hashes even if the music sounds identical. Never auto-delete.
3. DJ source checks: reuse the existing dj_source_drift.py read-only check
   if a library index is supplied. Compare Serato source database/crates
   and the LAST EXPORTED Rekordbox XML to the trusted imports. An unchanged
   Rekordbox XML says nothing about unexported changes in the live app.
4. Performance history: fingerprint user-supplied raw exports, and optionally
   compare previously normalized play fixtures with explicitly verified DJ
   set windows. Verify local file_id, stored and current track SHA, history
   export fingerprint and time. Return CANDIDATE_ONLY or REVIEW, never
   independent proof of performance. Real vendor history row parsing has
   not yet been validated with current installed-app samples.
5. Generate concise HTML and full JSON evidence for review. Do not move,
   rename, retag, delete, reorganize, rewrite Serato, or edit Rekordbox.

## One-time Mac launch

When the PR is approved and the code is available on the Mac, open Terminal
in the repository directory and run the following one-time command:

    chmod +x "DJ Library Review.command"

Then double-click DJ Library Review.command in Finder. Alternatively:

    bash "DJ Library Review.command"

The launcher asks for an inbox (default ~/Downloads), optionally the
existing DJ library index, optional _Serato_ folder and Rekordbox XML, and
a report folder (default ~/Desktop/DJ-Library-Reports). It opens the HTML
review in your default browser.

The launcher requires the existing project Python environment with mutagen.
It does not install packages or services. Select the actual music-to-move
directory if you prefer reviewing that inbox. No private paths are guessed.

## Command-line use

Downloads-only:

    python3 scripts/dj_library_review.py \
      --incoming "$HOME/Downloads" \
      --out-dir "$HOME/Desktop/DJ-Library-Reports"

Downloads or music-to-move with existing index and app exports:

    python3 scripts/dj_library_review.py \
      --incoming "<actual incoming folder>" \
      --db "<existing DJ library-index.sqlite3>" \
      --serato-dir "<actual _Serato_ folder>" \
      --rekordbox-xml "<freshly exported Rekordbox collection.xml>" \
      --out-dir "$HOME/Desktop/DJ-Library-Reports"

History time candidates, ONLY after authentic samples have been normalized by
a separate reviewed importer:

    python3 scripts/dj_library_review.py \
      --db "<existing DJ library-index.sqlite3>" \
      --events "<private-confirmed-DJ-set-windows.json>" \
      --plays "<private-normalized-history-rows.json>" \
      --history-export "<original untouched Serato History.csv>" \
      --out-dir "$HOME/Desktop/DJ-Library-Reports"

--history-export may be repeated. Raw exports are only hashed, not parsed.
The normalized play row format comes from scripts/dj_event_time.py plus
track_sha256. The CANDIDATE_ONLY classification cannot authenticate that
the supplied row truly came from the export; upstream source parsing and
independent confirmation are required.

## Guardrails

- Report destination must be outside the selected inbox, existing Music root,
  and selected Serato data folder.
- Writes ONLY a new timestamped report directory and its own files; does
  not touch any music, app records, or the local SQLite index.
- Without a trusted index, cross-library duplicate checks are NOT CHECKED.
  Indexed duplicate claims are verified by rehashing the actual library
  file; a stale path or changed file cannot count as a confirmed duplicate.
- Unknown, unreadable or changing files remain for review.
- Existing tags are informational. They are not approved genres or evidence
  of safe physical destinations. Final genre routes remain governed by the
  76-name reconciliation PR #37/#41 and later verified real folder map.
- A file_id is local to this particular library index. Rekordbox TrackID and
  Serato paths are app-specific. A History export hash does not prove an
  individual JSON playback entry is present in that export.
- No launchd agent, periodic watcher, new index creation, uncontrolled
  folder movement, deletion, hidden retagging, or app database edits.
- Validate on small throwaway files and verified backed-up read-only app
  snapshots on the Mac before relying on the reports.

Remote completion: program, Mac launcher, tests, runbook, full CI.
Live integration waits for real exported Histories, current indexed app
snapshots, genre folder map, and a separate approved safe move transaction.
