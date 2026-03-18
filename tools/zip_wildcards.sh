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

(
  cd "$PROJECT_DIR"
  zip -r "$ZIP_PATH" "Wildcards"
)

echo "Created: $ZIP_PATH"
