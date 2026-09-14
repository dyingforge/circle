"""End-to-end tests driving the CLI as a subprocess."""

from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import unittest

import support
from support import CircleTestCase, import_payload, make_issue


class InitializationTest(CircleTestCase):
    def test_commit_writes_documents_and_structured_issues(self):
        ids = self.preview_commit()
        self.assertEqual(
            {"AGENT.md", "ARCHITECTURE.md", "DOMAIN.md", "DAG.md", "issues"},
            {item.name for item in self.store.iterdir()},
        )
        agent = (self.store / "AGENT.md").read_text(encoding="utf-8")
        self.assertIn('name: "Demo"', agent)
        self.assertIn("三个 Issue 的验收项目。", agent)
        self.assertIn("先读 ARCHITECTURE.md", agent)
        self.assertIn("控制器 + 事实库。", (self.store / "ARCHITECTURE.md").read_text(encoding="utf-8"))
        self.assertIn("Goal / Acceptance", (self.store / "DOMAIN.md").read_text(encoding="utf-8"))

        text = (self.store / "issues" / f"{ids['model']}.md").read_text(encoding="utf-8")
        for heading in ("## Goal", "## Expected Behavior", "## Boundaries", "## Acceptance Criteria"):
            self.assertIn(heading, text)
        self.assertIn("- [ ] Model 可以通过验收", text)
        self.assertIn("blocked_by: []", text)
        self.assertIn('assignee: "A"', text)
        self.assertNotIn("estimate", text)
        self.circle("validate")

    def test_preview_leaves_the_project_untouched(self):
        preview = self.circle("preview", data=import_payload()).stdout
        self.assertIn("| ID | key | Issue | state | blocked_by | assignee |", preview)
        self.assertIn("**Goal**", preview)
        self.assertIn("Model 的预期行为。", preview)
        self.assertIn("- [ ] Model 可以通过验收", preview)
        self.assertIn("- `AGENT.md` (", preview)
        self.assertFalse(self.store.exists())

    def test_preview_refuses_an_initialised_project(self):
        self.preview_commit()
        self.assertIn("already exists", self.rejected("preview", data=import_payload()))

    def test_preview_requires_every_document(self):
        names = {"agent": "AGENT.md", "architecture": "ARCHITECTURE.md", "domain": "DOMAIN.md"}
        for field, filename in names.items():
            with self.subTest(field=field):
                payload = import_payload()
                del payload["docs"][field]
                stderr = self.rejected("preview", data=payload)
                self.assertIn("missing project documents", stderr)
                self.assertIn(filename, stderr)
                self.assertIn("ask the user", stderr)
                self.assertFalse(self.store.exists())

    def test_a_blank_document_counts_as_missing(self):
        payload = import_payload()
        payload["docs"]["domain"] = "   \n"
        self.assertIn("DOMAIN.md", self.rejected("preview", data=payload))
        self.assertFalse(self.store.exists())

    def test_preview_reports_inferences_warnings_and_accepts_placeholders(self):
        payload = import_payload()
        del payload["docs"]
        payload["inferences"] = ["项目名称来自一级标题"]
        payload["warnings"] = ["未提供负责人"]
        preview = self.circle("preview", "--allow-placeholder-docs", data=payload).stdout
        self.assertIn("项目名称来自一级标题", preview)
        self.assertIn("未提供负责人", preview)
        for field in ("agent", "architecture", "domain"):
            self.assertIn(f"docs.{field} 未提供", preview)

        digest = re.search(r"Snapshot: `([0-9a-f]{64})`", preview).group(1)
        self.circle("commit", "--snapshot", digest)
        domain = (self.store / "DOMAIN.md").read_text(encoding="utf-8")
        self.assertIn("TODO", domain)
        self.assertIn("circle:placeholder", domain)

    def test_commit_rejects_a_tampered_snapshot(self):
        preview = self.circle("preview", data=import_payload()).stdout
        digest = re.search(r"Snapshot: `([0-9a-f]{64})`", preview).group(1)
        snapshot_path = Path(tempfile.gettempdir()) / "circle-preview-snapshots" / f"{digest}.json"
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        snapshot["project"]["name"] = "Tampered"
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        try:
            self.assertIn("snapshot hash mismatch", self.rejected("commit", "--snapshot", digest))
            self.assertFalse(self.store.exists())
        finally:
            snapshot_path.unlink(missing_ok=True)

    def test_commit_rejects_unknown_snapshots_and_forged_roots(self):
        self.assertIn("invalid snapshot hash", self.rejected("commit", "--snapshot", "nope"))
        self.assertIn("missing or damaged", self.rejected("commit", "--snapshot", "0" * 64))

        preview = self.circle("preview", data=import_payload()).stdout
        digest = re.search(r"Snapshot: `([0-9a-f]{64})`", preview).group(1)
        with tempfile.TemporaryDirectory() as elsewhere:
            result = support.run_circle(Path(elsewhere), "commit", "--snapshot", digest)
            self.assertEqual(2, result.returncode)
            self.assertIn("different project root", result.stderr)


