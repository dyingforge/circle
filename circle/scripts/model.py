"""Circle domain model: issue schema, fact-store loading, validation, derived views.

An issue is a front-matter block plus fixed body sections. The heading-to-field
mapping lives in `PROSE_SECTIONS`, and the writer, the reader and the error hints
all derive from it, so there is one place to change the issue shape.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import os
from pathlib import Path
import re
import secrets
import string
from typing import Any, Iterator

from document import parse_document, parse_sections, write_document
from errors import CircleError
from graph import (
    DONE,
    READY,
    actionable_ids,
    assert_acyclic,
    is_unblocked,
    unfinished_blockers,
)


STORE_DIR = ".circle"
ISSUES_DIR = "issues"
AGENT_DOC = "AGENT.md"
ARCHITECTURE_DOC = "ARCHITECTURE.md"
DOMAIN_DOC = "DOMAIN.md"
DAG_DOC = "DAG.md"
DOCUMENTS = (("agent", AGENT_DOC), ("architecture", ARCHITECTURE_DOC), ("domain", DOMAIN_DOC))
DOC_FILENAMES = dict(DOCUMENTS)
SUPPORTING_DOCS = (ARCHITECTURE_DOC, DOMAIN_DOC)

DRAFT = "draft"
CANCELLED = "cancelled"
STATES = (DRAFT, READY, "in_progress", "review", DONE, CANCELLED)
FORWARD = {DRAFT: READY, READY: "in_progress", "in_progress": "review", "review": DONE}
UNSTARTABLE_STATES = (DONE, CANCELLED)

ID_PREFIX = "CIR-"
ID_SUFFIX_LENGTH = 10
ID_ALPHABET = string.ascii_uppercase + string.digits

FRONT_MATTER_FIELDS = (
    "id", "title", "state", "assignee", "blocked_by", "revision", "created_at", "updated_at",
)
CONTENT_FIELDS = ("goal", "expected_behavior", "boundaries", "acceptance")
PROSE_SECTIONS = (
    ("Goal", "goal"),
    ("Expected Behavior", "expected_behavior"),
    ("Boundaries", "boundaries"),
)
ACCEPTANCE_SECTION = "Acceptance Criteria"
COMMENTS_SECTION = "Comments"
KNOWN_SECTIONS = tuple(heading for heading, _ in PROSE_SECTIONS) + (ACCEPTANCE_SECTION, COMMENTS_SECTION)

EDITABLE_FIELDS = ("title",) + CONTENT_FIELDS + ("assignee", "blocked_by")
IMMUTABLE_WHEN_DONE = tuple(field for field in EDITABLE_FIELDS if field != "assignee")
CREATION_FIELDS = EDITABLE_FIELDS + ("state",)
IMPORT_FIELDS = ("key",) + CREATION_FIELDS

ACCEPTANCE_PATTERN = re.compile(r"^[-*]\s+\[( |x|X)\]\s+(.*)$")

PLACEHOLDER_MARKER = "<!-- circle:placeholder -->"
# Placeholder text written before the marker existed, so stores created by an
# earlier version are still reported by `placeholder_documents`.
LEGACY_PLACEHOLDER_TEXTS = (
    "TODO: 补充本项目的协作约定与实现规范。",
    "TODO: 补充Architecture相关内容。",
    "TODO: 补充Domain相关内容。",
)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

def store_path(root: Path) -> Path:
    return root / STORE_DIR


def issues_dir(root: Path) -> Path:
    return store_path(root) / ISSUES_DIR


def issue_path(root: Path, issue_id: str) -> Path:
    return issues_dir(root) / f"{issue_id}.md"


def require_store(root: Path) -> Path:
    store = store_path(root)
    if not (store / AGENT_DOC).is_file() or not (store / ISSUES_DIR).is_dir():
        raise CircleError(f"no Circle project found at {root}")
    return store


# --------------------------------------------------------------------------
# Scalar normalisation
# --------------------------------------------------------------------------

def validate_id(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith(ID_PREFIX):
        raise CircleError(f"invalid issue ID: {value!r}")
    suffix = value[len(ID_PREFIX) :]
    if len(suffix) != ID_SUFFIX_LENGTH or any(char not in ID_ALPHABET for char in suffix):
        raise CircleError(f"invalid issue ID: {value!r}")
    return value


def normalize_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CircleError(f"{field} must be a non-empty string")
    return value.strip()


def optional_text(value: Any, field: str) -> str | None:
    return None if value is None else normalize_text(value, field)


def normalize_body(value: Any, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CircleError(f"{field} must be a string")
    return value.strip()


def normalize_string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CircleError(f"{field} must be an array of strings")
    return value


def normalize_blockers(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CircleError("blocked_by must be an array")
    result = [validate_id(item) for item in value]
    if len(result) != len(set(result)):
        raise CircleError("blocked_by contains duplicate IDs")
    return result


def normalize_acceptance(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise CircleError(f"{field} must be a non-empty array")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value, 1):
        label = f"{field}[{index}]"
        if isinstance(item, str):
            text, done = item, False
        elif isinstance(item, dict):
            unknown = sorted(set(item) - {"text", "done"})
            if unknown:
                raise CircleError(f"unknown keys in {label}: {', '.join(unknown)}")
            text, done = item.get("text"), bool(item.get("done", False))
        else:
            raise CircleError(f"{label} must be a string or an object")
        result.append({"text": normalize_text(text, f"{label}.text"), "done": done})
    return result


def normalize_title(value: Any, field: str, taken: set[str]) -> str:
    """Normalise a title and reject one already present in the project."""
    title = normalize_text(value, field)
    if title.casefold() in taken:
        raise CircleError(f"duplicate issue title: {title}")
    taken.add(title.casefold())
    return title


def parse_acceptance(raw: str, path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = ACCEPTANCE_PATTERN.match(stripped)
        if not match:
            raise CircleError(f"invalid acceptance criterion in {path}: {line!r}")
        items.append({"text": match.group(2).strip(), "done": match.group(1).lower() == "x"})
    if not items:
        raise CircleError(f"acceptance criteria must not be empty in {path}")
    return items


# --------------------------------------------------------------------------
# Issue documents
# --------------------------------------------------------------------------

def acceptance_indices(issue: dict[str, Any], requested: list[int]) -> set[int]:
    """Validate 1-based acceptance item numbers against one issue."""
    total = len(issue["acceptance"])
    for index in requested:
        if index < 1 or index > total:
            raise CircleError(
                f"acceptance item out of range: {index}; {issue['id']} has {total} items"
            )
    return set(requested)


def issue_fields(issue: dict[str, Any]) -> list[tuple[str, Any]]:
    return [(field, issue[field]) for field in FRONT_MATTER_FIELDS]


def issue_body(issue: dict[str, Any]) -> str:
    lines: list[str] = []
    for heading, field in PROSE_SECTIONS:
        lines += [f"## {heading}", "", issue[field].strip(), ""]
    lines += [f"## {ACCEPTANCE_SECTION}", ""]
    for item in issue["acceptance"]:
        lines.append(f"- [{'x' if item['done'] else ' '}] {item['text']}")
    comments = issue["comments"].strip()
    if comments:
        lines += ["", f"## {COMMENTS_SECTION}", "", comments]
    return "\n".join(lines)


def write_issue(root: Path, issue: dict[str, Any]) -> None:
    write_issue_to(issue_path(root, issue["id"]), issue)


def write_issue_to(path: Path, issue: dict[str, Any]) -> None:
    write_document(path, issue_fields(issue), issue_body(issue))


def load_issue(path: Path) -> dict[str, Any]:
    data, body = parse_document(path)
    missing = sorted(set(FRONT_MATTER_FIELDS) - data.keys())
    if missing:
        raise CircleError(f"missing issue fields in {path}: {', '.join(missing)}")
    unknown = sorted(set(data) - set(FRONT_MATTER_FIELDS))
    if unknown:
        raise CircleError(f"unknown issue fields in {path}: {', '.join(unknown)}")

    issue: dict[str, Any] = {
        "id": validate_id(data["id"]),
        "title": normalize_text(data["title"], "title"),
        "state": data["state"],
        "assignee": optional_text(data["assignee"], "assignee"),
        "blocked_by": normalize_blockers(data["blocked_by"]),
        "revision": data["revision"],
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
    }
    if issue["state"] not in STATES:
        raise CircleError(f"invalid state in {path}: {issue['state']!r}")
    if not isinstance(issue["revision"], int) or issue["revision"] < 1:
        raise CircleError(f"invalid revision in {path}")
    for field in ("created_at", "updated_at"):
        if not isinstance(issue[field], str):
            raise CircleError(f"invalid {field} in {path}")

    sections = parse_sections(body, path)
    unknown_sections = sorted(set(sections) - set(KNOWN_SECTIONS))
    if unknown_sections:
        raise CircleError(
            f"unknown issue sections in {path}: {', '.join(unknown_sections)}; "
            f"use '## {COMMENTS_SECTION}'"
        )
    for heading, field in PROSE_SECTIONS:
        if heading not in sections:
            raise CircleError(f"missing section '## {heading}' in {path}")
        if not sections[heading]:
            raise CircleError(f"section '## {heading}' must not be empty in {path}")
        issue[field] = sections[heading]
    if ACCEPTANCE_SECTION not in sections:
        raise CircleError(f"missing section '## {ACCEPTANCE_SECTION}' in {path}")
    issue["acceptance"] = parse_acceptance(sections[ACCEPTANCE_SECTION], path)
    issue["comments"] = sections.get(COMMENTS_SECTION, "")
    return issue


def load_issues(store: Path) -> dict[str, dict[str, Any]]:
    """Load every issue file. IDs are unique because a file must be named after its ID."""
    issues: dict[str, dict[str, Any]] = {}
    for path in sorted((store / ISSUES_DIR).glob("*.md")):
        issue = load_issue(path)
        if path.name != f"{issue['id']}.md":
            raise CircleError(f"issue filename does not match ID: {path}")
        issues[issue["id"]] = issue
    return issues


# --------------------------------------------------------------------------
# Project documents and store loading
# --------------------------------------------------------------------------

def load_project(store: Path) -> dict[str, Any]:
    fields, body = parse_document(store / AGENT_DOC)
    if set(fields) != {"name", "created_at"}:
        raise CircleError(f"{AGENT_DOC} must contain exactly name and created_at fields")
    if not body.strip():
        raise CircleError(f"{AGENT_DOC} body must not be empty")
    name = normalize_text(fields["name"], "project name")
    for doc in SUPPORTING_DOCS:
        if not (store / doc).is_file():
            raise CircleError(f"missing required document: {STORE_DIR}/{doc}")
    return {"name": name, "created_at": fields["created_at"], "agent": body}


def placeholder_documents(store: Path) -> list[str]:
    """File names of the project documents still holding placeholder content.

    Reads the files directly rather than the loaded model, so a document that is
    malformed in some other way is still reported instead of raising.
    """
    result: list[str] = []
    for doc in (AGENT_DOC,) + SUPPORTING_DOCS:
        try:
            text = (store / doc).read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        if PLACEHOLDER_MARKER in text or any(item in text for item in LEGACY_PLACEHOLDER_TEXTS):
            result.append(doc)
    return result


def load_store(store: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Load and fully validate a fact store. `store` is a resolved `.circle` path."""
    project = load_project(store)
    issues = load_issues(store)
    validate_graph(issues)
    return project, issues


