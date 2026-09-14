"""Preview and Commit: the tamper-evident snapshot taken between the two phases.

`preview` normalises an import into a snapshot and stores it outside the target
repository. `commit` only ever accepts that snapshot back, verified by hash and
project root, so the source document is parsed exactly once.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import string
import tempfile
from typing import Any

from document import atomic_write
from errors import CircleError
from model import (
    ACCEPTANCE_SECTION,
    DOCUMENTS,
    PLACEHOLDER_MARKER,
    PROSE_SECTIONS,
    acceptance_line,
    normalize_body,
    normalize_issue_batch,
    normalize_string_list,
    normalize_text,
    now,
    validate_graph,
)


IMPORT_KEYS = ("project", "docs", "issues", "inferences", "warnings", "raw_summary")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in snapshot.items() if key != "snapshot_hash"}
    return hashlib.sha256(canonical_bytes(unsigned)).hexdigest()


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
    issues = snapshot.get("issues", [])
    graph = {item["id"]: item for item in issues}
    if len(graph) != len(issues):
        raise CircleError("snapshot contains duplicate issue IDs")
    validate_graph(graph)
    return snapshot, path


def default_agent_doc() -> str:
    return f"{PLACEHOLDER_MARKER}\n\n## 工作约定\n\nTODO: 补充本项目的协作约定与实现规范。"


def default_document(name: str, kind: str) -> str:
    label = kind.title()
    return f"{PLACEHOLDER_MARKER}\n\n# {name}: {label}\n\nTODO: 补充{label}相关内容。\n"


def compose_agent_body(description: str, agent_doc: str) -> str:
    parts = [part.strip() for part in (description, agent_doc) if part and part.strip()]
    return "\n\n".join(parts)


def normalize_import(
    raw: dict[str, Any], root: Path, allow_placeholder_docs: bool = False
) -> dict[str, Any]:
    unknown = sorted(set(raw) - set(IMPORT_KEYS))
    if unknown:
        raise CircleError(f"unknown import fields: {', '.join(unknown)}")
    project = raw.get("project")
    if not isinstance(project, dict):
        raise CircleError("import requires a project object")
    name = normalize_text(project.get("name"), "project.name")
    description = normalize_body(project.get("description", ""), "project.description")
    inferences = normalize_string_list(raw.get("inferences"), "inferences")
    warnings = normalize_string_list(raw.get("warnings"), "warnings")
    issues, issue_keys = normalize_issue_batch(raw.get("issues"), {})

    docs = raw.get("docs", {})
    if not isinstance(docs, dict):
        raise CircleError("docs must be an object")
    unknown = sorted(set(docs) - {field for field, _ in DOCUMENTS})
    if unknown:
        raise CircleError(f"unknown docs fields: {', '.join(unknown)}")
    resolved: dict[str, str] = {}
    missing: list[tuple[str, str]] = []
    for field, doc in DOCUMENTS:
        value = docs.get(field)
        if value is None or not str(value).strip():
            resolved[field] = (
                default_agent_doc() if field == "agent" else default_document(name, field)
            )
            missing.append((field, doc))
        else:
            resolved[field] = normalize_body(value, f"docs.{field}")
    if missing and not allow_placeholder_docs:
        raise CircleError(
            "missing project documents: "
            + ", ".join(doc for _, doc in missing)
            + "; ask the user for their content, or pass --allow-placeholder-docs to "
            "accept TODO placeholders"
        )
    inferences.extend(f"docs.{field} 未提供，已写入占位内容" for field, _ in missing)

    snapshot = {
        "project_root": str(root.resolve()),
        "project": {"name": name, "description": description},
        "docs": resolved,
        "issues": issues,
        "issue_keys": {issue_id: key for key, issue_id in issue_keys.items()},
        "inferences": inferences,
        "warnings": warnings,
        "raw_summary": normalize_body(raw.get("raw_summary", ""), "raw_summary"),
        "created_at": now(),
    }
    snapshot["snapshot_hash"] = snapshot_hash(snapshot)
    return snapshot


def render_preview(snapshot: dict[str, Any]) -> str:
    lines = [f"# Preview: {snapshot['project']['name']}", ""]
    description = snapshot["project"]["description"]
    if description:
        lines.extend([description, ""])
    lines.extend([
        "| ID | key | Issue | state | blocked_by | assignee |",
        "|---|---|---|---|---|---|",
    ])
    issue_keys = snapshot["issue_keys"]
    for issue in snapshot["issues"]:
        blockers = ", ".join(issue["blocked_by"]) or "—"
        lines.append(
            f"| {issue['id']} | {issue_keys[issue['id']]} | {issue['title']} | {issue['state']} | "
            f"{blockers} | {issue['assignee'] or '—'} |"
        )
    lines.extend(["", "## Documents", ""])
    for field, doc in DOCUMENTS:
        lines.append(f"- `{doc}` ({len(snapshot['docs'][field])} chars)")
    for issue in snapshot["issues"]:
        lines.extend(["", f"### {issue['id']}: {issue['title']}"])
        for heading, field in PROSE_SECTIONS:
            lines += ["", f"**{heading}**", "", issue[field]]
        lines += ["", f"**{ACCEPTANCE_SECTION}**", ""]
        lines.extend(acceptance_line(item) for item in issue["acceptance"])
    for label in ("inferences", "warnings"):
        if snapshot[label]:
            lines.extend(["", f"## {label.title()}", ""] + [f"- {item}" for item in snapshot[label]])
    if snapshot["raw_summary"]:
        lines.extend(["", "## Source summary", "", snapshot["raw_summary"]])
    lines.extend(["", f"Snapshot: `{snapshot['snapshot_hash']}`"])
    return "\n".join(lines)