class ImportValidationTest(CircleTestCase):
    def test_rejects_structural_problems(self):
        """One case per validation family: reference resolution and the graph.

        The individual messages are asserted by the model unit tests; what this
        covers is that `preview` refuses the import and writes nothing."""
        cases = {
            "duplicate issue key": [make_issue("same", "One"), make_issue("same", "Two")],
            "dependency cycle": [
                make_issue("one", "One", blocked_by=["two"]),
                make_issue("two", "Two", blocked_by=["one"]),
            ],
        }
        for message, issues in cases.items():
            with self.subTest(message=message):
                payload = {"project": {"name": "Invalid"}, "issues": issues}
                self.assertIn(message, self.rejected("preview", data=payload))
                self.assertFalse(self.store.exists())

    def test_rejects_issues_missing_required_content(self):
        for field in ("goal", "expected_behavior", "boundaries", "acceptance"):
            with self.subTest(field=field):
                item = make_issue("one", "One")
                item[field] = [] if field == "acceptance" else ""
                payload = {"project": {"name": "Invalid"}, "issues": [item]}
                self.assertIn(field, self.rejected("preview", data=payload))
                self.assertFalse(self.store.exists())

    def test_rejects_unsupported_import_shapes(self):
        cases = {
            "requires a project object": {"issues": [make_issue("a", "A")]},
            "at least one issue": {"project": {"name": "N"}, "issues": []},
            "must be an object": {"project": {"name": "N"}, "issues": ["nope"]},
            "unknown fields on issue": {
                "project": {"name": "N"},
                "issues": [dict(make_issue("a", "A"), estimate="1 day")],
            },
            "unknown docs fields": {
                "project": {"name": "N"}, "docs": {"readme": "x"},
                "issues": [make_issue("a", "A")],
            },
            "must be draft or ready": {
                "project": {"name": "N"},
                "issues": [dict(make_issue("a", "A"), state="in_progress")],
            },
            "unknown import fields": {
                "project": {"name": "N"}, "issues": [make_issue("a", "A")], "extra": True,
            },
        }
        for message, payload in cases.items():
            with self.subTest(message=message):
                self.assertIn(message, self.rejected("preview", data=payload))


