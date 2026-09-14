"""Command layer: one function per CLI subcommand.

Read-only commands resolve the store once. Mutations take the store lock, which
also yields the resolved store path, so nothing resolves it twice.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

import workbranch
from document import atomic_write, read_text, write_document, write_text
from errors import CircleError
from graph import actionable_ids, is_unblocked, unfinished_blockers
from model import (
    AGENT_DOC,
    ARCHITECTURE_DOC,
    CANCELLED,
    DAG_DOC,
    DOCUMENTS,
    DOMAIN_DOC,
    EDITABLE_FIELDS,
    FORWARD,
    IMMUTABLE_WHEN_DONE,
    ISSUES_DIR,
    STORE_DIR,
    STATES,
    check_revision,
    issue_path,
    issue_view,
    load_store,
    normalize_acceptance,
    normalize_blockers,
    normalize_issue_batch,
    normalize_new_issue,
    normalize_string_list,
    normalize_text,
    now,
    optional_text,
    render_dag,
    require_issue,
    require_store,
    root_lock,
    store_lock,
    store_path,
    validate_graph,
    validate_id,
    write_issue,
    write_issue_to,
)
from snapshot import (
    compose_agent_body,
    load_snapshot,
    normalize_import,
    render_preview,
    save_snapshot,
)


def json_stdin() -> dict[str, Any]:
    try:
        value = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        raise CircleError(f"invalid JSON input: {exc}") from exc
    if not isinstance(value, dict):
        raise CircleError("JSON input must be an object")
    return value


def read_store(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    return load_store(require_store(root))


def report(issue: dict[str, Any], issues: dict[str, dict[str, Any]], before: set[str]) -> None:
    output = issue_view(issue, issues)
    output["newly_actionable"] = sorted(actionable_ids(issues) - before)
    print(json.dumps(output, ensure_ascii=False, indent=2))


def render_context(root: Path, issue: dict[str, Any]) -> str:
    lines = ["", "## Execution context", "", "Read these documents before working on the issue:", ""]
    issue_relative = f"{STORE_DIR}/{ISSUES_DIR}/{issue['id']}.md"
    for _, doc in DOCUMENTS:
        lines.append(f"- {STORE_DIR}/{doc}")
    lines.append(f"- {issue_relative}")
    for _, doc in DOCUMENTS:
        lines += ["", f"===== {STORE_DIR}/{doc} =====", "", read_text(store_path(root) / doc).rstrip()]
    lines += ["", f"===== {issue_relative} =====", "", read_text(issue_path(root, issue["id"])).rstrip()]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Initialization
# --------------------------------------------------------------------------

def cmd_preview(args: argparse.Namespace) -> None:
    if store_path(args.project_root).exists():
        raise CircleError("Circle project already exists")
    snapshot = normalize_import(json_stdin(), args.project_root)
    save_snapshot(snapshot)
    print(render_preview(snapshot))


def cmd_commit(args: argparse.Namespace) -> None:
    snapshot, path = load_snapshot(args.snapshot, args.project_root)
    root = args.project_root
    with root_lock(root):
        target = store_path(root)
        if target.exists():
            raise CircleError("Circle project already exists; commit refused")
        stage = Path(tempfile.mkdtemp(prefix=f".{STORE_DIR}-stage-", dir=root))
        try:
            (stage / ISSUES_DIR).mkdir()
            project = snapshot["project"]
            docs = snapshot["docs"]
            write_document(
                stage / AGENT_DOC,
                [("name", project["name"]), ("created_at", snapshot["created_at"])],
                compose_agent_body(project["description"], docs["agent"]),
            )
            write_text(stage / ARCHITECTURE_DOC, docs["architecture"])
            write_text(stage / DOMAIN_DOC, docs["domain"])
            issues = {item["id"]: item for item in snapshot["issues"]}
            for issue in issues.values():
                write_issue_to(stage / ISSUES_DIR / f"{issue['id']}.md", issue)
            atomic_write(stage / DAG_DOC, render_dag(issues))
            stage.rename(target)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    path.unlink()
    print(f"Created Circle project with {len(snapshot['issues'])} issues from snapshot {args.snapshot}.")


# --------------------------------------------------------------------------
# Inspection
# --------------------------------------------------------------------------

def cmd_validate(args: argparse.Namespace) -> None:
    _, issues = read_store(args.project_root)
    print(f"Valid Circle project: {len(issues)} issues.")


def cmd_status(args: argparse.Namespace) -> None:
    project, issues = read_store(args.project_root)
    counts = {state: 0 for state in STATES}
    actionable: list[str] = []
    blocked: list[str] = []
    for issue in issues.values():
        counts[issue["state"]] += 1
        if not is_unblocked(issue, issues):
            blocked.append(issue["id"])
        elif issue["state"] == "ready":
            actionable.append(issue["id"])
    done = bool(issues) and all(issue["state"] == "done" for issue in issues.values())
    print(f"Project: {project['name']} ({'done' if done else 'active'})")
    print(f"Issues: {len(issues)}")
    print("States: " + ", ".join(f"{state}={counts[state]}" for state in STATES if counts[state]))
    print("Actionable: " + (", ".join(sorted(actionable)) or "none"))
    print("Blocked: " + (", ".join(sorted(blocked)) or "none"))


def cmd_render(args: argparse.Namespace) -> None:
    _, issues = read_store(args.project_root)
    atomic_write(store_path(args.project_root) / DAG_DOC, render_dag(issues))
    print(f"Rendered DAG for {len(issues)} issues.")


def cmd_issue_list(args: argparse.Namespace) -> None:
    _, issues = read_store(args.project_root)
    print("| ID | state | dependency | actionable | title |")
    print("|---|---|---|---|---|")
    for issue_id in sorted(issues):
        view = issue_view(issues[issue_id], issues)
        marker = "yes" if view["actionable"] else "no"
        print(
            f"| {issue_id} | {view['state']} | {view['dependency_status']} | "
            f"{marker} | {view['title']} |"
        )


def cmd_issue_show(args: argparse.Namespace) -> None:
    _, issues = read_store(args.project_root)
    print(json.dumps(issue_view(require_issue(issues, args.id), issues), ensure_ascii=False, indent=2))


def cmd_issue_context(args: argparse.Namespace) -> None:
    _, issues = read_store(args.project_root)
    issue = require_issue(issues, args.id)
    print(f"# Execution context for {issue['id']}: {issue['title']}")
    print(render_context(args.project_root, issue))


# --------------------------------------------------------------------------
# Issue mutations
# --------------------------------------------------------------------------

def cmd_issue_add(args: argparse.Namespace) -> None:
    data = json_stdin()
    root = args.project_root
    with store_lock(root):
        _, issues = load_store(require_store(root))
        before = actionable_ids(issues)
        issue = normalize_new_issue(data, issues)
        issues[issue["id"]] = issue
        validate_graph(issues)
        write_issue(root, issue)
    report(issue, issues, before)


def cmd_issue_import(args: argparse.Namespace) -> None:
    payload = json_stdin()
    unknown = sorted(set(payload) - {"issues", "inferences", "warnings"})
    if unknown:
        raise CircleError(f"unknown import fields: {', '.join(unknown)}")
    inferences = normalize_string_list(payload.get("inferences"), "inferences")
    warnings = normalize_string_list(payload.get("warnings"), "warnings")
    root = args.project_root
    with store_lock(root):
        _, issues = load_store(require_store(root))
        before = actionable_ids(issues)
        created, _ = normalize_issue_batch(payload.get("issues"), issues)
        for issue in created:
            issues[issue["id"]] = issue
        validate_graph(issues)
        for issue in created:
            write_issue(root, issue)
    print(json.dumps({
        "created": [issue_view(issue, issues) for issue in created],
        "newly_actionable": sorted(actionable_ids(issues) - before),
        "inferences": inferences,
        "warnings": warnings,
    }, ensure_ascii=False, indent=2))


def cmd_issue_edit(args: argparse.Namespace) -> None:
    changes = json_stdin()
    unknown = sorted(set(changes) - set(EDITABLE_FIELDS))
    if unknown:
        raise CircleError(f"unknown editable fields: {', '.join(unknown)}")
    if not changes:
        raise CircleError("no changes supplied")
    root = args.project_root
    with store_lock(root):
        _, issues = load_store(require_store(root))
        issue = require_issue(issues, args.id)
        before = actionable_ids(issues)
        check_revision(issue, args.expected_revision)
        if issue["state"] == "done" and set(changes) & set(IMMUTABLE_WHEN_DONE):
            raise CircleError("done issue title, execution content, and dependencies are immutable")
        updated = apply_edit(issue, changes)
        updated["revision"] += 1
        updated["updated_at"] = now()
        issues[args.id] = updated
        validate_graph(issues)
        write_issue(root, updated)
    report(updated, issues, before)


def apply_edit(issue: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    updated = dict(issue)
    for field in ("title", "goal", "expected_behavior", "boundaries"):
        if field in changes:
            updated[field] = normalize_text(changes[field], field)
    if "acceptance" in changes:
        updated["acceptance"] = normalize_acceptance(changes["acceptance"], "acceptance")
    if "assignee" in changes:
        updated["assignee"] = optional_text(changes["assignee"], "assignee")
    if "blocked_by" in changes:
        updated["blocked_by"] = normalize_blockers(changes["blocked_by"])
    return updated


def cmd_issue_transition(args: argparse.Namespace) -> None:
    if args.state not in STATES:
        raise CircleError(f"invalid target state: {args.state}")
    root = args.project_root
    with store_lock(root):
        _, issues = load_store(require_store(root))
        issue = require_issue(issues, args.id)
        before = actionable_ids(issues)
        check_revision(issue, args.expected_revision)
        current = issue["state"]
        target = args.state
        if current == "done":
            raise CircleError("done is terminal and cannot be reopened")
        allowed = (
            target == FORWARD.get(current)
            or (target == CANCELLED and current != CANCELLED)
            or (current == CANCELLED and target == "draft")
        )
        if not allowed:
            raise CircleError(f"invalid transition: {current} -> {target}")
        if target in {"in_progress", "done"}:
            unfinished = unfinished_blockers(issue, issues)
            if unfinished:
                raise CircleError(f"unfinished blockers prevent {target}: {', '.join(unfinished)}")
        updated = dict(issue)
        updated["state"] = target
        if args.note:
            updated["comments"] = append_note(updated["comments"], args.note)
        updated["revision"] += 1
        updated["updated_at"] = now()
        issues[args.id] = updated
        validate_graph(issues)
        write_issue(root, updated)
    report(updated, issues, before)


def append_note(comments: str, note: str) -> str:
    entry = f"- {now()}: {note.strip()}"
    return f"{comments.rstrip()}\n{entry}" if comments.strip() else entry


def mutate_dependency(args: argparse.Namespace, add: bool) -> None:
    validate_id(args.blocker)
    root = args.project_root
    with store_lock(root):
        _, issues = load_store(require_store(root))
        issue = require_issue(issues, args.id)
        before = actionable_ids(issues)
        check_revision(issue, args.expected_revision)
        if issue["state"] == "done":
            raise CircleError("dependencies of a done issue are immutable")
        if args.blocker not in issues:
            raise CircleError(f"unknown blocker: {args.blocker}")
        blocked_by = list(issue["blocked_by"])
        if add:
            if args.blocker in blocked_by:
                raise CircleError(f"dependency already exists: {args.blocker}")
            blocked_by.append(args.blocker)
        else:
            if args.blocker not in blocked_by:
                raise CircleError(f"dependency does not exist: {args.blocker}")
            blocked_by.remove(args.blocker)
        updated = dict(issue)
        updated["blocked_by"] = blocked_by
        updated["revision"] += 1
        updated["updated_at"] = now()
        issues[args.id] = updated
        validate_graph(issues)
        write_issue(root, updated)
    report(updated, issues, before)


def cmd_dependency_add(args: argparse.Namespace) -> None:
    mutate_dependency(args, True)


def cmd_dependency_remove(args: argparse.Namespace) -> None:
    mutate_dependency(args, False)


# --------------------------------------------------------------------------
# Work branches
# --------------------------------------------------------------------------

def cmd_issue_branch(args: argparse.Namespace) -> None:
    _, issues = read_store(args.project_root)
    issue = require_issue(issues, args.id)
    print(workbranch.start(args.project_root, issue, issues))
    print(render_context(args.project_root, issue))


def cmd_issue_finish(args: argparse.Namespace) -> None:
    _, issues = read_store(args.project_root)
    issue = require_issue(issues, args.id)
    print(workbranch.finish(args.project_root, issue, args.into))
