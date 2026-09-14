# Circle

Circle 是一个面向 Codex 的本地项目控制 Skill。它把项目拆成独立的 Markdown Issue，用有向无环图（DAG）描述依赖关系，并通过确定性的 Python 控制器维护状态、依赖和并发修订号。

它适合希望把项目事实保留在 Git 仓库中、又不想依赖 GitHub Issues、Linear 等外部服务的团队。

## 核心能力

- 从 Markdown 或纯文本中整理项目、整体文档与 Issue
- 初始化前先生成 Preview，确认后才写入工作区
- 为每个 Issue 分配不可变的 `CIR-XXXXXXXXXX` ID
- 每个 Issue 记录目标、预期行为、边界、验收标准、依赖和负责人
- 校验未知依赖、自依赖和依赖环
- 根据依赖自动计算 `blocked`、`unblocked` 和 `actionable`
- 用 `revision` 检测当前工作树中的陈旧写入
- 生成 Mermaid 依赖图
- 初始化后仍可从后续文档批量新增 Issue
- 执行 Issue 时按 Issue 创建临时 Git 分支，完成后合并回原分支并删除

Circle 负责项目控制，不会连接外部 Issue Tracker。执行 Issue 的主体是 agent 本身，Circle 只负责准备上下文、隔离分支和收尾合并。

## 文档模型

初始化后，目标项目会得到以下结构：

```text
.circle/
├── AGENT.md            # 项目身份（name、created_at）+ 协作约定
├── ARCHITECTURE.md     # 系统结构
├── DOMAIN.md           # 领域概念与术语
├── issues/
│   ├── CIR-XXXXXXXXXX.md
│   └── CIR-YYYYYYYYYY.md
└── DAG.md
```

- `AGENT.md`、`ARCHITECTURE.md`、`DOMAIN.md` 和 `issues/*.md` 是事实源。
- `DAG.md` 是可重新生成的派生视图，不是事实源。
- 每个 Issue 使用独立文件，降低不同 Issue 并行修改时的冲突概率。
- 修改 Issue 后不会自动刷新 DAG，需要显式执行 `/render`，从而减少多人协作时对同一文件的冲突。
- 执行某个 Issue 时，agent 只需要 `AGENT.md`、`ARCHITECTURE.md`、`DOMAIN.md` 和该 Issue 本身。

`AGENT.md` 的 front matter 固定包含 `name` 和 `created_at`，正文是项目介绍与协作约定。`ARCHITECTURE.md` 与 `DOMAIN.md` 是普通 Markdown 文档；初始化时未提供时，控制器会写入占位内容并在 Preview 的推断中说明。

## Issue 内容模型

每个 Issue 有六个内容字段。前四个固定为 Markdown 正文中的四个小节，缺失或为空都会被拒绝：

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

`blocked_by`（前置依赖）和 `assignee`（负责人）保存在 front matter：

```yaml
id: "CIR-XXXXXXXXXX"
title: "生成 DAG"
state: "ready"
assignee: "Bob"
blocked_by: ["CIR-AAAAAAAAAA"]
revision: 1
created_at: "..."
updated_at: "..."
```

`## Comments` 是唯一允许的额外小节，由 `/issue transition --note` 追加；任何其他小节都会被拒绝。

`## Acceptance Criteria` 是唯一的验收来源。执行 Issue 时逐项核对，未全部满足就不能进入 `done`。

## 工作方式

Circle 由两部分组成：

- [`circle/SKILL.md`](circle/SKILL.md)：定义 Codex 如何理解用户输入、何时调用控制器、Preview/Commit 交互规则，以及执行 Issue 的流程。
- [`circle/scripts/circle.py`](circle/scripts/circle.py)：无第三方依赖的确定性控制器，负责校验、修改 `.circle/` 事实库，以及创建和合并 Issue 分支。

## 环境要求

- Codex（用于加载并调用 Skill）
- Python 3.9 或更高版本（已在 3.9.6 与 3.13 上验证）
- macOS 或 Linux 等支持 `fcntl` 文件锁的系统
- Git（仅执行 Issue 的分支工作流需要）
- 无需安装第三方 Python 包

## 安装

将仓库中的 `circle/` 目录复制或链接到个人 Codex skills 目录：

```bash
mkdir -p ~/.codex/skills
ln -s /path/to/this-repository/circle ~/.codex/skills/circle
```

如果目标位置已经存在，请先确认其中内容，再选择更新或移除旧版本。安装后重新启动 Codex 或开始一个新会话，使 Skill 被重新发现。

