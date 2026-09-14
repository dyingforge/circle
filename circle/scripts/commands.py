"""Command layer: one function per CLI subcommand.

Read-only commands resolve the store once. Mutations go through the store lock,
which also yields the resolved store path, so nothing resolves it twice.
"""

from __future__ import annotations

import argparse
import contextlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Iterator

import workbranch
from document import read_text
from errors import CircleError
from graph import actionable_ids, is_unblocked, unfinished_blockers
from model import (
    AGENT_DOC,
    DOCUMENTS,
    DOC_FILENAMES,
    DONE,
    EDITABLE_FIELDS,
    IMMUTABLE_WHEN_DONE,
    IN_PROGRESS,
    ISSUES_DIR,
    STORE_DIR,
    STATES,
    SUPPORTING_DOCS,
    acceptance_indices,
    check_revision,
    create_store,
    issue_path,
    issue_view,
    load_project,
    load_store,
    normalize_acceptance,
    normalize_blockers,
    normalize_issue_batch,
    normalize_new_issue,
    normalize_string_list,
    normalize_text,
    now,
    optional_text,
    placeholder_documents,
    require_issue,
    require_store,
    root_lock,
    store_lock,
    store_path,
    transition_error,
    validate_graph,
    validate_id,
    write_dag,
    write_issue,
    write_project_document,
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
    snapshot = normalize_import(json_stdin(), args.project_root, args.allow_placeholder_docs)
    save_snapshot(snapshot)
    print(render_preview(snapshot))


def cmd_commit(args: argparse.Namespace) -> None:
    snapshot, path = load_snapshot(args.snapshot, args.project_root)
    root = args.project_root
    project = snapshot["project"]
    with root_lock(root):
        target = store_path(root)
        if target.exists():
            raise CircleError("Circle project already exists; commit refused")
        stage = Path(tempfile.mkdtemp(prefix=f".{STORE_DIR}-stage-", dir=root))
        docs = dict(snapshot["docs"])
        docs["agent"] = compose_agent_body(project["description"], docs["agent"])
        try:
            create_store(
                stage,
                name=project["name"],
                created_at=snapshot["created_at"],
                docs=docs,
                issues=snapshot["issues"],
            )
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
    placeholders = placeholder_documents(store_path(args.project_root))
    if placeholders:
        print("Warning: placeholder documents: " + ", ".join(placeholders))


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
    placeholders = placeholder_documents(store_path(args.project_root))
    print("Documents: " + ", ".join(
        f"{doc} {'placeholder' if doc in placeholders else 'ok'}"
        for doc in (AGENT_DOC,) + SUPPORTING_DOCS
    ))
    print("Actionable: " + (", ".join(sorted(actionable)) or "none"))
    print("Blocked: " + (", ".join(sorted(blocked)) or "none"))


def cmd_render(args: argparse.Namespace) -> None:
    _, issues = read_store(args.project_root)
    write_dag(store_path(args.project_root), issues)
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

class IssueMutation:
    """One guarded edit of an existing issue, staged for the lock to write."""

    def __init__(self, issues: dict[str, dict[str, Any]], issue: dict[str, Any]):
        self.issues = issues
        self.issue = issue
        self.before = actionable_ids(issues)
        self.updated = dict(issue)

    def stage(self, updated: dict[str, Any]) -> None:
        self.updated = updated


@contextlib.contextmanager
def issue_mutation(args: argparse.Namespace) -> Iterator[IssueMutation]:
    """Serialise one edit of an existing issue.

    The caller stages a replacement inside the block. On clean exit the revision
    is bumped, the graph re-validated and the issue written; a raise anywhere in
    the block drops the staged replacement, which is what makes every rejected
    mutation atomic.
    """
    root = args.project_root
    with store_lock(root) as store:
        _, issues = load_store(store)
        issue = require_issue(issues, args.id)
        mutation = IssueMutation(issues, issue)
        check_revision(issue, args.expected_revision)
        yield mutation
        updated = mutation.updated
        updated["revision"] = issue["revision"] + 1
        updated["updated_at"] = now()
        issues[issue["id"]] = updated
        validate_graph(issues)
        write_issue(root, updated)


class IssueCreation:
    """New issues staged for the lock to validate and write as one batch."""

    def __init__(self, issues: dict[str, dict[str, Any]]):
        self.issues = issues
        self.before = actionable_ids(issues)
        self.created: list[dict[str, Any]] = []

    def add(self, issue: dict[str, Any]) -> dict[str, Any]:
        self.issues[issue["id"]] = issue
        self.created.append(issue)
        return issue


@contextlib.contextmanager
def issue_creation(args: argparse.Namespace) -> Iterator[IssueCreation]:
    """Serialise one batch creation, writing every staged issue on clean exit."""
    root = args.project_root
    with store_lock(root) as store:
        _, issues = load_store(store)
        batch = IssueCreation(issues)
        yield batch
        validate_graph(issues)
        for issue in batch.created:
            write_issue(root, issue)


def cmd_issue_add(args: argparse.Namespace) -> None:
    data = json_stdin()
    with issue_creation(args) as batch:
        issue = batch.add(normalize_new_issue(data, batch.issues))
    report(issue, batch.issues, batch.before)


def cmd_issue_import(args: argparse.Namespace) -> None:
    payload = json_stdin()
    unknown = sorted(set(payload) - {"issues", "inferences", "warnings"})
    if unknown:
        raise CircleError(f"unknown import fields: {', '.join(unknown)}")
    inferences = normalize_string_list(payload.get("inferences"), "inferences")
    warnings = normalize_string_list(payload.get("warnings"), "warnings")
    with issue_creation(args) as batch:
        created, _ = normalize_issue_batch(payload.get("issues"), batch.issues)
        for issue in created:
            batch.add(issue)
    print(json.dumps({
        "created": [issue_view(issue, batch.issues) for issue in created],
        "newly_actionable": sorted(actionable_ids(batch.issues) - batch.before),
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
    with issue_mutation(args) as mutation:
        issue = mutation.issue
        if issue["state"] == DONE and set(changes) & set(IMMUTABLE_WHEN_DONE):
            raise CircleError("done issue title, execution content, and dependencies are immutable")
        mutation.stage(apply_edit(issue, changes))
    report(mutation.updated, mutation.issues, mutation.before)


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
    with issue_mutation(args) as mutation:
        issue = mutation.issue
        target = args.state
        reason = transition_error(issue["state"], target)
        if reason:
            raise CircleError(reason)
        if target in (IN_PROGRESS, DONE):
            unfinished = unfinished_blockers(issue, mutation.issues)
            if unfinished:
                raise CircleError(f"unfinished blockers prevent {target}: {', '.join(unfinished)}")
        if target == DONE:
            unchecked = [
                f"{index}. {item['text']}"
                for index, item in enumerate(issue["acceptance"], 1)
                if not item["done"]
            ]
            if unchecked:
                raise CircleError(
                    "unchecked acceptance criteria prevent done: " + "; ".join(unchecked)
                )
        updated = dict(issue, state=target)
        if args.note:
            updated["comments"] = append_note(updated["comments"], args.note)
        mutation.stage(updated)
    report(mutation.updated, mutation.issues, mutation.before)


def append_note(comments: str, note: str) -> str:
    entry = f"- {now()}: {note.strip()}"
    return f"{comments.rstrip()}\n{entry}" if comments.strip() else entry


def mutate_dependency(args: argparse.Namespace, add: bool) -> None:
    validate_id(args.blocker)
    with issue_mutation(args) as mutation:
        issue = mutation.issue
        if issue["state"] == DONE:
            raise CircleError("dependencies of a done issue are immutable")
        if args.blocker not in mutation.issues:
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
        mutation.stage(dict(issue, blocked_by=blocked_by))
    report(mutation.updated, mutation.issues, mutation.before)


def cmd_dependency_add(args: argparse.Namespace) -> None:
    mutate_dependency(args, True)


def cmd_dependency_remove(args: argparse.Namespace) -> None:
    mutate_dependency(args, False)


# --------------------------------------------------------------------------
# Acceptance criteria
# --------------------------------------------------------------------------

def mutate_acceptance(args: argparse.Namespace, done: bool) -> None:
    """Tick or untick individual acceptance items, addressed by 1-based number."""
    with issue_mutation(args) as mutation:
        issue = mutation.issue
        if issue["state"] == DONE:
            raise CircleError("acceptance criteria of a done issue are immutable")
        selected = acceptance_indices(issue, args.item)
        settled = sorted(
            index for index in selected if issue["acceptance"][index - 1]["done"] == done
        )
        if settled:
            state = "checked" if done else "unchecked"
            raise CircleError(
                f"acceptance item already {state}: "
                + ", ".join(str(index) for index in settled)
            )
        mutation.stage(dict(issue, acceptance=[
            dict(item, done=done) if index in selected else item
            for index, item in enumerate(issue["acceptance"], 1)
        ]))
    report(mutation.updated, mutation.issues, mutation.before)


def cmd_acceptance_check(args: argparse.Namespace) -> None:
    mutate_acceptance(args, True)


def cmd_acceptance_uncheck(args: argparse.Namespace) -> None:
    mutate_acceptance(args, False)


# --------------------------------------------------------------------------
# Project documents
# --------------------------------------------------------------------------

def cmd_docs_set(args: argparse.Namespace) -> None:
    """Replace one project document's body. Front matter is never hand-written."""
    body = sys.stdin.read().strip()
    if not body:
        raise CircleError("document body must not be empty")
    root = args.project_root
    with store_lock(root) as store:
        project = load_project(store)
        write_project_document(
            store, args.doc, body, name=project["name"], created_at=project["created_at"]
        )
        remaining = placeholder_documents(store)
    print(f"Updated {DOC_FILENAMES[args.doc]}.")
    print("Placeholder documents remaining: " + (", ".join(remaining) or "none"))


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
