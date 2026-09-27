# Circle 源码与 Excalidraw 架构严格对照审计

> **2026-09-24 实施更新：** 本报告列出的 P0 已在当前工作树实现：持久需求事实、发布锁定与状态投影、逐验收项结构化证据、每 Issue 独立 worktree、CI 语义校验。按本轮范围实现了用户/鉴权、release/cancel/retry/recover、Review/最终验收工作台、依赖提交预检及 P2-3 游标通知。任务调度、Agent Runtime、P1-6 生产运维、外部渠道和自动架构生成仍按要求不做。下文保留变更前审计结论，作为实现依据。

## 1. 审计结论

当前本地工作树已经实现了架构图的**项目控制骨架**，但还没有实现架构图表达的完整多人、多机、端到端协作产品。

按架构能力拆分，本轮判断为：

- **已实现：4 项**——Issue 结构、依赖图和校验、项目/任务上下文、初始化后的 Issue 追加。
- **部分实现：9 项**——需求确认、架构确认、Issue 拆分、人员分配、每 Issue 分支、Service 派发、Agent 执行、独立 Review、最终验收与并行协作。
- **未实现：3 项**——微信/QQ/飞书/Notion/Slack 等需求入口、自动启动/调度 Agent、生产级远程多机闭环。

这里的“当前源码”指 **上游提交 `5d4ea0c` 加本地尚未提交的修改**。中央 Service、Windows 锁适配、Service 测试和本报告都还不在上游 Git 提交中。因此：

- 对原始 GitHub 仓库而言，Service、任务租约、独立 Review、最终验收均**未实现**。
- 对当前本地工作树而言，这些能力已有代码和本地测试，但仍属于**未提交、未部署、未做真实两机验收的实现**。

架构图源为 [circle-workflow.excalidraw](architecture/circle-workflow.excalidraw)，官方存储快照更新时间为北京时间 2026-09-23 16:34:39。提取范围和 SHA-256 见 [source-metadata.json](architecture/source-metadata.json)。

## 2. 状态口径

| 状态 | 本报告含义 |
|---|---|
| 已实现 | 当前本地源码中有完整主路径和对应测试；不等于已部署或生产验证 |
| 部分实现 | 有代码或协议骨架，但缺少架构图隐含的关键运行环节、验证、界面或部署能力 |
| 未实现 | 当前源码未发现对应入口、运行器、适配器或闭环 |
| 未验证 | 有实现声明，但本轮证据不能支持真实环境结论 |

## 3. 架构节点逐项对照