## 快速开始

Circle 只会在用户明确调用 `$circle /init` 时初始化尚未安装 Circle 的项目。

在目标项目中向 Codex 提交：

```text
$circle /init

# 示例项目

用于演示 Circle 的项目。

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

Codex 会整理输入并调用控制器生成 Preview。此阶段不会在目标项目中创建 `.circle/`。Preview 会展示正式 Issue ID、文档、依赖、推断、警告和快照哈希。

确认 Preview 后，可以直接回复“确认”，也可以显式提交快照：

```text
$circle /commit <snapshot-hash>
```

Commit 只接受同一项目根目录下、内容完整且哈希匹配的 Preview 快照；成功后才会创建 `.circle/`。

## 执行 Issue

每个 Issue 都在自己的临时分支上执行，完成后合并回创建它的分支并删除该分支。分支名与基线分支始终从 Issue 推导得出。

```text
$circle /issue start <id>     # 创建 circle/<id>，并打印执行上下文
$circle /issue finish <id>    # 合并回基线分支并删除 circle/<id>
```

`/issue start` 会拒绝被阻塞的 Issue、`done`/`cancelled` 的 Issue，以及工作树不干净的情况。它同时打印执行上下文：`AGENT.md`、`ARCHITECTURE.md`、`DOMAIN.md` 和当前 Issue 的完整内容。只想查看上下文而不创建分支时使用 `/issue context <id>`。

需要 Git 仓库，且项目根目录必须是该仓库的工作树根目录。基线分支由 `git config branch.circle/<id>.circlebase` 记录，随分支删除自动清理；也可以用 `/issue finish <id> --into <branch>` 显式指定。

如果合并产生冲突，控制器会执行 `git merge --abort` 回到干净状态，并提示手动处理 `circle/<id>` 后重试，不会留下半合并状态。

## 常用命令

初始化完成后，既可以使用下列显式命令，也可以让 Codex 根据明确的自然语言请求管理项目。

| 命令 | 作用 |
|---|---|
| `$circle /init` | 整理文档并生成 Preview（不写入工作区） |
| `$circle /commit <snapshot-hash>` | 用已确认的快照创建事实库 |
| `$circle /status` | 查看项目状态、各状态数量、可执行和被阻塞的 Issue |
| `$circle /validate` | 校验事实库结构、字段和依赖图 |
| `$circle /render` | 根据当前事实重新生成 `.circle/DAG.md` |
| `$circle /issue list` | 列出全部 Issue 及其依赖状态 |
| `$circle /issue show <id>` | 查看单个 Issue 的完整信息 |
| `$circle /issue context <id>` | 查看执行该 Issue 所需的全部文档与内容 |
| `$circle /issue add` | 新增单个 Issue |
| `$circle /issue import` | 从文档批量新增 Issue |
| `$circle /issue edit <id>` | 编辑 Issue |
| `$circle /issue transition <id> <state> [备注]` | 推进或取消 Issue，`--note` 追加到 `## Comments` |
| `$circle /issue start <id>` | 为该 Issue 创建临时执行分支 |
| `$circle /issue finish <id> [--into <branch>]` | 合并并删除该 Issue 的执行分支 |
| `$circle /dependency add <issue> <blocker>` | 为 Issue 添加前置依赖 |
| `$circle /dependency remove <issue> <blocker>` | 移除前置依赖 |

Issue 修改成功后，Circle 会报告新的 `revision`，并列出因此变为 `actionable` 的 Issue。

### 初始化后新增 Issue

初始化完成后，后续文档可以继续追加 Issue，且不会改动已有事实：

- 单个 Issue 使用 `$circle /issue add`。
- 文档中的多个 Issue 使用 `$circle /issue import`。

`/issue import` 是追加式且全量校验的：批次内的 `key` 互相引用，引用已有 Issue 时直接使用 `CIR-` ID；任何未知引用都会拒绝整批写入。项目内不允许出现重复标题，`/issue add` 与 `/issue import` 都会拒绝。

## 生命周期与依赖规则

正常生命周期为：

```text
draft → ready → in_progress → review → done
```

- 任意非终态 Issue 可以转为 `cancelled`。
- `cancelled` 只能恢复为 `draft`。
- `done` 是终态，不能重新打开。
- Issue 进入 `in_progress` 或 `done` 前，所有 blocker 都必须为 `done`。
- 被取消的 blocker 仍然会阻塞下游 Issue。
- `ready` 且所有 blocker 均为 `done` 的 Issue 才是 `actionable`。
- 已完成 Issue 的标题、四个内容小节和依赖不可修改；负责人仍可更新。