class LifecycleTest(CircleTestCase):
    def test_complete_flow_and_failure_guards(self):
        ids = self.preview_commit()
        model_id, dag_id, status_id = ids["model"], ids["dag"], ids["status"]
        self.circle("validate")

        self.transition(dag_id, "ready", 1)
        self.assertIn("unfinished blockers", self.transition(dag_id, "in_progress", 2, expect=2))

        revision = self.complete(model_id, 1)
        shown = json.loads(self.circle("issue-show", "--id", dag_id).stdout)
        self.assertTrue(shown["actionable"])
        self.assertEqual("DAG 的目标。", shown["goal"])
        self.assertEqual([{"text": "DAG 可以通过验收", "done": False}], shown["acceptance"])
        self.assertEqual("unblocked", shown["dependency_status"])

        revision = self.complete(dag_id, 2, ("in_progress", "review", "done"))
        self.complete(status_id, 1)

        completed = self.circle("status").stdout
        self.assertIn("Project: Demo (done)", completed)
        self.assertIn("done=3", completed)
        self.assertIn("Actionable: none", completed)

        self.assertIn("done is terminal", self.transition(model_id, "draft", revision, expect=2))
        for field, value in (("goal", "rewrite"), ("acceptance", ["other"]),
                             ("boundaries", "x"), ("title", "New")):
            with self.subTest(field=field):
                stderr = self.rejected(
                    "issue-edit", "--id", model_id, "--expected-revision", str(revision),
                    data={field: value},
                )
                self.assertIn("immutable", stderr)
        self.assertIn(
            "immutable",
            self.rejected("dependency-add", "--id", model_id, "--blocker", dag_id,
                          "--expected-revision", str(revision)),
        )

        edited = json.loads(self.circle(
            "issue-edit", "--id", model_id, "--expected-revision", str(revision),
            data={"assignee": "D"},
        ).stdout)
        self.assertEqual(revision + 1, edited["revision"])
        self.assertEqual("D", edited["assignee"])
        self.assertIn(
            "stale revision",
            self.rejected("issue-edit", "--id", model_id, "--expected-revision", str(revision),
                          data={"assignee": "E"}),
        )

    def test_invalid_transition_and_target_state_are_rejected(self):
        ids = self.preview_commit()
        self.assertIn(
            "invalid transition", self.transition(ids["model"], "in_progress", 1, expect=2)
        )
        self.assertIn("invalid target state", self.transition(ids["model"], "shipped", 1, expect=2))

    def test_cancelled_can_be_restored_and_keeps_blocking(self):
        ids = self.preview_commit()
        self.transition(ids["dag"], "ready", 1)
        self.transition(ids["model"], "cancelled", 1)

        shown = json.loads(self.circle("issue-show", "--id", ids["dag"]).stdout)
        self.assertEqual("blocked", shown["dependency_status"])
        self.assertFalse(shown["actionable"])

        restored = json.loads(self.transition(ids["model"], "draft", 2).stdout)
        self.assertEqual("draft", restored["state"])
        self.complete(ids["model"], 3)
        self.assertTrue(json.loads(self.circle("issue-show", "--id", ids["dag"]).stdout)["actionable"])

    def test_transition_note_appends_dated_entries(self):
        ids = self.preview_commit()
        first = json.loads(self.transition(ids["model"], "ready", 1, note="开始处理").stdout)
        self.assertIn("开始处理", first["comments"])
        second = json.loads(self.transition(ids["model"], "in_progress", 2, note="继续").stdout)
        self.assertIn("开始处理", second["comments"])
        self.assertIn("继续", second["comments"])
        self.assertEqual(2, second["comments"].count("- "))

        text = (self.store / "issues" / f"{ids['model']}.md").read_text(encoding="utf-8")
        self.assertIn("## Comments", text)
        self.circle("validate")

    def test_dependency_add_and_remove(self):
        ids = self.preview_commit()
        added = json.loads(self.circle(
            "issue-add", data={"title": "独立", "goal": "g", "expected_behavior": "e",
                               "boundaries": "b", "acceptance": ["a"], "state": "ready"},
        ).stdout)
        new_id = added["id"]
        self.assertEqual([new_id], added["newly_actionable"])

        self.circle("dependency-add", "--id", new_id, "--blocker", ids["model"],
                    "--expected-revision", "1")
        self.assertFalse(json.loads(self.circle("issue-show", "--id", new_id).stdout)["actionable"])
        self.assertIn("already exists", self.rejected(
            "dependency-add", "--id", new_id, "--blocker", ids["model"], "--expected-revision", "2"))
        self.circle("dependency-remove", "--id", new_id, "--blocker", ids["model"],
                    "--expected-revision", "2")
        self.assertIn("does not exist", self.rejected(
            "dependency-remove", "--id", new_id, "--blocker", ids["model"], "--expected-revision", "3"))

    def test_cycle_is_refused_without_a_partial_write(self):
        ids = self.preview_commit()
        first = json.loads(self.circle(
            "issue-add", data={"title": "First", "goal": "g", "expected_behavior": "e",
                               "boundaries": "b", "acceptance": ["a"]}).stdout)["id"]
        second = json.loads(self.circle(
            "issue-add", data={"title": "Second", "goal": "g", "expected_behavior": "e",
                               "boundaries": "b", "acceptance": ["a"], "blocked_by": [first]}).stdout)["id"]
        self.assertIn("dependency cycle", self.rejected(
            "dependency-add", "--id", first, "--blocker", second, "--expected-revision", "1"))
        shown = json.loads(self.circle("issue-show", "--id", first).stdout)
        self.assertEqual(1, shown["revision"])
        self.assertEqual([], shown["blocked_by"])
        self.assertEqual(3 + 2, len(list((self.store / "issues").glob("*.md"))))

    def test_issue_edit_supports_every_editable_field(self):
        ids = self.preview_commit()
        edited = json.loads(self.circle(
            "issue-edit", "--id", ids["model"], "--expected-revision", "1",
            data={
                "title": "改名",
                "goal": "新目标",
                "expected_behavior": "新行为",
                "boundaries": "新边界",
                "acceptance": [{"text": "已达成", "done": True}],
                "assignee": "Zed",
                "blocked_by": [],
            },
        ).stdout)
        self.assertEqual(2, edited["revision"])
        for field, expected in (("title", "改名"), ("goal", "新目标"),
                                ("expected_behavior", "新行为"), ("boundaries", "新边界"),
                                ("assignee", "Zed")):
            self.assertEqual(expected, edited[field])
        self.assertEqual([{"text": "已达成", "done": True}], edited["acceptance"])

        self.circle("validate")
        text = (self.store / "issues" / f"{ids['model']}.md").read_text(encoding="utf-8")
        self.assertIn("- [x] 已达成", text)