| 图中节点或关系 | 源码对应 | 状态 | 严格判断 |
|---|---|---|---|
| 微信/QQ 记录需求 | 无连接器、Webhook、导入器或消息身份模型 | 未实现 | 只能由用户把文字交给 Codex；没有从聊天系统读取、去重、留存原消息或回链 |
| 飞书/Notion/Slack 记录需求 | 无适配器或授权配置 | 未实现 | 图中存在入口，源码完全没有对应模块 |
| 企业微信 API 或 computer use 触发 | 无实现，图中本身也带问号 | 设计未确认 | 应先确定正式入口、授权边界和事实源，不能把问号节点算作需求完成 |
| 如何记录需求 | Preview 支持 `raw_summary`、inferences、warnings；Commit 写入文档与 Issue | 部分实现 | 原始需求摘要只存在 Preview 快照并用于展示；Commit 没有将 `raw_summary` 写入 `.circle/`，缺少来源、提出人、时间、渠道和变更链。证据：`snapshot.py:35,143,178-179`；`commands.py:124-132` |
| 确定需求 | Skill 将输入整理为结构化 JSON，Preview 后人工确认 | 部分实现 | 有确认 Gate，但“确定需求”主要由 Codex 对话完成，不是控制器中可复现的需求决策流程；没有需求 ID、接受/拒绝状态或决策记录 |
| 确定架构 | 初始化要求提供 `ARCHITECTURE.md`，可用 docs-set 更新 | 部分实现 | 能保存架构文档，但控制器不会从需求推导架构，也没有架构评审、版本、批准状态或影响分析。图中的“架构师”是人/Agent 行为约定，不是独立模块 |
| 架构师写项目文档 | `AGENT.md`、`ARCHITECTURE.md`、`DOMAIN.md` | 已实现（存储）/部分实现（生成） | 三份文档是强制事实源，存储和校验已实现；内容生成仍由 Codex/用户完成，源码不会验证架构是否覆盖需求。证据：`model.py:36-37,339,357-378` |
| Issue：goal、预期行为、边界、验收标准 | 固定 Markdown 小节 | 已实现 | 四项内容必填且可解析、原子写入；验收项为空会拒绝。证据：`model.py:52-63,159-177` |
| Issue：具体任务 | title + Goal/Expected Behavior/Boundaries/Acceptance | 已实现 | 标题和四个执行字段构成任务定义；缺少独立“任务描述”字段，但 Goal 可承担该语义 |
| Issue：blocked_by | front matter + DAG 校验 | 已实现 | 支持未知依赖、自依赖、环、未完成 blocker 和 Mermaid DAG。证据：`model.py:405-433,590` |
| Issue：分配人员 | `assignee`；Service 领取时强制身份一致 | 部分实现 | 能记录和执行静态分配；没有人员目录、技能、负载、可用性或自动分配算法。证据：`service.py:200-202` |
| 拆分/转化 Issues | Skill 整理自由文本；控制器批量 normalize/import | 部分实现 | 控制器能验证并持久化已经拆好的 Issue；真正的需求分解由 Codex 完成，没有确定性分解器、需求覆盖矩阵或遗漏检测。证据：`model.py:513`；`commands.py:284` |
| 中途加入需求 | issue-add / issue-import | 已实现（追加）/部分实现（变更治理） | 支持全量校验和原子追加，不修改旧 Issue；但新需求没有链接到原需求、架构变更或受影响的现有任务。证据：`commands.py:277-301` |
| Agent 执行需要 Agent/architecture/domain/current issue | `render_context()` 和 Service 冻结 docs | 已实现 | 本地 context 只输出三份项目文档和当前 Issue；Service 任务也保存发布时的文档快照。证据：`commands.py:91-103`；`service.py:103-106` |
| 每个 Issue 一个分支 | `circle/<issue-id>` 创建、合并、删除 | 已实现（单工作树） | 分支创建和冲突中止已实现。证据：`workbranch.py:73-126` |
| 当前分支只有当前 Issue | 只限制执行上下文，不裁剪 Git 文件 | 部分实现 | Git 分支仍包含整个仓库和全部 `.circle/issues`；只是给 Agent 的上下文只列当前 Issue。同一个工作树还禁止同时开始第二个 Issue。证据：`workbranch.py:88-93` |
| Service 派发 Issues | SQLite + HTTP claim + dependency/assignee Gate | 部分实现 | 当前是 Worker 按 ID 主动 claim，属于“拉取领取”；没有 Service 主动选择 Worker、推送任务、优先级、容量、公平性或队列。证据：`service.py:179-205,291-310` |
| 多个 Agent 并行实现 | 不同机器/克隆可各自 claim 不同 Issue | 部分实现 | 中央事务能防止同一任务被两个 HTTP Worker 同时领取；Service 不启动 Agent，也不确认 Worker 已拉取依赖代码。单机单工作树仍一次只能执行一个 Issue |
| Agent 进行 Issue 实现 | `SKILL.md` 规定执行流程 | 部分实现 | Agent 实现代码属于 Codex 行为，不是 Service 能执行的 Worker Runtime；没有启动、停止、日志流、超时终止或沙箱管理 |
| 委派人员 Review | admin assign reviewer，reviewer approve/reject | 部分实现 | 身份、版本和独立性 Gate 已有；没有自动选人、Diff/PR 获取、证据读取与验证、评审清单或 UI。证据：`service.py:228-249` |
| 最终验收 | admin accept；sync 回写 done | 部分实现 | 必须先 Review 通过，且不能由执行者验收；但 accept 只要求 note，不会运行验收命令、验证 evidence、核对提交内容或逐项记录验收结论。证据：`service.py:250-256` |
| 完成结果回到项目事实 | Service sync 检查 commit 已合入 HEAD，再回写 Issue | 部分实现 | 能检查 source hash、Git ancestor、DAG 并幂等回写；多文件回写不是一个文件系统事务，Git push/pull/PR/merge 仍由人处理。证据：`service.py:126-168` |