def require_issue(issues: dict[str, dict[str, Any]], issue_id: str) -> dict[str, Any]:
    validate_id(issue_id)
    if issue_id not in issues:
        raise CircleError(f"unknown issue: {issue_id}")
    return issues[issue_id]


def check_revision(issue: dict[str, Any], expected: int) -> None:
    if issue["revision"] != expected:
        raise CircleError(
            f"stale revision for {issue['id']}: expected {expected}, "
            f"current {issue['revision']}; re-read the issue"
        )


# --------------------------------------------------------------------------
# Graph invariants
# --------------------------------------------------------------------------

def validate_graph(issues: dict[str, dict[str, Any]]) -> None:
    known = set(issues)
    for issue_id, issue in issues.items():
        for blocker in issue["blocked_by"]:
            if blocker not in known:
                raise CircleError(f"unknown blocker {blocker} on {issue_id}")
            if blocker == issue_id:
                raise CircleError(f"self dependency on {issue_id}")
        if issue["state"] in {"in_progress", DONE}:
            unfinished = unfinished_blockers(issue, issues)
            if unfinished:
                raise CircleError(
                    f"{issue_id} is {issue['state']} with unfinished blockers: "
                    f"{', '.join(unfinished)}"
                )
    assert_acyclic(issues)


# --------------------------------------------------------------------------
# Creation
# --------------------------------------------------------------------------