class AcceptanceTest(CircleTestCase):
    def three_items(self):
        """Commit a store whose single issue carries three acceptance criteria."""
        payload = import_payload()
        item = make_issue("model", "Model")
        item["acceptance"] = ["第一项", "第二项", "第三项"]
        payload["issues"] = [item]
        return self.preview_commit(payload)["model"]

    def acceptance(self, issue_id):
        shown = json.loads(self.circle("issue-show", "--id", issue_id).stdout)
        return [(item["text"], item["done"]) for item in shown["acceptance"]]

    def test_checking_one_item_leaves_the_others_alone(self):
        issue_id = self.three_items()
        revision = self.check_acceptance(issue_id, 1, 2)
        self.assertEqual(2, revision)
        self.assertEqual(
            [("第一项", False), ("第二项", True), ("第三项", False)], self.acceptance(issue_id)
        )
        text = (self.store / "issues" / f"{issue_id}.md").read_text(encoding="utf-8")
        self.assertEqual(["- [ ] 第一项", "- [x] 第二项", "- [ ] 第三项"],
                         [line for line in text.splitlines() if line.startswith("- [")])

    def test_several_items_can_be_checked_in_one_call(self):
        issue_id = self.three_items()
        self.check_acceptance(issue_id, 1, 1, 3)
        self.assertEqual(
            [("第一项", True), ("第二项", False), ("第三项", True)], self.acceptance(issue_id)
        )

    def test_uncheck_restores_a_single_item(self):
        issue_id = self.three_items()
        revision = self.check_acceptance(issue_id, 1, 1, 2, 3)
        unchecked = json.loads(self.circle(
            "acceptance-uncheck", "--id", issue_id, "--item", "2",
            "--expected-revision", str(revision)).stdout)
        self.assertEqual(3, unchecked["revision"])
        self.assertEqual(
            [("第一项", True), ("第二项", False), ("第三项", True)], self.acceptance(issue_id)
        )

    def test_rejects_bad_item_numbers(self):
        issue_id = self.three_items()
        for item in ("0", "-1", "4"):
            with self.subTest(item=item):
                stderr = self.rejected(
                    "acceptance-check", "--id", issue_id, "--item", item,
                    "--expected-revision", "1")
                self.assertIn("out of range", stderr)
        self.assertEqual(
            [("第一项", False), ("第二项", False), ("第三项", False)], self.acceptance(issue_id)
        )

    def test_rejects_a_state_that_is_already_settled(self):
        issue_id = self.three_items()
        revision = self.check_acceptance(issue_id, 1, 2)
        self.assertIn("already checked", self.rejected(
            "acceptance-check", "--id", issue_id, "--item", "2",
            "--expected-revision", str(revision)))
        self.assertIn("already unchecked", self.rejected(
            "acceptance-uncheck", "--id", issue_id, "--item", "1",
            "--expected-revision", str(revision)))
        self.assertEqual(2, json.loads(self.circle("issue-show", "--id", issue_id).stdout)["revision"])

    def test_requires_a_current_revision(self):
        issue_id = self.three_items()
        self.check_acceptance(issue_id, 1, 1)
        self.assertIn("stale revision", self.rejected(
            "acceptance-check", "--id", issue_id, "--item", "2", "--expected-revision", "1"))

    def test_done_requires_every_item_checked(self):
        issue_id = self.three_items()
        for state in ("ready", "in_progress", "review"):
            self.transition(issue_id, state, 1 + ("ready", "in_progress", "review").index(state))
        revision = 4
        self.check_acceptance(issue_id, revision, 2)
        revision += 1
        stderr = self.transition(issue_id, "done", revision, expect=2)
        self.assertIn("unchecked acceptance criteria", stderr)
        self.assertIn("第一项", stderr)
        self.assertIn("第三项", stderr)
        self.assertEqual("review", json.loads(
            self.circle("issue-show", "--id", issue_id).stdout)["state"])

        revision = self.check_acceptance(issue_id, revision, 1, 3)
        self.transition(issue_id, "done", revision)
        self.assertEqual("done", json.loads(
            self.circle("issue-show", "--id", issue_id).stdout)["state"])

    def test_a_done_issue_cannot_be_reticked(self):
        issue_id = self.three_items()
        self.complete(issue_id, 1)
        shown = json.loads(self.circle("issue-show", "--id", issue_id).stdout)
        self.assertIn("immutable", self.rejected(
            "acceptance-check", "--id", issue_id, "--item", "1",
            "--expected-revision", str(shown["revision"])))
        self.assertIn("immutable", self.rejected(
            "acceptance-uncheck", "--id", issue_id, "--item", "1",
            "--expected-revision", str(shown["revision"])))

    def test_issue_edit_still_replaces_the_whole_list(self):
        issue_id = self.three_items()
        edited = json.loads(self.circle(
            "issue-edit", "--id", issue_id, "--expected-revision", "1",
            data={"acceptance": ["只剩一项"]}).stdout)
        self.assertEqual([{"text": "只剩一项", "done": False}], edited["acceptance"])
        self.assertIn("out of range", self.rejected(
            "acceptance-check", "--id", issue_id, "--item", "2", "--expected-revision", "2"))