## 4. 源码已经实现、但架构图没有表达的机制

架构图低估了当前代码中的这些重要控制面：

1. **Preview/Commit 防篡改确认**：初始化先在仓库外保存带哈希快照，确认后才写入。
2. **Issue 生命周期**：本地模式具有 `draft → ready → in_progress → review → done` 和 cancelled 分支。
3. **乐观并发控制**：每个 Issue 有 revision，陈旧修改会被拒绝。
4. **本机进程锁**：Windows 使用 `msvcrt`，POSIX 使用 `fcntl`；只解决本机同一路径并发，不是分布式锁。
5. **完整图校验**：未知 blocker、自依赖、环、done 验收项和重复标题均参与 validate。
6. **Service 运行状态**：`ready → running → review → approved → done`，另有 lease、attempt 和 version。
7. **身份与传输边界**：worker/reviewer/admin 角色、Bearer token hash、远程监听强制 TLS。
8. **追加式事件记录**：Service mutations 进入 SQLite events 表，但失败请求和读取操作不记录。
9. **双事实源同步**：`.circle/` 是编写事实，发布后 SQLite 是执行状态事实，最终通过 sync 回写。

架构图应补上这些节点，否则维护者会看不出本地状态和 Service 状态为何不同，也无法理解发布、领取、合并、验收、回写之间的顺序。

## 5. 当前源码的具体缺陷与风险

### P0：影响事实正确性或闭环成立

#### P0-1 原始需求和决策链没有持久化

`raw_summary` 进入 Preview，但 Commit 只把 docs 和 issues 写入事实库。系统无法回答“这个 Issue 来自哪条消息、谁确认、为何如此拆分、架构为何改变”。

建议：新增 `.circle/requirements/REQ-*.md` 或等价事实模型，至少包含：

- source channel、source locator、原文摘要或内容 hash；
- requester、recorded_at、confirmed_by、confirmed_at；
- state（candidate/confirmed/rejected/superseded）；
- 派生的 architecture revision 和 issue IDs；
- supersedes / affected_by 关系。

Issue 增加 `requirement_ids`，架构文档增加 revision/decision 记录。Preview/Commit 应同时提交这些事实。

#### P0-2 本地与 Service 有两套生命周期，靠文档约定避免混用

本地状态是 draft/ready/in_progress/review/done；Service 是 ready/running/review/approved/done。源码没有统一状态投影或强制禁止已发布 Issue 继续用本地 transition/check 修改，只在使用说明里要求不要混用。

建议：

- 为 `.circle` 增加 execution mode / publication metadata；
- 发布后本地 mutation 对执行字段失败关闭；
- 明确定义 Service 状态到本地状态的投影；
- status 同时显示 authoring revision、published source hash、attempt、lease、review 和 sync 状态；
- 将状态转换集中到一个 domain module，避免两套表各自演进。

#### P0-3 evidence 只是字符串引用，没有证据验证

submit 只检查 evidence 是非空字符串列表，最终验收只检查 note。服务不会检查文件是否存在、是否对应该 commit、测试是否成功、验收项与证据如何对应。

建议将证据改成结构化对象：