def generate_id(existing: set[str]) -> str:
    while True:
        candidate = ID_PREFIX + "".join(secrets.choice(ID_ALPHABET) for _ in range(ID_SUFFIX_LENGTH))
        if candidate not in existing:
            return candidate


def read_state(data: dict[str, Any], label: str) -> str:
    state = data.get("state", DRAFT)
    if state not in {DRAFT, READY}:
        raise CircleError(f"{label}.state must be {DRAFT} or {READY}")
    return state


def build_issue(
    data: dict[str, Any],
    label: str,
    issue_id: str,
    title: str,
    blocked_by: list[str],
    state: str,
) -> dict[str, Any]:
    created = now()
    return {
        "id": issue_id,
        "title": title,
        "state": state,
        "assignee": optional_text(data.get("assignee"), f"{label}.assignee"),
        "blocked_by": blocked_by,
        "revision": 1,
        "created_at": created,
        "updated_at": created,
        "goal": normalize_text(data.get("goal"), f"{label}.goal"),
        "expected_behavior": normalize_text(
            data.get("expected_behavior"), f"{label}.expected_behavior"
        ),
        "boundaries": normalize_text(data.get("boundaries"), f"{label}.boundaries"),
        "acceptance": normalize_acceptance(data.get("acceptance"), f"{label}.acceptance"),
        "comments": "",
    }


