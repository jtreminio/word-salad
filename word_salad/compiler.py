"""Compile wildcard source files without changing source or output directories.

``<file:folder/card>`` expands a source file at build time. Paths are relative
to the data root; ``./`` and ``../`` explicitly select the containing source's
directory. The old ``$file:[_data/folder/card]`` spelling remains supported.
One include per logical line is supported, with arbitrary prefix and suffix
text. Escape an include with a backslash to emit it literally. Other tags,
including SwarmUI's runtime tags, are opaque text.

Normalization intentionally matches the original TXT builder: join physical
lines ending in a backslash, strip and deduplicate logical source lines before
expansion, and collapse doubled backslashes only when producing final output.
Expanded duplicates are retained because they can affect wildcard weighting.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path, PurePosixPath
import re
import unicodedata


class CompileError(ValueError):
    """Source data cannot be compiled safely or unambiguously."""


@dataclass(frozen=True)
class Card:
    """One public output and its provenance.

    ``dependencies`` includes the source itself and every transitive include.
    ``direct`` means the source bytes exactly match generated output, allowing
    the synchronizer to distinguish lossless imports from transformed content.
    """

    source: str
    content: bytes
    dependencies: tuple[str, ...]
    derived: bool
    direct: bool


@dataclass(frozen=True)
class _Include:
    path: str
    legacy: bool


@dataclass(frozen=True)
class _Expanded:
    lines: tuple[str, ...]
    dependencies: tuple[str, ...]
    derived: bool


_INCLUDE_START = re.compile(r"<file:|\$file:\[")
_UNSAFE_PATH = re.compile(r'[\x00-\x1f\x7f\\:*?<>|\[\]{}]')


def _check_literal_path(value: str, context: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CompileError(f"{context}: expected a nonempty literal path without outer whitespace")
    if _UNSAFE_PATH.search(value) or value.startswith("/"):
        raise CompileError(f"{context}: unsafe or dynamic path {value!r}; use a literal relative path")
    if "//" in value or value.endswith("/"):
        raise CompileError(f"{context}: invalid path {value!r}")


def _check_public_path(value: str, context: str) -> str:
    _check_literal_path(value, context)
    if any(part in (".", "..") for part in value.split("/")):
        raise CompileError(f"{context}: path traversal is not allowed: {value!r}")
    if not value.endswith(".txt"):
        raise CompileError(f"{context}: output and source names must end in .txt: {value!r}")
    return value


def _logical_lines(raw: bytes, source: str) -> list[tuple[int, str]]:
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CompileError(f"{source}: source is not valid UTF-8: {exc}") from exc

    # TextIO's universal newlines reproduce the original Path.open('r') behavior
    # without treating other Unicode line separators as physical line breaks.
    result: list[tuple[int, str]] = []
    seen: set[str] = set()
    buffer = ""
    start = 1

    def append(number: int, value: str) -> None:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append((number, normalized))

    for number, raw_line in enumerate(io.StringIO(decoded, newline=None), 1):
        line = raw_line.rstrip("\n")
        if not buffer:
            start = number
        if line.endswith("\\"):
            buffer += line[:-1]
        else:
            append(start, buffer + line)
            buffer = ""
    if buffer:
        append(start, buffer)
    return result


def _parse_line(line: str, context: str) -> list[str | _Include]:
    parts: list[str | _Include] = []
    position = 0
    count = 0
    while match := _INCLUDE_START.search(line, position):
        prefix = line[position : match.start()]
        slash_position = match.start()
        while slash_position and line[slash_position - 1] == "\\":
            slash_position -= 1
        escaped = (match.start() - slash_position) % 2 == 1
        legacy = match.group() == "$file:["
        closing = "]" if legacy else ">"
        end = line.find(closing, match.end())
        if escaped:
            # A literal token does not need a valid path or even a terminator.
            parts.append(prefix[:-1])
            if end == -1:
                parts.append(line[match.start() :])
                return parts
            parts.append(line[match.start() : end + 1])
        else:
            if end == -1:
                raise CompileError(f"{context}: unterminated {match.group()} include")
            count += 1
            if count > 1:
                raise CompileError(f"{context}: only one file include per logical line is supported")
            parts.append(prefix)
            parts.append(_Include(line[match.end() : end], legacy))
        position = end + 1
    parts.append(line[position:])
    return parts


def _reference_name(include: _Include, source: str, context: str) -> str:
    value = include.path
    _check_literal_path(value, context)
    if include.legacy and value.startswith("_data/"):
        value = value[len("_data/") :]
    if not value.endswith(".txt"):
        value += ".txt"

    relative = value.startswith("./") or value.startswith("../")
    segments = list(PurePosixPath(source).parent.parts) if relative else []
    for part in value.split("/"):
        if part == ".":
            if not relative:
                raise CompileError(f"{context}: use ./ to make an include relative to its source")
        elif part == "..":
            if not relative or not segments:
                raise CompileError(f"{context}: include escapes the data root: {include.path!r}")
            segments.pop()
        else:
            segments.append(part)
    name = "/".join(segments)
    return _check_public_path(name, context)


def _output_aliases(
    sources: dict[str, bytes], output_map: dict[str, list[str]] | None
) -> dict[str, tuple[str, ...]]:
    mapping = {} if output_map is None else output_map
    if not isinstance(mapping, dict):
        raise CompileError("output_map must map source paths to lists of public .txt paths")
    for source in mapping:
        _check_public_path(source, "output_map source")
        if source not in sources:
            raise CompileError(f"output_map refers to missing source {source!r}")

    aliases: dict[str, tuple[str, ...]] = {}
    occupied: dict[str, tuple[str, str]] = {}
    for source in sources:
        names = mapping.get(source, [source])
        if not isinstance(names, list):
            raise CompileError(f"{source}: output_map value must be a list of public paths")
        aliases[source] = tuple(names)
        for name in names:
            _check_public_path(name, f"{source} output")
            key = unicodedata.normalize("NFC", name).casefold()
            if key in occupied:
                other_name, other_source = occupied[key]
                raise CompileError(
                    f"output collision: {source!r} -> {name!r} conflicts with "
                    f"{other_source!r} -> {other_name!r} (case and Unicode normalization included)"
                )
            occupied[key] = (name, source)

    # Alias maps can create file/directory conflicts that source trees cannot.
    for key, (name, source) in occupied.items():
        for parent in PurePosixPath(key).parents:
            if parent.as_posix() in occupied:
                parent_name, parent_source = occupied[parent.as_posix()]
                raise CompileError(
                    f"output collision: {source!r} -> {name!r} is inside output file "
                    f"{parent_source!r} -> {parent_name!r}"
                )
    return aliases


def compile_tree(
    data_root: Path, *, output_map: dict[str, list[str]] | None = None
) -> dict[str, Card]:
    """Return complete generated output, raising before any writes on errors.

    Output names preserve source-relative spelling by default. Explicit mapping
    entries replace that default with zero or more aliases; an empty list keeps
    a source available for includes without publishing it. Unknown mapping keys
    and casefold/NFC collisions are errors. Symlinks within the data root are
    rejected to prevent reading data outside the managed tree.
    """

    data_root = Path(data_root).resolve()
    if not data_root.is_dir():
        raise CompileError(f"data root not found: {data_root}")
    sources: dict[str, bytes] = {}
    try:
        for path in sorted(data_root.rglob("*")):
            if path.is_symlink():
                raise CompileError(f"symlinks are not supported in the data root: {path}")
            if path.is_file() and path.suffix == ".txt":
                source = path.relative_to(data_root).as_posix()
                _check_public_path(source, "source")
                sources[source] = path.read_bytes()
    except OSError as exc:
        raise CompileError(f"cannot read source tree: {exc}") from exc

    aliases = _output_aliases(sources, output_map)
    expanded: dict[str, _Expanded] = {}
    visiting: list[str] = []

    def expand(source: str) -> _Expanded:
        if source in expanded:
            return expanded[source]
        if source in visiting:
            cycle = visiting[visiting.index(source) :] + [source]
            raise CompileError("include cycle: " + " -> ".join(cycle))
        visiting.append(source)
        lines: list[str] = []
        dependencies = {source}
        derived = False
        for number, line in _logical_lines(sources[source], source):
            context = f"{source}:{number}"
            parts = _parse_line(line, context)
            token = next((part for part in parts if isinstance(part, _Include)), None)
            if token is None:
                lines.append("".join(parts))
                continue
            derived = True
            reference = _reference_name(token, source, context)
            if reference not in sources:
                raise CompileError(f"{context}: missing include {token.path!r} (resolved to {reference!r})")
            included = expand(reference)
            dependencies.update(included.dependencies)
            token_index = parts.index(token)
            prefix = "".join(parts[:token_index])
            suffix = "".join(parts[token_index + 1 :])
            lines.extend(prefix + included_line + suffix for included_line in included.lines)
        visiting.pop()
        result = _Expanded(tuple(lines), tuple(sorted(dependencies)), derived)
        expanded[source] = result
        return result

    cards: dict[str, Card] = {}
    try:
        for source, raw in sources.items():
            result = expand(source)
            content = ("\n".join(line.replace("\\\\", "\\") for line in result.lines) + "\n").encode("utf-8")
            card = Card(source, content, result.dependencies, result.derived, raw == content)
            for name in aliases[source]:
                cards[name] = card
    except RecursionError as exc:
        raise CompileError("include nesting exceeds the supported recursion depth") from exc
    return cards