```json
{
  "criterion": 1,
  "kind": "test-log",
  "locator": "evidence/tests.json",
  "sha256": "...",
  "command": "python -m unittest ...",
  "exit_code": 0,
  "observed_at": "...",
  "commit": "..."
}
```

Service 至少应验证 schema、criterion 覆盖、hash、commit 绑定和允许的 evidence kind；Reviewer/Owner 明确记录逐项 verdict。涉及执行命令时，应由可信 CI/Worker 生成签名结果，中央服务不能直接执行用户提交的任意命令。

#### P0-4 “分支只有当前 Issue”没有按图实现

当前代码只切 Git branch，不创建独立 worktree，也不隐藏其他 Issue 文件。在一个工作树上开始第二个 Issue 会被拒绝。这不符合图中并行多 Agent 的直观含义。

建议将执行隔离升级为：

- 每个 Issue 一个 `git worktree`；
- worktree 与 task attempt 绑定；
- Service 返回 base commit、branch 和依赖完成 commit；
- Worker 开工前验证当前 HEAD/base/dependency commits；
- finish 使用明确目标分支或 PR，不依赖只存于本地 `.git/config` 的 circlebase。

如果产品只要求“独立分支但仍是完整仓库”，应修改架构图文字，不再写“当前分支只有当前 Issue”。

#### P0-5 合并后的语义校验没有自动进入 Git Gate

测试已经证明：两个克隆各自合法的依赖修改可能合并成依赖环；只有手工运行 validate 才能发现。Service sync 会校验其回写目标，但普通 Git merge 和 PR 不会自动执行。

建议增加 CI：每个 PR 和主分支合并后运行 `circle.py validate`，若 DAG、重复标题、done 验收或文档结构非法则失败；render 的漂移可另设检查或自动生成。

### P1：影响可用的多机协作

#### P1-1 派发实际是按 ID 领取，没有调度

需要增加“列出 actionable tasks”过滤、优先级、能力标签、负载、租约容量、领取顺序和幂等 dispatch policy。若仍保留主动领取，应把架构图的“派发”改成“发布/领取”。

#### P1-2 没有 Worker Runtime

需要一个独立 Worker 进程完成：注册身份 → 拉取可执行任务 → claim → 建 worktree → 启动 Codex/其他 Agent → 定期 heartbeat → 收集 commit/evidence → submit → 租约丢失时停止。第一版应只支持显式启动和单任务容量，避免静默执行外部副作用。

#### P1-3 缺少显式 release、cancel、retry 和管理员恢复

当前运行任务只能等待 lease 过期，Review 驳回只能回 ready。建议增加 release/cancel/requeue，并记录 reason、actor、旧 attempt；管理员操作必须带 expected_version。

#### P1-4 Review 与验收没有工作台

需要至少一个 CLI/TUI 或 Web 页面显示：Issue 定义、源需求、commit diff、验收项、证据、Worker、attempt、lease、Reviewer、历史意见和最终决定。图中的多条 Review 汇聚到最终验收，当前只有底层 JSON API。

#### P1-5 Worker 看不到明确的代码同步前置

Service 只检查 blocker 的运行状态为 done，不证明 Worker 当前克隆包含 blocker 的代码。任务响应应包含 base commit 和每个依赖的 accepted commit，claim 前或启动前验证这些提交已在本地 HEAD 中。

#### P1-6 服务缺少生产运行治理

需要：SQLite schema version/migration、备份恢复、token rotate/revoke、失败认证审计、请求限流、健康/就绪检查区分、结构化日志、指标、数据库损坏恢复测试。当前 `log_message` 明确关闭请求日志，events 只记录成功 mutation。

### P2：补齐产品入口和体验

#### P2-1 需求渠道适配器

按一个渠道一个适配器实施，先确定唯一首发渠道。适配器只写入 candidate requirement，不直接创建 ready Issue；必须经过人工确认和去重。不要同时铺开微信、QQ、飞书、Notion、Slack。

