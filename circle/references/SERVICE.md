# Circle Service：按架构图实现的协作层

## 当前执行合同

- 凭据包含持久用户、启停状态、多角色和可吊销令牌；明文令牌只写个人 token 文件。
- 修改用户状态或轮换/吊销令牌后重启 Service，使新的凭据快照生效。
- Worker 手动领取或释放；管理员分配 Reviewer，并可带版本和原因执行取消、重试或恢复。
- 提交使用结构化证据，每项验收标准必须被覆盖且绑定提交；本地可见文件会校验 SHA-256。
- Review 和最终验收逐项保存 pass/fail 结论；批准与最终接受要求全部通过。
- `/v1/notifications?after=<seq>` 提供游标通知投影，`/workbench` 使用同源 HttpOnly 会话。
- `service_preflight.py` 在开工前检查发布基线和依赖提交均已存在于 Worker 当前 HEAD。

架构依据：[原始 Excalidraw 区域](../../docs/architecture/circle-workflow.excalidraw)、[可读预览](../../docs/architecture/circle-workflow-preview.png)。官方存储更新时间为北京时间 2026-09-23 16:34:39。预览按源元素位置、文字、颜色和连线绘制，字体与手绘笔触不是浏览器像素级复刻；可编辑源文件保留原始元素。

## 架构对应

| 图中的环节 | 当前实现 |
|---|---|
| 架构师确认需求、架构，形成项目文档 | 原有 Preview/Commit、AGENT/ARCHITECTURE/DOMAIN 与 Issue 内容模型 |
| 拆分 Issue、处理中途加入的需求 | 原有 add/import；新 Issue 经 ready 确认后追加发布到 Service |
| Service 派发 | 中央 SQLite + HTTP，按依赖与负责人提供任务领取；同一任务同时只能有一个有效租约 |
| Agent 执行时需要四类上下文 | GET 单个任务返回冻结的三份项目文档和当前 Issue |
| 一个 Issue 一个工作树 | issue-branch 为每个 Issue/attempt 创建独立 git worktree；机器各自拥有 Git 克隆 |
| 委派人员 Review | 管理员指定 reviewer；独立 reviewer approve/reject，审核绑定提交哈希 |
| 最终验收 | 独立 admin 验收；完成后可解除 Service 中下游任务的依赖 |
| 完成结果进入项目事实库 | sync 检查源版本与 Git 合入情况，回写 done、负责人、验收项与审核记录 |

**HTTP、SQLite、租约和角色令牌是本次实现选择，原图没有规定这些技术细节。** 原图提到“当前分支只有当前 Issue”：本版做到每任务独立分支和只获取当前任务上下文，不从 Git 分支中删除其他 Issue 文件。

## 数据与状态

`.circle/` 是需求与任务的编写来源。管理员发布后，Service 数据库保存任务及上下文的冻结快照，并成为本次执行的权威状态源。执行状态为：

```text
ready → running → review → approved → done
           ↓          ↓
        租约超时    审核驳回
           └→重新领取←┘
```

发布后控制器会锁定该任务的本地执行字段，Service 状态通过 `local_state` 显式投影；最终由 sync 回写。

- 仅允许发布 `ready`、`done`、`cancelled` 任务；draft 必须先确认需求再转 ready。
- 已发布任务不可用重新 publish 覆盖，避免抹去运行状态与审核证据。变更需求时新增后续 Issue。
- publish 幂等，支持追加任务；上下文随任务冻结，新文档不会偷偷替换已派发任务的上下文。
- 负责人非空时，只允许与 `assignee` 一致的 worker 身份领取；未指定负责人时可由任一 worker 领取。
- 默认租约 300 秒，允许 30–3600 秒；heartbeat 续期。每次重新领取增加 attempt，旧执行者不能再提交。
- 租约过期只解除协调占用，**不会终止另一台机器上已启动的进程**。Worker 必须在失去租约后停止并避免继续产生外部副作用。
- submit 必须包含完整 Git commit hash、证据引用、全部验收项序号。服务检查结构与流程，不自动执行测试，不证明证据或提交真实存在。
- reviewer 必须是已配置的 reviewer 身份且不能是执行者。审核绑定该次提交。
- 最终验收后 Service 状态为 done；同步前本地 Markdown 仍保持发布时状态，查看执行进度使用 Service。
- sync 只处理经 Service 最终验收的任务；对应 Git commit 必须已是当前 HEAD 的祖先，否则拒绝。
- sync 遇到源 Issue 修改会拒绝覆盖；先验证全部目标，再按依赖顺序写入，输出确定、可重试。跨文件写入不是单次文件系统事务，但中断后已写入的依赖前缀合法，可再次运行恢复。

## 启动

需要 Python 3.9+、Git，无第三方运行依赖。所有命令在 Circle 仓库根目录执行，`--project-root` 指向已经初始化 `.circle/` 的目标项目。

数据库和凭据应放在中央主机的用户目录，**不要放进 Git、百度同步盘或网络共享目录**。