def normalize_new_issue(data: dict[str, Any], issues: dict[str, dict[str, Any]]) -> dict[str, Any]:
    unknown = sorted(set(data) - set(CREATION_FIELDS))
    if unknown:
        raise CircleError(f"unknown issue fields: {', '.join(unknown)}")
    taken = {issue["title"].casefold() for issue in issues.values()}
    return build_issue(
        data,
        "issue",
        generate_id(set(issues)),
        normalize_title(data.get("title"), "issue.title", taken),
        normalize_blockers(data.get("blocked_by", [])),
        read_state(data, "issue"),
    )


def normalize_issue_batch(
    raw_issues: Any, existing: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Normalise a batch of issues, resolving `key` references to permanent IDs.

    A `blocked_by` entry is either a `key` from this batch or the ID of an issue
    that already exists. Returns the issues plus the key-to-ID map.
    """
    if not isinstance(raw_issues, list) or not raw_issues:
        raise CircleError("at least one issue is required")

    ids = set(existing)
    key_map: dict[str, str] = {}
    taken = {issue["title"].casefold() for issue in existing.values()}
    built: list[dict[str, Any]] = []
    references: dict[str, list[str]] = {}

    for index, item in enumerate(raw_issues, 1):
        label = f"issues[{index}]"
        if not isinstance(item, dict):
            raise CircleError(f"issue {index} must be an object")
        unknown = sorted(set(item) - set(IMPORT_FIELDS))
        if unknown:
            raise CircleError(f"unknown fields on issue {index}: {', '.join(unknown)}")
        key = normalize_text(item.get("key"), f"{label}.key")
        if key in key_map:
            raise CircleError(f"duplicate issue key: {key}")
        refs = item.get("blocked_by", [])
        if not isinstance(refs, list) or any(not isinstance(x, str) for x in refs):
            raise CircleError(f"{label}.blocked_by must be an array of keys or issue IDs")

        issue_id = generate_id(ids)
        ids.add(issue_id)
        key_map[key] = issue_id
        built.append(build_issue(
            item,
            label,
            issue_id,
            normalize_title(item.get("title"), f"{label}.title", taken),
            [],
            read_state(item, label),
        ))
        references[issue_id] = refs

    known = dict(existing)
    known.update({issue["id"]: issue for issue in built})
    for issue in built:
        resolved: list[str] = []
        for reference in references[issue["id"]]:
            if reference in key_map:
                resolved.append(key_map[reference])
            elif reference in existing:
                resolved.append(reference)
            else:
                raise CircleError(
                    f"unknown blocker {reference!r} on {issue['title']}: expected a key from "
                    f"this batch or an existing issue ID"
                )
        if len(resolved) != len(set(resolved)):
            raise CircleError(f"duplicate blockers on {issue['title']}")
        issue["blocked_by"] = resolved
    validate_graph(known)
    return built, key_map


# --------------------------------------------------------------------------
# Derived views
# --------------------------------------------------------------------------

def issue_view(issue: dict[str, Any], issues: dict[str, dict[str, Any]]) -> dict[str, Any]:
    unblocked = is_unblocked(issue, issues)
    view = dict(issue)
    view["dependency_status"] = "unblocked" if unblocked else "blocked"
    view["actionable"] = issue["state"] == READY and unblocked
    return view


def render_dag(issues: dict[str, dict[str, Any]]) -> str:
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


# --------------------------------------------------------------------------
# Locks
# --------------------------------------------------------------------------

@contextlib.contextmanager
def root_lock(root: Path) -> Iterator[None]:
    """Serialise store creation against other commits into the same root."""
    fd = os.open(root, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextlib.contextmanager
def store_lock(root: Path) -> Iterator[Path]:
    """Serialise mutations on one fact store, yielding the resolved store path."""
    store = require_store(root)
    with (store / AGENT_DOC).open("r", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield store
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
