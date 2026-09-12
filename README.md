# Circle

Circle 是一个面向 Codex 的本地项目控制 Skill。它把项目拆成独立的 Markdown Issue，用有向无环图（DAG）描述依赖关系，并通过确定性的 Python 控制器维护状态、依赖和并发修订号。

它适合希望把项目事实保留在 Git 仓库中、又不想依赖 GitHub Issues、Linear 等外部服务的团队。

## 核心能力

- 从 Markdown 或纯文本中整理项目与 Issue
- 初始化前先生成 Preview，确认后才写入工作区
- 为每个 Issue 分配不可变的 `CIR-XXXXXXXXXX` ID
- 校验未知依赖、自依赖和依赖环
- 根据依赖自动计算 `blocked`、`unblocked` 和 `actionable`
- 用 `revision` 检测当前工作树中的陈旧写入
- 生成 Mermaid 依赖图
- 将事实按 Issue 分文件保存，便于 Git 协作与合并

Circle 只负责项目控制，不会自动执行 Issue，也不会连接外部 Issue Tracker。

## 工作方式

Circle 由两部分组成：

- [`circle/SKILL.md`](circle/SKILL.md)：定义 Codex 如何理解用户输入、何时调用控制器，以及 Preview/Commit 交互规则。
- [`circle/scripts/circle.py`](circle/scripts/circle.py)：无第三方依赖的确定性控制器，负责校验和修改 `.circle/` 事实库。

初始化后，目标项目会得到以下结构：

```text
.circle/
├── PROJECT.md
├── issues/
│   ├── CIR-XXXXXXXXXX.md
│   └── CIR-YYYYYYYYYY.md
└── DAG.md
```

其中 `PROJECT.md` 和 `issues/*.md` 是事实源；`DAG.md` 是可重新生成的派生视图。修改 Issue 后不会自动刷新 DAG，需要显式执行 `/render`，从而减少多人协作时对同一文件的冲突。

## 环境要求

- Codex（用于加载并调用 Skill）
- Python 3.10 或更高版本
- macOS 或 Linux 等支持 `fcntl` 文件锁的系统
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

Codex 会整理输入并调用控制器生成 Preview。此阶段不会在目标项目中创建 `.circle/`。Preview 会展示正式 Issue ID、依赖、推断、警告和快照哈希。

确认 Preview 后，可以直接回复“确认”，也可以显式提交快照：

```text
$circle /commit <snapshot-hash>
```

Commit 只接受同一项目根目录下、内容完整且哈希匹配的 Preview 快照；成功后才会创建 `.circle/`。

## 常用命令

初始化完成后，既可以使用下列显式命令，也可以让 Codex 根据明确的自然语言请求管理项目。

| 命令 | 作用 |
|---|---|
| `$circle /status` | 查看项目状态、各状态数量、可执行和被阻塞的 Issue |
| `$circle /validate` | 校验事实库结构、字段和依赖图 |
| `$circle /render` | 根据当前事实重新生成 `.circle/DAG.md` |
| `$circle /issue list` | 列出全部 Issue 及其依赖状态 |
| `$circle /issue show <id>` | 查看单个 Issue 的完整信息 |
| `$circle /issue add` | 新增 Issue |
| `$circle /issue edit <id>` | 编辑 Issue |
| `$circle /issue transition <id> <state>` | 推进或取消 Issue |
| `$circle /dependency add <issue> <blocker>` | 为 Issue 添加前置依赖 |
| `$circle /dependency remove <issue> <blocker>` | 移除前置依赖 |

Issue 修改成功后，Circle 会报告新的 `revision`，并列出因此变为 `actionable` 的 Issue。

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
- 已完成 Issue 的标题、正文和依赖不可修改；负责人和预估仍可更新。

所有依赖修改都会在写入前检查未知 ID、自依赖和环。校验失败时，操作整体拒绝，不写入部分结果。

## 并发与 Git 协作

每个 Issue 都保存一个从 `1` 开始的 `revision`。修改已有 Issue 时，Skill 会先读取当前 revision，并把它作为预期值提交给控制器；如果磁盘内容已经变化，控制器会拒绝陈旧写入，要求重新读取。

这个机制只保护当前工作树，不能自动解决不同 Git 分支间的冲突：

- 不同 Issue 位于不同文件，通常可以直接合并。
- 同一 Issue 的并行修改由 Git merge conflict 暴露。
- 解决 Circle 文件的合并冲突后，应运行 `/validate`，再运行 `/render` 刷新依赖图。

## 直接使用控制器

通常应通过 Codex Skill 使用 Circle。开发或调试时，也可以直接调用控制器：

```bash
python3 circle/scripts/circle.py --project-root /path/to/project status
python3 circle/scripts/circle.py --project-root /path/to/project validate
python3 circle/scripts/circle.py --project-root /path/to/project render
python3 circle/scripts/circle.py --project-root /path/to/project issue-list
python3 circle/scripts/circle.py --project-root /path/to/project issue-show --id CIR-XXXXXXXXXX
```

注意：控制器的 `preview`、`issue-add` 和 `issue-edit` 子命令从标准输入读取 JSON；将自由格式文本整理为 JSON 是 Skill 的职责。修改已有 Issue 或依赖的底层命令还要求提供 `--expected-revision`。运行完整帮助：

```bash
python3 circle/scripts/circle.py --help
```

## 测试

项目使用 Python 标准库 `unittest`，测试覆盖初始化、快照防篡改、完整状态流转、依赖解除、DAG 渲染、陈旧 revision、终态保护和依赖环拒绝等场景。

```bash
python3 -m unittest discover -s circle/tests -v
```

## 项目结构

```text
.
├── circle/
│   ├── SKILL.md              # Skill 行为与调用约定
│   ├── agents/openai.yaml    # Codex 展示信息与隐式调用配置
│   ├── scripts/circle.py     # 确定性控制器
│   └── tests/test_circle.py  # 端到端测试
├── plan.md                   # v1 产品目标与设计说明
└── README.md
```

## v1 边界

当前版本不包含：

- 自动执行或分派 Issue
- GitHub、Linear 或其他外部平台集成
- GitHub 身份校验
- 工期格式校验与计算
- 关键路径分析或人员排期
- Codex 原生 Slash Command 注册

更完整的设计背景与验收标准见 [`plan.md`](plan.md)。
