#!/usr/bin/env python3

import re
from pathlib import Path


class WordSalad:
    def __init__(self, output: Path, data_root: Path):
        self.output = output
        self.data_root = data_root.resolve()
        self.project_root = self.data_root.parent
        self.file_chunk_size = 10000

    def sanitize_filename(self, name):
        name = name.strip().lower()
        name = re.sub(r"[^\w\s-]", "", name)
        name = name.replace(" ", "_")
        name = re.sub(r"\s+", "_", name)
        name = name.replace("__", "_")
        return name

    def load_file_with_continuations(self, path: Path) -> list[str]:
        with path.open("r", encoding="utf-8") as f:
            lines = []
            buffer = ""
            for raw_line in f:
                line = raw_line.rstrip("\n")
                if line.endswith("\\"):
                    buffer += line[:-1]  # remove the backslash, join with next line
                else:
                    full_line = buffer + line
                    lines.append(full_line)
                    buffer = ""
            if buffer:
                lines.append(buffer)

            lines = self._dedupe_normalize(lines)

            return lines

    def resolve_reference_file(self, rel_path: str, base_path=".") -> Path | None:
        rel_path = rel_path.removesuffix(".txt")

        candidates = []
        if base_path is not None:
            candidates.append(Path(base_path) / f"{rel_path}.txt")

        if rel_path.startswith("_data/"):
            candidates.append(self.data_root / f"{rel_path[len('_data/'):]}.txt")
        else:
            candidates.append(self.data_root / f"{rel_path}.txt")

        candidates.append(self.project_root / f"{rel_path}.txt")
        candidates.append(Path(f"{rel_path}.txt"))

        seen = set()
        for candidate in candidates:
            normalized = candidate.resolve(strict=False)
            if normalized in seen:
                continue
            seen.add(normalized)

            if candidate.exists():
                return candidate

        return None

    def resolve_all_file_directives(self, data, base_path="."):
        def resolve_file_path(match):
            rel_path = match.group(1)
            ref_file = self.resolve_reference_file(rel_path, base_path)
            if ref_file is None:
                print(f"Warning: {Path(base_path) / f'{rel_path}.txt'} not found")
                return f"$file_missing:{rel_path}"
            try:
                lines = self.load_file_with_continuations(ref_file)
                return self.resolve_all_file_directives(lines, base_path)
            except Exception as e:
                print(f"Error reading {ref_file}: {e}")
                return [f"$file_error:{rel_path}"]

        def resolve_file_with_affixes(match):
            """Handle $file:[] directives with optional prefix and suffix."""
            prefix = match.group(1) or ""
            filename = match.group(2)
            suffix = match.group(3) or ""

            ref_file = self.resolve_reference_file(filename, base_path)
            if ref_file is None:
                print(f"Warning: {Path(base_path) / f'{filename}.txt'} not found")
                return f"$file_missing:{filename}"

            try:
                lines = self.resolve_all_file_directives(
                    self.load_file_with_continuations(ref_file), base_path
                )
                # Apply prefix and suffix to each line
                modified_lines = []
                for line in lines:
                    modified_line = f"{prefix}{line}{suffix}"
                    modified_lines.append(modified_line)
                return modified_lines
            except Exception as e:
                print(f"Error reading {ref_file}: {e}")
                return [f"$file_error:{filename}"]

        if isinstance(data, str):
            # If the entire string is just $file:..., expand to a list
            only_file = re.fullmatch(r"\$file:\[([a-zA-Z0-9/_\-]+)\]", data.strip())
            if only_file:
                rel_path = only_file.group(1)
                ref_file = self.resolve_reference_file(rel_path, base_path)
                if ref_file is None:
                    print(f"Warning: {Path(base_path) / f'{rel_path}.txt'} not found")
                    return [f"$file_missing:{rel_path}"]
                try:
                    return self.resolve_all_file_directives(
                        self.load_file_with_continuations(ref_file), base_path
                    )
                except Exception as e:
                    print(f"Error reading {ref_file}: {e}")
                    return [f"$file_error:{rel_path}"]

            # Handle file directives with optional prefix/suffix: prefix$file:[filename]suffix
            # Use a more specific regex that captures prefix and suffix
            pattern = r"(.*)\$file:\[([a-zA-Z0-9/_\-]+)\](.*)"
            match = re.search(pattern, data)
            if match:
                return resolve_file_with_affixes(match)

            # Otherwise, substitute inline for backward compatibility
            return re.sub(
                r"\$file:\[([a-zA-Z0-9/_\-]+)\]",
                lambda m: "\n".join(resolve_file_path(m)),
                data,
            )

        elif isinstance(data, list):
            result = []
            for item in data:
                resolved = self.resolve_all_file_directives(item, base_path)
                if isinstance(resolved, list):
                    result.extend(resolved)
                else:
                    result.append(resolved)
            return result

        elif isinstance(data, dict):
            for k, v in data.items():
                data[k] = self.resolve_all_file_directives(v, base_path)
            return data

        else:
            return data

    def write_list_to_file(self, base_path: Path, filename: str, data):
        base_path.mkdir(parents=True, exist_ok=True)
        file_path = base_path / f"{self.sanitize_filename(filename)}.txt"

        if isinstance(data, str):
            data = [data]
        elif not isinstance(data, list):
            raise TypeError(f"Expected string or list for {filename}, got {type(data)}")

        cleaned_lines = []
        for item in data:
            item = item.replace("\\\\", "\\")
            cleaned_lines.append(item)
        new_content = "\n".join(cleaned_lines) + "\n"

        if file_path.exists():
            with file_path.open("r", encoding="utf-8") as f:
                existing_content = f.read()
            if existing_content == new_content:
                return  # Skip if identical

        with file_path.open("w", encoding="utf-8") as f:
            f.write(new_content)

    def split_and_write(self, base_path: Path, base_name: str, lines: list[str]):
        base_path.mkdir(parents=True, exist_ok=True)
        total = len(lines)
        if total == 0:
            out_file = base_path / f"{base_name}-01.txt"
            if not out_file.exists() or out_file.read_text(encoding="utf-8") != "":
                out_file.write_text("", encoding="utf-8")
            return

        num_files = (total + self.file_chunk_size - 1) // self.file_chunk_size
        pad = max(2, len(str(num_files)))

        for i in range(num_files):
            chunk = lines[i * self.file_chunk_size : (i + 1) * self.file_chunk_size]
            out_file = base_path / f"{base_name}-{str(i + 1).zfill(pad)}.txt"
            new_content = "\n".join(chunk) + "\n"

            if out_file.exists():
                existing_content = out_file.read_text(encoding="utf-8")
                if existing_content == new_content:
                    continue  # Skip if identical

            out_file.write_text(new_content, encoding="utf-8")

    def recurse(self, obj, path):
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_clean = self.sanitize_filename(key)

                if isinstance(value, (list, str)):
                    self.write_list_to_file(path, key_clean, value)

                elif isinstance(value, dict):
                    if "DATA--" in value and isinstance(value["DATA--"], list):
                        self.write_list_to_file(
                            path / key_clean, key_clean, value["DATA--"]
                        )
                        for sub_key, sub_value in value.items():
                            if sub_key != "DATA--":
                                self.recurse({sub_key: sub_value}, path / key_clean)

                    elif "SPLIT--" in value and isinstance(
                        value["SPLIT--"], (list, str)
                    ):
                        if isinstance(value["SPLIT--"], str):
                            lines = [value["SPLIT--"]]
                        else:
                            lines = value["SPLIT--"]
                        self.split_and_write(path / key_clean, key_clean, lines)
                        for sub_key, sub_value in value.items():
                            if sub_key != "SPLIT--":
                                self.recurse({sub_key: sub_value}, path / key_clean)

                    else:
                        self.recurse(value, path / key_clean)

    def write_bundles(self, bundles: dict, output_path: Path):
        for bundle_name, paths in bundles.items():
            if isinstance(paths, str):
                paths = [paths]

            expanded = []
            for line in paths:
                expanded_line = self.resolve_all_file_directives(line)
                if isinstance(expanded_line, str) and "\n" in expanded_line:
                    expanded.extend(expanded_line.splitlines())
                elif isinstance(expanded_line, list):
                    expanded.extend(expanded_line)
                else:
                    expanded.append(expanded_line)

            self.write_list_to_file(output_path / "_bundles", bundle_name, expanded)

    def process_data_directory(self):
        for source_file in sorted(self.data_root.rglob("*.txt")):
            relative_path = source_file.relative_to(self.data_root)
            source_lines = self.load_file_with_continuations(source_file)
            resolved = self.resolve_all_file_directives(source_lines, self.data_root)
            self.write_list_to_file(self.output / relative_path.parent, relative_path.stem, resolved)

    def _dedupe_normalize(self, lines: list[str]) -> list[str]:
        """Trim, remove empty, and deduplicate while preserving order."""
        seen = set()
        result = []
        for line in lines:
            norm = line.strip()
            if not norm:
                continue  # skip blank lines
            if norm not in seen:
                seen.add(norm)
                result.append(norm)
        return result


def main():
    data_root = Path(__file__).parent / "_data"
    output = Path(__file__).parent.parent / "Wildcards"
    word_salad = WordSalad(output, data_root)
    word_salad.process_data_directory()


if __name__ == "__main__":
    main()
