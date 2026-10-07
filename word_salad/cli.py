"""Safe publishing, inspection, and release commands for word-salad."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from .compiler import CompileError
from .sync import SyncError, Synchronizer


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _common_options(parser: argparse.ArgumentParser) -> None:
    # SUPPRESS lets a command inherit flags supplied before its name without
    # its own defaults accidentally replacing them.
    parser.add_argument("--data-root", type=Path, default=argparse.SUPPRESS,
                        metavar="DIR", help="source text directory (default: _data)")
    parser.add_argument("--output", type=Path, default=argparse.SUPPRESS,
                        metavar="DIR", help="live output, or empty build destination (default: ../Wildcards)")
    parser.add_argument("--state-root", type=Path, default=argparse.SUPPRESS,
                        metavar="DIR", help="local synchronization state (default: .word-salad)")
    parser.add_argument("--overrides-root", type=Path, default=argparse.SUPPRESS,
                        metavar="DIR", help="versioned output overrides (default: _overrides)")
    parser.add_argument("--config", type=Path, default=argparse.SUPPRESS,
                        metavar="FILE", help="ownership configuration (default: word-salad.json)")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="print structured results")
    parser.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                        help="inspect pending sync actions without writing (sync/status/watch only)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage text wildcard recipes and safely import edits from their output.",
        epilog="No command means sync. Existing output must first be backed up and adopted.",
    )
    _common_options(parser)
    parser.set_defaults(
        data_root=PROJECT_ROOT / "_data",
        output=None,
        state_root=PROJECT_ROOT / ".word-salad",
        overrides_root=PROJECT_ROOT / "_overrides",
        config=PROJECT_ROOT / "word-salad.json",
        json=False,
        dry_run=False,
        command="sync",
    )
    subparsers = parser.add_subparsers(dest="command")
    commands = {
        "status": "preview changes and conflicts without writing",
        "sync": "import safe output edits and publish validated changes",
        "adopt": "establish a baseline for existing output, preserving divergent data",
        "recover": "recover an interrupted publication",
        "resolve": "resolve a reported conflict using source or output",
        "watch": "watch both trees and synchronize stable changes",
        "build": "export validated cards into a new or empty directory",
        "release": "package a validated staged build into a new ZIP",
    }
    for command, help_text in commands.items():
        child = subparsers.add_parser(command, help=help_text, description=help_text)
        _common_options(child)
        if command == "resolve":
            child.add_argument("path", help="public wildcard path, including .txt")
            child.add_argument("--use", choices=("source", "output"), required=True)
        elif command == "adopt":
            child.add_argument("--use-source", action="append", default=[], metavar="PATH",
                               help="publish the source for this public card during adoption; repeat for intentional migrations")
        elif command == "watch":
            child.add_argument("--interval", type=float, default=2.0,
                               help="poll interval in seconds; changes must survive two polls (default: 2)")
        elif command == "build":
            child.add_argument("--live-output", type=Path, default=PROJECT_ROOT.parent / "Wildcards",
                               metavar="DIR", help="managed live tree to check before export (default: ../Wildcards)")
        elif command == "release":
            child.add_argument("--archive", type=Path,
                               help="new archive filename (default: releases/wildcards-TIMESTAMP.zip)")
    return parser


def _report(result: dict, *, as_json: bool, command: str) -> int:
    if as_json:
        print(json.dumps({"command": command, **result}, ensure_ascii=False, sort_keys=True))
    else:
        actions = result.get("actions", [])
        conflicts = result.get("conflicts", [])
        print(f"{command}: {len(actions)} action(s), {len(conflicts)} conflict(s)")
        for action in actions:
            detail = action.get("detail")
            print(f"  {action.get('kind', 'change')}: {action.get('path', '')}"
                  + (f" — {detail}" if detail else ""))
        for conflict in conflicts:
            detail = conflict.get("detail") or conflict.get("reason") or conflict.get("kind", "conflict")
            print(f"  conflict: {conflict.get('path', '')} — {detail}")
        overrides = result.get("overrides", [])
        if overrides:
            print(f"  Active overrides: {len(overrides)}")
            for override in overrides[:5]:
                print(f"    {override['path']}")
            if len(overrides) > 5:
                print("    See status --json for the full list and source mappings.")
        if result.get("archive"):
            print(f"Created: {result['archive']}")
    return 1 if result.get("conflicts") else 0


def _signature(paths: tuple[Path, ...]) -> tuple:
    """Observe stable saves without reading every large card on every poll."""
    signature = []
    for root in paths:
        if not root.exists():
            signature.append((str(root), None))
            continue
        entries = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in entries:
            if path.is_dir():
                continue
            try:
                info = path.stat()
                signature.append((str(path), info.st_size, info.st_mtime_ns, info.st_ctime_ns))
            except FileNotFoundError:
                # An editor may atomically replace the path during this scan.
                signature.append((str(path), None))
    return tuple(signature)


def _watch(manager: Synchronizer, paths: tuple[Path, ...], interval: float,
           as_json: bool) -> int:
    previous = None
    handled = None
    if not as_json:
        print(f"Watching; checking every {interval:g}s. Press Ctrl-C to stop.", flush=True)
    try:
        while True:
            current = _signature(paths)
            if current == previous and current != handled:
                try:
                    result = manager.sync()
                    if handled is None or result.get("actions") or result.get("conflicts") or result.get("changed"):
                        _report(result, as_json=as_json, command="watch")
                except (SyncError, CompileError, OSError) as exc:
                    _report_error(exc, as_json=as_json)
                # Do not swallow events that happened during sync. The next
                # two stable polls process the new signature; an unchanged
                # conflicted signature is reported only once.
                handled = current
            previous = current
            time.sleep(interval)
    except KeyboardInterrupt:
        if not as_json:
            print("Watch stopped.")
        return 0


def _release(manager: Synchronizer, archive: Path) -> dict:
    if archive.exists() or archive.is_symlink():
        raise SyncError(f"Archive already exists: {archive}; choose a new filename")
    with tempfile.TemporaryDirectory(prefix="word-salad-release-") as staging:
        output = Path(staging) / "Wildcards"
        result = manager.export(output)
        if result.get("conflicts"):
            return result
        archive.parent.mkdir(parents=True, exist_ok=True)
        # Build the ZIP fully before exposing it. Hard-link publication refuses
        # an existing target, including one created after the earlier check.
        descriptor, temporary = tempfile.mkstemp(prefix=".word-salad-release-", suffix=".zip",
                                                  dir=archive.parent)
        os.close(descriptor)
        temporary_path = Path(temporary)
        try:
            with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                for path in sorted(output.rglob("*.txt")):
                    bundle.write(path, arcname="Wildcards/" + path.relative_to(output).as_posix())
            os.link(temporary_path, archive)
        finally:
            temporary_path.unlink(missing_ok=True)
    return {**result, "archive": str(archive.resolve()), "changed": True}


def _report_error(error: Exception, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"error": str(error)}, ensure_ascii=False, sort_keys=True), flush=True)
    else:
        print(f"error: {error}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "sync"
    if args.dry_run and command not in {"status", "sync", "watch"}:
        parser.error(f"--dry-run is not supported with {command}; use status to inspect synchronization")
    if command == "build" and args.output is None:
        parser.error("build requires --output DIR pointing to a new or empty destination")
    if command == "watch" and (args.interval <= 0 or not args.interval < float("inf")):
        parser.error("--interval must be a finite number greater than zero")
    # For build, --output selects an export target, never a new live root.
    # The manager still checks its actual live tree before exporting recipes.
    live_output = args.live_output if command == "build" else (
        args.output if args.output is not None else PROJECT_ROOT.parent / "Wildcards"
    )
    try:
        manager = Synchronizer(
            args.data_root,
            live_output,
            args.state_root,
            overrides_root=args.overrides_root,
            config_path=args.config,
        )
        if args.dry_run or command == "status":
            return _report(manager.status(), as_json=args.json, command="status")
        if command == "watch":
            return _watch(manager, (args.data_root, live_output, args.overrides_root, args.config),
                          args.interval, args.json)
        if command == "build":
            result = manager.export(args.output)
        elif command == "release":
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            archive = args.archive if args.archive is not None else (
                PROJECT_ROOT / "releases" / f"wildcards-{stamp}.zip"
            )
            result = _release(manager, archive)
        elif command == "resolve":
            result = manager.resolve(args.path, args.use)
        elif command == "adopt":
            result = manager.adopt(prefer_source=tuple(args.use_source))
        else:
            result = getattr(manager, command)()
        return _report(result, as_json=args.json, command=command)
    except (SyncError, CompileError, OSError, ValueError) as exc:
        _report_error(exc, as_json=args.json)
        return 1