#### P2-2 自动架构和 Issue 覆盖检查

可以由 Agent 生成候选架构与 Issue，但必须输出 requirement → architecture decision → issue → acceptance 的覆盖矩阵；人工确认后才进入事实库。架构更新要显示受影响 Issue，不能直接覆盖文档。

#### P2-3 通知与状态订阅

增加任务可领取、租约将过期、等待 Review、Review 驳回、等待最终验收等通知。通知是状态投影，不是新的事实源。

## 6. 两个应立即修复的源码级问题

### 6.1 重复标题校验大小写不一致

创建路径使用 `casefold()` 判断重复，但全库 `validate_graph()` 只用 `strip()`。因此两个克隆分别新增 `Task` 和 `task`，合并后可能通过 validate。

修改：`model.py:409` 改为 `issue["title"].strip().casefold()`，并增加双克隆大小写差异测试。

### 6.2 acceptance.done 接受非布尔值

`normalize_acceptance()` 使用 `bool(item.get("done", False))`。字符串 `"false"` 会被当成 `True`。

修改：只接受真正的 `bool`，缺省为 false；其他类型明确拒绝，并补充 `"false"`、`0`、`1`、`null` 的边界测试。证据：`model.py:159-172`。

## 7. 推荐实施顺序

### 阶段 A：先使事实链可信

1. 修复两个源码级校验问题。
2. 建立 requirement 事实模型和 Issue 来源关系。
3. 统一本地/Service 生命周期与发布锁定规则。
4. 结构化 evidence，逐验收项记录 verdict。
5. 将 validate 接入 Git CI。

完成标准：任意 Issue 都能追溯到确认过的需求、架构版本、实现 commit、验收项证据、Reviewer 和最终验收者；不同入口不能绕开相同 Gate。

### 阶段 B：使多机协作真正可运行

1. worktree/branch 与 attempt 绑定。
2. Service 返回 base 和 dependency commits。
3. 增加 Worker Runtime、heartbeat 失效处理、release/cancel/requeue。
4. 增加 Review/验收工作台。
5. 完成两台物理机 + TLS + 断网/恢复测试。

完成标准：两台机器可以领取不同任务并行工作；同一任务不会双重提交；依赖代码可验证；Review 驳回能重做；网络中断和租约过期不会接受旧结果。

### 阶段 C：接入需求渠道和自动化

1. 选择一个需求入口实现 candidate ingestion。
2. 增加去重、来源回链和人工确认。
3. 引入候选架构/Issue 生成及覆盖矩阵。
4. 增加通知与状态订阅。

完成标准：外部需求进入后保留原始来源，未经确认不会生成可执行任务；每个交付能反向追溯到入口消息。

## 8. 验证范围

现有证据 [architecture-development-tests.log](../evidence/architecture-development-tests.log) 显示 115 项本地测试通过，覆盖控制器、Windows 文件锁、双克隆 Git 模拟、HTTP Service、租约、Review、最终验收和 sync。该证据支持“本地实现和模拟协作流程可运行”，不能支持：

- 已提交到 GitHub；
- 已安装为个人 Skill；
- 已部署中央服务；
- 两台物理机器真实协同；
- TLS、网络分区、长期运行或生产负载已验收；
- Agent 会被 Service 自动启动；
- evidence 内容已被自动验证。

## 9. 最终判断

当前源码与架构图的关系不是“完全没实现”，也不是“架构已经完成”。更准确的结论是：

> 本地工作树已经完成 Circle 的确定性项目事实库和中央协作控制面的第一版；架构图中的需求入口、可追溯需求决策、真正的任务调度/Agent Runtime、证据验证、工作台和生产级多机运行仍未完成。

下一步不应继续横向增加渠道或 Agent 类型，应先完成阶段 A，让需求、架构、任务、代码、证据和验收形成同一条可验证事实链。
