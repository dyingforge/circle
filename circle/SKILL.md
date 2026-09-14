---
name: circle
description: Control a local Markdown project with structured CIR issues and a dependency DAG. Require an explicit `$circle /init` to initialize a workspace. When the workspace already contains `.circle/`, use this skill for explicit `$circle` commands or clear natural-language requests to inspect project status, add or maintain issues, change lifecycle state, manage dependencies, validate the fact store, render its Mermaid DAG, or execute an issue on its own git branch. Also use it to continue an unambiguous confirmation of a Preview started by an explicit Circle initialization.
---

# Circle

Manage the `.circle/` fact store with the deterministic `scripts/circle.py` controller. Never connect to an external issue tracker.

Circle controls projects; it does not implement them. You implement an issue when the user asks you to execute it, following the branch workflow below.

## Invocation contract

- If `.circle/` does not exist, start initialization only when the user explicitly invokes `$circle /init`. Never infer initialization from a general project-management request.
- Treat an unambiguous confirmation or rejection of a Preview produced by an explicit `$circle /init` in this conversation as part of that same explicit initialization. Do not require the user to repeat `$circle`. On confirmation, commit only the displayed snapshot hash and project root; on rejection, leave the workspace unchanged.
- Accept `$circle /commit <snapshot-hash>` before installation only when it refers to a Preview previously produced by an explicit `$circle /init` for the same project root.
- If `.circle/` exists, handle both explicit `$circle` commands and clear natural-language requests to manage that Circle project.
- Treat `.circle/AGENT.md`, `.circle/ARCHITECTURE.md`, `.circle/DOMAIN.md` and `.circle/issues/*.md` as facts. Treat `.circle/DAG.md` as a derived view.
- Run all controller commands with `python3 <skill-dir>/scripts/circle.py`.
- Pass the user's current workspace as `--project-root`.
- Show controller errors as-is and leave the fact store unchanged on failure.
- Run `/render` only when explicitly requested; issue mutations do not refresh `DAG.md`.

## Fact store

```text
.circle/
├── AGENT.md            # 项目身份（front matter: name, created_at）+ 协作约定
├── ARCHITECTURE.md     # 系统结构
├── DOMAIN.md           # 领域概念与术语
├── issues/
│   └── CIR-XXXXXXXXXX.md
└── DAG.md              # 派生视图，由 /render 重建
```

`AGENT.md`、`ARCHITECTURE.md`、`DOMAIN.md` 和每个 Issue 文件都是事实源。执行 Issue 时只需要这些文档加上仓库本身。

## Route commands

```text
/init <document or pasted text>       normalize input, then preview
/commit <snapshot-hash>               commit --snapshot <snapshot-hash>
/status                               status
/render                               render
/validate                             validate
/issue list                           issue-list
/issue show <id>                      issue-show --id <id>
/issue context <id>                   issue-context --id <id>
/issue add                            issue-add
/issue import                         issue-import
/issue edit <id>                      issue-edit --id <id> --expected-revision <revision>
/issue transition <id> <state>        issue-transition --id <id> --state <state>
/issue start <id>                     issue-branch --id <id>
/issue finish <id>                    issue-finish --id <id> [--into <branch>]
/dependency add <issue> <blocker>     dependency-add --id <issue> --blocker <blocker>
/dependency remove <issue> <blocker>  dependency-remove --id <issue> --blocker <blocker>
```

For every mutation of an existing issue, first read it with `issue-show`, take its current `revision`, and pass that value as `--expected-revision`. Pass an optional transition note with `--note`.

## Issue content model

Every issue carries six content fields. The first four live in the Markdown body as fixed sections; the controller rejects an issue whose sections are missing or empty.

```markdown
## Goal

这个 Issue 要达成什么。

## Expected Behavior

完成后系统可观察到的行为。

## Boundaries

明确不做什么。

## Acceptance Criteria

- [ ] 可验证的验收项
- [ ] 可验证的验收项
```

`blocked_by`（前置依赖）和 `assignee`（负责人）保存在 front matter。`## Comments` 是唯一允许的额外小节，由 `--note` 追加；任何其他小节都会被拒绝。

`## Acceptance Criteria` 是唯一的验收来源：执行 Issue 时逐项核对，未满足就不能进入 `done`。

## Import with Preview and Commit

Parse the user's import input exactly once. Normalize it into this JSON shape:

```json
{
  "project": {"name": "Project name", "description": "Project description"},
  "docs": {
    "agent": "AGENT.md 正文：协作约定与实现规范",
    "architecture": "ARCHITECTURE.md 正文",
    "domain": "DOMAIN.md 正文"
  },
  "issues": [
    {
      "key": "temporary-key",
      "title": "Issue title",
      "goal": "要达成什么",
      "expected_behavior": "完成后可观察到的行为",
      "boundaries": "明确不做什么",
      "acceptance": ["可验证的验收项", "可验证的验收项"],
      "blocked_by": ["other-temporary-key"],
      "assignee": "Alice",
      "state": "draft"
    }
  ],
  "inferences": ["Fields inferred from context"],
  "warnings": [],
  "raw_summary": "Brief summary of the supplied input"
}
```

