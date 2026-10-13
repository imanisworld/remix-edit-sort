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

read -r -p "Existing DJ library-index SQLite path [blank to skip]: " INDEX_PATH
INDEX_PATH="$(expand_home "$INDEX_PATH")"

read -r -p "Fresh Rekordbox collection XML [blank to skip]: " RB_XML
RB_XML="$(expand_home "$RB_XML")"

read -r -p "Serato _Serato_ directory [blank to skip]: " SERATO_DIR
SERATO_DIR="$(expand_home "$SERATO_DIR")"

REPORTS="$HOME/Desktop/DJ-Library-Reports"
read -r -p "Report location [$REPORTS]: " CUSTOM_REPORTS
if [[ -n "$CUSTOM_REPORTS" ]]; then REPORTS="$CUSTOM_REPORTS"; fi
REPORTS="$(expand_home "$REPORTS")"

set -- --incoming "$INCOMING" --out-dir "$REPORTS"
if [[ -n "$INDEX_PATH" ]]; then set -- "$@" --db "$INDEX_PATH"; fi
if [[ -n "$RB_XML" ]]; then set -- "$@" --rekordbox-xml "$RB_XML"; fi
if [[ -n "$SERATO_DIR" ]]; then set -- "$@" --serato-dir "$SERATO_DIR"; fi

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
