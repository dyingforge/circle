"""Unit tests for the Markdown document codec."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import support  # noqa: F401  (puts scripts/ on sys.path)
import document
from errors import CircleError


class FrontMatterTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def round_trip(self, fields, body="正文内容"):
        path = self.root / "doc.md"
        document.write_document(path, fields, body)
        return document.parse_document(path)

    def test_round_trips_awkward_scalar_values(self):
        fields = [
            ("plain", "text"),
            ("quoted", 'he said "hi"'),
            ("multiline", "first\nsecond"),
            ("chinese", "中文与标点：，。"),
            ("colon", "key: value"),
            ("empty_list", []),
            ("list", ["CIR-ABCDEFGHIJ"]),
            ("null", None),
            ("number", 7),
            ("boolean", True),
        ]
        parsed, body = self.round_trip(fields)
        self.assertEqual(dict(fields), parsed)
        self.assertEqual("正文内容", body)

    def test_body_with_sections_survives_round_trip(self):
        body = "## Goal\n\n目标。\n\n## Acceptance Criteria\n\n- [ ] 一项"
        _, parsed_body = self.round_trip([("id", "x")], body)
        self.assertEqual(body, parsed_body)

    def test_rejects_malformed_documents(self):
        cases = {
            "missing front matter": "no front matter here\n",
            "unterminated front matter": "---\nid: x\nbody\n",
            "invalid front-matter line": "---\nnot a pair\n---\n\nbody\n",
            "invalid front-matter value": "---\nid: {broken\n---\n\nbody\n",
        }
        for message, text in cases.items():
            with self.subTest(message=message):
                path = self.root / "bad.md"
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(CircleError) as caught:
                    document.parse_document(path)
                self.assertIn(message, str(caught.exception))

    def test_missing_file_reports_the_path(self):
        with self.assertRaises(CircleError) as caught:
            document.parse_document(self.root / "absent.md")
        self.assertIn("missing file", str(caught.exception))


class SectionTest(unittest.TestCase):
    path = Path("/tmp/issue.md")

    def test_parses_sections_in_any_order(self):
        body = "## One\n\na\n\n## Two\n\nb\nc\n"
        self.assertEqual({"One": "a", "Two": "b\nc"}, document.parse_sections(body, self.path))

    def test_empty_body_has_no_sections(self):
        self.assertEqual({}, document.parse_sections("", self.path))

    def test_rejects_duplicate_heading(self):
        with self.assertRaises(CircleError) as caught:
            document.parse_sections("## One\n\na\n\n## One\n\nb\n", self.path)
        self.assertIn("duplicate section '## One'", str(caught.exception))

    def test_rejects_content_before_the_first_heading(self):
        with self.assertRaises(CircleError) as caught:
            document.parse_sections("stray text\n\n## One\n\na\n", self.path)
        self.assertIn("content before the first section", str(caught.exception))

    def test_section_may_be_empty(self):
        self.assertEqual({"One": "", "Two": "b"}, document.parse_sections("## One\n\n## Two\n\nb\n", self.path))


class AtomicWriteTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def test_creates_parents_and_leaves_no_temp_files(self):
        target = self.root / "nested" / "deeper" / "out.md"
        document.atomic_write(target, "content\n")
        self.assertEqual("content\n", target.read_text(encoding="utf-8"))
        self.assertEqual(["out.md"], [item.name for item in target.parent.iterdir()])

    def test_overwrites_existing_content(self):
        target = self.root / "out.md"
        document.atomic_write(target, "first\n")
        document.atomic_write(target, "second\n")
        self.assertEqual("second\n", target.read_text(encoding="utf-8"))

    def test_write_text_normalises_the_trailing_newline(self):
        target = self.root / "out.md"
        document.write_text(target, "no newline")
        self.assertEqual("no newline\n", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
