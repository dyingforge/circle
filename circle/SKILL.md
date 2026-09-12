---
name: circle
description: Control a local Markdown project with independent CIR issues and a dependency DAG. Use only when the user explicitly invokes `$circle` to import a project document, preview or commit a Circle project, inspect project status, maintain issues, change issue lifecycle state, add or remove dependencies, validate the fact store, or render its Mermaid DAG.
---

# Circle

Manage the `.circle/` fact store with the deterministic `scripts/circle.py` controller. Never execute issues automatically and never connect to an external issue tracker.

## Invocation contract

- Act only on explicit `$circle` invocation.
- Treat `.circle/PROJECT.md` and `.circle/issues/*.md` as facts. Treat `.circle/DAG.md` as a derived view.
- Run all controller commands with `python3 <skill-dir>/scripts/circle.py`.
- Pass the user's current workspace as `--project-root`.
- Show controller errors as-is and leave the fact store unchanged on failure.
- Run `/render` only when explicitly requested; issue mutations do not refresh `DAG.md`.

## Route commands

Map user commands to controller subcommands:

```text
/status                              status
/render                              render
/validate                            validate
/issue list                          issue-list
/issue show <id>                     issue-show --id <id>
/issue transition <id> <state>       issue-transition --id <id> --state <state>
/dependency add <issue> <blocker>    dependency-add --id <issue> --blocker <blocker>
/dependency remove <issue> <blocker> dependency-remove --id <issue> --blocker <blocker>
```

For every mutation of an existing issue, first read it with `issue-show`, take its current `revision`, and pass that value as `--expected-revision`. Pass an optional transition note with `--note`.

For `/issue add`, send one JSON object on standard input to `issue-add`. Supported fields are `title`, `body`, `assignee`, `estimate`, `blocked_by`, and `state`; default state is `draft`.

For `/issue edit`, first show the issue, then send only the requested changes as a JSON object on standard input to `issue-edit --id <id> --expected-revision <revision>`. Supported editable fields are `title`, `body`, `assignee`, `estimate`, and `blocked_by`.

## Import with Preview and Commit

Parse the user's import input exactly once. Normalize it into this JSON shape:

```json
{
  "project": {"name": "Project name", "description": "Project description"},
  "issues": [
    {
      "key": "temporary-key",
      "title": "Issue title",
      "body": "Execution details",
      "blocked_by": ["other-temporary-key"],
      "estimate": "2 days",
      "assignee": "Alice"
    }
  ],
  "inferences": ["Fields inferred from context"],
  "warnings": [],
  "raw_summary": "Brief summary of the supplied input"
}
```

When the user needs a starting point, provide this Chinese Markdown template:

```markdown
# 项目名称

项目介绍。

## Issue: 实现数据模型
key: data-model
blocked_by: none
estimate: 2 days
assignee: Alice

定义项目和 Issue 的存储结构。

## Issue: 生成 DAG
key: dag-render
blocked_by: data-model
estimate: 1 day
assignee: Bob

根据 Issue 依赖生成 DAG。
```

Accept loosely structured input. Infer fields from titles or context only when the result is unambiguous, list every inference in the Preview, and ask the user to resolve ambiguous dependencies before running `preview`.

Use `null` for unknown assignee or estimate and `[]` for no dependencies. Record every inferred field in `inferences`. Do not invent an ambiguous dependency: stop and ask the user to resolve it.

1. Pipe the normalized JSON to `preview`. The controller allocates permanent IDs, resolves temporary keys, validates the graph, saves a snapshot outside the target repository, and prints the Preview plus a snapshot hash.
2. Present the Preview and ask for confirmation. Do not modify the target project before confirmation.
3. After confirmation, run `commit --snapshot <hash>` using the printed hash and the same project root. Do not parse the source input again. If the snapshot is absent or invalid, request a new Preview.

## Interpret status

- Advance issues only along `draft → ready → in_progress → review → done`.
- Allow any non-terminal issue to transition to `cancelled`; restore `cancelled` only to `draft`.
- `blocked`: at least one blocker is not `done`.
- `unblocked`: every blocker is `done`.
- `actionable`: state is `ready` and the issue is unblocked.
- `done` is terminal. Create a correction issue for later omissions or mistakes.
- A cancelled blocker remains blocking until restored and completed.

After resolving a Git merge conflict in Circle facts, run `validate`; tell the user that `/render` is also required to refresh the DAG.

After a successful mutation, summarize the changed issue ID, new revision, and any newly actionable issues reported by the controller.
