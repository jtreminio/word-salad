"""Recoverable, conservative synchronization of source cards and published text.

State belongs to one publisher and one destination. Output is never a scratch
directory: unknown cards are imported, and absence is a conflict, not a deletion.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import unicodedata
import uuid

from .compiler import Card, CompileError, compile_tree

_UNSPECIFIED = object()


class SyncError(Exception):
    """A synchronization cannot safely proceed."""


def digest(content: bytes | None) -> str | None:
    return hashlib.sha256(content).hexdigest() if content is not None else None


def json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SyncError(f"Invalid relative path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ("", ".", "..") for p in value.split("/")):
        raise SyncError(f"Unsafe relative path: {value!r}")
    if not value.endswith(".txt"):
        raise SyncError(f"Card paths must end in .txt: {value!r}")
    return value


def identity(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def inventory(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    if root.is_symlink() or not root.is_dir():
        raise SyncError(f"Expected a real directory: {root}")
    files = {}
    identities = {}
    for parent, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            if (Path(parent) / name).is_symlink():
                raise SyncError(f"Symlinks are not supported in managed trees: {Path(parent) / name}")
        for name in sorted(names):
            if not name.endswith(".txt"):
                continue
            path = Path(parent) / name
            relative = safe_relative(path.relative_to(root).as_posix())
            key = identity(relative)
            if key in identities:
                raise SyncError(f"Case/Unicode path collision: {identities[key]} and {relative}")
            identities[key] = relative
            files[relative] = path.read_bytes()
    return files


@dataclass
class Plan:
    result: dict
    before: dict[str, dict[str, bytes]]
    after: dict[str, dict[str, bytes]]
    manifest_before: bytes | None
    manifest: dict
    config_before: bytes | None
    blobs: dict[str, bytes]


class Synchronizer:
    def __init__(self, data_root: Path, output_root: Path, state_root: Path,
                 overrides_root: Path | None = None, config_path: Path | None = None):
        self.data_root = Path(data_root).absolute()
        self.output_root = Path(output_root).absolute()
        self.state_root = Path(state_root).absolute()
        self.overrides_root = Path(overrides_root).absolute() if overrides_root else self.data_root.parent / "_overrides"
        self.config_path = Path(config_path).absolute() if config_path else None
        for root in (self.data_root, self.output_root, self.state_root, self.overrides_root):
            if root.is_symlink():
                raise SyncError(f"Managed root cannot be a symlink: {root}")
        # macOS exposes temporary directories through /var -> /private/var.
        # Canonicalize system ancestors; symlinks inside managed trees stay forbidden.
        self.data_root = self.data_root.resolve()
        self.output_root = self.output_root.resolve()
        self.state_root = self.state_root.resolve()
        self.overrides_root = self.overrides_root.resolve()
        self.roots = {"source": self.data_root, "output": self.output_root,
                      "override": self.overrides_root, "state": self.state_root}
        for name, root in self.roots.items():
            if root.is_symlink():
                raise SyncError(f"Managed root cannot be a symlink: {root}")
            for other, candidate in self.roots.items():
                if name != other and (root.resolve() == candidate.resolve() or root.resolve() in candidate.resolve().parents):
                    raise SyncError(f"Managed directories must not overlap: {root} and {candidate}")
        self.manifest_path = self.state_root / "manifest.json"
        self.pending_path = self.state_root / "pending.json"

    def _config_bytes(self):
        if self.config_path and self.config_path.exists():
            if self.config_path.is_symlink():
                raise SyncError("Configuration cannot be a symlink")
            return self.config_path.read_bytes()
        return None

    def _config(self, raw):
        try:
            config = json.loads(raw) if raw is not None else {"version": 1}
            if not isinstance(config, dict) or config.get("version", 1) != 1:
                raise ValueError("unsupported version")
            mapping = config.get("output_map", {})
            if not isinstance(mapping, dict):
                raise ValueError("output_map must be an object")
            for source, outputs in mapping.items():
                safe_relative(source)
                if not isinstance(outputs, list):
                    raise ValueError("output_map values must be arrays")
                for output in outputs:
                    safe_relative(output)
            for key in ("retired_paths", "external_references"):
                if not isinstance(config.get(key, []), list):
                    raise ValueError(f"{key} must be an array")
            for path in config.get("retired_paths", []):
                safe_relative(path)
            if not all(isinstance(x, str) for x in config.get("external_references", [])):
                raise ValueError("external_references must contain strings")
            return config
        except (ValueError, TypeError) as error:
            raise SyncError(f"Invalid configuration: {error}") from error

    def _load_manifest(self):
        if not self.manifest_path.exists():
            return None, None
        if self.manifest_path.is_symlink():
            raise SyncError("Manifest cannot be a symlink")
        raw = self.manifest_path.read_bytes()
        try:
            value = json.loads(raw)
            if value["version"] != 1 or not isinstance(value["entries"], dict):
                raise ValueError("unsupported state schema")
            if value["roots"] != {k: str(v.resolve()) for k, v in self.roots.items()}:
                raise ValueError("state belongs to different source/output directories; use a separate state directory")
            for path, entry in value["entries"].items():
                safe_relative(path)
                safe_relative(entry["source"])
            return value, raw
        except (ValueError, KeyError, TypeError) as error:
            raise SyncError(f"Invalid synchronization state: {error}") from error

    def _compile(self, sources, config):
        # Compile immutable bytes from our snapshot, never a changing live tree.
        try:
            occupied = {}
            for name in sources:
                safe_relative(name)
                key = identity(name)
                if key in occupied:
                    raise SyncError(f"Case/Unicode source collision: {occupied[key]} and {name}")
                occupied[key] = name
            with tempfile.TemporaryDirectory(prefix="word-salad-stage-") as temporary:
                root = Path(temporary)
                for name, content in sources.items():
                    target = root / safe_relative(name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                return compile_tree(root, output_map=config.get("output_map", {}))
        except (CompileError, UnicodeError, ValueError) as error:
            raise SyncError(str(error)) from error

    def _roundtrips(self, content):
        try:
            card = self._compile({"card.txt": content}, {})["card.txt"]
            return card.direct and not card.derived
        except SyncError:
            return False

    @staticmethod
    def _entry(card, published, sources, override=None, recipe=None):
        return {
            "source": card.source,
            "source_hashes": {p: digest(sources[p]) for p in card.dependencies},
            "recipe": recipe if recipe is not None else digest(card.content),
            "published": digest(published),
            "direct": card.direct,
            "derived": card.derived,
            "override": override is not None,
            "override_hash": digest(override),
        }

    def _validate(self, cards, overrides, config):
        retired = {identity(x): x for x in config.get("retired_paths", [])}
        active = {identity(x.removesuffix(".txt")) for x in cards}
        external = {identity(x.removesuffix(".txt")) for x in config.get("external_references", [])}
        warnings = []
        for path, card in cards.items():
            if identity(path) in retired:
                raise SyncError(f"Retired output is still generated: {path}")
            content = overrides.get(path, card.content)
            try:
                text = content.decode("utf-8")
            except UnicodeError as error:
                raise SyncError(f"Card is not UTF-8: {path}") from error
            for match in re.finditer(r"<(?:wc|wildcard)(?:\[[^\]]*\])?:([^<>]+)>", text):
                target = match[1].split(",not=", 1)[0].strip().removesuffix(".txt")
                if identity(target + ".txt") in retired:
                    raise SyncError(f"{path} references retired wildcard {target}")
                if identity(target) not in active | external:
                    warnings.append({"path": path, "target": target, "detail": "Literal runtime reference not found locally"})
        return list({(x["path"], x["target"]): x for x in warnings}.values())

    def _plan(self, *, adoption=False, resolution=None, prefer_source=()):
        if self.pending_path.exists():
            raise SyncError("An interrupted transaction exists. Run recover before synchronizing.")
        config_raw = self._config_bytes()
        config = self._config(config_raw)
        manifest, manifest_raw = self._load_manifest()
        if adoption and manifest is not None:
            raise SyncError("Already adopted; use sync instead of replacing the baseline")
        before = {key: inventory(self.roots[key]) for key in ("source", "output", "override")}
        if not self.data_root.is_dir():
            raise SyncError(f"Source directory not found: {self.data_root}")
        if not adoption and manifest is None:
            if before["output"]:
                raise SyncError("Existing Wildcards have no baseline. Back them up, then run adopt.")
            adoption = True
        previous = manifest["entries"] if manifest else {}
        after = {key: dict(value) for key, value in before.items()}
        sources, outputs, overrides = after["source"], after["output"], after["override"]
        actions, conflicts = [], []
        entries = dict(previous)

        def action(kind, path, detail=""):
            actions.append({"kind": kind, "path": path, "detail": detail})

        def conflict(kind, path, detail):
            conflicts.append({"kind": kind, "path": path, "detail": detail})

        cards = self._compile(sources, config)
        for path in prefer_source:
            safe_relative(path)
            if not adoption or path not in cards:
                raise SyncError(f"Initial source choice requires an existing source output: {path}")
        retired = set(config.get("retired_paths", []))
        # Import new output cards, but never resurrect a missing previously owned source.
        for path, content in before["output"].items():
            if path in cards or path in previous:
                continue
            if path in retired:
                conflict("retired_unowned", path, "Retired data is present outside managed ownership; archive and remove explicitly")
                continue
            if path in sources:
                conflict("unmapped_output", path, "An unpublished source occupies this path; configure its output mapping explicitly")
                continue
            sources[path] = content
            action("import_new", path, "Import exact live bytes into source")
        if sources != before["source"]:
            cards = self._compile(sources, config)

        source_uses = Counter(card.source for card in cards.values())
        # Decide lossless direct imports before recompiling affected dependents.
        imports = set()
        for path, card in cards.items():
            base = previous.get(path)
            live = before["output"].get(path)
            forced = resolution is not None and resolution[0] == path and resolution[1] == "output"
            source_dirty = base is not None and base["source_hashes"] != {p: digest(sources[p]) for p in card.dependencies}
            live_dirty = base is not None and digest(live) != base["published"]
            if live is None or adoption or (not forced and (not base or not live_dirty or source_dirty or base["override"] or path in overrides)):
                continue
            if source_uses[card.source] == 1 and card.direct and not card.derived and self._roundtrips(live):
                sources[card.source] = live
                overrides.pop(path, None)
                imports.add(path)
                action("import", path, f"Import edited output into {card.source}")
        if imports:
            cards = self._compile(sources, config)

        if resolution is not None and resolution[0] not in cards:
            raise SyncError(f"Cannot resolve {resolution[0]}: no source card; restore its source or configure retirement")

        for path, base in previous.items():
            if path in cards:
                continue
            live = before["output"].get(path)
            if path in retired:
                if digest(before["override"].get(path)) != base.get("override_hash"):
                    conflict("retired_override_edited", path, "Retired card has an edited or missing override; preserve and resolve it before removal")
                    continue
                if live is not None and digest(live) != base["published"]:
                    conflict("retired_edited", path, "Retired output has external edits; preserve and resolve them before removal")
                    continue
                if live is not None:
                    outputs.pop(path, None)
                    action("retire", path, "Remove explicitly retired, unchanged owned output")
                overrides.pop(path, None)
                entries.pop(path, None)
            else:
                conflict("missing_source", path, "Source or output mapping disappeared; restore it or explicitly retire the public path")

        for path, card in sorted(cards.items()):
            base = previous.get(path)
            live = before["output"].get(path)
            override = overrides.get(path)
            choice = "source" if path in prefer_source else (
                resolution[1] if resolution is not None and resolution[0] == path else None
            )

            if choice == "source":
                overrides.pop(path, None)
                outputs[path] = card.content
                entries[path] = self._entry(card, card.content, sources)
                action("resolve_source", path, "Use current compiled source; previous output and override retained in history")
                continue
            if choice == "output":
                if live is None:
                    raise SyncError(f"Cannot choose missing output: {path}")
                if path in imports:
                    entries[path] = self._entry(card, live, sources)
                else:
                    overrides[path] = live
                    entries[path] = self._entry(card, live, sources, live)
                action("resolve_output", path, "Keep current output and acknowledge current recipe as its baseline")
                continue

            if base is None:
                if live is None:
                    desired = override if override is not None else card.content
                    outputs[path] = desired
                    entries[path] = self._entry(card, desired, sources, override)
                    action("publish_new", path)
                else:
                    if override is not None and override != live:
                        conflict("initial_override", path, "Existing override differs from live output; choose source or output explicitly")
                        continue
                    if live != card.content:
                        overrides[path] = live
                        override = live
                        action("capture_override", path, "Preserve existing live bytes without changing source")
                    entries[path] = self._entry(card, live, sources, override)
                continue

            if live is None:
                conflict("missing_output", path, "Output disappeared; restore with resolve --use source, or explicitly retire the card")
                continue
            if base["source"] != card.source:
                conflict("ownership_changed", path, "Public path now maps to a different source; resolve ownership explicitly")
                continue
            if path in imports:
                entries[path] = self._entry(card, live, sources)
                continue

            source_dirty = base["source_hashes"] != {p: digest(sources[p]) for p in card.dependencies}
            live_dirty = digest(live) != base["published"]
            if base["override"] or override is not None:
                if override is None:
                    conflict("missing_override", path, "Override disappeared; use resolve --use source to restore recipe control")
                    continue
                override_dirty = digest(override) != base.get("override_hash")
                if live_dirty and override_dirty and live != override:
                    conflict("concurrent_override", path, "Output and override changed independently; both versions preserved")
                    continue
                if live_dirty and not override_dirty:
                    override = live
                    overrides[path] = live
                    action("update_override", path, "Capture edited output into its existing override")
                recipe_dirty = digest(card.content) != base["recipe"]
                if recipe_dirty:
                    conflict("recipe_changed", path, "Recipe/dependencies changed beneath an active override; resolve --use source or output")
                    # Output edits still enter the override, without acknowledging the pending recipe.
                    if live_dirty and not override_dirty:
                        updated = dict(base)
                        updated.update(published=digest(live), override_hash=digest(live))
                        entries[path] = updated
                    continue
                if live != override:
                    outputs[path] = override
                    action("publish_override", path)
                entries[path] = self._entry(card, override, sources, override)
                continue

            if live_dirty:
                if source_dirty and live != card.content:
                    conflict("concurrent_edit", path, "Source and output changed independently; both versions preserved")
                    continue
                if live != card.content:
                    overrides[path] = live
                    entries[path] = self._entry(card, live, sources, live)
                    action("capture_override", path, "Generated or non-lossless edit preserved as a per-card override")
                else:
                    entries[path] = self._entry(card, live, sources)
                    action("acknowledge", path, "Source and output agree")
            else:
                outputs[path] = card.content
                entries[path] = self._entry(card, card.content, sources)
                if card.content != live:
                    action("publish", path)

        for path in overrides.keys() - cards.keys() - retired:
            conflict("unowned_override", path, "Override has no generated card; restore its source or retire it explicitly")
        warnings = self._validate(cards, overrides, config)
        # Validate actual planned output too, including output held back by conflicts.
        staged_overrides = {p: outputs[p] for p in cards if p in outputs}
        warnings.extend(self._validate(cards, staged_overrides, config))
        new_manifest = {"version": 1, "roots": {k: str(v.resolve()) for k, v in self.roots.items()}, "entries": entries}
        blobs = {}
        for tree in list(before.values()) + list(after.values()):
            for content in tree.values():
                blobs[digest(content)] = content
        for card in cards.values():
            blobs[digest(card.content)] = card.content
        manifest_changed = manifest_raw != json_bytes(new_manifest)
        result = {
            "actions": actions, "conflicts": conflicts,
            "warnings": list({(x["path"], x["target"]): x for x in warnings}.values()),
            "changed": before != after or manifest_changed,
            "overrides": [{"path": p, "source": cards[p].source if p in cards else None,
                           "differs_from_recipe": p in cards and content != cards[p].content}
                          for p, content in sorted(overrides.items())],
            "managed_files": len(entries),
        }
        return Plan(result, before, after, manifest_raw, new_manifest, config_raw, blobs)

    @contextmanager
    def _lock(self):
        self.state_root.mkdir(parents=True, exist_ok=True)
        if self.state_root.is_symlink():
            raise SyncError("State root cannot be a symlink")
        path = self.state_root / "publisher.lock"
        if path.is_symlink():
            raise SyncError("Publisher lock cannot be a symlink")
        with path.open("a+b") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise SyncError("Another publisher is running for this state directory") from error
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def _fsync_directory(path):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _atomic_write(path, content, *, expected=_UNSPECIFIED):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise SyncError(f"Refusing to write through symlink: {path}")
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".word-salad-", delete=False) as file:
            temporary = Path(file.name)
            try:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            if expected is not _UNSPECIFIED:
                current = digest(path.read_bytes()) if path.exists() else None
                if current != expected:
                    raise SyncError(f"File changed before replacement: {path}. Its edit was preserved; run recover after resolving the conflict.")
            os.replace(temporary, path)
            Synchronizer._fsync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def _blob(self, content):
        key = digest(content)
        path = self.state_root / "blobs" / key
        if not path.exists():
            self._atomic_write(path, content)
        elif path.is_symlink() or digest(path.read_bytes()) != key:
            raise SyncError(f"Corrupt baseline blob: {key}")
        return key

    def _read_blob(self, key):
        if key is None:
            return None
        if not isinstance(key, str) or not re.fullmatch("[a-f0-9]{64}", key):
            raise SyncError("Invalid content hash in transaction")
        path = self.state_root / "blobs" / key
        if path.is_symlink() or not path.is_file():
            raise SyncError(f"Missing baseline blob: {key}")
        content = path.read_bytes()
        if digest(content) != key:
            raise SyncError(f"Corrupt baseline blob: {key}")
        return content

    def _operation_path(self, operation):
        area, relative = operation["area"], operation["path"]
        if area not in self.roots or (area == "state" and relative != "manifest.json"):
            raise SyncError("Invalid transaction destination")
        if area != "state":
            safe_relative(relative)
        path = self.roots[area] / relative
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise SyncError(f"Symlink in transaction destination: {path}")
        return path

    def _assert_fresh(self, plan):
        for area, expected in plan.before.items():
            if inventory(self.roots[area]) != expected:
                raise SyncError(f"{area} changed during planning; no managed files written. Run sync again.")
        raw = self.manifest_path.read_bytes() if self.manifest_path.exists() else None
        if raw != plan.manifest_before or self._config_bytes() != plan.config_before:
            raise SyncError("Configuration/state changed during planning; run sync again")

    def _apply_pending(self, journal):
        if journal.get("version") != 1 or journal.get("roots") != {k: str(v.resolve()) for k, v in self.roots.items()}:
            raise SyncError("Transaction belongs to different managed directories")
        operations = journal.get("operations")
        if not isinstance(operations, list):
            raise SyncError("Invalid transaction operations")
        if not isinstance(journal.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]+", journal["id"]):
            raise SyncError("Invalid transaction identifier")
        # Preflight every remaining operation before completing an interrupted batch.
        for op in operations:
            path = self._operation_path(op)
            current = digest(path.read_bytes()) if path.exists() else None
            if current not in (op["before"], op["after"]):
                raise SyncError(f"Recovery conflict at {path}; preserve current edits before recovering")
            self._read_blob(op["after"])
        for op in operations:
            path = self._operation_path(op)
            current = digest(path.read_bytes()) if path.exists() else None
            if current == op["after"]:
                continue
            if current != op["before"]:
                raise SyncError(f"File changed while publishing: {path}. Run recover after resolving the edit.")
            content = self._read_blob(op["after"])
            if content is None:
                if (digest(path.read_bytes()) if path.exists() else None) != op["before"]:
                    raise SyncError(f"File changed before removal: {path}; it was preserved")
                path.unlink()
                self._fsync_directory(path.parent)
            else:
                self._atomic_write(path, content, expected=op["before"])
        archive = self.state_root / "transactions" / (journal["id"] + ".json")
        self._atomic_write(archive, json_bytes(journal))
        self.pending_path.unlink()
        self._fsync_directory(self.state_root)

    def _commit(self, plan):
        self._assert_fresh(plan)
        for content in plan.blobs.values():
            self._blob(content)
        # Keep snapshots of conflicts even when there is no safe managed-file change.
        if plan.result["conflicts"]:
            saved = {"conflicts": plan.result["conflicts"],
                     "source": {p: digest(b) for p, b in plan.before["source"].items()},
                     "output": {p: digest(b) for p, b in plan.before["output"].items()},
                     "override": {p: digest(b) for p, b in plan.before["override"].items()}}
            data = json_bytes(saved)
            target = self.state_root / "conflicts" / (digest(data) + ".json")
            if not target.exists():
                self._atomic_write(target, data)
        operations = []
        for area in ("source", "override", "output"):
            for relative in sorted(plan.before[area].keys() | plan.after[area].keys()):
                old, new = plan.before[area].get(relative), plan.after[area].get(relative)
                if old != new:
                    operations.append({"area": area, "path": relative,
                                       "before": digest(old), "after": digest(new)})
        new_manifest = json_bytes(plan.manifest)
        if new_manifest != plan.manifest_before:
            self._blob(new_manifest)
            if plan.manifest_before is not None:
                self._blob(plan.manifest_before)
            operations.append({"area": "state", "path": "manifest.json",
                               "before": digest(plan.manifest_before), "after": digest(new_manifest)})
        if operations:
            # Blob storage can take time on first adoption. Recheck immediately before publication.
            self._assert_fresh(plan)
            journal = {"version": 1, "id": datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex,
                       "roots": {k: str(v.resolve()) for k, v in self.roots.items()}, "operations": operations}
            self._atomic_write(self.pending_path, json_bytes(journal))
            self._apply_pending(journal)
        return plan.result

    def status(self):
        try:
            return self._plan().result
        except SyncError as error:
            if not self.manifest_path.exists() and not self.pending_path.exists() and "run adopt" in str(error):
                plan = self._plan(adoption=True)
                plan.result["conflicts"].insert(0, {"kind": "not_adopted", "path": "", "detail": str(error)})
                return plan.result
            raise

    def sync(self):
        if not self.manifest_path.exists() and inventory(self.output_root):
            self._plan()  # Reject unadopted existing data without even creating state.
        with self._lock():
            return self._commit(self._plan())

    def adopt(self, *, prefer_source=()):
        with self._lock():
            return self._commit(self._plan(adoption=True, prefer_source=tuple(prefer_source)))

    def resolve(self, path, choice):
        path = safe_relative(path)
        if choice not in ("source", "output"):
            raise SyncError("Resolution must choose source or output")
        with self._lock():
            return self._commit(self._plan(resolution=(path, choice)))

    def recover(self):
        with self._lock():
            if not self.pending_path.exists():
                return {"actions": [], "conflicts": [], "changed": False}
            try:
                journal = json.loads(self.pending_path.read_bytes())
                self._apply_pending(journal)
            except (ValueError, KeyError, TypeError) as error:
                raise SyncError(f"Invalid interrupted transaction: {error}") from error
            return {"actions": [{"kind": "recovered", "path": "", "detail": journal["id"]}], "conflicts": [], "changed": True}

    def export(self, destination):
        destination = Path(destination).absolute()
        if destination.is_symlink():
            raise SyncError("Build destination cannot be a symlink")
        destination = destination.resolve()
        for root in self.roots.values():
            if destination.resolve() == root.resolve() or destination.resolve() in root.resolve().parents or root.resolve() in destination.resolve().parents:
                raise SyncError("Build destination must be separate from all managed directories")
        if destination.is_symlink() or (destination.exists() and (not destination.is_dir() or any(destination.iterdir()))):
            raise SyncError("Build destination must be new or empty")
        if self.pending_path.exists():
            raise SyncError("Recover the pending transaction before exporting")
        config_raw = self._config_bytes()
        config = self._config(config_raw)
        observed = {area: inventory(self.roots[area]) for area in ("source", "output", "override")}
        manifest_raw = self.manifest_path.read_bytes() if self.manifest_path.exists() else None
        if manifest_raw is not None or observed["output"]:
            status = self.status()
            if status["conflicts"] or status["changed"] or status["actions"]:
                raise SyncError("Synchronize or resolve pending changes before exporting")
        sources, overrides = observed["source"], observed["override"]
        cards = self._compile(sources, config)
        unknown = overrides.keys() - cards.keys()
        if unknown:
            raise SyncError(f"Overrides have no source: {', '.join(sorted(unknown))}")
        warnings = self._validate(cards, overrides, config)
        if any(observed[area] != inventory(self.roots[area]) for area in observed) or self._config_bytes() != config_raw:
            raise SyncError("Managed files changed during export; synchronize and retry")
        if (self.manifest_path.read_bytes() if self.manifest_path.exists() else None) != manifest_raw or self.pending_path.exists():
            raise SyncError("Publication state changed during export; retry")
        if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
            raise SyncError("Build destination changed during export; existing files were preserved")
        destination.mkdir(parents=True, exist_ok=True)
        for path, card in cards.items():
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or any(p.is_symlink() for p in target.parents):
                raise SyncError(f"Symlink in export destination: {target}")
            # Publish complete bytes without replacing a file created after preflight.
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".word-salad-", delete=False) as file:
                temporary = Path(file.name)
                try:
                    file.write(overrides.get(path, card.content))
                    file.flush()
                    os.fsync(file.fileno())
                except BaseException:
                    temporary.unlink(missing_ok=True)
                    raise
            try:
                os.link(temporary, target)
            except FileExistsError as error:
                raise SyncError(f"Export destination appeared during publication: {target}; it was not overwritten") from error
            finally:
                temporary.unlink(missing_ok=True)
        return {"actions": [{"kind": "export", "path": str(destination), "detail": f"{len(cards)} cards"}],
                "conflicts": [], "warnings": warnings, "changed": bool(cards), "managed_files": len(cards)}