`docs` 是可选的整体文档设计。未提供时控制器写入占位内容并记入 `inferences`。`state` 只能是 `draft`（默认）或 `ready`。

When the user needs a starting point, provide this Chinese Markdown template:

```markdown
# 项目名称

项目介绍。

## Issue: 实现数据模型
key: data-model
blocked_by: none
assignee: Alice

**目标**：定义项目与 Issue 的存储结构。
**预期行为**：字段可读写，revision 单调递增。
**边界**：不实现查询语言。
**验收标准**：
- [ ] 可以创建并校验 Issue 文件
- [ ] 非法字段会被拒绝

## Issue: 生成 DAG
key: dag-render
blocked_by: data-model
assignee: Bob

**目标**：从事实源生成依赖图。
**预期行为**：`/render` 重建 `.circle/DAG.md`。
**边界**：不计算关键路径。
**验收标准**：
- [ ] DAG 包含全部 Issue 与依赖边
```

Accept loosely structured input. Infer fields from titles or context only when the result is unambiguous, list every inference in the Preview, and ask the user to resolve ambiguous dependencies before running `preview`.

Use `null` for an unknown assignee and `[]` for no dependencies. Record every inferred field in `inferences`. Do not invent an ambiguous dependency: stop and ask the user to resolve it.

1. Pipe the normalized JSON to `preview`. The controller allocates permanent IDs, resolves temporary keys, validates the graph, saves a snapshot outside the target repository, and prints the Preview plus a snapshot hash.
2. Present the Preview and ask for confirmation. State that a plain, unambiguous confirmation is sufficient; `$circle /commit <hash>` is also accepted. Do not modify the target project before confirmation.
3. After an unambiguous confirmation, run `commit --snapshot <hash>` using the hash shown in that Preview and the same project root. Treat this as continuation of the original explicit initialization even when the confirmation does not repeat `$circle`. Do not parse the source input again. If the snapshot is absent or invalid, request a new explicit `$circle /init` Preview.

## Add issues after import

After `.circle/` exists, a later document or pasted text can add issues without touching existing facts.

- One issue: send one JSON object on standard input to `issue-add`. Supported fields are `title`, `goal`, `expected_behavior`, `boundaries`, `acceptance`, `assignee`, `blocked_by`, and `state`; the default state is `draft`.
- Several issues from a document: normalize the text into `{"issues": [...], "inferences": [], "warnings": []}` and send it to `issue-import`.
- For `/issue edit`, first show the issue, then send only the requested changes as a JSON object on standard input to `issue-edit --id <id> --expected-revision <revision>`. Supported editable fields are `title`, `goal`, `expected_behavior`, `boundaries`, `acceptance`, `assignee`, and `blocked_by`.

`issue-import` is additive and all-or-nothing: `key` values reference other issues in the same batch, an existing issue is referenced by its `CIR-` ID, and any unknown reference rejects the whole batch without writing. Never reuse a title already present in the project.

## Execute an issue

When the user asks to execute, implement, or start an issue, follow this procedure:

1. Check with `issue-show` that the issue is unblocked and not `done` or `cancelled`.
2. Run `issue-branch --id <id>`. It creates `circle/<id>` from the current branch, records the base branch, and prints the execution context.
3. Read only the documents the context lists: `.circle/AGENT.md`, `.circle/ARCHITECTURE.md`, `.circle/DOMAIN.md`, and the issue file. Use `.circle/AGENT.md` for project conventions and `.circle/DOMAIN.md` for vocabulary. Use `issue-context --id <id>` alone when you only need the bundle without creating a branch.
4. Implement the issue on that branch and commit there as usual. The controller never implements anything for you.
5. When every acceptance criterion holds, run `issue-finish --id <id>`. It merges `circle/<id>` back into the branch it was created from and deletes it. Pass `--into <branch>` only when the recorded base branch is gone.
6. Advance the issue with `issue-transition`, for example `in_progress` → `review` → `done`.

Rules for this workflow:

- Never start work on a blocked issue, and never implement an issue on the base branch directly.
- Never merge or delete the issue branch by hand; `issue-finish` derives both the branch name and the base branch from the issue.
- `issue-branch` and `issue-finish` refuse to run with uncommitted changes.
- If `issue-finish` reports a conflict it has already aborted the merge. Reconcile `circle/<id>` manually, then retry.
- Do not leave an issue branch behind: finish it, or tell the user it is still open.

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