class MutationAtomicityTest(CircleTestCase):
    """Every refused mutation must leave the issue file byte for byte unchanged."""

    def assert_untouched(self, issue_id, command, *args, **kwargs):
        path = self.store / "issues" / f"{issue_id}.md"
        before = path.read_text(encoding="utf-8")
        self.rejected(command, *args, **kwargs)
        self.assertEqual(before, path.read_text(encoding="utf-8"))

    def test_refused_mutations_never_touch_the_issue_file(self):
        ids = self.preview_commit()
        model_id, dag_id = ids["model"], ids["dag"]
        self.transition(dag_id, "ready", 1)

        cases = {
            "stale revision": (
                "issue-edit", ["--id", model_id, "--expected-revision", "99"],
                {"assignee": "E"}),
            "blank content": (
                "issue-edit", ["--id", model_id, "--expected-revision", "1"],
                {"goal": "   "}),
            "unknown field": (
                "issue-edit", ["--id", model_id, "--expected-revision", "1"],
                {"estimate": "1 day"}),
            "invalid transition": (
                "issue-transition", ["--id", model_id, "--state", "in_progress",
                                     "--expected-revision", "1"], None),
            "unknown target state": (
                "issue-transition", ["--id", model_id, "--state", "shipped",
                                     "--expected-revision", "1"], None),
            "item out of range": (
                "acceptance-check", ["--id", model_id, "--item", "5",
                                     "--expected-revision", "1"], None),
            "cycle": (
                "dependency-add", ["--id", model_id, "--blocker", dag_id,
                                   "--expected-revision", "1"], None),
            "unknown blocker": (
                "dependency-add", ["--id", model_id, "--blocker", "CIR-NOTAREAL1",
                                   "--expected-revision", "1"], None),
        }
        for label, (command, args, data) in cases.items():
            with self.subTest(label=label):
                self.assert_untouched(model_id, command, *args, data=data)

    def test_a_refused_creation_writes_no_issue_file(self):
        self.preview_commit()
        issues = self.store / "issues"
        before = sorted(item.name for item in issues.iterdir())
        self.rejected("issue-add", data={"title": "坏", "goal": "g", "expected_behavior": "e",
                                        "boundaries": "b", "acceptance": []})
        self.assertEqual(before, sorted(item.name for item in issues.iterdir()))


