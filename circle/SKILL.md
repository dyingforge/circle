---
name: circle
description: Control a local Markdown project with independent CIR issues and a dependency DAG. Require an explicit `$circle /init` to initialize a workspace. When the workspace already contains `.circle/`, use this skill for explicit `$circle` commands or clear natural-language requests to inspect project status, maintain issues, change lifecycle state, manage dependencies, validate the fact store, or render its Mermaid DAG. Also use it to continue an unambiguous confirmation of a Preview started by an explicit Circle initialization.
---

# Circle

Manage the `.circle/` fact store with the deterministic `scripts/circle.py` controller. Never execute issues automatically and never connect to an external issue tracker.

## Invocation contract

- If `.circle/` does not exist, start initialization only when the user explicitly invokes `$circle /init`. Never infer initialization from a general project-management request.
- Treat an unambiguous confirmation or rejection of a Preview produced by an explicit `$circle /init` in this conversation as part of that same explicit initialization. Do not require the user to repeat `$circle`. On confirmation, commit only the displayed snapshot hash and project root; on rejection, leave the workspace unchanged.
- Accept `$circle /commit <snapshot-hash>` before installation only when it refers to a Preview previously produced by an explicit `$circle /init` for the same project root.
- If `.circle/` exists, handle both explicit `$circle` commands and clear natural-language requests to manage that Circle project. Do not interpret requests to execute project work as Circle management commands.
- Treat `.circle/PROJECT.md` and `.circle/issues/*.md` as facts. Treat `.circle/DAG.md` as a derived view.
- Run all controller commands with `python3 <skill-dir>/scripts/circle.py`.
- Pass the user's current workspace as `--project-root`.
- Show controller errors as-is and leave the fact store unchanged on failure.
- Run `/render` only when explicitly requested; issue mutations do not refresh `DAG.md`.

## Route commands

Map user commands to controller subcommands:

```text
/init <document or pasted text>       normalize input, then preview
/commit <snapshot-hash>               commit --snapshot <snapshot-hash>
/status                               status
/render                               render
/validate                             validate
/issue list                           issue-list
/issue show <id>                      issue-show --id <id>
/issue transition <id> <state>        issue-transition --id <id> --state <state>
/dependency add <issue> <blocker>     dependency-add --id <issue> --blocker <blocker>
/dependency remove <issue> <blocker>  dependency-remove --id <issue> --blocker <blocker>
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
2. Present the Preview and ask for confirmation. State that a plain, unambiguous confirmation is sufficient; `$circle /commit <hash>` is also accepted. Do not modify the target project before confirmation.
3. After an unambiguous confirmation, run `commit --snapshot <hash>` using the hash shown in that Preview and the same project root. Treat this as continuation of the original explicit initialization even when the confirmation does not repeat `$circle`. Do not parse the source input again. If the snapshot is absent or invalid, request a new explicit `$circle /init` Preview.

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
