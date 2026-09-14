"""Dependency graph: acyclicity and blocker predicates.

Every function here assumes blockers reference known issues. `model.validate_graph`
is what guarantees that, and it owns the unknown-blocker check; keeping it in one
place is why these functions carry no defensive branch for it.
"""

from __future__ import annotations

from typing import Any, Iterator

from errors import CircleError


DONE = "done"
READY = "ready"


def unfinished_blockers(issue: dict[str, Any], issues: dict[str, dict[str, Any]]) -> list[str]:
    return [item for item in issue["blocked_by"] if issues[item]["state"] != DONE]


def is_unblocked(issue: dict[str, Any], issues: dict[str, dict[str, Any]]) -> bool:
    return not unfinished_blockers(issue, issues)


def actionable_ids(issues: dict[str, dict[str, Any]]) -> set[str]:
    return {
        issue["id"] for issue in issues.values()
        if issue["state"] == READY and is_unblocked(issue, issues)
    }


def assert_acyclic(issues: dict[str, dict[str, Any]]) -> None:
    """Iterative depth-first search; the recursive form overflowed on long chains."""
    done: set[str] = set()
    for start in issues:
        if start in done:
            continue
        stack: list[tuple[str, Iterator[str]]] = [(start, iter(issues[start]["blocked_by"]))]
        path: list[str] = [start]
        on_path = {start}
        while stack:
            node, blockers = stack[-1]
            descended = False
            for blocker in blockers:
                if blocker in on_path:
                    cycle = path[path.index(blocker) :] + [blocker]
                    raise CircleError(f"dependency cycle: {' -> '.join(cycle)}")
                if blocker in done:
                    continue
                on_path.add(blocker)
                path.append(blocker)
                stack.append((blocker, iter(issues[blocker]["blocked_by"])))
                descended = True
                break
            if not descended:
                stack.pop()
                path.pop()
                on_path.discard(node)
                done.add(node)
