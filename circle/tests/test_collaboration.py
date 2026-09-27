"""Local process safety and two-clone Git collaboration acceptance checks.

Tests named 'gap' document current limitations, not desired guarantees.
No network or second physical machine is involved.
"""
import concurrent.futures
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from support import CircleTestCase, SCRIPTS, import_payload, make_issue, run_circle


class LocalConcurrencyTest(CircleTestCase):
    def test_concurrent_initialization_has_exactly_one_winner(self):
        preview = self.circle("preview", data=import_payload()).stdout
        digest = re.search(r"Snapshot: `([0-9a-f]{64})`", preview).group(1)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: run_circle(self.root, "commit", "--snapshot", digest), range(2)))
        self.assertEqual([0, 2], sorted(r.returncode for r in results))
        self.circle("validate")

    def test_concurrent_revision_has_exactly_one_winner(self):
        issue = self.preview_commit()["model"]
        def edit(owner):
            return run_circle(self.root, "issue-edit", "--id", issue,
                              "--expected-revision", "1", data={"assignee": owner})
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(edit, ["甲", "乙"]))
        self.assertEqual([0, 2], sorted(r.returncode for r in results))
        self.assertIn("revision", next(r.stderr for r in results if r.returncode))
        self.assertEqual(2, json.loads(self.circle("issue-show", "--id", issue).stdout)["revision"])

    def test_lock_blocks_and_is_released_when_holder_is_killed(self):
        ids = self.preview_commit()
        code = ("import sys,time; from pathlib import Path; "
                "sys.path.insert(0,sys.argv[1]); from locking import project_lock; "
                "\nwith project_lock(Path(sys.argv[2])):\n"
                " print('locked',flush=True)\n time.sleep(30)\n")
        holder = subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS), str(self.root)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                self.assertEqual("locked", pool.submit(holder.stdout.readline).result(10).strip())
                edit = pool.submit(run_circle, self.root, "issue-edit", "--id", ids["model"],
                                   "--expected-revision", "1", data={"assignee": "after crash"})
                try:
                    with self.assertRaises(concurrent.futures.TimeoutError):
                        edit.result(0.4)
                finally:
                    holder.kill()
                    holder.wait(timeout=10)
                self.assertEqual(0, edit.result(15).returncode)
        finally:
            if holder.poll() is None:
                holder.kill()
            holder.communicate(timeout=10)

    def test_unicode_root_and_docs_replace_then_mutate(self):
        self.root = self.root / "中文项目 😀"
        self.root.mkdir()
        ids = self.preview_commit()
        self.circle("docs-set", "--doc", "agent", stdin="协作约定 😀\n")
        self.circle("issue-edit", "--id", ids["model"], "--expected-revision", "1",
                    data={"assignee": "张三 😀"})
        self.circle("validate")
        self.init_git()
        self.circle("issue-branch", "--id", ids["model"])
        self.circle("issue-finish", "--id", ids["model"])

    def test_merge_conflict_aborts_and_preserves_issue_branch(self):
        issue = self.preview_commit()["model"]
        path = self.root / "implementation.txt"
        path.write_text("base\n", encoding="utf-8")
        self.init_git()
        started=self.circle("issue-branch", "--id", issue).stdout
        worktree=Path(re.search(r"worktree at (.+) from [0-9a-f]+",started).group(1))
        issue_path=worktree / "implementation.txt"
        issue_path.write_text("issue version\n", encoding="utf-8")
        subprocess.run(["git","add","-A"],cwd=worktree,check=True)
        subprocess.run(["git","commit","-qm","issue"],cwd=worktree,check=True)
        path.write_text("main version\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "main")
        self.assertIn("failed", self.rejected("issue-finish", "--id", issue))
        self.assertEqual("", self.git("status", "--porcelain"))
        self.assertEqual("main version\n", path.read_text(encoding="utf-8"))
        self.assertIn("circle/" + issue, self.git("branch", "--list"))

    def test_validate_rejects_hand_edited_done_with_unchecked_acceptance(self):
        issue = self.preview_commit()["model"]
        path = self.store / "issues" / (issue + ".md")
        body = path.read_text(encoding="utf-8").replace('state: "draft"', 'state: "done"')
        path.write_text(body, encoding="utf-8")
        self.assertIn("unchecked acceptance", self.rejected("validate"))


class TwoCloneTest(CircleTestCase):
    def setUp(self):
        super().setUp()
        payload = import_payload()
        for issue in payload["issues"]:
            issue["blocked_by"] = []
        self.ids = self.preview_commit(payload)
        self.init_git()
        self.peer_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.peer_temp.cleanup)
        self.peer = Path(self.peer_temp.name) / "peer"
        self.git_at(self.root, "clone", "--no-local", str(self.root), str(self.peer))
        self.git_at(self.peer, "config", "user.email", "peer@example.com")
        self.git_at(self.peer, "config", "user.name", "Peer")
        self.git_at(self.peer, "checkout", "-b", "peer-work")

    def git_at(self, root, *args, expect=0):
        result = subprocess.run(["git", *args], cwd=root, capture_output=True,
                                text=True, encoding="utf-8")
        self.assertEqual(expect, result.returncode, result.stdout + result.stderr)
        return result.stdout

    def run_at(self, root, command, *args, data=None, expect=0):
        result = run_circle(root, command, *args, data=data)
        self.assertEqual(expect, result.returncode, result.stdout + result.stderr)
        return result

    def commit_at(self, root):
        self.git_at(root, "add", "-A")
        self.git_at(root, "commit", "-qm", "local changes")

    def merge_peer(self, expect=0):
        self.git_at(self.root, "fetch", str(self.peer), "peer-work")
        return self.git_at(self.root, "merge", "--no-edit", "FETCH_HEAD", expect=expect)

    def edit_at(self, root, key, owner):
        self.run_at(root, "issue-edit", "--id", self.ids[key], "--expected-revision", "1",
                    data={"assignee": owner})

    def test_different_issues_merge_validate_and_render(self):
        self.edit_at(self.root, "model", "machine A")
        self.edit_at(self.peer, "dag", "machine B")
        self.commit_at(self.root)
        self.commit_at(self.peer)
        self.merge_peer()
        self.circle("validate")
        self.circle("render")
        for key, owner in [("model", "machine A"), ("dag", "machine B")]:
            item = json.loads(self.circle("issue-show", "--id", self.ids[key]).stdout)
            self.assertEqual(owner, item["assignee"])

    def test_same_issue_revision_does_not_prevent_two_clone_writes(self):
        self.edit_at(self.root, "model", "machine A")
        self.edit_at(self.peer, "model", "machine B")
        self.commit_at(self.root)
        self.commit_at(self.peer)
        self.merge_peer(expect=1)
        self.assertIn(".circle/issues/", self.git_at(self.root, "diff", "--name-only", "--diff-filter=U"))
        self.git_at(self.root, "merge", "--abort")
        self.circle("validate")

    def test_cross_clone_cycle_merges_but_validate_rejects(self):
        for root, key, blocker in [(self.root, "model", "dag"), (self.peer, "dag", "model")]:
            self.run_at(root, "dependency-add", "--id", self.ids[key],
                        "--blocker", self.ids[blocker], "--expected-revision", "1")
            self.commit_at(root)
        self.merge_peer()
        self.assertIn("cycle", self.rejected("validate"))

    def test_duplicate_titles_across_clones_fail_validate(self):
        for root in [self.root, self.peer]:
            data = make_issue("new", "Same title")
            del data["key"]
            self.run_at(root, "issue-add", data=data)
            self.commit_at(root)
        self.merge_peer()
        self.assertIn("duplicate issue title", self.rejected("validate"))
        from model import load_issues
        self.assertEqual(2, sum(i["title"] == "Same title" for i in load_issues(self.store).values()))

    def test_gap_task_branch_can_be_started_on_both_clones(self):
        self.git_at(self.peer, "checkout", "main")
        for root in [self.root, self.peer]:
            result = self.run_at(root, "issue-branch", "--id", self.ids["model"])
            self.assertIn("Created", result.stdout)

    def test_transferred_branch_requires_worktree_metadata(self):
        branch = "circle/" + self.ids["model"]
        self.circle("issue-branch", "--id", self.ids["model"])
        self.git_at(self.peer, "fetch", "origin", branch)
        self.git_at(self.peer, "checkout", "-b", branch, "FETCH_HEAD")
        failed = self.run_at(self.peer, "issue-finish", "--id", self.ids["model"], expect=2)
        self.assertIn("worktree does not exist", failed.stderr)


if __name__ == "__main__":
    unittest.main()
