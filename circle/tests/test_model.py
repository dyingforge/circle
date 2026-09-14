"""Unit tests for the domain model and the dependency graph."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import support  # noqa: F401  (puts scripts/ on sys.path)
import document
import graph
import model
from errors import CircleError


def issue_dict(issue_id="CIR-ABCDEFGHIJ", title="标题", state="draft", blocked_by=(), **extra):
    issue = {
        "id": issue_id,
        "title": title,
        "state": state,
        "assignee": None,
        "blocked_by": list(blocked_by),
        "revision": 1,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "goal": "目标。",
        "expected_behavior": "预期行为。",
        "boundaries": "无。",
        "acceptance": [{"text": "可以验收", "done": False}],
        "comments": "",
    }
    issue.update(extra)
    return issue


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def build_store(self, issues, *, agent_body="项目介绍。", agent_name="Demo"):
        store = model.store_path(self.root)
        (store / model.ISSUES_DIR).mkdir(parents=True)
        document.write_document(
            store / model.AGENT_DOC,
            [("name", agent_name), ("created_at", "2026-01-01T00:00:00+00:00")],
            agent_body,
        )
        document.write_text(store / model.ARCHITECTURE_DOC, "# Architecture\n\n结构。")
        document.write_text(store / model.DOMAIN_DOC, "# Domain\n\n概念。")
        for issue in issues:
            model.write_issue(self.root, issue)
        return store

    def read_issue(self, issue_id):
        return model.load_issue(model.issue_path(self.root, issue_id))


class NormalizationTest(unittest.TestCase):
    def test_normalize_text_strips_and_rejects(self):
        self.assertEqual("value", model.normalize_text("  value  ", "field"))
        for bad in ("", "   ", None, 7, []):
            with self.subTest(bad=bad):
                with self.assertRaises(CircleError):
                    model.normalize_text(bad, "field")

    def test_optional_text_allows_none_only(self):
        self.assertIsNone(model.optional_text(None, "assignee"))
        self.assertEqual("A", model.optional_text("  A ", "assignee"))
        with self.assertRaises(CircleError):
            model.optional_text("  ", "assignee")

    def test_normalize_blockers_requires_ids(self):
        self.assertEqual([], model.normalize_blockers(None))
        self.assertEqual(["CIR-ABCDEFGHIJ"], model.normalize_blockers(["CIR-ABCDEFGHIJ"]))
        for bad in ("not-a-list", ["short"], ["CIR-abcdefghij"]):
            with self.subTest(bad=bad):
                with self.assertRaises(CircleError):
                    model.normalize_blockers(bad)
        with self.assertRaises(CircleError) as caught:
            model.normalize_blockers(["CIR-ABCDEFGHIJ", "CIR-ABCDEFGHIJ"])
        self.assertIn("duplicate", str(caught.exception))

    def test_normalize_acceptance_accepts_strings_and_objects(self):
        self.assertEqual(
            [{"text": "a", "done": False}, {"text": "b", "done": True}],
            model.normalize_acceptance(["a", {"text": "b", "done": True}], "acceptance"),
        )
        for bad in (None, [], "not-a-list", [7], [{"text": "a", "extra": 1}], [{"done": True}]):
            with self.subTest(bad=bad):
                with self.assertRaises(CircleError):
                    model.normalize_acceptance(bad, "acceptance")

    def test_generate_id_shape_and_uniqueness(self):
        existing = {"CIR-ABCDEFGHIJ"}
        generated = model.generate_id(existing)
        self.assertFalse(generated in existing)
        self.assertEqual(14, len(generated))
        self.assertTrue(generated.startswith("CIR-"))
        self.assertEqual(generated, model.validate_id(generated))

    def test_validate_id_rejects_bad_shapes(self):
        for bad in ("CIR-ABC", "CIR-ABCDEFGHI", "CIR-abcdefghij", "XIR-ABCDEFGHIJ", 7, None):
            with self.subTest(bad=bad):
                with self.assertRaises(CircleError):
                    model.validate_id(bad)


class IssueDocumentTest(StoreTestCase):
    def test_write_then_load_round_trips_content(self):
        original = issue_dict(
            goal="多行\n目标。",
            expected_behavior='带 "引号" 的行为。',
            boundaries="- 不做 A\n- 不做 B",
            acceptance=[{"text": "第一项", "done": True}, {"text": "第二项", "done": False}],
            comments="- 2026-01-01: 备注",
        )
        self.build_store([original])
        loaded = self.read_issue("CIR-ABCDEFGHIJ")
        for field in model.FRONT_MATTER_FIELDS + model.CONTENT_FIELDS + ("comments",):
            self.assertEqual(original[field], loaded[field], field)

    def test_acceptance_checkbox_state_is_parsed(self):
        self.build_store([issue_dict(acceptance=[{"text": "done", "done": True}, {"text": "todo", "done": False}])])
        loaded = self.read_issue("CIR-ABCDEFGHIJ")
        self.assertEqual(
            [{"text": "done", "done": True}, {"text": "todo", "done": False}],
            loaded["acceptance"],
        )

    def test_load_assigns_normalised_values(self):
        """A hand-written padded value must be normalised on read, not just validated."""
        store = self.build_store([issue_dict()])
        path = store / model.ISSUES_DIR / "CIR-ABCDEFGHIJ.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace('"标题"', '"  标题  "'), encoding="utf-8"
        )
        self.assertEqual("标题", model.load_issue(path)["title"])

    def test_rejects_unknown_and_missing_front_matter_fields(self):
        store = self.build_store([issue_dict()])
        path = store / model.ISSUES_DIR / "CIR-ABCDEFGHIJ.md"
        original = path.read_text(encoding="utf-8")

        path.write_text(
            original.replace("assignee: null", 'assignee: null\nestimate: "2 days"'),
            encoding="utf-8",
        )
        with self.assertRaises(CircleError) as caught:
            model.load_issue(path)
        self.assertIn("unknown issue fields", str(caught.exception))

        path.write_text(original.replace('state: "draft"\n', ""), encoding="utf-8")
        with self.assertRaises(CircleError) as caught:
            model.load_issue(path)
        self.assertIn("missing issue fields", str(caught.exception))

    def test_rejects_invalid_state_and_revision(self):
        store = self.build_store([issue_dict()])
        path = store / model.ISSUES_DIR / "CIR-ABCDEFGHIJ.md"
        original = path.read_text(encoding="utf-8")

        path.write_text(original.replace('"draft"', '"shipped"'), encoding="utf-8")
        with self.assertRaises(CircleError) as caught:
            model.load_issue(path)
        self.assertIn("invalid state", str(caught.exception))

        path.write_text(original.replace("revision: 1", "revision: 0"), encoding="utf-8")
        with self.assertRaises(CircleError) as caught:
            model.load_issue(path)
        self.assertIn("invalid revision", str(caught.exception))

    def test_rejects_section_problems(self):
        store = self.build_store([issue_dict()])
        path = store / model.ISSUES_DIR / "CIR-ABCDEFGHIJ.md"
        original = path.read_text(encoding="utf-8")
        cases = [
            ("unknown section", "unknown issue sections",
             original.replace("## Boundaries", "## Extras")),
            ("missing section", "missing section '## Boundaries'",
             original.replace("## Boundaries\n\n无。\n\n", "")),
            ("empty section", "must not be empty",
             original.replace("## Boundaries\n\n无。", "## Boundaries\n\n")),
            ("stray acceptance text", "invalid acceptance criterion",
             original.replace("- [ ] 可以验收", "just text")),
            ("uncheckboxed acceptance", "invalid acceptance criterion",
             original.replace("- [ ] 可以验收", "- [ ] ")),
            ("no acceptance items", "acceptance criteria must not be empty",
             original.replace("- [ ] 可以验收\n", "")),
        ]
        for label, message, text in cases:
            with self.subTest(label=label):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(CircleError) as caught:
                    model.load_issue(path)
                self.assertIn(message, str(caught.exception))

    def test_issue_filename_must_match_id(self):
        store = self.build_store([issue_dict()])
        path = store / model.ISSUES_DIR / "CIR-ABCDEFGHIJ.md"
        path.rename(store / model.ISSUES_DIR / "CIR-ZZZZZZZZZZ.md")
        with self.assertRaises(CircleError) as caught:
            model.load_issues(store)
        self.assertIn("does not match ID", str(caught.exception))

    def test_stray_non_markdown_files_are_ignored(self):
        store = self.build_store([issue_dict()])
        (store / model.ISSUES_DIR / "notes.txt").write_text("not an issue", encoding="utf-8")
        self.assertEqual({"CIR-ABCDEFGHIJ"}, set(model.load_issues(store)))


class ProjectDocumentTest(StoreTestCase):
    def test_load_store_returns_project_and_issues(self):
        store = self.build_store([issue_dict(agent_name="  Demo  ")])
        project, issues = model.load_store(store)
        self.assertEqual("Demo", project["name"])
        self.assertEqual({"CIR-ABCDEFGHIJ"}, set(issues))

    def test_missing_supporting_document_is_rejected(self):
        store = self.build_store([])
        (store / model.DOMAIN_DOC).unlink()
        with self.assertRaises(CircleError) as caught:
            model.load_store(store)
        self.assertIn("missing required document", str(caught.exception))

    def test_agent_front_matter_is_exact(self):
        store = self.build_store([])
        document.write_document(
            store / model.AGENT_DOC,
            [("name", "Demo"), ("created_at", "x"), ("extra", "y")],
            "body",
        )
        with self.assertRaises(CircleError) as caught:
            model.load_store(store)
        self.assertIn("exactly name and created_at", str(caught.exception))

    def test_empty_agent_body_is_rejected(self):
        store = self.build_store([], agent_body="")
        with self.assertRaises(CircleError) as caught:
            model.load_store(store)
        self.assertIn("body must not be empty", str(caught.exception))

    def test_missing_store_is_reported(self):
        with self.assertRaises(CircleError) as caught:
            model.require_store(self.root)
        self.assertIn("no Circle project found", str(caught.exception))


class GraphPredicateTest(unittest.TestCase):
    def test_unfinished_blockers_and_actionable(self):
        issues = {
            "CIR-AAAAAAAAAA": issue_dict("CIR-AAAAAAAAAA", state="done"),
            "CIR-BBBBBBBBBB": issue_dict("CIR-BBBBBBBBBB"),
            "CIR-CCCCCCCCCC": issue_dict("CIR-CCCCCCCCCC", state="ready", blocked_by=["CIR-AAAAAAAAAA"]),
            "CIR-DDDDDDDDDD": issue_dict("CIR-DDDDDDDDDD", state="ready", blocked_by=["CIR-BBBBBBBBBB"]),
            "CIR-EEEEEEEEEE": issue_dict("CIR-EEEEEEEEEE", blocked_by=["CIR-AAAAAAAAAA"]),
        }
        ready_blocked = issues["CIR-DDDDDDDDDD"]
        self.assertEqual(["CIR-BBBBBBBBBB"], graph.unfinished_blockers(ready_blocked, issues))
        self.assertFalse(graph.is_unblocked(ready_blocked, issues))
        self.assertTrue(graph.is_unblocked(issues["CIR-CCCCCCCCCC"], issues))
        self.assertEqual({"CIR-CCCCCCCCCC"}, graph.actionable_ids(issues))

    def test_cancelled_blocker_still_blocks(self):
        issues = {
            "CIR-AAAAAAAAAA": issue_dict("CIR-AAAAAAAAAA", state="cancelled"),
            "CIR-BBBBBBBBBB": issue_dict("CIR-BBBBBBBBBB", state="ready", blocked_by=["CIR-AAAAAAAAAA"]),
        }
        self.assertFalse(graph.is_unblocked(issues["CIR-BBBBBBBBBB"], issues))


class ValidateGraphTest(unittest.TestCase):
    def test_rejects_structural_problems(self):
        cases = {
            "unknown blocker": {
                "CIR-AAAAAAAAAA": issue_dict("CIR-AAAAAAAAAA", blocked_by=["CIR-ZZZZZZZZZZ"]),
            },
            "self dependency": {
                "CIR-AAAAAAAAAA": issue_dict("CIR-AAAAAAAAAA", blocked_by=["CIR-AAAAAAAAAA"]),
            },
            "unfinished blockers": {
                "CIR-AAAAAAAAAA": issue_dict("CIR-AAAAAAAAAA", state="draft"),
                "CIR-BBBBBBBBBB": issue_dict("CIR-BBBBBBBBBB", state="in_progress", blocked_by=["CIR-AAAAAAAAAA"]),
            },
            "dependency cycle": {
                "CIR-AAAAAAAAAA": issue_dict("CIR-AAAAAAAAAA", blocked_by=["CIR-BBBBBBBBBB"]),
                "CIR-BBBBBBBBBB": issue_dict("CIR-BBBBBBBBBB", blocked_by=["CIR-AAAAAAAAAA"]),
            },
        }
        for message, issues in cases.items():
            with self.subTest(message=message):
                with self.assertRaises(CircleError) as caught:
                    model.validate_graph(issues)
                self.assertIn(message, str(caught.exception))

    def test_accepts_a_valid_graph(self):
        model.validate_graph({
            "CIR-AAAAAAAAAA": issue_dict("CIR-AAAAAAAAAA", state="done"),
            "CIR-BBBBBBBBBB": issue_dict("CIR-BBBBBBBBBB", state="done", blocked_by=["CIR-AAAAAAAAAA"]),
            "CIR-CCCCCCCCCC": issue_dict("CIR-CCCCCCCCCC", "ready", blocked_by=["CIR-BBBBBBBBBB"]),
        })


class DeepGraphTest(unittest.TestCase):
    count = 20000

    def chain(self, cyclic: bool):
        ids = [f"CIR-{index:010d}" for index in range(self.count)]
        issues = {}
        for index in reversed(range(self.count)):
            if index:
                blockers = [ids[index - 1]]
            else:
                blockers = [ids[-1]] if cyclic else []
            issues[ids[index]] = issue_dict(ids[index], blocked_by=blockers)
        return issues

    def test_long_acyclic_chain_does_not_overflow(self):
        graph.assert_acyclic(self.chain(cyclic=False))

    def test_long_cycle_is_detected_without_overflow(self):
        with self.assertRaises(CircleError) as caught:
            graph.assert_acyclic(self.chain(cyclic=True))
        self.assertIn("dependency cycle", str(caught.exception))


class IssueCreationTest(StoreTestCase):
    def existing(self, *titles):
        return {
            f"CIR-{index:010d}": issue_dict(f"CIR-{index:010d}", title)
            for index, title in enumerate(titles)
        }

    def test_normalize_new_issue_defaults_to_draft(self):
        issue = model.normalize_new_issue(
            {
                "title": "新的",
                "goal": "g",
                "expected_behavior": "e",
                "boundaries": "b",
                "acceptance": ["a"],
            },
            {},
        )
        self.assertEqual("draft", issue["state"])
        self.assertEqual(1, issue["revision"])
        self.assertEqual([], issue["blocked_by"])
        self.assertIsNone(issue["assignee"])
        self.assertEqual("", issue["comments"])

    def test_normalize_new_issue_rejects_unknown_fields_and_duplicate_titles(self):
        with self.assertRaises(CircleError) as caught:
            model.normalize_new_issue({"title": "x", "estimate": "1 day", "goal": "g",
                                       "expected_behavior": "e", "boundaries": "b",
                                       "acceptance": ["a"]}, {})
        self.assertIn("unknown issue fields", str(caught.exception))

        with self.assertRaises(CircleError) as caught:
            model.normalize_new_issue({"title": "SAME", "goal": "g", "expected_behavior": "e",
                                       "boundaries": "b", "acceptance": ["a"]},
                                      self.existing("same"))
        self.assertIn("duplicate issue title", str(caught.exception))

    def test_normalize_new_issue_rejects_unsupported_initial_state(self):
        with self.assertRaises(CircleError) as caught:
            model.normalize_new_issue({"title": "x", "state": "in_progress", "goal": "g",
                                       "expected_behavior": "e", "boundaries": "b",
                                       "acceptance": ["a"]}, {})
        self.assertIn("must be draft or ready", str(caught.exception))

    def test_batch_resolves_keys_and_existing_ids(self):
        existing = self.existing("已有")
        existing_id = next(iter(existing))
        built, key_map = model.normalize_issue_batch(
            [
                {"key": "first", "title": "第一个", "goal": "g", "expected_behavior": "e",
                 "boundaries": "b", "acceptance": ["a"]},
                {"key": "second", "title": "第二个", "goal": "g", "expected_behavior": "e",
                 "boundaries": "b", "acceptance": ["a"], "blocked_by": ["first", existing_id]},
            ],
            existing,
        )
        second = built[1]
        self.assertEqual(sorted([key_map["first"], existing_id]), sorted(second["blocked_by"]))
        self.assertNotIn("key", second)

    def test_batch_rejects_bad_references_and_duplicates(self):
        existing = self.existing("已有")
        valid = {"title": "新", "goal": "g", "expected_behavior": "e", "boundaries": "b",
                 "acceptance": ["a"]}
        cases = {
            "unknown blocker": [dict(valid, key="k", blocked_by=["CIR-ZZZZZZZZZZ"])],
            "duplicate issue key": [dict(valid, key="k"), dict(valid, key="k", title="别的")],
            "duplicate issue title": [dict(valid, key="k", title="已有")],
            "at least one issue is required": [],
        }
        for message, batch in cases.items():
            with self.subTest(message=message):
                with self.assertRaises(CircleError) as caught:
                    model.normalize_issue_batch(batch, existing)
                self.assertIn(message, str(caught.exception))

        duplicate_blockers = [dict(valid, key="k", blocked_by=["k", "k"])]
        with self.assertRaises(CircleError):
            model.normalize_issue_batch(duplicate_blockers, existing)


if __name__ == "__main__":
    unittest.main()
