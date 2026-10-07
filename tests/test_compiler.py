"""Compiler regressions: legacy semantics, explicit includes, and safe names."""

from pathlib import Path
import tempfile
import unittest

from word_salad.compiler import CompileError, compile_tree


class CompilerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        return path

    def test_normalizes_before_expansion_and_preserves_expanded_duplicates(self):
        self.write("item.txt", "  apple  \r\n\r\npear\r\napple\r\n")
        self.write("recipe.txt", " <file:item> \n<file:item>\napple\n")
        cards = compile_tree(self.root)
        self.assertEqual(cards["item.txt"].content, b"apple\npear\n")
        self.assertEqual(cards["recipe.txt"].content, b"apple\npear\napple\n")
        self.assertFalse(cards["item.txt"].direct)

    def test_continuations_and_final_backslash_collapse_match_legacy_behavior(self):
        self.write("item.txt", " left\\\n right\npath\\\\name\ntail\\")
        self.assertEqual(compile_tree(self.root)["item.txt"].content, b"left right\npath\\name\ntail\n")

    def test_lone_trailing_continuation_and_empty_source_keep_legacy_newline(self):
        self.write("empty.txt", "")
        self.write("slash.txt", "\\")
        self.assertEqual(compile_tree(self.root)["empty.txt"].content, b"\n")
        self.assertEqual(compile_tree(self.root)["slash.txt"].content, b"\n")

    def test_expansion_preserves_affix_whitespace_and_runtime_tags(self):
        self.write("item.txt", "apple\npear\n")
        self.write("recipe.txt", "a  <file:item>, <wcrandom[0-1]:<wc:clothes/_body>>\n")
        self.assertEqual(
            compile_tree(self.root)["recipe.txt"].content,
            b"a  apple, <wcrandom[0-1]:<wc:clothes/_body>>\n"
            b"a  pear, <wcrandom[0-1]:<wc:clothes/_body>>\n",
        )

    def test_new_and_legacy_include_spellings_match(self):
        self.write("items/tasty.txt", "apple\npear\n")
        forms = (
            "<file:items/tasty>",
            "<file:items/tasty.txt>",
            "$file:[items/tasty]",
            "$file:[_data/items/tasty]",
            "$file:[_data/items/tasty.txt]",
        )
        for index, form in enumerate(forms):
            self.write(f"recipe{index}.txt", f"a {form}!\n")
        cards = compile_tree(self.root)
        for index in range(len(forms)):
            with self.subTest(form=forms[index]):
                self.assertEqual(cards[f"recipe{index}.txt"].content, b"a apple!\na pear!\n")

    def test_explicit_relative_paths_follow_each_containing_source(self):
        self.write("item.txt", "root\n")
        self.write("folder/item.txt", "sibling\n")
        self.write("folder/recipe.txt", "<file:./item>\n<file:../item>\n")
        self.write("outer.txt", "<file:folder/recipe>\n")
        cards = compile_tree(self.root)
        self.assertEqual(cards["outer.txt"].content, b"sibling\nroot\n")
        self.assertEqual(
            cards["outer.txt"].dependencies,
            ("folder/item.txt", "folder/recipe.txt", "item.txt", "outer.txt"),
        )
        self.assertTrue(cards["outer.txt"].derived)

    def test_bare_paths_always_use_the_data_root(self):
        self.write("item.txt", "root\n")
        self.write("folder/item.txt", "sibling\n")
        self.write("folder/recipe.txt", "<file:item>\n")
        self.assertEqual(compile_tree(self.root)["folder/recipe.txt"].content, b"root\n")

    def test_unicode_spaces_and_punctuation_are_not_sanitized(self):
        self.write("Artists/Émile Z. (photo).txt", "example\n")
        self.write("recipe.txt", "<file:Artists/Émile Z. (photo)>\n")
        cards = compile_tree(self.root)
        self.assertIn("Artists/Émile Z. (photo).txt", cards)
        self.assertEqual(cards["recipe.txt"].content, b"example\n")

    def test_unknown_runtime_tags_remain_opaque(self):
        value = "<wc:missing> <random:a|b> <repeat[2]:x> <future:some/tag>\n"
        self.write("runtime.txt", value)
        card = compile_tree(self.root)["runtime.txt"]
        self.assertEqual(card.content, value.encode())
        self.assertTrue(card.direct)
        self.assertFalse(card.derived)
        self.assertEqual(card.dependencies, ("runtime.txt",))

    def test_escaped_include_tokens_are_literal_without_a_dependency(self):
        self.write("literal.txt", "\\<file:missing> and \\$file:[missing]\n")
        card = compile_tree(self.root)["literal.txt"]
        self.assertEqual(card.content, b"<file:missing> and $file:[missing]\n")
        self.assertFalse(card.derived)
        self.assertFalse(card.direct)
        self.assertEqual(card.dependencies, ("literal.txt",))

    def test_escape_backslash_parity_and_literals_in_affixes(self):
        self.write("item.txt", "apple\n")
        self.write("two.txt", "\\\\<file:item>\n")
        self.write("three.txt", "\\\\\\<file:missing>\n")
        self.write("affix.txt", "\\<file:literal> <file:item> \\$file:[literal]\n")
        cards = compile_tree(self.root)
        self.assertEqual(cards["two.txt"].content, b"\\apple\n")
        self.assertEqual(cards["three.txt"].content, b"\\<file:missing>\n")
        self.assertEqual(cards["affix.txt"].content, b"<file:literal> apple $file:[literal]\n")

    def test_included_literal_tokens_are_not_reparsed(self):
        self.write("literal.txt", "\\<file:missing>\n")
        self.write("recipe.txt", "prefix <file:literal> suffix\n")
        self.assertEqual(compile_tree(self.root)["recipe.txt"].content, b"prefix <file:missing> suffix\n")

    def test_empty_include_removes_its_entry_not_its_affixes_as_an_entry(self):
        self.write("empty.txt", "\n\n")
        self.write("recipe.txt", "prefix <file:empty> suffix\nsurvivor\n")
        self.assertEqual(compile_tree(self.root)["recipe.txt"].content, b"survivor\n")

    def test_duplicate_includes_on_separate_lines_only_dedupe_before_expansion(self):
        self.write("item.txt", "apple\n")
        self.write("recipe.txt", "<file:item>\n$file:[item]\n")
        self.assertEqual(compile_tree(self.root)["recipe.txt"].content, b"apple\napple\n")

    def test_multiple_includes_on_one_logical_line_fail(self):
        self.write("item.txt", "apple\n")
        self.write("recipe.txt", "<file:item> \\\n$file:[item]\n")
        with self.assertRaisesRegex(CompileError, "recipe.txt:1: only one"):
            compile_tree(self.root)

    def test_missing_include_fails_with_source_location(self):
        self.write("recipe.txt", "ordinary\n<file:missing>\n")
        with self.assertRaisesRegex(CompileError, "recipe.txt:2: missing include"):
            compile_tree(self.root)

    def test_cycles_report_the_include_chain(self):
        self.write("a.txt", "<file:b>\n")
        self.write("b.txt", "<file:c>\n")
        self.write("c.txt", "<file:a>\n")
        with self.assertRaisesRegex(CompileError, "a.txt -> b.txt -> c.txt -> a.txt"):
            compile_tree(self.root)

    def test_dynamic_and_unsafe_include_paths_fail(self):
        for token in (
            "<file:<wc:choice>>",
            "<file:*.txt>",
            "<file:../outside>",
            "<file:/etc/passwd>",
            "<file:a/../../outside>",
            "<file:folder\\item>",
            "<file:>",
            "<file: item>",
            "$file:[{choice}]",
        ):
            with self.subTest(token=token):
                self.write("recipe.txt", token + "\n")
                with self.assertRaises(CompileError):
                    compile_tree(self.root)

    def test_unterminated_includes_fail_but_escaped_literals_are_allowed(self):
        for token in ("<file:item", "$file:[item"):
            self.write("recipe.txt", token + "\n")
            with self.assertRaisesRegex(CompileError, "unterminated"):
                compile_tree(self.root)
            self.write("recipe.txt", "\\" + token + "\n")
            self.assertEqual(compile_tree(self.root)["recipe.txt"].content, (token + "\n").encode())

    def test_explicit_aliases_replace_default_and_share_provenance(self):
        self.write("source.txt", "apple\n")
        cards = compile_tree(self.root, output_map={"source.txt": ["public.txt", "old/alias.txt"]})
        self.assertEqual(set(cards), {"public.txt", "old/alias.txt"})
        self.assertIs(cards["public.txt"], cards["old/alias.txt"])
        self.assertEqual(cards["public.txt"].source, "source.txt")

    def test_empty_alias_list_keeps_include_only_source(self):
        self.write("item.txt", "apple\n")
        self.write("recipe.txt", "<file:item>\n")
        cards = compile_tree(self.root, output_map={"item.txt": []})
        self.assertEqual(set(cards), {"recipe.txt"})
        self.assertEqual(cards["recipe.txt"].content, b"apple\n")

    def test_unknown_map_keys_and_non_list_values_fail(self):
        self.write("item.txt", "apple\n")
        for mapping in ({"missing.txt": ["public.txt"]}, {"item.txt": "public.txt"}):
            with self.subTest(mapping=mapping):
                with self.assertRaises(CompileError):
                    compile_tree(self.root, output_map=mapping)

    def test_output_collisions_include_case_unicode_and_file_directory_conflicts(self):
        self.write("a.txt", "apple\n")
        self.write("b.txt", "banana\n")
        for names in (
            ("same.txt", "same.txt"),
            ("CARD.txt", "card.txt"),
            ("caf\u00e9.txt", "cafe\u0301.txt"),
            ("card.txt", "card.txt/nested.txt"),
        ):
            with self.subTest(names=names):
                with self.assertRaisesRegex(CompileError, "output collision"):
                    compile_tree(self.root, output_map={"a.txt": [names[0]], "b.txt": [names[1]]})

    def test_colliding_aliases_of_same_source_are_rejected(self):
        self.write("item.txt", "apple\n")
        with self.assertRaisesRegex(CompileError, "output collision"):
            compile_tree(self.root, output_map={"item.txt": ["Item.txt", "item.txt"]})

    def test_unsafe_output_aliases_fail(self):
        self.write("item.txt", "apple\n")
        for name in ("../escape.txt", "/tmp/escape.txt", "./alias.txt", "a//b.txt", "x.json"):
            with self.subTest(name=name):
                with self.assertRaises(CompileError):
                    compile_tree(self.root, output_map={"item.txt": [name]})

    def test_symlink_sources_and_directories_are_rejected(self):
        self.write("item.txt", "apple\n")
        link = self.root / "link.txt"
        link.symlink_to(self.root / "item.txt")
        with self.assertRaisesRegex(CompileError, "symlinks"):
            compile_tree(self.root)
        link.unlink()
        (self.root / "linked-dir").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(CompileError, "symlinks"):
            compile_tree(self.root)

    def test_invalid_utf8_fails_instead_of_replacing_data(self):
        self.write("broken.txt", b"\xff\n")
        with self.assertRaisesRegex(CompileError, "not valid UTF-8"):
            compile_tree(self.root)

    def test_direct_only_when_raw_bytes_match_output(self):
        self.write("canonical.txt", "apple\n")
        self.write("no-newline.txt", "apple")
        self.write("crlf.txt", b"apple\r\n")
        cards = compile_tree(self.root)
        self.assertTrue(cards["canonical.txt"].direct)
        self.assertFalse(cards["no-newline.txt"].direct)
        self.assertFalse(cards["crlf.txt"].direct)

    def test_compilation_does_not_modify_sources(self):
        content = b"  apple  \r\n\r\n"
        source = self.write("item.txt", content)
        compile_tree(self.root)
        self.assertEqual(source.read_bytes(), content)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["item.txt"])


if __name__ == "__main__":
    unittest.main()
