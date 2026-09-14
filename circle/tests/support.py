"""Shared fixtures for the Circle test suite.

Importing this module puts `scripts/` on `sys.path` so the unit tests can import
the controller modules directly instead of only driving the CLI.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

ENTRY = SCRIPTS / "circle.py"


def run_circle(root, command, *args, data=None):
    """Run the CLI against an explicit project root."""
    return subprocess.run(
        [sys.executable, str(ENTRY), "--project-root", str(root), command, *args],
        input=None if data is None else json.dumps(data),
        text=True,
        capture_output=True,
    )


def make_issue(key, title, blocked_by=None, **extra):
    issue = {
        "key": key,
        "title": title,
        "goal": f"{title} 的目标。",
        "expected_behavior": f"{title} 的预期行为。",
        "boundaries": "无",
        "acceptance": [f"{title} 可以通过验收"],
        "blocked_by": blocked_by or [],
        "assignee": None,
    }
    issue.update(extra)
    return issue


def import_payload():
    return {
        "project": {"name": "Demo", "description": "三个 Issue 的验收项目。"},
        "docs": {
            "agent": "## 工作约定\n\n- 修改前先读 ARCHITECTURE.md",
            "architecture": "# Demo: Architecture\n\n控制器 + 事实库。",
            "domain": "# Demo: Domain\n\nGoal / Acceptance / Blocker / Actionable。",
        },
        "issues": [
            make_issue("model", "Model", assignee="A"),
            make_issue("dag", "DAG", blocked_by=["model"], assignee="B"),
            make_issue("status", "Status", blocked_by=["dag"], assignee="C"),
        ],
        "inferences": [],
        "warnings": [],
        "raw_summary": "Acceptance fixture",
    }


class CircleTestCase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    @property
    def store(self):
        return self.root / ".circle"

    def circle(self, command, *args, data=None, expect=0):
        result = run_circle(self.root, command, *args, data=data)
        if result.returncode != expect:
            self.fail(
                f"circle {command} exited {result.returncode}, expected {expect}\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
        return result

    def rejected(self, command, *args, data=None):
        """Run a command expected to fail, returning its stderr."""
        return self.circle(command, *args, data=data, expect=2).stderr

    def git(self, *args, expect=0):
        result = subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True)
        if result.returncode != expect:
            self.fail(f"git {' '.join(args)} exited {result.returncode}\n{result.stderr}")
        return result.stdout.strip()

    def init_git(self):
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.git("add", "-A")
        self.git("commit", "-qm", "circle facts")

    def preview_commit(self, payload=None):
        preview = self.circle("preview", data=payload or import_payload()).stdout
        digest = re.search(r"Snapshot: `([0-9a-f]{64})`", preview).group(1)
        found = re.findall(r"\| (CIR-[A-Z0-9]{10}) \| (\w+) \|", preview)
        self.circle("commit", "--snapshot", digest)
        return {key: issue_id for issue_id, key in found}

    def transition(self, issue_id, state, revision, **kwargs):
        args = ["--id", issue_id, "--state", state, "--expected-revision", str(revision)]
        if kwargs.get("note"):
            args += ["--note", kwargs["note"]]
        if kwargs.get("expect", 0):
            return self.rejected("issue-transition", *args)
        return self.circle("issue-transition", *args)

    def complete(self, issue_id, revision, states=("ready", "in_progress", "review", "done")):
        for state in states:
            self.transition(issue_id, state, revision)
            revision += 1
        return revision