```powershell
$runtime = Join-Path $env:LOCALAPPDATA 'CircleService'
$database = Join-Path $runtime 'project.sqlite3'
$credentials = Join-Path $runtime 'credentials'

# 目录必须不存在；每个人一个身份、一个角色，不覆盖旧凭据。
python circle/scripts/service_credentials.py --directory $credentials --worker Alice --worker Bob --reviewer Reviewer --admin Owner

# 目标项目必须已有完整文档和已确认的 ready 任务。
python circle/scripts/service.py --database $database publish --project-root 'C:\Projects\MyProject'

# 本机测试服务，前台运行，Ctrl+C 停止。
python circle/scripts/service.py --database $database serve --credentials "$credentials\server.json"
```

`identities.json` 记录身份与令牌文件的映射。只给每个人发其自己的 token 文件；服务器只读取 token_hash，不需要 worker 明文令牌。工具不会在控制台打印令牌。在 Windows 上，凭据目录使用所在用户目录的继承 ACL，`chmod` 不等价于额外配置完整 Windows ACL。

跨机器时中央主机必须使用 HTTPS：

```powershell
python circle/scripts/service.py --database $database serve --credentials "$credentials\server.json" --host 0.0.0.0 --port 8769 --certfile 'C:\TLS\server.crt' --keyfile 'C:\TLS\server.key'
```

证书应受 Worker 机器信任，服务名应与证书一致。客户端不会跳过证书校验；未提供证书时服务器拒绝非回环地址绑定。本文不自动部署证书、开放防火墙或开启公网服务。

## Worker、Reviewer、Owner 操作

每台机器各自 clone 同一 Git 仓库；先同步代码。推荐先取单个任务，检查源 revision/任务内容与本地 Issue 一致，再调用现有 issue-branch。

```powershell
# URL 与令牌路径使用本人的配置。
python circle/scripts/service_client.py --url 'https://circle.example:8769' --token-file 'C:\Private\alice.token'

# 查看单个 Issue 的完整执行上下文。
python circle/scripts/service_client.py --url 'https://circle.example:8769' --token-file 'C:\Private\alice.token' --path '/v1/tasks/CIR-XXXXXXXXXX'

# POST 的正文来自 UTF-8 JSON 文件。
python circle/scripts/service_client.py --url 'https://circle.example:8769' --token-file 'C:\Private\alice.token' --path '/v1/tasks/CIR-XXXXXXXXXX/claim' --body claim.json
```

| 动作 | 角色 | JSON 正文示例 |
|---|---|---|
| claim | worker | `{"ttl":300}` |
| heartbeat | worker | `{"attempt":1,"ttl":300}` |
| submit | worker | `{"attempt":1,"commit":"完整哈希","evidence":[{"criterion":1,"kind":"test-log","locator":"evidence/test.log","sha256":"...","exit_code":0,"observed_at":"...","commit":"完整哈希"}]}` |
| assign | admin | `{"expected_version":3,"reviewer":"Reviewer"}` |
| review | 指定 reviewer | `{"expected_version":4,"decision":"approve","note":"结论","verdicts":[{"criterion":1,"status":"pass","note":"证据说明"}]}` |
| accept | admin | 同样提供逐项全 pass 的 `verdicts` 与最终 `note` |
| reject | admin | `{"expected_version":5,"reason":"最终验收驳回原因"}`，回到 ready 并开启新尝试 |
| release | worker | `{"attempt":1,"reason":"交接原因"}` |
| cancel/retry/recover | admin | `{"expected_version":5,"reason":"操作原因"}` |

attempt/version 均取最新响应中的实际值，不照抄示例。heartbeat 会改变 version；提交后再读取版本安排审核。证据引用不会被服务自动下载或执行。

建议完整顺序：领取 → 建任务分支 → 实现并续期 → 测试、提交代码 → submit → 独立 Review → 按 Git 流程合入并同步中央项目克隆 → 最终验收 → sync。

```powershell
# 中央项目已经拉到验收提交后，回写 Circle 事实。
python circle/scripts/service.py --database $database sync --project-root 'C:\Projects\MyProject'
python circle/scripts/circle.py --project-root 'C:\Projects\MyProject' validate
python circle/scripts/circle.py --project-root 'C:\Projects\MyProject' render
# 随后将这些事实变更提交到 Git，其他机器再拉取。
```

GET `/v1/events` 返回最近 500 条流程事件；事件表保留此前记录。数据库须由管理员备份。服务无需 GitHub API、模型 API 或额外账户。

## 当前边界

- 不自动启动 Codex、不自动抢占或杀死远程 Agent、不替人判断代码及验收证据是否正确。
- 不自动 push/pull、创建 PR 或合并代码。Worker 需同步已完成依赖对应的代码，不能仅凭服务状态直接执行旧工作树。
- 图中带问号的企业微信/API/computer-use，以及飞书、Notion、Slack 接入未实现。
- 中央 SQLite 服务为单主机设计，不是多副本高可用集群，也不支持共享磁盘上的多服务器部署。
- 当前验证包含同机并发 HTTP 客户端、真实 Git 提交与分支、持久化重启、租约与审核门禁；尚未进行两台物理机器、网络分区或生产负载验收。
