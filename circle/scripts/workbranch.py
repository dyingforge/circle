"""Git work branches: one temporary branch per issue, merged back and deleted.

The branch name and the base branch are both derived from the issue, so no extra
state file is needed. The base branch is recorded in the branch's own git config,
which `git branch -d` removes along with the branch.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
import subprocess
from typing import Any

from errors import CircleError
from graph import is_unblocked, unfinished_blockers
from model import UNSTARTABLE_STATES


BRANCH_PREFIX = "circle/"


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(root), text=True, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git failure"
        raise CircleError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def git_ok(root: Path, *args: str) -> bool:
    result = subprocess.run(["git", *args], cwd=str(root), text=True, capture_output=True)
    return result.returncode == 0


def require_repo_root(root: Path) -> Path:
    if not git_ok(root, "rev-parse", "--is-inside-work-tree"):
        raise CircleError(f"project root is not a git work tree: {root}")
    top = Path(git(root, "rev-parse", "--show-toplevel")).resolve()
    if top != root:
        raise CircleError(f"git work tree root is {top}, not the project root {root}")
    return top


def current_branch(root: Path) -> str:
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        raise CircleError("repository is in detached HEAD state; check out a branch first")
    return branch


def require_clean_tree(root: Path) -> None:
    if git(root, "status", "--porcelain"):
        raise CircleError("working tree has uncommitted changes; commit or stash them first")


def branch_exists(root: Path, branch: str) -> bool:
    return git_ok(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")


def work_branch(issue: dict[str, Any]) -> str:
    return BRANCH_PREFIX + issue["id"]


def recorded_base(root: Path, branch: str) -> str:
    result = subprocess.run(
        ["git", "config", "--local", "--get", f"branch.{branch}.circlebase"],
        cwd=str(root), text=True, capture_output=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def start(root: Path, issue: dict[str, Any], issues: dict[str, dict[str, Any]]) -> str:
    if issue["state"] in UNSTARTABLE_STATES:
        raise CircleError(f"cannot start work on a {issue['state']} issue")
    if not is_unblocked(issue, issues):
        unfinished = unfinished_blockers(issue, issues)
        raise CircleError(f"unfinished blockers prevent starting work: {', '.join(unfinished)}")

    require_repo_root(root)
    branch = work_branch(issue)
    if branch_exists(root, branch):
        if current_branch(root) == branch:
            return f"Already on {branch}; reusing it."
        raise CircleError(f"issue branch already exists: {branch}; run issue-finish first")

    base = current_branch(root)
    if base.startswith(BRANCH_PREFIX):
        raise CircleError(
            f"current branch is another issue branch ({base}); finish it before starting a new issue"
        )
    require_clean_tree(root)
    git(root, "checkout", "-b", branch, base)
    git(root, "config", "--local", f"branch.{branch}.circlebase", base)
    return f"Created {branch} from {base}."


def finish(root: Path, issue: dict[str, Any], into: str | None) -> str:
    require_repo_root(root)
    branch = work_branch(issue)
    if not branch_exists(root, branch):
        raise CircleError(f"issue branch does not exist: {branch}")
    current = current_branch(root)
    if current != branch:
        raise CircleError(f"current branch is {current}; check out {branch} before finishing")
    require_clean_tree(root)

    base = into or recorded_base(root, branch)
    if not base:
        raise CircleError(f"base branch for {branch} is unknown; pass --into <branch>")
    if not branch_exists(root, base):
        raise CircleError(f"base branch does not exist: {base}")

    git(root, "checkout", base)
    try:
        git(root, "merge", "--no-edit", branch)
    except CircleError as exc:
        with contextlib.suppress(CircleError):
            git(root, "merge", "--abort")
        raise CircleError(
            f"merge of {branch} into {base} failed and was aborted; reconcile {branch} "
            f"manually and retry ({exc})"
        ) from exc
    git(root, "branch", "-d", branch)
    return f"Merged {branch} into {base} and deleted {branch}."