class DocumentsTest(CircleTestCase):
    def placeholder_store(self):
        """A store whose three documents were never supplied."""
        payload = {"project": {"name": "Demo"}, "issues": [make_issue("model", "Model")]}
        return self.preview_commit(payload, allow_placeholders=True)

    def test_status_and_validate_report_placeholders(self):
        self.placeholder_store()
        status = self.circle("status").stdout
        for doc in ("AGENT.md", "ARCHITECTURE.md", "DOMAIN.md"):
            self.assertIn(f"{doc} placeholder", status)
        self.assertIn("Warning: placeholder documents", self.circle("validate").stdout)

    def test_a_complete_project_reports_no_placeholders(self):
        self.preview_commit()
        self.assertIn("Documents: AGENT.md ok, ARCHITECTURE.md ok, DOMAIN.md ok",
                      self.circle("status").stdout)
        self.assertNotIn("Warning", self.circle("validate").stdout)

    def test_legacy_placeholder_text_is_still_detected(self):
        self.placeholder_store()
        (self.store / "DOMAIN.md").write_text(
            "# Demo: Domain\n\nTODO: 补充Domain相关内容。\n", encoding="utf-8")
        self.assertIn("DOMAIN.md placeholder", self.circle("status").stdout)

    def test_docs_set_fills_each_document(self):
        self.placeholder_store()
        for doc, body in (("agent", "## 约定\n\n先读 DOMAIN.md。"),
                          ("architecture", "# 真实架构\n\n控制器 + 事实库。"),
                          ("domain", "# 真实领域\n\nGoal / Blocker。")):
            with self.subTest(doc=doc):
                result = self.circle("docs-set", "--doc", doc, stdin=body)
                self.assertIn("Updated", result.stdout)
        self.assertIn("Documents: AGENT.md ok, ARCHITECTURE.md ok, DOMAIN.md ok",
                      self.circle("status").stdout)
        self.assertIn("先读 DOMAIN.md。", (self.store / "AGENT.md").read_text(encoding="utf-8"))
        self.assertIn("真实架构", (self.store / "ARCHITECTURE.md").read_text(encoding="utf-8"))
        self.circle("validate")

    def test_docs_set_keeps_the_agent_front_matter(self):
        self.placeholder_store()
        before = (self.store / "AGENT.md").read_text(encoding="utf-8").splitlines()
        created = [line for line in before if line.startswith("created_at:")][0]
        self.circle("docs-set", "--doc", "agent", stdin="## 约定\n\n只用标准库。")
        after = (self.store / "AGENT.md").read_text(encoding="utf-8")
        self.assertIn('name: "Demo"', after)
        self.assertIn(created, after)
        self.assertIn("只用标准库。", after)
        self.assertNotIn("circle:placeholder", after)

    def test_docs_set_rejects_an_empty_body_and_an_unknown_document(self):
        self.placeholder_store()
        self.assertIn("must not be empty", self.rejected("docs-set", "--doc", "domain", stdin="  \n"))
        result = support.run_circle(self.root, "docs-set", "--doc", "nope")
        self.assertEqual(2, result.returncode)
        self.assertIn("invalid choice", result.stderr)


class AddIssuesAfterImportTest(CircleTestCase):
    def test_issue_import_appends_to_existing_store(self):
        ids = self.preview_commit()
        result = json.loads(self.circle("issue-import", data={
            "issues": [
                make_issue("docs", "Docs", blocked_by=[ids["model"]]),
                make_issue("metrics", "Metrics", blocked_by=["docs", ids["status"]]),
            ],
            "inferences": ["docs 依赖自 model"],
        }).stdout)
        self.assertEqual(["docs 依赖自 model"], result["inferences"])
        created = {item["title"]: item for item in result["created"]}
        metrics = created["Metrics"]
        self.assertEqual(
            sorted([created["Docs"]["id"], ids["status"]]), sorted(metrics["blocked_by"]),
        )
        self.assertEqual("blocked", metrics["dependency_status"])

        self.circle("validate")
        self.assertEqual(5, len(list((self.store / "issues").glob("*.md"))))
        self.assertEqual(
            1, json.loads(self.circle("issue-show", "--id", ids["model"]).stdout)["revision"],
        )

        loop = json.loads(self.circle("issue-import", data={
            "issues": [make_issue("loop", "Loop", blocked_by=[metrics["id"]])],
        }).stdout)["created"][0]["id"]
        self.assertIn("dependency cycle", self.rejected(
            "dependency-add", "--id", metrics["id"], "--blocker", loop, "--expected-revision", "1"))
        self.assertEqual(6, len(list((self.store / "issues").glob("*.md"))))

    def test_issue_import_is_atomic(self):
        self.preview_commit()
        self.assertIn("unknown blocker", self.rejected("issue-import", data={
            "issues": [make_issue("ok", "Ok"), make_issue("bad", "Bad", blocked_by=["CIR-NOTAREAL1"])],
        }))
        self.assertEqual(3, len(list((self.store / "issues").glob("*.md"))))

    def test_duplicate_titles_are_rejected_on_every_creation_path(self):
        self.preview_commit()
        single = {"title": "Model", "goal": "g", "expected_behavior": "e",
                  "boundaries": "b", "acceptance": ["a"]}
        self.assertIn("duplicate issue title", self.rejected("issue-add", data=single))
        self.assertIn("duplicate issue title", self.rejected(
            "issue-import", data={"issues": [make_issue("dup", "model")]}))
        self.assertIn("duplicate issue title", self.rejected(
            "issue-import", data={"issues": [make_issue("a", "Fresh"), make_issue("b", "fresh")]}))
        self.assertEqual(3, len(list((self.store / "issues").glob("*.md"))))


