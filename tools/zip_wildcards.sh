#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORD_SALAD_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(cd "$WORD_SALAD_DIR/.." && pwd)"
WILDCARDS_DIR="$PROJECT_DIR/Wildcards"
RELEASES_DIR="$WORD_SALAD_DIR/releases"
TIMESTAMP="$(date +"%Y%m%d-%H%M%S")"
ZIP_NAME="wildcards-$TIMESTAMP.zip"
ZIP_PATH="$RELEASES_DIR/$ZIP_NAME"

if [[ ! -d "$WILDCARDS_DIR" ]]; then
  echo "Wildcards directory not found: $WILDCARDS_DIR"
  exit 1
fi

mkdir -p "$RELEASES_DIR"
for old_zip in "$RELEASES_DIR"/wildcards-*.zip; do
  [[ -e "$old_zip" ]] || continue
  [[ "$(basename "$old_zip")" == "Datadump.zip" ]] && continue
  rm -f "$old_zip"
done

(
  cd "$PROJECT_DIR"
  zip -r "$ZIP_PATH" "Wildcards"
)

echo "Created: $ZIP_PATH"
