#!/usr/bin/env python3

import argparse
import re
import sys
from pathlib import Path


NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def normalize_line(line: str) -> str:
    return NORMALIZE_RE.sub("", line.lower())


def process_file(path: Path) -> tuple[int, int, int]:
    original_lines = path.read_text(encoding="utf-8").splitlines()

    kept_lines: list[str] = []
    seen_normalized: set[str] = set()
    removed_comments = 0
    removed_duplicates = 0

    for line in original_lines:
        if line.startswith("#"):
            removed_comments += 1
            continue

        normalized = normalize_line(line)
        if normalized in seen_normalized:
            removed_duplicates += 1
            continue

        seen_normalized.add(normalized)
        kept_lines.append(line)

    sorted_lines = sorted(kept_lines, key=lambda line: line.lower())
    path.write_text("\n".join(sorted_lines) + ("\n" if sorted_lines else ""), encoding="utf-8")

    return len(original_lines), removed_comments + removed_duplicates, len(sorted_lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sort a text file in place, remove comment lines, and keep normalized unique lines."
    )
    parser.add_argument("file", type=Path, help="Path to the .txt file to process")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = args.file

    if path.suffix.lower() != ".txt":
        print(f"Error: expected a .txt file, got {path}", file=sys.stderr)
        return 1

    if not path.is_file():
        print(f"Error: file not found: {path}", file=sys.stderr)
        return 1

    total_lines, removed_lines, kept_lines = process_file(path)

    print(f"Sorted file: {path}")
    print(f"Lines read: {total_lines}")
    print(f"Duplicates/comments removed: {removed_lines}")
    print(f"Lines kept: {kept_lines}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
