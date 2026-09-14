# Circle：协作项目控制 Skill

## 1. 产品目标

在当前工作区创建 Codex skill：`circle`。初始化必须通过 `$circle /init` 显式触发；工作区已经安装 `.circle/` 后，允许通过明确的自然语言项目管理请求隐式触发。

用户通过 `$circle` 提供项目 Markdown 或纯文本。Circle 将内容归一化为项目文档、独立 Issue 文件和依赖 DAG，并提供项目状态及 Issue 维护命令。

Circle 只负责项目控制：

- 不自动执行或分派 Issue（只准备执行分支与上下文，实现由 agent 完成）
- 不验证 GitHub 身份
- 不连接 GitHub 或 Linear
- 不进行时间估算校验、关键路径计算或人员排期
- 不自动解决 Issue 分支的合并冲突
- 不注册 Codex 原生 Slash Command

## 2. 项目导入流程

### 2.1 输入模板

提供中文 Markdown 模板，包括：

- 项目名称与项目介绍
- 可选的整体文档（`AGENT.md`、`ARCHITECTURE.md`、`DOMAIN.md`）
- Issue 临时引用名
- 目标、预期行为、边界、验收标准
- `blocked_by`
- 委托人员

示例：

```markdown
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

`key` 只在导入阶段引用其他 Issue，不会成为正式 Issue ID。

输入无需严格符合模板。LLM 可以根据标题或上下文推断字段，但所有推断必须显示在 Preview 中；无法唯一确定的依赖必须让用户修正，不能猜测后直接写入。

### 2.2 两阶段 ID 分配

导入采用两阶段处理，解决 `blocked_by` 引用尚未生成 CIR ID 的问题。

第一阶段：

- 收集所有 Issue。
- 为每个 Issue 分配稳定的 `CIR-XXXXXXXXXX` ID。
- 建立临时 `key → CIR ID` 映射。
- 检测重复 key 和重复 Issue。

第二阶段：

- 使用映射把 `blocked_by` 中的临时 key 转换为正式 CIR ID。
- 检查未知引用、自引用和依赖环。
- 生成包含正式 ID 和正式依赖边的 Preview。

正式 Issue 文件中的 `blocked_by` 只允许使用 CIR ID，不保存临时 key。

### 2.3 Preview 快照

LLM 只解析输入一次。

完成解析与 ID 分配后，控制脚本生成一个标准化的暂存快照，其中包含：

- 快照 schema 版本与整体文档（`AGENT.md`、`ARCHITECTURE.md`、`DOMAIN.md`）
- 项目数据
- 全部正式 Issue ID 与临时 `key` 的映射
- 标准化 Issue 字段
- 已解析的依赖边
- 警告和推断说明
- 原始输入摘要
- 快照哈希

Preview 直接从该快照渲染。

用户确认后，写入命令使用同一个快照和哈希创建事实库，不重新读取或重新调用 LLM 解析原始文档。若快照丢失、损坏或哈希不一致，则要求重新 Preview，不能静默重建。

暂存快照保存在仓库外的临时目录；确认前不修改目标项目。

## 3. 项目事实库与文档模型

确认 Preview 后创建：

```text
.circle/
├── AGENT.md
├── ARCHITECTURE.md
├── DOMAIN.md
├── issues/
│   ├── CIR-XXXXXXXXXX.md
│   └── CIR-YYYYYYYYYY.md
└── DAG.md
```

其中：

- `AGENT.md`、`ARCHITECTURE.md`、`DOMAIN.md` 和 `issues/*.md` 是项目事实源。
- `AGENT.md` 的 front matter 固定为 `name` 与 `created_at`，既承担项目身份，正文又是协作约定。
- `ARCHITECTURE.md` 记录系统结构，`DOMAIN.md` 记录领域概念与术语。
- 每个 Issue 使用独立文件，降低不同 Issue 并行修改时的冲突概率。
- `DAG.md` 是派生视图，不是事实源，可随时重新生成。
- Issue 更新时不自动修改 `DAG.md`；只有 `/render` 显式重建，避免多人修改不同 Issue 时都冲突同一个 DAG 文件。
- 执行某个 Issue 时只需要这四份文档，上下文因此是可控且确定的。

三份整体文档在初始化时由输入提供；未提供时控制器写入占位内容，并记入 Preview 的推断说明。

## 4. Issue 数据模型

每个 Issue 保存六个内容字段。前四个固定为 Markdown 正文中的小节：

```markdown
## Goal

这个 Issue 要达成什么。

## Expected Behavior

完成后系统可观察到的行为。

## Boundaries

明确不做什么。

## Acceptance Criteria

- [ ] 可验证的验收项
```

`blocked_by`、`assignee` 以及机器字段保存在 front matter：

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

字段规则：

- 四个内容小节缺失或为空都会被拒绝，控制器不接受只有标题的 Issue。
- `## Acceptance Criteria` 是唯一的验收来源，执行时必须逐项核对。
- 只有 `## Comments` 是可选的额外小节，由 `--note` 追加；其他未知小节会被拒绝。
- `assignee` 是普通名称字符串，可以为空。
- 不保存工期字段：估算格式校验与计算明确不在 Circle 的职责内。
- `blocked_by` 只能包含当前项目中存在的 CIR ID。
- Issue ID 创建后不可修改或重复使用。
- `revision` 每次成功修改后递增。
- 项目内不允许重复标题（大小写不敏感），`/issue add` 与 `/issue import` 都拒绝。

## 5. 状态与依赖模型

生命周期为：

```text
draft → ready → in_progress → review → done
```

非终态 Issue 可以进入 `cancelled`；`cancelled` 可以恢复到 `draft`。

`done` 是不可重新打开的终态。进入 `done` 后：

- 生命周期状态不可修改。
- `blocked_by` 不可修改。
- 核心执行内容不可修改。
- 若发现遗漏或错误，应创建新的修正 Issue，并通过依赖关系表达后续工作。

这样可以维持以下不变量：

> 任意 `done` Issue 的所有 blocker 永远保持为 `done`。

Issue 进入 `in_progress` 或 `done` 前，所有 blocker 必须已经为 `done`。

阻塞状态不保存为生命周期状态，而是从 DAG 计算：

- 存在非 `done` blocker：`blocked`
- 所有 blocker 均为 `done`：`unblocked`
- `state = ready` 且 `unblocked`：`actionable`
- 被取消的 blocker 不算完成，仍会阻塞下游

自引用、未知引用或产生环的依赖更新必须整笔拒绝，不产生部分写入。

## 6. 命令接口

初始化只能通过 `$circle /init` 显式触发。Preview 来自显式初始化后，用户下一轮的明确确认视为同一初始化流程的延续，无需重复 `$circle`。工作区已经存在 `.circle/` 后，以下命令既可显式调用，也可由明确的自然语言项目管理请求触发：

```text
$circle /init <文档路径或粘贴内容>
$circle /commit <snapshot-hash>
$circle /status
$circle /validate
$circle /render

$circle /issue list
$circle /issue show <id>
$circle /issue context <id>
$circle /issue add
$circle /issue import
$circle /issue edit <id>
$circle /issue transition <id> <state> [备注]
$circle /issue start <id>
$circle /issue finish <id>

$circle /dependency add <issue-id> <blocker-id>
$circle /dependency remove <issue-id> <blocker-id>
```

`/init` 分为 Preview 和 Commit 两次交互。用户可以直接确认 Preview，也可以显式调用 `/commit <snapshot-hash>`；两种方式都必须使用 Preview 已生成的快照。

初始化完成后，`/issue add` 与 `/issue import` 允许继续从后续文档新增 Issue。`/issue import` 是追加式且全量校验的：批次内的 `key` 互相引用，引用已有 Issue 时使用 `CIR-` ID，任何未知引用都会拒绝整批写入。

任何包含多个文件的操作都必须先完成全部校验，再进行原子写入。

## 7. 执行 Issue 的分支工作流

执行 Issue 时，每次只把与该 Issue 相关的上下文交给 agent：

```text
$circle /issue start <id>     # 创建 circle/<id> 并打印执行上下文
$circle /issue context <id>   # 只查看上下文，不创建分支
$circle /issue finish <id>    # 合并回基线分支并删除 circle/<id>
```

流程：

1. `/issue start` 校验 Issue 未被阻塞、不是 `done`/`cancelled`，且工作树干净。
2. 从当前分支创建 `circle/<id>`，并把基线分支记录在 `git config branch.circle/<id>.circlebase`。
3. 打印执行上下文：`AGENT.md`、`ARCHITECTURE.md`、`DOMAIN.md` 与当前 Issue 的完整内容。
4. agent 在该分支上实现并提交，逐项核对验收标准。
5. `/issue finish` 切回基线分支、合并 `circle/<id>`、删除该分支；分支级 git config 随分支删除自动清理。

约束：

- 被阻塞的 Issue 不能开始执行。
- 分支名与基线分支都从 Issue 推导，不引入额外的状态文件；`revision` 仍然只用于检测陈旧写入。
- 工作树不干净时拒绝开始或结束，避免把无关改动带进合并。
- 合并失败时不留下半合并状态：控制器执行 `git merge --abort` 并保留 `circle/<id>`，要求人工处理后重试。
- Circle 不自动实现、也不自动验收 Issue；执行和验收的主体始终是 agent。
- 需要 Git 仓库，且项目根目录必须是该仓库的工作树根目录。

## 8. 多人协作与并发

`revision` 只用于防止当前工作树中的 stale write：

1. Circle 读取 Issue 及其 revision。
2. 修改前重新检查磁盘中的 revision。
3. revision 已变化时拒绝覆盖，并要求重新读取。
4. 写入成功后 revision 加一。

`revision` 不负责解决不同 Git 分支之间的并发修改。

跨 Git 分支的行为是：

- 修改不同 Issue 文件时通常可以正常合并。
- 修改同一个 Issue 文件时，由 Git merge conflict 暴露冲突。
- Circle 不自动选择冲突一方，也不使用 revision 掩盖 Git 冲突。
- Git 冲突解决后必须重新运行项目校验和 DAG 渲染。

Git 历史作为变更记录，不额外维护集中式事件日志。

## 9. 最小验收方案

首先使用一个包含三个 Issue 的小项目跑通完整流程：

1. 从 Markdown 创建 Preview。
2. 在 Preview 中确认临时 key 已转换为正式 CIR ID，且四个内容小节完整。
3. 确认后使用同一个快照写入项目，并生成 `AGENT.md`、`ARCHITECTURE.md`、`DOMAIN.md`。
4. 查看项目状态与 actionable Issue。
5. 将第一个 Issue 从 `draft` 推进到 `done`。
6. 验证依赖它的 Issue 自动解除阻塞。
7. 新增一个 Issue，并添加、删除依赖。
8. 从后续文档批量新增 Issue，并拒绝未知引用。
9. 生成 Mermaid DAG。
10. 为某个 Issue 创建执行分支、提交改动、合并回基线分支，并确认分支已删除。
11. 完成剩余 Issue，验证项目状态正确。

在同一组测试数据上补充最小失败场景：

- 添加依赖环时拒绝写入。
- 使用陈旧 revision 修改当前工作树中的同一 Issue 时拒绝覆盖。
- 尝试重新打开或修改 `done` Issue 时拒绝操作。
- 缺少四个内容小节之一的 Issue 被拒绝导入。
- 工作树不干净或被阻塞时拒绝开始执行。
- 超长依赖链可以完成环检测，不出现栈溢出。

最后在真实 Git 仓库中执行一次完整流程，并用一次独立 Codex 会话复现。

## 10. 实现与版本边界

- skill 源码创建在当前工作区的 `circle/`。
- 使用 `agents/openai.yaml` 设置 `allow_implicit_invocation: true`，由 `SKILL.md` 强制“未安装时仅显式 `/init`，安装后允许隐式管理”的条件式触发策略。
- 使用无第三方依赖的 Python 脚本实现确定性数据更新、校验和 Git 分支管理，按关注点拆分为若干模块，入口固定为 `scripts/circle.py`。
- 执行 Issue 的分支工作流由控制器提供，但实现与验收始终由 agent 完成。
- 当前版本已包含 `/issue context`、`/issue import`、`/issue start`、`/issue finish` 与整体文档模型。
- 后续可升级为 plugin，提供更正式的命令和交互能力。
- 再之后引入 Linear，并为本地 CIR ID 与 Linear Issue ID 建立显式映射。
