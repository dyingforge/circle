#!/usr/bin/env python3
"""Deterministic local controller for Circle project fact stores."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import commands
from errors import CircleError
from model import DOC_FILENAMES


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--project-root", type=Path, required=True)
    subs = result.add_subparsers(dest="command", required=True)
    handlers = {
        "preview": commands.cmd_preview,
        "commit": commands.cmd_commit,
        "validate": commands.cmd_validate,
        "status": commands.cmd_status,
        "render": commands.cmd_render,
        "issue-list": commands.cmd_issue_list,
        "issue-show": commands.cmd_issue_show,
        "issue-context": commands.cmd_issue_context,
        "issue-add": commands.cmd_issue_add,
        "issue-import": commands.cmd_issue_import,
        "issue-edit": commands.cmd_issue_edit,
        "issue-transition": commands.cmd_issue_transition,
        "issue-branch": commands.cmd_issue_branch,
        "issue-finish": commands.cmd_issue_finish,
        "dependency-add": commands.cmd_dependency_add,
        "dependency-remove": commands.cmd_dependency_remove,
        "acceptance-check": commands.cmd_acceptance_check,
        "acceptance-uncheck": commands.cmd_acceptance_uncheck,
        "docs-set": commands.cmd_docs_set,
    }
    for name, handler in handlers.items():
        subs.add_parser(name).set_defaults(function=handler)

    subs.choices["commit"].add_argument("--snapshot", required=True)
    subs.choices["preview"].add_argument(
        "--allow-placeholder-docs",
        action="store_true",
        help="write TODO placeholders instead of requiring all project documents",
    )
    subs.choices["docs-set"].add_argument("--doc", choices=tuple(DOC_FILENAMES), required=True)
    for name in ("issue-show", "issue-context", "issue-edit", "issue-branch", "issue-finish"):
        subs.choices[name].add_argument("--id", required=True)
    subs.choices["issue-edit"].add_argument("--expected-revision", type=int, required=True)
    subs.choices["issue-finish"].add_argument(
        "--into", help="base branch to merge into; defaults to the recorded base"
    )
    transition = subs.choices["issue-transition"]
    transition.add_argument("--id", required=True)
    transition.add_argument("--state", required=True)
    transition.add_argument("--expected-revision", type=int, required=True)
    transition.add_argument("--note", help="append a dated entry to '## Comments'")
    for name in ("dependency-add", "dependency-remove"):
        sub = subs.choices[name]
        sub.add_argument("--id", required=True)
        sub.add_argument("--blocker", required=True)
        sub.add_argument("--expected-revision", type=int, required=True)
    for name in ("acceptance-check", "acceptance-uncheck"):
        sub = subs.choices[name]
        sub.add_argument("--id", required=True)
        sub.add_argument("--item", type=int, action="append", required=True)
        sub.add_argument("--expected-revision", type=int, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    args.project_root = args.project_root.resolve()
    if not args.project_root.is_dir():
        print(f"Circle error: project root is not a directory: {args.project_root}", file=sys.stderr)
        return 2
    try:
        args.function(args)
    except CircleError as exc:
        print(f"Circle error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