所有依赖修改都会在写入前检查未知 ID、自依赖和环。校验失败时，操作整体拒绝，不写入部分结果。

## 并发与 Git 协作

每个 Issue 都保存一个从 `1` 开始的 `revision`。修改已有 Issue 时，Skill 会先读取当前 revision，并把它作为预期值提交给控制器；如果磁盘内容已经变化，控制器会拒绝陈旧写入，要求重新读取。

这个机制只保护当前工作树，不能自动解决不同 Git 分支间的冲突：

- 不同 Issue 位于不同文件，通常可以直接合并。
- 同一 Issue 的并行修改由 Git merge conflict 暴露。
- 解决 Circle 文件的合并冲突后，应运行 `/validate`，再运行 `/render` 刷新依赖图。

执行 Issue 的分支本身也会被合并回来。若合并冲突，`/issue finish` 会中止合并并保留分支，由人工处理后再重试。

## 直接使用控制器

通常应通过 Codex Skill 使用 Circle。开发或调试时，也可以直接调用控制器：

```bash
python3 circle/scripts/circle.py --project-root /path/to/project status
python3 circle/scripts/circle.py --project-root /path/to/project validate
python3 circle/scripts/circle.py --project-root /path/to/project render
python3 circle/scripts/circle.py --project-root /path/to/project issue-list
python3 circle/scripts/circle.py --project-root /path/to/project issue-show --id CIR-XXXXXXXXXX
python3 circle/scripts/circle.py --project-root /path/to/project issue-context --id CIR-XXXXXXXXXX
python3 circle/scripts/circle.py --project-root /path/to/project issue-branch --id CIR-XXXXXXXXXX
python3 circle/scripts/circle.py --project-root /path/to/project issue-finish --id CIR-XXXXXXXXXX
```

注意：控制器的 `preview`、`issue-add`、`issue-import` 和 `issue-edit` 子命令从标准输入读取 JSON；将自由格式文本整理为 JSON 是 Skill 的职责。修改已有 Issue 或依赖的底层命令还要求提供 `--expected-revision`。运行完整帮助：

```bash
python3 circle/scripts/circle.py --help
```

## 测试

测试分三层，全部使用 Python 标准库 `unittest`：

- `test_document.py`：front matter 与小节的编解码、原子写入。
- `test_model.py`：Issue 归一化、结构校验、依赖图不变量、超长依赖链的环检测。
- `test_commands.py`：以子进程驱动 CLI 的端到端流程，覆盖初始化、快照防篡改、完整状态流转、依赖解除、批量新增 Issue、执行上下文、损坏事实库、Issue 分支的创建/合并/删除、DAG 渲染、陈旧 revision、终态保护与依赖环拒绝。

```bash
python3 -m unittest discover -s circle/tests -v
```

## 项目结构

```text
.
├── circle/
│   ├── SKILL.md              # Skill 行为、调用约定与执行流程
│   ├── agents/openai.yaml    # Codex 展示信息与隐式调用配置
│   ├── scripts/
│   │   ├── circle.py         # 入口：参数解析与命令分发
│   │   ├── commands.py       # 命令层，每个子命令一个函数
│   │   ├── model.py          # Issue schema、事实库装载与校验、派生视图
│   │   ├── graph.py          # 环检测与依赖谓词
│   │   ├── snapshot.py       # Preview / Commit 快照与哈希
│   │   ├── workbranch.py     # Issue 的 Git 工作分支
│   │   ├── document.py       # front matter 与小节编解码、原子写入
│   │   └── errors.py         # CircleError
│   └── tests/
│       ├── support.py        # 共享夹具
│       ├── test_document.py
│       ├── test_model.py
│       └── test_commands.py
├── plan.md                   # 产品目标与设计说明
└── README.md
```

模块之间依赖方向单向无环：`errors` ← `document` / `graph` ← `model` ← `snapshot` / `workbranch` ← `commands` ← `circle`。

## 当前边界

当前版本不包含：

- 自动执行或分派 Issue（agent 需要被显式要求执行）
- GitHub、Linear 或其他外部平台集成
- GitHub 身份校验
- 工期估算、格式校验与计算
- 关键路径分析与人员排期
- 自动解决 Issue 分支的合并冲突
- Codex 原生 Slash Command 注册

更完整的设计背景与验收标准见 [`plan.md`](plan.md)。
