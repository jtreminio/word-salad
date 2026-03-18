#!/bin/bash

# Usage: ./pick_random_lines.sh input.txt 100 1000000
# Arguments: <file> <number_of_lines_to_pick> <total_lines_in_file>

FILE="$1"
COUNT="$2"
TOTAL=1657024
if [[ ! -f "$FILE" ]]; then
  echo "File not found: $FILE"
  exit 1
fi

# Create a temporary sorted list of line numbers
TMP_LINES=$(mktemp)
shuf -i 1-"$TOTAL" -n "$COUNT" | sort -n > "$TMP_LINES"

# Use awk to pull only those lines
awk -v nums="$TMP_LINES" '
  BEGIN {
    while ((getline line < nums) > 0) {
      wanted[line] = 1
    }
    close(nums)
  }
  {
    if (NR in wanted) print
  }
' "$FILE"

# Cleanup
rm -f "$TMP_LINES"
