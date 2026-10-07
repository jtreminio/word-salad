#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORD_SALAD_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# The CLI builds and validates a temporary stage, then creates a new archive.
# Existing releases are retained; the live Wildcards tree is never zipped.
exec python3 "$WORD_SALAD_DIR/main.py" release "$@"
