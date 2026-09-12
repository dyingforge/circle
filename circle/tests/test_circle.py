import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "circle.py"


class CircleFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_circle(self, command, *args, data=None, success=True):
        process = subprocess.run(
            [sys.executable, str(SCRIPT), "--project-root", str(self.root), command, *args],
            input=None if data is None else json.dumps(data),
            text=True,
            capture_output=True,
        )
        if success and process.returncode != 0:
            self.fail(f"command failed: {process.stderr}")
        if not success and process.returncode == 0:
            self.fail(f"command unexpectedly succeeded: {process.stdout}")
        return process

    def import_three_issues(self):
        payload = {
            "project": {"name": "Demo", "description": "Three issue acceptance project."},
            "issues": [
                {"key": "model", "title": "Model", "body": "Define model.", "blocked_by": [], "estimate": "1 day", "assignee": "A"},
                {"key": "dag", "title": "DAG", "body": "Render DAG.", "blocked_by": ["model"], "estimate": "1 day", "assignee": "B"},
                {"key": "status", "title": "Status", "body": "Report status.", "blocked_by": ["dag"], "estimate": "1 day", "assignee": "C"},
            ],
            "inferences": [],
            "warnings": [],
            "raw_summary": "Acceptance fixture",
        }
        preview = self.run_circle("preview", data=payload)
        self.assertFalse((self.root / ".circle").exists())
        snapshot = re.search(r"Snapshot: `([0-9a-f]{64})`", preview.stdout).group(1)
        ids = re.findall(r"\| (CIR-[A-Z0-9]{10}) \| (model|dag|status) \|", preview.stdout)
        mapping = {key: issue_id for issue_id, key in ids}
        self.run_circle("commit", "--snapshot", snapshot)
        return mapping

    def transition(self, issue_id, state, revision, success=True):
        return self.run_circle(
            "issue-transition", "--id", issue_id, "--state", state,
            "--expected-revision", str(revision), success=success,
        )

    def test_complete_flow_and_failure_guards(self):
        ids = self.import_three_issues()
        model, dag, status = ids["model"], ids["dag"], ids["status"]
        self.run_circle("validate")

        self.transition(dag, "ready", 1)
        blocked = self.transition(dag, "in_progress", 2, success=False)
        self.assertIn("unfinished blockers", blocked.stderr)

        revision = 1
        for state in ("ready", "in_progress", "review", "done"):
            self.transition(model, state, revision)
            revision += 1

        shown = json.loads(self.run_circle("issue-show", "--id", dag).stdout)
        self.assertTrue(shown["actionable"])
        self.transition(dag, "in_progress", 2)
        self.transition(dag, "review", 3)
        self.transition(dag, "done", 4)

        self.transition(status, "ready", 1)
        self.transition(status, "in_progress", 2)
        self.transition(status, "review", 3)
        self.transition(status, "done", 4)
        completed_status = self.run_circle("status").stdout
        self.assertIn("Project: done", completed_status)
        self.assertIn("done=3", completed_status)

        reopen = self.transition(model, "draft", 5, success=False)
        self.assertIn("done is terminal", reopen.stderr)
        immutable = self.run_circle(
            "issue-edit", "--id", model, "--expected-revision", "5",
            data={"body": "rewrite"}, success=False,
        )
        self.assertIn("immutable", immutable.stderr)

        self.run_circle(
            "issue-edit", "--id", model, "--expected-revision", "5",
            data={"assignee": "D"},
        )
        stale = self.run_circle(
            "issue-edit", "--id", model, "--expected-revision", "5",
            data={"assignee": "E"}, success=False,
        )
        self.assertIn("stale revision", stale.stderr)

        added = json.loads(self.run_circle("issue-add", data={"title": "Correction", "body": "Fix it."}).stdout)
        correction = added["id"]
        self.run_circle(
            "dependency-add", "--id", correction, "--blocker", status,
            "--expected-revision", "1",
        )
        self.run_circle(
            "dependency-remove", "--id", correction, "--blocker", status,
            "--expected-revision", "2",
        )

        first = json.loads(self.run_circle("issue-add", data={"title": "First"}).stdout)["id"]
        second = json.loads(self.run_circle("issue-add", data={"title": "Second", "blocked_by": [first]}).stdout)["id"]
        cycle = self.run_circle(
            "dependency-add", "--id", first, "--blocker", second,
            "--expected-revision", "1", success=False,
        )
        self.assertIn("dependency cycle", cycle.stderr)
        first_after = json.loads(self.run_circle("issue-show", "--id", first).stdout)
        self.assertEqual(1, first_after["revision"])
        self.assertEqual([], first_after["blocked_by"])

        self.run_circle("render")
        dag_text = (self.root / ".circle" / "DAG.md").read_text()
        self.assertIn("flowchart LR", dag_text)
        self.run_circle("validate")

    def test_unknown_import_dependency_does_not_write_project(self):
        payload = {
            "project": {"name": "Broken", "description": "Bad dependency."},
            "issues": [{"key": "a", "title": "A", "blocked_by": ["missing"]}],
        }
        result = self.run_circle("preview", data=payload, success=False)
        self.assertIn("unknown blocker key", result.stderr)
        self.assertFalse((self.root / ".circle").exists())


if __name__ == "__main__":
    unittest.main()
