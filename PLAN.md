# Circle v1：协作项目控制 Skill

## 1. 产品目标

在当前工作区创建仅手动触发的 Codex skill：`circle`。

用户通过 `$circle` 提供项目 Markdown 或纯文本。Circle 将内容归一化为项目文档、独立 Issue 文件和依赖 DAG，并提供项目状态及 Issue 维护命令。

v1 只负责项目控制：

- 不自动执行 Issue
- 不验证 GitHub 身份
- 不连接 GitHub 或 Linear
- 不进行时间估算校验、关键路径计算或人员排期
- 不注册 Codex 原生 Slash Command

## 2. 项目导入流程

### 2.1 输入模板

提供中文 Markdown 模板，包括：

- 项目名称与项目介绍
- Issue 临时引用名
- 具体执行事项
- `blocked_by`
- 预估用时
- 委托人员

示例：

```markdown
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

- 项目数据
- 全部正式 Issue ID
- 标准化 Issue 字段
- 已解析的依赖边
- 警告和推断说明
- 原始输入摘要
- 快照哈希

Preview 直接从该快照渲染。

用户确认后，写入命令使用同一个快照和哈希创建事实库，不重新读取或重新调用 LLM 解析原始文档。若快照丢失、损坏或哈希不一致，则要求重新 Preview，不能静默重建。

暂存快照保存在仓库外的临时目录；确认前不修改目标项目。

## 3. 项目事实库

确认 Preview 后创建：

```text
.circle/
├── PROJECT.md
├── issues/
│   ├── CIR-XXXXXXXXXX.md
│   └── CIR-YYYYYYYYYY.md
└── DAG.md
```

其中：

- `PROJECT.md` 和 `issues/*.md` 是项目事实源。
- 每个 Issue 使用独立文件，降低不同 Issue 并行修改时的冲突概率。
- `DAG.md` 是派生视图，不是事实源，可随时重新生成。
- Issue 更新时不自动修改 `DAG.md`；只有 `/render` 显式重建，避免多人修改不同 Issue 时都冲突同一个 DAG 文件。

## 4. Issue 数据模型

每个 Issue 保存以下字段：

```yaml
id: "CIR-XXXXXXXXXX"
title: "生成 DAG"
state: "ready"
assignee: "Bob"
estimate: "1 day"
blocked_by: ["CIR-AAAAAAAAAA"]
revision: 1
created_at: "..."
updated_at: "..."
```

Markdown 正文保存具体执行事项和可选备注。

字段规则：

- `assignee` 是普通名称字符串。
- `estimate` 原样保存，不进行格式校验或计算。
- `blocked_by` 只能包含当前项目中存在的 CIR ID。
- Issue ID 创建后不可修改或重复使用。
- `revision` 每次成功修改后递增。

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

所有命令都通过 `$circle` 显式触发：

```text
$circle /init <文档路径或粘贴内容>
$circle /status
$circle /render

$circle /issue list
$circle /issue show <id>
$circle /issue add
$circle /issue edit <id>
$circle /issue transition <id> <state> [备注]

$circle /dependency add <issue-id> <blocker-id>
$circle /dependency remove <issue-id> <blocker-id>
```

`/init` 分为 Preview 和 Commit 两次交互，但 Commit 使用 Preview 已生成的快照。

任何包含多个文件的操作都必须先完成全部校验，再进行原子写入。

## 7. 多人协作与并发

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

Git 历史作为 v1 的变更记录，不额外维护集中式事件日志。

## 8. 最小验收方案

首先使用一个包含三个 Issue 的小项目跑通完整流程：

1. 从 Markdown 创建 Preview。
2. 在 Preview 中确认临时 key 已转换为正式 CIR ID。
3. 确认后使用同一个快照写入项目。
4. 查看项目状态与 actionable Issue。
5. 将第一个 Issue 从 `draft` 推进到 `done`。
6. 验证依赖它的 Issue 自动解除阻塞。
7. 新增一个 Issue，并添加、删除依赖。
8. 生成 Mermaid DAG。
9. 完成剩余 Issue，验证项目状态正确。

在同一组测试数据上补充三个最小失败场景：

- 添加依赖环时拒绝写入。
- 使用陈旧 revision 修改当前工作树中的同一 Issue 时拒绝覆盖。
- 尝试重新打开或修改 `done` Issue 时拒绝操作。

最后运行 skill 结构校验，并通过一次独立 Codex 会话执行上述完整流程。

## 9. 实现与版本边界

- skill 源码创建在当前工作区的 `circle/`。
- 使用 `agents/openai.yaml` 设置 `allow_implicit_invocation: false`。
- 使用无第三方依赖的 Python 脚本实现确定性数据更新和校验。
- v2 可升级为 plugin，提供更正式的命令和交互能力。
- v3 再引入 Linear，并为本地 CIR ID 与 Linear Issue ID 建立显式映射。