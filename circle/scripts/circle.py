#!/usr/bin/env python3
"""Deterministic local controller for Circle project fact stores."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import string
import sys
import tempfile
from typing import Any, Iterator


STATES = ("draft", "ready", "in_progress", "review", "done", "cancelled")
FORWARD = {
    "draft": "ready",
    "ready": "in_progress",
    "in_progress": "review",
    "review": "done",
}
ID_ALPHABET = string.ascii_uppercase + string.digits
EDITABLE_FIELDS = {"title", "body", "assignee", "estimate", "blocked_by"}


class CircleError(Exception):
    pass


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in snapshot.items() if key != "snapshot_hash"}
    return hashlib.sha256(canonical_bytes(unsigned)).hexdigest()


def dump_scalar(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def parse_scalar(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CircleError(f"invalid front-matter value: {raw}") from exc


def parse_document(path: Path) -> tuple[dict[str, Any], str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CircleError(f"missing file: {path}") from exc
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise CircleError(f"missing front matter: {path}")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise CircleError(f"unterminated front matter: {path}") from exc
    data: dict[str, Any] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        if ":" not in line:
            raise CircleError(f"invalid front-matter line in {path}: {line}")
        key, raw = line.split(":", 1)
        data[key.strip()] = parse_scalar(raw.strip())
    body = "\n".join(lines[end + 1 :]).strip()
    return data, body


def write_document(path: Path, fields: list[tuple[str, Any]], body: str) -> None:
    content = ["---"]
    content.extend(f"{key}: {dump_scalar(value)}" for key, value in fields)
    content.extend(["---", "", body.strip(), ""])
    atomic_write(path, "\n".join(content))


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_name)
        raise


def issue_fields(issue: dict[str, Any]) -> list[tuple[str, Any]]:
    return [
        ("id", issue["id"]),
        ("title", issue["title"]),
        ("state", issue["state"]),
        ("assignee", issue.get("assignee")),
        ("estimate", issue.get("estimate")),
        ("blocked_by", issue["blocked_by"]),
        ("revision", issue["revision"]),
        ("created_at", issue["created_at"]),
        ("updated_at", issue["updated_at"]),
    ]


def write_issue(path: Path, issue: dict[str, Any]) -> None:
    write_document(path, issue_fields(issue), issue.get("body", ""))


def require_store(root: Path) -> Path:
    store = root / ".circle"
    if not (store / "PROJECT.md").is_file() or not (store / "issues").is_dir():
        raise CircleError(f"no Circle project found at {root}")
    return store


def validate_id(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 14 or not value.startswith("CIR-"):
        raise CircleError(f"invalid issue ID: {value!r}")
    suffix = value[4:]
    if any(char not in ID_ALPHABET for char in suffix):
        raise CircleError(f"invalid issue ID: {value!r}")
    return value


def normalize_text(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CircleError(f"{field} must be a non-empty string")
    return value.strip()


def normalize_blockers(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CircleError("blocked_by must be an array")
    result = [validate_id(item) for item in value]
    if len(result) != len(set(result)):
        raise CircleError("blocked_by contains duplicate IDs")
    return result


def normalize_body(value: Any, field: str = "body") -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CircleError(f"{field} must be a string")
    return value.strip()


def load_issue(path: Path) -> dict[str, Any]:
    data, body = parse_document(path)
    required = {
        "id", "title", "state", "assignee", "estimate", "blocked_by",
        "revision", "created_at", "updated_at",
    }
    missing = sorted(required - data.keys())
    if missing:
        raise CircleError(f"missing issue fields in {path}: {', '.join(missing)}")
    issue = dict(data)
    issue["body"] = body
    validate_id(issue["id"])
    normalize_text(issue["title"], "title")
    if issue["state"] not in STATES:
        raise CircleError(f"invalid state in {path}: {issue['state']!r}")
    if issue["assignee"] is not None:
        normalize_text(issue["assignee"], "assignee")
    if issue["estimate"] is not None:
        normalize_text(issue["estimate"], "estimate")
    issue["blocked_by"] = normalize_blockers(issue["blocked_by"])
    if not isinstance(issue["revision"], int) or issue["revision"] < 1:
        raise CircleError(f"invalid revision in {path}")
    for timestamp in ("created_at", "updated_at"):
        if not isinstance(issue[timestamp], str):
            raise CircleError(f"invalid {timestamp} in {path}")
    return issue


def load_issues(root: Path) -> dict[str, dict[str, Any]]:
    store = require_store(root)
    issues: dict[str, dict[str, Any]] = {}
    for path in sorted((store / "issues").glob("*.md")):
        issue = load_issue(path)
        issue_id = issue["id"]
        if path.name != f"{issue_id}.md":
            raise CircleError(f"issue filename does not match ID: {path}")
        if issue_id in issues:
            raise CircleError(f"duplicate issue ID: {issue_id}")
        issues[issue_id] = issue
    return issues


def assert_acyclic(issues: dict[str, dict[str, Any]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(issue_id: str, trail: list[str]) -> None:
        if issue_id in visiting:
            start = trail.index(issue_id)
            cycle = trail[start:] + [issue_id]
            raise CircleError(f"dependency cycle: {' -> '.join(cycle)}")
        if issue_id in visited:
            return
        visiting.add(issue_id)
        for blocker in issues[issue_id]["blocked_by"]:
            visit(blocker, trail + [issue_id])
        visiting.remove(issue_id)
        visited.add(issue_id)

    for current in issues:
        visit(current, [])


def validate_graph(issues: dict[str, dict[str, Any]]) -> None:
    known = set(issues)
    for issue_id, issue in issues.items():
        for blocker in issue["blocked_by"]:
            if blocker not in known:
                raise CircleError(f"unknown blocker {blocker} on {issue_id}")
            if blocker == issue_id:
                raise CircleError(f"self dependency on {issue_id}")
        if issue["state"] in {"in_progress", "done"}:
            unfinished = [item for item in issue["blocked_by"] if issues[item]["state"] != "done"]
            if unfinished:
                raise CircleError(f"{issue_id} is {issue['state']} with unfinished blockers: {', '.join(unfinished)}")
    assert_acyclic(issues)


def validate_store(root: Path) -> dict[str, dict[str, Any]]:
    store = require_store(root)
    project, _ = parse_document(store / "PROJECT.md")
    if set(project) != {"name", "created_at"}:
        raise CircleError("PROJECT.md must contain exactly name and created_at fields")
    normalize_text(project["name"], "project name")
    issues = load_issues(root)
    validate_graph(issues)
    return issues


def is_unblocked(issue: dict[str, Any], issues: dict[str, dict[str, Any]]) -> bool:
    return all(issues[item]["state"] == "done" for item in issue["blocked_by"])


def issue_view(issue: dict[str, Any], issues: dict[str, dict[str, Any]]) -> dict[str, Any]:
    result = dict(issue)
    result["dependency_status"] = "unblocked" if is_unblocked(issue, issues) else "blocked"
    result["actionable"] = issue["state"] == "ready" and is_unblocked(issue, issues)
    return result


def actionable_ids(issues: dict[str, dict[str, Any]]) -> set[str]:
    return {
        issue["id"] for issue in issues.values()
        if issue["state"] == "ready" and is_unblocked(issue, issues)
    }


def generate_id(existing: set[str]) -> str:
    while True:
        candidate = "CIR-" + "".join(secrets.choice(ID_ALPHABET) for _ in range(10))
        if candidate not in existing:
            return candidate


def json_stdin() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise CircleError(f"invalid JSON input: {exc}") from exc
    if not isinstance(value, dict):
        raise CircleError("JSON input must be an object")
    return value


def normalize_import(raw: dict[str, Any], root: Path) -> dict[str, Any]:
    project = raw.get("project")
    raw_issues = raw.get("issues")
    if not isinstance(project, dict) or not isinstance(raw_issues, list) or not raw_issues:
        raise CircleError("import requires a project object and at least one issue")
    normalized_project = {
        "name": normalize_text(project.get("name"), "project.name"),
        "description": normalize_text(project.get("description", ""), "project.description", nullable=True) or "",
    }
    ids: set[str] = set()
    key_map: dict[str, str] = {}
    titles: set[str] = set()
    pending: list[dict[str, Any]] = []
    for index, item in enumerate(raw_issues, 1):
        if not isinstance(item, dict):
            raise CircleError(f"issue {index} must be an object")
        key = normalize_text(item.get("key"), f"issues[{index}].key")
        assert key is not None
        if key in key_map:
            raise CircleError(f"duplicate issue key: {key}")
        title = normalize_text(item.get("title"), f"issues[{index}].title")
        assert title is not None
        folded = title.casefold()
        if folded in titles:
            raise CircleError(f"duplicate issue title: {title}")
        titles.add(folded)
        issue_id = generate_id(ids)
        ids.add(issue_id)
        key_map[key] = issue_id
        blockers = item.get("blocked_by", [])
        if not isinstance(blockers, list) or any(not isinstance(x, str) for x in blockers):
            raise CircleError(f"issues[{index}].blocked_by must be an array of keys")
        assignee = item.get("assignee")
        estimate = item.get("estimate")
        if assignee is not None:
            assignee = normalize_text(assignee, f"issues[{index}].assignee")
        if estimate is not None:
            estimate = normalize_text(estimate, f"issues[{index}].estimate")
        pending.append({
            "id": issue_id,
            "key": key,
            "title": title,
            "body": normalize_body(item.get("body", ""), f"issues[{index}].body"),
            "assignee": assignee,
            "estimate": estimate,
            "blocked_by_keys": blockers,
        })
    created = now()
    issues: list[dict[str, Any]] = []
    for item in pending:
        resolved: list[str] = []
        for blocker_key in item.pop("blocked_by_keys"):
            if blocker_key not in key_map:
                raise CircleError(f"unknown blocker key {blocker_key!r} on {item['key']}")
            resolved.append(key_map[blocker_key])
        if len(resolved) != len(set(resolved)):
            raise CircleError(f"duplicate blockers on {item['key']}")
        item["blocked_by"] = resolved
        item["state"] = "draft"
        item["revision"] = 1
        item["created_at"] = created
        item["updated_at"] = created
        issues.append(item)
    graph = {item["id"]: item for item in issues}
    validate_graph(graph)
    for field in ("inferences", "warnings"):
        if not isinstance(raw.get(field, []), list) or any(not isinstance(x, str) for x in raw.get(field, [])):
            raise CircleError(f"{field} must be an array of strings")
    snapshot = {
        "schema_version": 1,
        "project_root": str(root.resolve()),
        "project": normalized_project,
        "issues": issues,
        "inferences": raw.get("inferences", []),
        "warnings": raw.get("warnings", []),
        "raw_summary": normalize_body(raw.get("raw_summary", ""), "raw_summary"),
        "created_at": created,
    }
    snapshot["snapshot_hash"] = snapshot_hash(snapshot)
    return snapshot


def snapshot_dir() -> Path:
    return Path(tempfile.gettempdir()) / "circle-preview-snapshots"


def save_snapshot(snapshot: dict[str, Any]) -> Path:
    directory = snapshot_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f"{snapshot['snapshot_hash']}.json"
    atomic_write(path, json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
    os.chmod(path, 0o600)
    return path


def load_snapshot(value: str, root: Path) -> tuple[dict[str, Any], Path]:
    if len(value) != 64 or any(char not in string.hexdigits for char in value):
        raise CircleError("invalid snapshot hash")
    path = snapshot_dir() / f"{value.lower()}.json"
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise CircleError("snapshot is missing or damaged; create a new Preview") from exc
    if snapshot.get("snapshot_hash") != value.lower() or snapshot_hash(snapshot) != value.lower():
        raise CircleError("snapshot hash mismatch; create a new Preview")
    if snapshot.get("project_root") != str(root.resolve()):
        raise CircleError("snapshot belongs to a different project root")
    graph = {item["id"]: item for item in snapshot.get("issues", [])}
    if len(graph) != len(snapshot.get("issues", [])):
        raise CircleError("snapshot contains duplicate issue IDs")
    validate_graph(graph)
    return snapshot, path


def render_preview(snapshot: dict[str, Any]) -> str:
    lines = [f"# Preview: {snapshot['project']['name']}", ""]
    description = snapshot["project"]["description"]
    if description:
        lines.extend([description, ""])
    lines.extend(["| ID | key | Issue | blocked_by | assignee | estimate |", "|---|---|---|---|---|---|"])
    for issue in snapshot["issues"]:
        blockers = ", ".join(issue["blocked_by"]) or "—"
        lines.append(
            f"| {issue['id']} | {issue['key']} | {issue['title']} | {blockers} | "
            f"{issue['assignee'] or '—'} | {issue['estimate'] or '—'} |"
        )
    for label in ("inferences", "warnings"):
        values = snapshot[label]
        if values:
            lines.extend(["", f"## {label.title()}", ""] + [f"- {item}" for item in values])
    if snapshot["raw_summary"]:
        lines.extend(["", "## Source summary", "", snapshot["raw_summary"]])
    lines.extend(["", f"Snapshot: `{snapshot['snapshot_hash']}`"])
    return "\n".join(lines)


def make_dag(issues: dict[str, dict[str, Any]]) -> str:
    lines = ["# Dependency DAG", "", "```mermaid", "flowchart LR"]
    for issue_id in sorted(issues):
        issue = issues[issue_id]
        label = f"{issue_id} {issue['title']} [{issue['state']}]".replace('"', "'")
        lines.append(f'  {issue_id.replace("-", "_")}["{label}"]')
    for issue_id in sorted(issues):
        for blocker in issues[issue_id]["blocked_by"]:
            lines.append(f'  {blocker.replace("-", "_")} --> {issue_id.replace("-", "_")}')
    lines.extend(["```", ""])
    return "\n".join(lines)


@contextlib.contextmanager
def root_lock(root: Path) -> Iterator[None]:
    fd = os.open(root, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextlib.contextmanager
def store_lock(root: Path) -> Iterator[None]:
    store = require_store(root)
    with (store / "PROJECT.md").open("r", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def check_revision(issue: dict[str, Any], expected: int) -> None:
    if issue["revision"] != expected:
        raise CircleError(
            f"stale revision for {issue['id']}: expected {expected}, current {issue['revision']}; re-read the issue"
        )


def cmd_preview(args: argparse.Namespace) -> None:
    if (args.project_root / ".circle").exists():
        raise CircleError("Circle project already exists")
    snapshot = normalize_import(json_stdin(), args.project_root)
    save_snapshot(snapshot)
    print(render_preview(snapshot))


def cmd_commit(args: argparse.Namespace) -> None:
    snapshot, path = load_snapshot(args.snapshot, args.project_root)
    root = args.project_root
    with root_lock(root):
        target = root / ".circle"
        if target.exists():
            raise CircleError("Circle project already exists; commit refused")
        stage = Path(tempfile.mkdtemp(prefix=".circle-stage-", dir=root))
        try:
            (stage / "issues").mkdir()
            project = snapshot["project"]
            write_document(stage / "PROJECT.md", [("name", project["name"]), ("created_at", snapshot["created_at"])], project["description"])
            issues = {item["id"]: item for item in snapshot["issues"]}
            for issue in issues.values():
                issue = {key: value for key, value in issue.items() if key != "key"}
                write_issue(stage / "issues" / f"{issue['id']}.md", issue)
            atomic_write(stage / "DAG.md", make_dag(issues))
            os.rename(stage, target)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    path.unlink()
    print(f"Created Circle project with {len(snapshot['issues'])} issues from snapshot {args.snapshot}.")


def cmd_validate(args: argparse.Namespace) -> None:
    issues = validate_store(args.project_root)
    print(f"Valid Circle project: {len(issues)} issues.")


def cmd_status(args: argparse.Namespace) -> None:
    issues = validate_store(args.project_root)
    counts = {state: 0 for state in STATES}
    actionable: list[str] = []
    blocked: list[str] = []
    for issue in issues.values():
        counts[issue["state"]] += 1
        if issue["state"] == "ready" and is_unblocked(issue, issues):
            actionable.append(issue["id"])
        if not is_unblocked(issue, issues):
            blocked.append(issue["id"])
    project_state = "done" if issues and all(issue["state"] == "done" for issue in issues.values()) else "active"
    print(f"Project: {project_state}")
    print(f"Issues: {len(issues)}")
    print("States: " + ", ".join(f"{state}={counts[state]}" for state in STATES if counts[state]))
    print("Actionable: " + (", ".join(sorted(actionable)) or "none"))
    print("Blocked: " + (", ".join(sorted(blocked)) or "none"))


def cmd_render(args: argparse.Namespace) -> None:
    issues = validate_store(args.project_root)
    atomic_write(args.project_root / ".circle" / "DAG.md", make_dag(issues))
    print(f"Rendered DAG for {len(issues)} issues.")


def cmd_issue_list(args: argparse.Namespace) -> None:
    issues = validate_store(args.project_root)
    print("| ID | state | dependency | actionable | title |")
    print("|---|---|---|---|---|")
    for issue_id in sorted(issues):
        view = issue_view(issues[issue_id], issues)
        print(f"| {issue_id} | {view['state']} | {view['dependency_status']} | {'yes' if view['actionable'] else 'no'} | {view['title']} |")


def find_issue(root: Path, issue_id: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]], Path]:
    validate_id(issue_id)
    issues = validate_store(root)
    if issue_id not in issues:
        raise CircleError(f"unknown issue: {issue_id}")
    return issues[issue_id], issues, root / ".circle" / "issues" / f"{issue_id}.md"


def cmd_issue_show(args: argparse.Namespace) -> None:
    issue, issues, _ = find_issue(args.project_root, args.id)
    view = issue_view(issue, issues)
    print(json.dumps(view, ensure_ascii=False, indent=2))


def normalize_new_issue(data: dict[str, Any], issues: dict[str, dict[str, Any]]) -> dict[str, Any]:
    unknown = set(data) - {"title", "body", "assignee", "estimate", "blocked_by", "state"}
    if unknown:
        raise CircleError(f"unknown issue fields: {', '.join(sorted(unknown))}")
    title = normalize_text(data.get("title"), "title")
    state = data.get("state", "draft")
    if state not in {"draft", "ready"}:
        raise CircleError("new issue state must be draft or ready")
    assignee = data.get("assignee")
    estimate = data.get("estimate")
    if assignee is not None:
        assignee = normalize_text(assignee, "assignee")
    if estimate is not None:
        estimate = normalize_text(estimate, "estimate")
    created = now()
    return {
        "id": generate_id(set(issues)),
        "title": title,
        "state": state,
        "assignee": assignee,
        "estimate": estimate,
        "blocked_by": normalize_blockers(data.get("blocked_by", [])),
        "revision": 1,
        "created_at": created,
        "updated_at": created,
        "body": normalize_body(data.get("body", "")),
    }


def cmd_issue_add(args: argparse.Namespace) -> None:
    data = json_stdin()
    with store_lock(args.project_root):
        issues = validate_store(args.project_root)
        issue = normalize_new_issue(data, issues)
        issues[issue["id"]] = issue
        validate_graph(issues)
        write_issue(args.project_root / ".circle" / "issues" / f"{issue['id']}.md", issue)
    print(json.dumps(issue_view(issue, issues), ensure_ascii=False, indent=2))


def cmd_issue_edit(args: argparse.Namespace) -> None:
    changes = json_stdin()
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise CircleError(f"unknown editable fields: {', '.join(sorted(unknown))}")
    if not changes:
        raise CircleError("no changes supplied")
    with store_lock(args.project_root):
        issue, issues, path = find_issue(args.project_root, args.id)
        actionable_before = actionable_ids(issues)
        check_revision(issue, args.expected_revision)
        if issue["state"] == "done" and set(changes) & {"title", "body", "blocked_by"}:
            raise CircleError("done issue title, execution content, and dependencies are immutable")
        updated = dict(issue)
        if "title" in changes:
            updated["title"] = normalize_text(changes["title"], "title")
        if "body" in changes:
            updated["body"] = normalize_body(changes["body"])
        for field in ("assignee", "estimate"):
            if field in changes:
                updated[field] = None if changes[field] is None else normalize_text(changes[field], field)
        if "blocked_by" in changes:
            updated["blocked_by"] = normalize_blockers(changes["blocked_by"])
        updated["revision"] += 1
        updated["updated_at"] = now()
        issues[args.id] = updated
        validate_graph(issues)
        write_issue(path, updated)
    output = issue_view(updated, issues)
    output["newly_actionable"] = sorted(actionable_ids(issues) - actionable_before)
    print(json.dumps(output, ensure_ascii=False, indent=2))


def append_note(body: str, note: str) -> str:
    entry = f"- {now()}: {note.strip()}"
    if "## Comments" in body:
        return body.rstrip() + "\n" + entry
    return body.rstrip() + ("\n\n" if body.strip() else "") + "## Comments\n\n" + entry


def cmd_issue_transition(args: argparse.Namespace) -> None:
    if args.state not in STATES:
        raise CircleError(f"invalid target state: {args.state}")
    with store_lock(args.project_root):
        issue, issues, path = find_issue(args.project_root, args.id)
        actionable_before = actionable_ids(issues)
        check_revision(issue, args.expected_revision)
        current = issue["state"]
        target = args.state
        if current == "done":
            raise CircleError("done is terminal and cannot be reopened")
        allowed = target == FORWARD.get(current) or (target == "cancelled" and current != "cancelled") or (current == "cancelled" and target == "draft")
        if not allowed:
            raise CircleError(f"invalid transition: {current} -> {target}")
        if target in {"in_progress", "done"}:
            unfinished = [item for item in issue["blocked_by"] if issues[item]["state"] != "done"]
            if unfinished:
                raise CircleError(f"unfinished blockers prevent {target}: {', '.join(unfinished)}")
        updated = dict(issue)
        updated["state"] = target
        if args.note:
            updated["body"] = append_note(updated["body"], args.note)
        updated["revision"] += 1
        updated["updated_at"] = now()
        issues[args.id] = updated
        validate_graph(issues)
        write_issue(path, updated)
    output = issue_view(updated, issues)
    output["newly_actionable"] = sorted(actionable_ids(issues) - actionable_before)
    print(json.dumps(output, ensure_ascii=False, indent=2))


def mutate_dependency(args: argparse.Namespace, add: bool) -> None:
    validate_id(args.blocker)
    with store_lock(args.project_root):
        issue, issues, path = find_issue(args.project_root, args.id)
        actionable_before = actionable_ids(issues)
        check_revision(issue, args.expected_revision)
        if issue["state"] == "done":
            raise CircleError("dependencies of a done issue are immutable")
        if args.blocker not in issues:
            raise CircleError(f"unknown blocker: {args.blocker}")
        blockers = list(issue["blocked_by"])
        if add:
            if args.blocker in blockers:
                raise CircleError(f"dependency already exists: {args.blocker}")
            blockers.append(args.blocker)
        else:
            if args.blocker not in blockers:
                raise CircleError(f"dependency does not exist: {args.blocker}")
            blockers.remove(args.blocker)
        updated = dict(issue)
        updated["blocked_by"] = blockers
        updated["revision"] += 1
        updated["updated_at"] = now()
        issues[args.id] = updated
        validate_graph(issues)
        write_issue(path, updated)
    output = issue_view(updated, issues)
    output["newly_actionable"] = sorted(actionable_ids(issues) - actionable_before)
    print(json.dumps(output, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--project-root", type=Path, required=True)
    subs = result.add_subparsers(dest="command", required=True)
    commands = {
        "preview": cmd_preview,
        "commit": cmd_commit,
        "validate": cmd_validate,
        "status": cmd_status,
        "render": cmd_render,
        "issue-list": cmd_issue_list,
        "issue-show": cmd_issue_show,
        "issue-add": cmd_issue_add,
        "issue-edit": cmd_issue_edit,
        "issue-transition": cmd_issue_transition,
    }
    for name, function in commands.items():
        sub = subs.add_parser(name)
        sub.set_defaults(function=function)
    subs.choices["commit"].add_argument("--snapshot", required=True)
    subs.choices["issue-show"].add_argument("--id", required=True)
    subs.choices["issue-edit"].add_argument("--id", required=True)
    subs.choices["issue-edit"].add_argument("--expected-revision", type=int, required=True)
    transition = subs.choices["issue-transition"]
    transition.add_argument("--id", required=True)
    transition.add_argument("--state", required=True)
    transition.add_argument("--expected-revision", type=int, required=True)
    transition.add_argument("--note")
    for name, add in (("dependency-add", True), ("dependency-remove", False)):
        sub = subs.add_parser(name)
        sub.add_argument("--id", required=True)
        sub.add_argument("--blocker", required=True)
        sub.add_argument("--expected-revision", type=int, required=True)
        sub.set_defaults(function=lambda args, add=add: mutate_dependency(args, add))
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
