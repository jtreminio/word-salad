"""CLI safety checks run entirely inside temporary directories."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from word_salad import cli


class CliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project"
        self.data = self.project / "_data"
        self.output = self.root / "Wildcards"
        self.data.mkdir(parents=True)
        self.output.mkdir()
        (self.data / "card.txt").write_bytes(b"original\n")
        (self.output / "card.txt").write_bytes(b"original\n")
        root_patch = patch.object(cli, "PROJECT_ROOT", self.project)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def adopt(self):
        code, output, error = self.invoke("adopt")
        self.assertEqual(0, code, output + error)

    def snapshot(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def test_default_sync_refuses_unadopted_output(self):
        before = self.snapshot()
        code, output, error = self.invoke()
        self.assertEqual(1, code)
        self.assertIn("adopt", (output + error).lower())
        self.assertEqual(before, self.snapshot())

    def test_common_flags_work_before_and_after_command(self):
        parser = cli.build_parser()
        before = parser.parse_args(["--json", "--data-root", str(self.data), "status"])
        after = parser.parse_args(["status", "--json", "--data-root", str(self.data)])
        self.assertTrue(before.json)
        self.assertEqual(vars(before), vars(after))

    def test_dry_run_reports_pending_import_and_writes_nothing(self):
        self.adopt()
        (self.output / "card.txt").write_bytes(b"live edit\n")
        before = self.snapshot()
        code, output, error = self.invoke("--dry-run", "--json")
        self.assertEqual(0, code, error)
        result = json.loads(output)
        self.assertEqual("status", result["command"])
        self.assertTrue(result["actions"], result)
        self.assertEqual(before, self.snapshot())

    def test_default_sync_imports_and_second_run_is_noop(self):
        self.adopt()
        (self.output / "card.txt").write_bytes(b"live edit\n")
        code, output, error = self.invoke()
        self.assertEqual(0, code, output + error)
        self.assertEqual(b"live edit\n", (self.data / "card.txt").read_bytes())
        code, output, error = self.invoke("sync", "--json")
        self.assertEqual(0, code, error)
        self.assertFalse(json.loads(output)["changed"])

    def test_adopt_use_source_applies_only_to_explicit_migration_cards(self):
        (self.data / "card.txt").rename(self.data / "recipe.txt")
        (self.data / "recipe.txt").write_bytes(b"corrected source\n")
        (self.output / "card.txt").write_bytes(b"<wc:retired/card>\n")
        (self.data / "other.txt").write_bytes(b"other source\n")
        (self.output / "other.txt").write_bytes(b"other live detail\n")
        (self.project / "word-salad.json").write_text(json.dumps({
            "version": 1,
            "output_map": {"recipe.txt": ["card.txt"]},
            "retired_paths": ["retired/card.txt"],
        }), encoding="utf-8")
        before = self.snapshot()
        code, _, _ = self.invoke("adopt")
        self.assertEqual(1, code)
        # A failed adoption may create its local publisher lock, never card data.
        after = self.snapshot()
        after.pop("project/.word-salad/publisher.lock", None)
        self.assertEqual(before, after)

        code, output, error = self.invoke("adopt", "--use-source", "card.txt")
        self.assertEqual(0, code, output + error)
        self.assertEqual(b"corrected source\n", (self.output / "card.txt").read_bytes())
        self.assertEqual(b"other source\n", (self.data / "other.txt").read_bytes())
        self.assertEqual(b"other live detail\n", (self.output / "other.txt").read_bytes())
        self.assertEqual(b"other live detail\n", (self.project / "_overrides" / "other.txt").read_bytes())

    def test_conflicts_return_nonzero_and_resolve_accepts_output(self):
        self.adopt()
        (self.data / "card.txt").write_bytes(b"source edit\n")
        (self.output / "card.txt").write_bytes(b"live edit\n")
        code, output, error = self.invoke("sync", "--json")
        self.assertEqual(1, code, error)
        self.assertTrue(json.loads(output)["conflicts"])
        code, output, error = self.invoke("resolve", "card.txt", "--use", "output")
        self.assertEqual(0, code, output + error)
        self.assertEqual(b"live edit\n", (self.output / "card.txt").read_bytes())

    def test_build_requires_explicit_destination(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            cli.main(["build"])
        self.assertEqual(2, error.exception.code)

    def test_build_rejects_nonempty_destination_and_preserves_it(self):
        self.adopt()
        destination = self.root / "populated"
        destination.mkdir()
        (destination / "personal.txt").write_bytes(b"irreplaceable\n")
        before = self.snapshot()
        code, _, _ = self.invoke("build", "--output", str(destination))
        self.assertEqual(1, code)
        self.assertEqual(before, self.snapshot())

    def test_build_cannot_target_live_output(self):
        self.adopt()
        before = self.snapshot()
        code, _, _ = self.invoke("build", "--output", str(self.output))
        self.assertEqual(1, code)
        self.assertEqual(before, self.snapshot())

    def test_build_into_new_directory_does_not_write_live_or_state(self):
        self.adopt()
        before = self.snapshot()
        destination = self.root / "stage"
        code, output, error = self.invoke("build", "--output", str(destination))
        self.assertEqual(0, code, output + error)
        self.assertEqual(b"original\n", (destination / "card.txt").read_bytes())
        after = {path: value for path, value in self.snapshot().items() if not path.startswith("stage/")}
        self.assertEqual(before, after)

    def test_build_with_pending_output_import_refuses_stale_export(self):
        self.adopt()
        (self.output / "card.txt").write_bytes(b"not imported yet\n")
        destination = self.root / "stage"
        before = self.snapshot()
        code, _, _ = self.invoke("build", "--output", str(destination))
        self.assertEqual(1, code)
        self.assertEqual(before, self.snapshot())

    def test_release_packages_overrides_and_retains_previous_archives(self):
        (self.output / "card.txt").write_bytes(b"live detail\r\nrepeated\r\nrepeated\r\n")
        self.adopt()
        releases = self.project / "releases"
        releases.mkdir()
        older = releases / "older.zip"
        older.write_bytes(b"older release")
        archive = releases / "test.zip"
        code, output, error = self.invoke("release", "--archive", str(archive), "--json")
        self.assertEqual(0, code, output + error)
        with zipfile.ZipFile(archive) as bundle:
            self.assertEqual(["Wildcards/card.txt"], bundle.namelist())
            self.assertEqual((self.output / "card.txt").read_bytes(), bundle.read("Wildcards/card.txt"))
        self.assertEqual(b"older release", older.read_bytes())
        self.assertEqual(str(archive.resolve()), json.loads(output)["archive"])

    def test_release_refuses_existing_archive(self):
        self.adopt()
        archive = self.root / "existing.zip"
        archive.write_bytes(b"preserve me")
        before = self.snapshot()
        code, _, _ = self.invoke("release", "--archive", str(archive))
        self.assertEqual(1, code)
        self.assertEqual(before, self.snapshot())

    def test_watch_waits_for_stable_signature_and_reports_unchanged_conflict_once(self):
        manager = Mock()
        manager.sync.return_value = {"actions": [], "conflicts": [{"path": "card.txt", "kind": "changed"}], "changed": False}
        signatures = [("partial",), ("finished",), ("finished",), ("finished",), ("finished",)]
        with patch.object(cli, "_signature", side_effect=signatures), \
                patch.object(cli.time, "sleep", side_effect=[None, None, None, None, KeyboardInterrupt]), \
                redirect_stdout(io.StringIO()) as output:
            code = cli._watch(manager, (self.data, self.output), 2, True)
        self.assertEqual(0, code)
        manager.sync.assert_called_once()
        self.assertEqual(1, len(output.getvalue().splitlines()))

    def test_watch_invalid_interval_is_rejected(self):
        for interval in ("0", "-1", "nan", "inf"):
            with self.subTest(interval=interval), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                cli.main(["watch", "--interval", interval])
            self.assertEqual(2, error.exception.code)


if __name__ == "__main__":
    unittest.main()