class InspectionTest(CircleTestCase):
    def test_issue_list_reports_dependency_state(self):
        ids = self.preview_commit()
        self.transition(ids["model"], "ready", 1)
        listing = self.circle("issue-list").stdout
        self.assertIn("| ID | state | dependency | actionable | title |", listing)
        self.assertIn(f"| {ids['model']} | ready | unblocked | yes | Model |", listing)
        self.assertIn(f"| {ids['dag']} | draft | blocked | no | DAG |", listing)

    def test_status_reports_counts_and_actionable(self):
        ids = self.preview_commit()
        self.transition(ids["model"], "ready", 1)
        status = self.circle("status").stdout
        self.assertIn("Project: Demo (active)", status)
        self.assertIn("Issues: 3", status)
        self.assertIn("draft=2", status)
        self.assertIn("ready=1", status)
        self.assertIn(f"Actionable: {ids['model']}", status)

    def test_issue_context_exposes_documents_and_issue(self):
        ids = self.preview_commit()
        output = self.circle("issue-context", "--id", ids["model"]).stdout
        for doc in ("AGENT.md", "ARCHITECTURE.md", "DOMAIN.md"):
            self.assertIn(f"===== .circle/{doc} =====", output)
        self.assertIn("先读 ARCHITECTURE.md", output)
        self.assertIn("控制器 + 事实库。", output)
        self.assertIn("Goal / Acceptance", output)
        self.assertIn(f"===== .circle/issues/{ids['model']}.md =====", output)
        self.assertIn("Model 的预期行为。", output)
        self.assertIn("- [ ] Model 可以通过验收", output)

    def test_render_is_never_implicit(self):
        ids = self.preview_commit()
        before = (self.store / "DAG.md").read_text(encoding="utf-8")
        added = json.loads(self.circle(
            "issue-add", data={"title": "Independent", "state": "ready", "goal": "g",
                               "expected_behavior": "e", "boundaries": "b",
                               "acceptance": ["done when shipped"]}).stdout)
        self.assertEqual(before, (self.store / "DAG.md").read_text(encoding="utf-8"))
        self.circle("dependency-add", "--id", added["id"], "--blocker", ids["model"],
                    "--expected-revision", "1")
        self.assertEqual(before, (self.store / "DAG.md").read_text(encoding="utf-8"))

        self.circle("render")
        rendered = (self.store / "DAG.md").read_text(encoding="utf-8")
        self.assertIn("flowchart LR", rendered)
        self.assertIn(added["id"], rendered)
        self.assertNotEqual(before, rendered)


