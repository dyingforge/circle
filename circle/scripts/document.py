"""Markdown document codec: JSON front matter, `##` sections, atomic writes.

Front-matter values are written with `json.dumps`, so a value may contain
quotes, newlines or non-ASCII text and still round-trip on a single line.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from errors import CircleError


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CircleError(f"missing file: {path}") from exc


def parse_document(path: Path) -> tuple[dict[str, Any], str]:
    lines = read_text(path).splitlines()
    if not lines or lines[0] != "---":
        raise CircleError(f"missing front matter: {path}")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise CircleError(f"unterminated front matter: {path}") from exc
    fields: dict[str, Any] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        if ":" not in line:
            raise CircleError(f"invalid front-matter line in {path}: {line}")
        key, raw = line.split(":", 1)
        fields[key.strip()] = parse_scalar(raw.strip())
    return fields, "\n".join(lines[end + 1 :]).strip()


def parse_sections(body: str, path: Path) -> dict[str, str]:
    """Split a body into `## Heading` sections. Order is irrelevant to callers."""
    sections: dict[str, str] = {}
    heading: str | None = None
    buffer: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            if heading is not None:
                sections[heading] = "\n".join(buffer).strip()
            heading = line[3:].strip()
            if heading in sections:
                raise CircleError(f"duplicate section '## {heading}' in {path}")
            buffer = []
        elif heading is None:
            if line.strip():
                raise CircleError(f"content before the first section in {path}: {line!r}")
        else:
            buffer.append(line)
    if heading is not None:
        sections[heading] = "\n".join(buffer).strip()
    return sections


def write_document(path: Path, fields: list[tuple[str, Any]], body: str) -> None:
    lines = ["---"]
    lines.extend(f"{key}: {dump_scalar(value)}" for key, value in fields)
    lines.extend(["---", "", body.strip(), ""])
    atomic_write(path, "\n".join(lines))


def write_text(path: Path, text: str) -> None:
    atomic_write(path, text.rstrip("\n") + "\n")


def dump_scalar(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def parse_scalar(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CircleError(f"invalid front-matter value: {raw}") from exc


def atomic_write(path: Path, content: str) -> None:
    """Write via a sibling temp file so readers never observe a partial file."""
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
