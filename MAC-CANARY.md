# Mac canary gate — required before the first production apply

CI runs on Linux. The library runs on macOS/APFS. Those two filesystems
disagree about things this toolkit depends on, and **no amount of green CI
proves the production behavior**. This document is the gate that closes that
hole: a small, real run on the actual Mac, against a throwaway folder, before
any real library apply.

## What CI genuinely cannot prove

| Behavior | Linux CI (ext4/overlayfs) | macOS (APFS) |
| --- | --- | --- |
| `Track.mp3` vs `track.mp3` | two different files | **the same file** |
| case-only rename | ordinary rename | needs a two-step move via a temp name |
| `dest.exists()` for a case-only rename | `False` | **`True`** — collides with itself |
| filename Unicode form | stored as given | historically decomposed (NFD) |
| `os.path.normcase()` | identity | **identity** (only lowercases on Windows) |

That last row is a trap the repo already fell into once: the retired
`standardize_names.py` detected case-only renames with `os.path.normcase`,
which does nothing on a Mac, so the detection never fired on the only machine
it was written for. `tests/test_macos_filesystem_behavior.py` pins the correct
logic (explicit `casefold()`), and skips the host-behavior assertions when the
host is case-sensitive — which is exactly the coverage gap this canary fills.

## The canary run

Do this on the real Mac, on a **throwaway copy**, never on the live library.

```bash
# 0. A scratch folder on the SAME VOLUME as the music library, so it is the
#    same filesystem with the same case and normalization behavior.
CANARY="$HOME/Music/DJ-Music/_canary-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$CANARY/source" "$CANARY/backup"

# 1. Confirm the volume really is case-insensitive.
touch "$CANARY/source/CaseProbe.tmp"
test -e "$CANARY/source/caseprobe.tmp" \
  && echo "case-insensitive (expected for APFS)" \
  || echo "CASE-SENSITIVE VOLUME -- stop and re-check which disk this is"
rm "$CANARY/source/CaseProbe.tmp"

# 2. Generate synthetic tracks, including the awkward names.
python3 - "$CANARY/source" <<'PY'
import sys, unicodedata
sys.path.insert(0, "tests")
import fixtures
root = sys.argv[1]
fixtures.make_library(root, [
    {"relative": "bt005.mp3", "genre": "House"},
    {"relative": "Song (Clean).mp3", "genre": "Hip Hop", "serato": True},
    {"relative": "Song (Dirty).mp3", "genre": "Hip Hop", "serato": True},
    {"relative": unicodedata.normalize("NFD", "Beyoncé - Déjà Vu.mp3"), "genre": "R&b"},
    {"relative": "old-tag.mp3", "genre": "Pop", "v2_version": 2},
])
PY

# 3. Real backup + verified manifest.
cp -Rp "$CANARY/source/." "$CANARY/backup/"
python3 scripts/backup_manifest.py verify \
    --source-root "$CANARY/source" --backup "$CANARY/backup" \
    --out "$CANARY/manifest.json"
# Must print "status": "PASS".

# 4. Lock, run a small real apply against the canary folder, then undo it.
python3 scripts/folder_lock.py acquire --folder "_canary" --session "mac-canary" --agent claude
#   ... build a plan against $CANARY/source and run the appropriate
#   ... apply_*.py with --apply --backup-manifest "$CANARY/manifest.json"
#   ... then re-run with --undo <the undo log it wrote>
python3 scripts/folder_lock.py release --folder "_canary" --session "mac-canary"
```

## What must be true before you proceed to a real apply

Tick every one of these. Any failure blocks production.

- [ ] The canary volume is confirmed **case-insensitive** (step 1).
- [ ] `backup_manifest.py verify` returned **PASS** against the canary backup,
      and returned **FAIL** when you deliberately deleted one backed-up file.
- [ ] A **case-only rename** (`bt005.mp3` → `BT005.mp3`) landed with the new
      casing on disk, and did **not** get a `" (2)"` suffix appended.
- [ ] The **NFD-named file** was found, hashed, backed up and verified — the
      run did not report it as a missing source.
- [ ] `Song (Clean).mp3` and `Song (Dirty).mp3` both still exist afterwards and
      neither was treated as a duplicate or collision of the other.
- [ ] The **ID3v2.2** file was skipped rather than upgraded — check its version
      is still `(2, 2, 0)` after the run.
- [ ] **Serato GEOB frames** on the tagged files are byte-identical before and
      after (the engine verifies this; confirm the run reported zero Serato
      changes).
- [ ] The **audio payload** SHA-256 of every touched file is unchanged.
- [ ] The **recovery journal** was written, and
      `python3 scripts/mutation_engine.py recover --journal <path>` reports
      `"clean": true` after a completed run.
- [ ] The **undo** restored every file to its exact pre-apply SHA-256.
- [ ] The canary folder was deleted afterwards, and the `_canary` lock released.

Record the results — the canary output, plus which repo SHA it ran against —
in the handoff notes before starting real work. A canary run only vouches for
the commit it ran on; re-run it after any change to `mutation_engine.py`,
`backup_manifest.py`, `folder_lock.py`, or `lock_guard.py`.

## Why this is a document and not a test

It could be automated, but only on a Mac — and the machine that would run it
is the same machine holding the irreplaceable library. Making it an explicit,
human-executed checklist keeps a person in the loop immediately before the
first destructive run, which is exactly where this project's incident history
says a person should be.