class BrokenStoreTest(CircleTestCase):
    read_commands = ("validate", "status", "render", "issue-list")

    def snapshot_store(self):
        return {
            path.relative_to(self.store): path.read_text(encoding="utf-8")
            for path in self.store.rglob("*")
            if path.is_file()
        }

    def restore_store(self, snapshot):
        for path in self.store.rglob("*"):
            if path.is_file():
                path.unlink()
        for relative, text in snapshot.items():
            path = self.store / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def test_every_read_command_reports_a_corrupted_store(self):
        """One representative corruption per layer: the per-message mapping from
        corruption to error text is covered by the model and codec unit tests."""
        self.preview_commit()
        snapshot = self.snapshot_store()
        issue_file = next((self.store / "issues").glob("*.md"))

        corruptions = {
            "missing required document": lambda: (self.store / "DOMAIN.md").unlink(),
            "missing front matter": lambda: issue_file.write_text(
                "no front matter\n", encoding="utf-8"),
        }
        for message, corrupt in corruptions.items():
            for command in self.read_commands:
                with self.subTest(message=message, command=command):
                    corrupt()
                    stderr = self.rejected(command)
                    self.assertIn("Circle error:", stderr)
                    self.assertIn(message, stderr)
                    self.restore_store(snapshot)

    def test_absent_store_is_reported(self):
        for command in self.read_commands:
            with self.subTest(command=command):
                self.assertIn("no Circle project found", self.rejected(command))


class WorkBranchTest(CircleTestCase):
    def test_creates_merges_and_deletes_the_branch(self):
        ids = self.preview_commit()
        model_id = ids["model"]
        self.init_git()
        branch = f"circle/{model_id}"

        started = self.circle("issue-branch", "--id", model_id).stdout
        self.assertIn(f"Created {branch} from main.", started)
        self.assertIn("## Execution context", started)
        self.assertEqual(branch, self.git("rev-parse", "--abbrev-ref", "HEAD"))
        self.assertEqual("main", self.git("config", "--local", "--get", f"branch.{branch}.circlebase"))

        (self.root / "implementation.txt").write_text("work\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "implement model")

        finished = self.circle("issue-finish", "--id", model_id).stdout
        self.assertIn(f"Merged {branch} into main and deleted {branch}.", finished)
        self.assertEqual("main", self.git("rev-parse", "--abbrev-ref", "HEAD"))
        self.assertTrue((self.root / "implementation.txt").is_file())
        self.assertEqual("", self.git("branch", "--list", branch))
        self.assertEqual(
            "", self.git("config", "--local", "--get", f"branch.{branch}.circlebase", expect=1))

    def test_refuses_dirty_tree_blocked_issue_and_wrong_branch(self):
        ids = self.preview_commit()
        model_id, dag_id = ids["model"], ids["dag"]
        branch = f"circle/{model_id}"
        self.init_git()

        (self.root / "scratch.txt").write_text("dirty\n", encoding="utf-8")
        self.assertIn("uncommitted changes", self.rejected("issue-branch", "--id", model_id))
        (self.root / "scratch.txt").unlink()

        self.assertIn("unfinished blockers", self.rejected("issue-branch", "--id", dag_id))

        self.circle("issue-branch", "--id", model_id)
        self.assertIn("Already on", self.circle("issue-branch", "--id", model_id).stdout)

        self.assertIn("does not exist", self.rejected("issue-finish", "--id", dag_id))
        self.git("checkout", "-q", "main")
        self.assertIn("check out", self.rejected("issue-finish", "--id", model_id))
        self.assertIn("already exists", self.rejected("issue-branch", "--id", model_id))

        (self.root / "work.txt").write_text("x\n", encoding="utf-8")
        self.git("checkout", "-q", branch)
        self.assertIn("uncommitted changes", self.rejected("issue-finish", "--id", model_id))

    def test_finish_honours_into_and_reports_an_unknown_base(self):
        ids = self.preview_commit()
        model_id = ids["model"]
        branch = f"circle/{model_id}"
        self.init_git()
        self.circle("issue-branch", "--id", model_id)
        (self.root / "work.txt").write_text("x\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "work")

        self.git("config", "--local", "--unset", f"branch.{branch}.circlebase")
        self.assertIn("unknown", self.rejected("issue-finish", "--id", model_id))

        self.circle("issue-finish", "--id", model_id, "--into", "main")
        self.assertEqual("main", self.git("rev-parse", "--abbrev-ref", "HEAD"))
        self.assertTrue((self.root / "work.txt").is_file())

    def test_cannot_start_a_finished_issue(self):
        ids = self.preview_commit()
        self.init_git()
        self.complete(ids["model"], 1)
        self.assertIn("cannot start work on a done issue",
                      self.rejected("issue-branch", "--id", ids["model"]))

    def test_branch_is_refused_outside_a_git_repository(self):
        ids = self.preview_commit()
        self.assertIn("not a git work tree", self.rejected("issue-branch", "--id", ids["model"]))


if __name__ == "__main__":
    unittest.main()
