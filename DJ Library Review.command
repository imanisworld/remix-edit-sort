#!/bin/bash
# DJ Library Review - manual launch. Writes reports only; never moves music.
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1

expand_home() {
  case "$1" in
    "~/"*) printf "%s/%s" "$HOME" "$(printf "%s" "$1" | cut -c 3-)" ;;
    *) printf "%s" "$1" ;;
  esac
}

echo ""
echo "DJ LIBRARY REVIEW"
echo "Scan Downloads / music-to-move and optionally check your existing DJ index."
echo "No music will be moved, retagged, deleted or organized automatically."
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Open this repo's Mac setup instructions."
  read -r -p "Press Return to close..." _
  exit 1
fi
if ! python3 -c "import mutagen" >/dev/null 2>&1; then
  echo "The project's Python dependency 'mutagen' is not installed for this Python."
  echo "Use the project's existing Python environment or install mutagen separately."
  echo "No packages were installed by this launcher."
  read -r -p "Press Return to close..." _
  exit 1
fi

read -r -p "Folder to scan [~/Downloads]: " INCOMING
if [[ -z "$INCOMING" ]]; then INCOMING="$HOME/Downloads"; fi
INCOMING="$(expand_home "$INCOMING")"

INDEX_HINT="$HOME/Library/Application Support/DJ Library Tool/library.sqlite3"
if [[ -f "$INDEX_HINT" ]]; then
  read -r -p "Existing DJ index [$INDEX_HINT]: " INDEX_PATH
  if [[ -z "$INDEX_PATH" ]]; then INDEX_PATH="$INDEX_HINT"; fi
else
  read -r -p "Existing DJ library-index SQLite path [blank to skip]: " INDEX_PATH
fi
INDEX_PATH="$(expand_home "$INDEX_PATH")"

RB_XML=""
SERATO_DIR=""
if [[ -n "$INDEX_PATH" ]]; then
  read -r -p "Fresh Rekordbox collection XML [blank to use last indexed source]: " RB_XML
  RB_XML="$(expand_home "$RB_XML")"
  read -r -p "Serato _Serato_ directory [blank to use last indexed source]: " SERATO_DIR
  SERATO_DIR="$(expand_home "$SERATO_DIR")"
else
  echo "No index supplied: DJ snapshot drift and cross-library duplicate checks will be skipped."
fi

REPORTS="$HOME/Desktop/DJ-Library-Reports"
read -r -p "Report location [$REPORTS]: " CUSTOM_REPORTS
if [[ -n "$CUSTOM_REPORTS" ]]; then REPORTS="$CUSTOM_REPORTS"; fi
REPORTS="$(expand_home "$REPORTS")"

set -- --incoming "$INCOMING" --out-dir "$REPORTS"
if [[ -n "$INDEX_PATH" ]]; then set -- "$@" --db "$INDEX_PATH"; fi
if [[ -n "$RB_XML" ]]; then set -- "$@" --rekordbox-xml "$RB_XML"; fi
if [[ -n "$SERATO_DIR" ]]; then set -- "$@" --serato-dir "$SERATO_DIR"; fi

read -r -p "Include original DJ History export(s) in this review? [y/N]: " INCLUDE_HISTORY
if [[ "$INCLUDE_HISTORY" == "y" || "$INCLUDE_HISTORY" == "Y" ]]; then
  echo "Original exports will be hashed only; no unverified rows are interpreted."
  for COUNT in 1 2 3 4 5; do
    read -r -p "History export file $COUNT [blank to finish]: " HIST_PATH
    if [[ -z "$HIST_PATH" ]]; then break; fi
    HIST_PATH="$(expand_home "$HIST_PATH")"
    set -- "$@" --history-export "$HIST_PATH"
  done
  if [[ -n "$INDEX_PATH" ]]; then
    read -r -p "Reviewed actual DJ set windows JSON [blank to skip]: " SETS_JSON
    if [[ -n "$SETS_JSON" ]]; then
      read -r -p "Normalized playback fixture JSON: " PLAYS_JSON
      if [[ -n "$PLAYS_JSON" ]]; then
        set -- "$@" --events "$(expand_home "$SETS_JSON")" --plays "$(expand_home "$PLAYS_JSON")"
      else
        echo "Normalized playback input absent: set-time matching skipped."
      fi
    fi
  fi
fi

echo ""
python3 "$ROOT/scripts/dj_library_review.py" "$@"
RC=$?
if [[ "$RC" -eq 0 ]]; then
  LATEST=$(find "$REPORTS" -maxdepth 2 -type f -name review.html -print 2>/dev/null | sort | tail -n 1)
  if [[ -n "$LATEST" ]]; then
    echo "Opening the read-only HTML report."
    open "$LATEST"
  fi
else
  echo "Nothing in the library was changed. See the error above."
fi
echo ""
read -r -p "Press Return to close..." _
exit "$RC"
