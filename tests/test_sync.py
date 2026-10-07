"""Exercise preservation and reconciliation through the public sync API.

Every fixture uses a temporary destination. These tests never touch ../Wildcards.
"""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from word_salad.sync import SyncError, Synchronizer


class SynchronizerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "_data"
        self.output = self.root / "Wildcards"
        self.state = self.root / "state"
        self.overrides = self.root / "overrides"
        self.config = self.root / "word-salad.json"
        self.data.mkdir()
        self.output.mkdir()

    @staticmethod
    def write(root, relative, content):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        return path

    @staticmethod
    def snapshot(root):
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        } if root.exists() else {}

    def manager(self):
        return Synchronizer(
            data_root=self.data,
            output_root=self.output,
            state_root=self.state,
            overrides_root=self.overrides,
            config_path=self.config if self.config.exists() else None,
        )

    def configure(self, **options):
        self.config.write_text(json.dumps({"version": 1, **options}), encoding="utf-8")

    def baseline(self, sources=None, outputs=None):
        sources = {"card.txt": "original\n"} if sources is None else sources
        outputs = sources if outputs is None else outputs
        for path, content in sources.items():
            self.write(self.data, path, content)
        for path, content in outputs.items():
            self.write(self.output, path, content)
        manager = self.manager()
        result = manager.adopt()
        self.assertFalse(result["conflicts"], result)
        return manager

    def assert_clean(self, result):
        self.assertFalse(result["conflicts"], result)

    def assert_conflict(self, result, path):
        self.assertTrue(result["conflicts"], result)
        self.assertIn(path, [item["path"] for item in result["conflicts"]])

    def assert_no_conflict_markers(self):
        for path, content in self.snapshot(self.output).items():
            self.assertNotIn(b"<<<<<<<", content, path)
            self.assertNotIn(b">>>>>>>", content, path)
            self.assertNotIn(b"$file_missing:", content, path)
            self.assertNotIn(b"$file_error:", content, path)

    def test_existing_output_requires_explicit_adoption(self):
        self.write(self.data, "card.txt", "source version\n")
        self.write(self.output, "card.txt", "irreplaceable output\n")
        before = self.snapshot(self.data), self.snapshot(self.output)

        with self.assertRaises(SyncError) as error:
            self.manager().sync()

        self.assertIn("adopt", str(error.exception).lower())
        self.assertEqual(before, (self.snapshot(self.data), self.snapshot(self.output)))

    def test_adoption_preserves_different_live_bytes_and_source(self):
        source = b"source-only detail\n"
        live = b"live-only detail\r\nrepeated\r\nrepeated\r\n"
        self.write(self.data, "card.txt", source)
        self.write(self.output, "card.txt", live)

        result = self.manager().adopt()

        self.assert_clean(result)
        self.assertEqual(source, (self.data / "card.txt").read_bytes())
        self.assertEqual(live, (self.output / "card.txt").read_bytes())
        self.assertEqual(live, (self.overrides / "card.txt").read_bytes())
        self.assert_clean(self.manager().sync())
        self.assertEqual(live, (self.output / "card.txt").read_bytes())

    def test_adoption_imports_unknown_files_without_losing_duplicates_or_order(self):
        live = b"zebra\r\nalpaca\r\nzebra\r\n"
        self.write(self.output, "new/nested-card.txt", live)

        result = self.manager().adopt()

        self.assert_clean(result)
        self.assertEqual(live, (self.data / "new/nested-card.txt").read_bytes())
        self.assertEqual(live, (self.output / "new/nested-card.txt").read_bytes())
        self.assert_clean(self.manager().sync())
        self.assertEqual(live, (self.output / "new/nested-card.txt").read_bytes())

    def test_adoption_publishes_source_only_cards(self):
        self.write(self.data, "source-only.txt", "unpublished\n")

        result = self.manager().adopt()

        self.assert_clean(result)
        self.assertEqual(b"unpublished\n", (self.output / "source-only.txt").read_bytes())

    def test_repeated_sync_is_a_noop_including_after_reopening_manager(self):
        manager = self.baseline()
        before = self.snapshot(self.data), self.snapshot(self.output), self.snapshot(self.overrides)

        first = manager.sync()
        second = self.manager().sync()

        self.assert_clean(first)
        self.assert_clean(second)
        self.assertFalse(first["changed"], first)
        self.assertFalse(second["changed"], second)
        self.assertEqual(before, (self.snapshot(self.data), self.snapshot(self.output), self.snapshot(self.overrides)))

    def test_status_previews_output_import_without_writing(self):
        self.baseline()
        self.write(self.output, "card.txt", "edited live\n")
        before = tuple(self.snapshot(path) for path in (self.data, self.output, self.overrides, self.state))

        result = self.manager().status()

        self.assert_clean(result)
        self.assertTrue(result["actions"], result)
        self.assertEqual(before, tuple(self.snapshot(path) for path in (self.data, self.output, self.overrides, self.state)))

    def test_source_only_edit_publishes(self):
        self.baseline()
        updated = "new source line\n<random:red, blue>\n"
        self.write(self.data, "card.txt", updated)

        result = self.manager().sync()

        self.assert_clean(result)
        self.assertTrue(result["changed"], result)
        self.assertEqual(updated.encode(), (self.output / "card.txt").read_bytes())
        self.assertFalse(self.manager().sync()["changed"])

    def test_direct_output_edit_imports_to_source(self):
        self.baseline()
        updated = b"edited directly\n<random:one,two> <extension:anything>\n"
        self.write(self.output, "card.txt", updated)

        result = self.manager().sync()

        self.assert_clean(result)
        self.assertEqual(updated, (self.data / "card.txt").read_bytes())
        self.assertEqual(updated, (self.output / "card.txt").read_bytes())
        self.assertFalse((self.overrides / "card.txt").exists())
        self.assertFalse(self.manager().sync()["changed"])

    def test_concurrent_edits_preserve_both_versions(self):
        self.baseline()
        self.write(self.data, "card.txt", "source edit\n")
        self.write(self.output, "card.txt", "live edit\n")
        before = self.snapshot(self.data), self.snapshot(self.output)

        result = self.manager().sync()

        self.assert_conflict(result, "card.txt")
        self.assertEqual(before, (self.snapshot(self.data), self.snapshot(self.output)))
        self.assert_conflict(self.manager().sync(), "card.txt")
        self.assertEqual(before, (self.snapshot(self.data), self.snapshot(self.output)))
        self.assert_no_conflict_markers()

    def derived_baseline(self):
        return self.baseline(
            {"leaf.txt": "red\nblue\n", "combo.txt": "<file:leaf> coat\n"},
            {"leaf.txt": "red\nblue\n", "combo.txt": "red coat\nblue coat\n"},
        )

    def test_derived_edit_creates_override_without_editing_recipe_or_leaf(self):
        self.derived_baseline()
        original_sources = self.snapshot(self.data)
        edited = b"red silk coat\nblue coat\n"
        self.write(self.output, "combo.txt", edited)

        result = self.manager().sync()

        self.assert_clean(result)
        self.assertEqual(original_sources, self.snapshot(self.data))
        self.assertEqual(edited, (self.overrides / "combo.txt").read_bytes())
        self.assertEqual(edited, (self.output / "combo.txt").read_bytes())
        self.assertEqual(b"red\nblue\n", (self.output / "leaf.txt").read_bytes())
        self.assertFalse(self.manager().sync()["changed"])

    def test_direct_leaf_output_import_rebuilds_dependents(self):
        self.derived_baseline()
        self.write(self.output, "leaf.txt", "green\n")

        result = self.manager().sync()

        self.assert_clean(result)
        self.assertEqual(b"green\n", (self.data / "leaf.txt").read_bytes())
        self.assertEqual(b"green\n", (self.output / "leaf.txt").read_bytes())
        self.assertEqual(b"green coat\n", (self.output / "combo.txt").read_bytes())
        self.assertFalse((self.overrides / "combo.txt").exists())

    def test_later_derived_output_edit_updates_override(self):
        self.derived_baseline()
        self.write(self.output, "combo.txt", "first customization\n")
        self.assert_clean(self.manager().sync())
        self.write(self.output, "combo.txt", "second customization\n")

        result = self.manager().sync()

        self.assert_clean(result)
        self.assertEqual(b"second customization\n", (self.overrides / "combo.txt").read_bytes())
        self.assertEqual(b"second customization\n", (self.output / "combo.txt").read_bytes())
        self.assertEqual(b"<file:leaf> coat\n", (self.data / "combo.txt").read_bytes())

    def test_editing_override_text_publishes_it(self):
        self.derived_baseline()
        self.write(self.output, "combo.txt", "first customization\n")
        self.assert_clean(self.manager().sync())
        self.write(self.overrides, "combo.txt", "authored in override\n")

        result = self.manager().sync()

        self.assert_clean(result)
        self.assertEqual(b"authored in override\n", (self.output / "combo.txt").read_bytes())
        self.assertEqual(b"<file:leaf> coat\n", (self.data / "combo.txt").read_bytes())
        self.assertFalse(self.manager().sync()["changed"])

    def test_concurrent_override_and_output_edits_preserve_both(self):
        self.derived_baseline()
        self.write(self.output, "combo.txt", "first customization\n")
        self.assert_clean(self.manager().sync())
        self.write(self.overrides, "combo.txt", "changed override\n")
        self.write(self.output, "combo.txt", "changed output\n")

        result = self.manager().sync()

        self.assert_conflict(result, "combo.txt")
        self.assertEqual(b"changed override\n", (self.overrides / "combo.txt").read_bytes())
        self.assertEqual(b"changed output\n", (self.output / "combo.txt").read_bytes())
        self.assert_no_conflict_markers()

    def test_recipe_change_with_active_override_is_visible_conflict(self):
        self.derived_baseline()
        self.write(self.output, "combo.txt", "custom coat\n")
        self.assert_clean(self.manager().sync())
        self.write(self.data, "combo.txt", "<file:leaf> jacket\n")

        result = self.manager().sync()

        self.assert_conflict(result, "combo.txt")
        self.assertEqual(b"custom coat\n", (self.output / "combo.txt").read_bytes())
        self.assertEqual(b"custom coat\n", (self.overrides / "combo.txt").read_bytes())
        self.assertEqual(b"<file:leaf> jacket\n", (self.data / "combo.txt").read_bytes())

    def test_changed_shared_dependency_conflicts_with_active_override(self):
        self.derived_baseline()
        self.write(self.output, "combo.txt", "custom coat\n")
        self.assert_clean(self.manager().sync())
        self.write(self.data, "leaf.txt", "green\n")

        result = self.manager().sync()

        self.assert_conflict(result, "combo.txt")
        self.assertEqual(b"custom coat\n", (self.output / "combo.txt").read_bytes())
        self.assertEqual(b"green\n", (self.data / "leaf.txt").read_bytes())

    def test_missing_source_is_conflict_without_deleting_output(self):
        self.baseline()
        (self.data / "card.txt").unlink()

        result = self.manager().sync()

        self.assert_conflict(result, "card.txt")
        self.assertFalse((self.data / "card.txt").exists())
        self.assertEqual(b"original\n", (self.output / "card.txt").read_bytes())

    def test_missing_output_is_conflict_without_deleting_source(self):
        self.baseline()
        (self.output / "card.txt").unlink()

        result = self.manager().sync()

        self.assert_conflict(result, "card.txt")
        self.assertFalse((self.output / "card.txt").exists())
        self.assertEqual(b"original\n", (self.data / "card.txt").read_bytes())

    def test_new_output_after_adoption_is_imported(self):
        self.baseline()
        added = b"new first\nnew second\n"
        self.write(self.output, "nested/new.txt", added)

        result = self.manager().sync()

        self.assert_clean(result)
        self.assertEqual(added, (self.data / "nested/new.txt").read_bytes())
        self.assertEqual(added, (self.output / "nested/new.txt").read_bytes())
        self.assertFalse(self.manager().sync()["changed"])

    def test_import_retains_unsanitized_public_filename_on_repeat_sync(self):
        self.baseline()
        name = "photographers/Jérôme Sessini.txt"
        self.write(self.output, name, "preserve this public name\n")
        expected = self.snapshot(self.output)

        result = self.manager().sync()

        self.assert_clean(result)
        self.assertEqual(expected, self.snapshot(self.output))
        self.assertFalse(self.manager().sync()["changed"])
        self.assertEqual(expected, self.snapshot(self.output))

    def test_new_source_after_adoption_is_published(self):
        self.baseline()
        added = b"new source\n"
        self.write(self.data, "nested/new.txt", added)

        result = self.manager().sync()

        self.assert_clean(result)
        self.assertEqual(added, (self.output / "nested/new.txt").read_bytes())
        self.assertFalse(self.manager().sync()["changed"])

    def test_non_roundtrippable_output_edit_is_preserved_exactly(self):
        self.baseline()
        edited = b"  second  \r\nfirst\r\nsecond\r\nsecond\r\n"
        self.write(self.output, "card.txt", edited)

        result = self.manager().sync()

        self.assert_clean(result)
        self.assertEqual(edited, (self.output / "card.txt").read_bytes())
        authoring_versions = [(self.data / "card.txt").read_bytes()]
        if (self.overrides / "card.txt").exists():
            authoring_versions.append((self.overrides / "card.txt").read_bytes())
        self.assertIn(edited, authoring_versions)
        self.assertFalse(self.manager().sync()["changed"])
        self.assertEqual(edited, (self.output / "card.txt").read_bytes())

    def test_unmanaged_non_card_files_are_never_removed(self):
        opaque = b"\x00\xff\x7fbackup information"
        self.write(self.output, "notes.bin", opaque)
        self.baseline()
        self.write(self.data, "card.txt", "changed source\n")

        result = self.manager().sync()

        self.assert_clean(result)
        self.assertEqual(opaque, (self.output / "notes.bin").read_bytes())
        self.assertFalse((self.data / "notes.bin").exists())

    def test_filename_collision_prevents_source_and_output_writes(self):
        self.configure(output_map={"one.txt": ["Card.txt"], "two.txt": ["card.txt"]})
        self.write(self.data, "one.txt", "first\n")
        self.write(self.data, "two.txt", "second\n")
        self.write(self.output, "unmanaged.txt", "precious\n")
        before = self.snapshot(self.data), self.snapshot(self.output)

        with self.assertRaises(SyncError):
            self.manager().adopt()

        self.assertEqual(before, (self.snapshot(self.data), self.snapshot(self.output)))
        self.assert_no_conflict_markers()

    def test_case_collision_between_source_and_import_fails_before_staging(self):
        self.write(self.data, "Card.txt", "source version\n")
        self.write(self.output, "card.txt", "live version\n")
        before = self.snapshot(self.data), self.snapshot(self.output)

        with self.assertRaises(SyncError):
            self.manager().adopt()

        self.assertEqual(before, (self.snapshot(self.data), self.snapshot(self.output)))
        self.assertFalse((self.state / "pending.json").exists())
        self.assertFalse((self.state / "manifest.json").exists())

    def test_retirement_cannot_discard_an_edited_override(self):
        self.derived_baseline()
        self.write(self.output, "combo.txt", "first customization\n")
        self.assert_clean(self.manager().sync())
        self.write(self.overrides, "combo.txt", "new unpublished override\n")
        (self.data / "combo.txt").unlink()
        self.configure(retired_paths=["combo.txt"])
        before = tuple(self.snapshot(path) for path in (self.data, self.output, self.overrides))

        result = self.manager().sync()

        self.assert_conflict(result, "combo.txt")
        self.assertEqual(before, tuple(self.snapshot(path) for path in (self.data, self.output, self.overrides)))

    def test_retirement_removes_only_explicit_unchanged_owned_output(self):
        self.baseline({"retired.txt": "retire me\n", "kept.txt": "keep me\n"})
        (self.data / "retired.txt").unlink()
        self.configure(retired_paths=["retired.txt"])
        self.write(self.output, "unowned.txt", "external new data\n")

        result = self.manager().sync()

        self.assert_clean(result)
        self.assertFalse((self.output / "retired.txt").exists())
        self.assertEqual(b"keep me\n", (self.output / "kept.txt").read_bytes())
        self.assertEqual(b"external new data\n", (self.output / "unowned.txt").read_bytes())
        self.assertEqual(b"external new data\n", (self.data / "unowned.txt").read_bytes())
        self.assertFalse(self.manager().sync()["changed"])

    def test_retired_path_without_ownership_is_preserved_as_conflict(self):
        self.baseline()
        self.configure(retired_paths=["unowned.txt"])
        self.write(self.output, "unowned.txt", "external data must survive\n")
        before = self.snapshot(self.data), self.snapshot(self.output)

        result = self.manager().sync()

        self.assert_conflict(result, "unowned.txt")
        self.assertEqual(before, (self.snapshot(self.data), self.snapshot(self.output)))

    def test_missing_include_prevents_other_pending_publication(self):
        self.baseline()
        self.write(self.data, "card.txt", "pending source edit\n")
        self.write(self.data, "broken.txt", "<file:missing>\n")
        before = self.snapshot(self.data), self.snapshot(self.output)

        with self.assertRaises(SyncError):
            self.manager().sync()

        self.assertEqual(before, (self.snapshot(self.data), self.snapshot(self.output)))
        self.assertFalse((self.output / "broken.txt").exists())
        self.assert_no_conflict_markers()

    def test_explicit_source_resolution_publishes_source_version(self):
        self.baseline()
        self.write(self.data, "card.txt", "source choice\n")
        self.write(self.output, "card.txt", "live choice\n")
        self.assert_conflict(self.manager().sync(), "card.txt")

        result = self.manager().resolve("card.txt", "source")

        self.assert_clean(result)
        self.assertEqual(b"source choice\n", (self.data / "card.txt").read_bytes())
        self.assertEqual(b"source choice\n", (self.output / "card.txt").read_bytes())
        self.assertFalse(self.manager().sync()["changed"])

    def test_explicit_output_resolution_retains_live_version(self):
        self.baseline()
        self.write(self.data, "card.txt", "source choice\n")
        self.write(self.output, "card.txt", "live choice\n")
        self.assert_conflict(self.manager().sync(), "card.txt")

        result = self.manager().resolve("card.txt", "output")

        self.assert_clean(result)
        self.assertEqual(b"live choice\n", (self.output / "card.txt").read_bytes())
        self.assertFalse(self.manager().sync()["changed"])

    def test_explicit_source_resolution_restores_recipe_control(self):
        self.derived_baseline()
        self.write(self.output, "combo.txt", "custom coat\n")
        self.assert_clean(self.manager().sync())
        self.write(self.data, "combo.txt", "<file:leaf> jacket\n")
        self.assert_conflict(self.manager().sync(), "combo.txt")

        result = self.manager().resolve("combo.txt", "source")

        self.assert_clean(result)
        self.assertEqual(b"red jacket\nblue jacket\n", (self.output / "combo.txt").read_bytes())
        self.assertFalse(self.manager().sync()["changed"])

    def test_aliases_preserve_public_paths_and_one_alias_edit_does_not_change_shared_source(self):
        self.configure(output_map={"card.txt": ["public-one.txt", "nested/public-two.txt"]})
        self.baseline(
            {"card.txt": "shared\n"},
            {"public-one.txt": "shared\n", "nested/public-two.txt": "shared\n"},
        )
        self.write(self.output, "public-one.txt", "one customized alias\n")

        result = self.manager().sync()

        self.assert_clean(result)
        self.assertEqual(b"shared\n", (self.data / "card.txt").read_bytes())
        self.assertEqual(b"shared\n", (self.output / "nested/public-two.txt").read_bytes())
        self.assertEqual(b"one customized alias\n", (self.overrides / "public-one.txt").read_bytes())
        self.assertFalse((self.output / "card.txt").exists())

    def test_mapping_collision_prevents_writes(self):
        self.configure(output_map={"one.txt": ["same.txt"], "two.txt": ["same.txt"]})
        self.write(self.data, "one.txt", "one\n")
        self.write(self.data, "two.txt", "two\n")
        before = self.snapshot(self.data), self.snapshot(self.output)

        with self.assertRaises(SyncError):
            self.manager().adopt()

        self.assertEqual(before, (self.snapshot(self.data), self.snapshot(self.output)))

    def test_resolve_rejects_invalid_choice_and_path_traversal(self):
        self.baseline()
        before = self.snapshot(self.data), self.snapshot(self.output)

        for path, choice in (("card.txt", "both"), ("../outside.txt", "source")):
            with self.subTest(path=path, choice=choice):
                with self.assertRaises(SyncError):
                    self.manager().resolve(path, choice)

        self.assertEqual(before, (self.snapshot(self.data), self.snapshot(self.output)))

    def test_pending_transaction_blocks_another_sync(self):
        self.baseline()
        self.write(self.state, "pending.json", "{}\n")
        self.write(self.data, "card.txt", "unpublished change\n")
        before = self.snapshot(self.data), self.snapshot(self.output)

        with self.assertRaises(SyncError) as error:
            self.manager().sync()

        self.assertIn("recover", str(error.exception).lower())
        self.assertEqual(before, (self.snapshot(self.data), self.snapshot(self.output)))
        self.assertTrue((self.state / "pending.json").exists())

    def test_malformed_pending_transaction_cannot_change_files(self):
        self.baseline()
        self.write(self.state, "pending.json", "{malformed")
        before = self.snapshot(self.data), self.snapshot(self.output)

        with self.assertRaises(SyncError):
            self.manager().recover()

        self.assertEqual(before, (self.snapshot(self.data), self.snapshot(self.output)))
        self.assertEqual(b"{malformed", (self.state / "pending.json").read_bytes())

    def interrupt_publication(self):
        manager = self.baseline({"one.txt": "old one\n", "two.txt": "old two\n"})
        self.write(self.data, "one.txt", "new one\n")
        self.write(self.data, "two.txt", "new two\n")
        write_atomically = manager._atomic_write

        def interrupt_at_second_output(path, content, **kwargs):
            if path.resolve() == (self.output / "two.txt").resolve():
                raise OSError("simulated interruption after first published file")
            return write_atomically(path, content, **kwargs)

        with mock.patch.object(manager, "_atomic_write", side_effect=interrupt_at_second_output):
            with self.assertRaises((OSError, SyncError)):
                manager.sync()

        self.assertTrue((self.state / "pending.json").exists())
        self.assertEqual(b"new one\n", (self.output / "one.txt").read_bytes())
        self.assertEqual(b"old two\n", (self.output / "two.txt").read_bytes())

    def test_interrupted_publication_recovers_and_next_sync_is_noop(self):
        self.interrupt_publication()

        with self.assertRaises(SyncError):
            self.manager().sync()
        result = self.manager().recover()

        self.assert_clean(result)
        self.assertTrue(result["changed"])
        self.assertFalse((self.state / "pending.json").exists())
        self.assertEqual(b"new one\n", (self.output / "one.txt").read_bytes())
        self.assertEqual(b"new two\n", (self.output / "two.txt").read_bytes())
        self.assertFalse(self.manager().sync()["changed"])
        self.assertFalse(self.manager().recover()["changed"])

    def test_recovery_preserves_third_party_edit_after_interruption(self):
        self.interrupt_publication()
        self.write(self.output, "two.txt", "edited while interrupted\n")
        before = self.snapshot(self.data), self.snapshot(self.output)

        with self.assertRaises(SyncError):
            self.manager().recover()

        self.assertEqual(before, (self.snapshot(self.data), self.snapshot(self.output)))
        self.assertTrue((self.state / "pending.json").exists())
        self.assert_no_conflict_markers()

    def test_external_save_before_atomic_replace_is_preserved(self):
        manager = self.baseline()
        self.write(self.data, "card.txt", "source change to publish\n")
        write_atomically = manager._atomic_write
        injected = False

        def atomic_write_with_concurrent_save(path, content, **kwargs):
            nonlocal injected
            if path.resolve() == (self.output / "card.txt").resolve() and not injected:
                self.assertIn("expected", kwargs)
                self.write(self.output, "card.txt", "external save during publication\n")
                injected = True
            return write_atomically(path, content, **kwargs)

        with mock.patch.object(manager, "_atomic_write", side_effect=atomic_write_with_concurrent_save):
            with self.assertRaises(SyncError):
                manager.sync()

        self.assertTrue(injected)
        self.assertEqual({"card.txt": b"external save during publication\n"}, self.snapshot(self.output))
        self.assertEqual(b"source change to publish\n", (self.data / "card.txt").read_bytes())
        self.assertTrue((self.state / "pending.json").exists())
        with self.assertRaises(SyncError):
            self.manager().recover()
        self.assertEqual(b"external save during publication\n", (self.output / "card.txt").read_bytes())

        # After the user preserves the competing edit and restores the old version,
        # the recorded transaction can finish without rebuilding a new baseline.
        self.write(self.output, "card.txt", "original\n")
        self.assert_clean(self.manager().recover())
        self.assertEqual(b"source change to publish\n", (self.output / "card.txt").read_bytes())
        self.assertFalse((self.state / "pending.json").exists())
        self.assertFalse(self.manager().sync()["changed"])

    def test_recovery_rejects_path_traversal_in_transaction_identifier(self):
        self.interrupt_publication()
        journal_path = self.state / "pending.json"
        journal = json.loads(journal_path.read_bytes())
        journal["id"] = "../escaped-transaction"
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
        before = self.snapshot(self.root)

        with self.assertRaises(SyncError):
            self.manager().recover()

        self.assertEqual(before, self.snapshot(self.root))
        self.assertTrue(journal_path.exists())

    def test_export_uses_active_override_and_preserves_managed_trees(self):
        self.derived_baseline()
        edited = b"customized\r\ncustomized\r\n"
        self.write(self.output, "combo.txt", edited)
        self.assert_clean(self.manager().sync())
        before = tuple(self.snapshot(path) for path in (self.data, self.output, self.overrides))
        destination = self.root / "export"

        result = self.manager().export(destination)

        self.assert_clean(result)
        self.assertEqual(self.snapshot(self.output), self.snapshot(destination))
        self.assertEqual(before, tuple(self.snapshot(path) for path in (self.data, self.output, self.overrides)))

    def test_export_preserves_destination_file_created_during_compilation(self):
        manager = self.baseline()
        destination = self.root / "export"
        destination.mkdir()
        original_compile = manager._compile
        injected = False

        def compile_with_external_destination_write(*args, **kwargs):
            nonlocal injected
            result = original_compile(*args, **kwargs)
            if not injected:
                self.write(destination, "card.txt", "precious concurrent destination data\n")
                injected = True
            return result

        with mock.patch.object(manager, "_compile", side_effect=compile_with_external_destination_write):
            with self.assertRaises(SyncError):
                manager.export(destination)

        self.assertTrue(injected)
        self.assertEqual(
            {"card.txt": b"precious concurrent destination data\n"},
            self.snapshot(destination),
        )
        self.assertEqual(b"original\n", (self.output / "card.txt").read_bytes())

    def test_export_rechecks_live_output_after_initial_clean_status(self):
        manager = self.baseline()
        destination = self.root / "export"
        original_status = manager.status
        injected = False

        def status_with_subsequent_live_edit(*args, **kwargs):
            nonlocal injected
            result = original_status(*args, **kwargs)
            if not injected:
                self.assert_clean(result)
                self.assertFalse(result["changed"])
                self.write(self.output, "card.txt", "edited after clean status\n")
                injected = True
            return result

        with mock.patch.object(manager, "status", side_effect=status_with_subsequent_live_edit):
            with self.assertRaises(SyncError):
                manager.export(destination)

        self.assertTrue(injected)
        self.assertEqual({}, self.snapshot(destination))
        self.assertEqual(b"edited after clean status\n", (self.output / "card.txt").read_bytes())
        self.assertEqual(b"original\n", (self.data / "card.txt").read_bytes())

    def test_export_cannot_clobber_destination_created_at_final_publication(self):
        manager = self.baseline()
        destination = self.root / "export"
        original_link = os.link
        injected = False

        def link_with_concurrent_creator(source, target, *args, **kwargs):
            nonlocal injected
            if Path(target).resolve() == (destination / "card.txt").resolve():
                self.write(destination, "card.txt", "created at publication\n")
                injected = True
            return original_link(source, target, *args, **kwargs)

        with mock.patch("word_salad.sync.os.link", side_effect=link_with_concurrent_creator):
            with self.assertRaises(SyncError):
                manager.export(destination)

        self.assertTrue(injected)
        self.assertEqual({"card.txt": b"created at publication\n"}, self.snapshot(destination))


if __name__ == "__main__":
    unittest.main()
