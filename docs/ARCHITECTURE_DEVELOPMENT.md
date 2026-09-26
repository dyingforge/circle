# 基于 Excalidraw 架构的开发记录

## 图的来源与范围

从用户提供的 Excalidraw 房间，按官方开源客户端的存储格式读取官方 Firestore 场景快照，在本机解密。未修改在线画布，未向第三方阅读服务转发房间密钥。

- 最新已保存快照更新时间：2026-09-23 08:34:39.893495 UTC，即北京时间 16:34:39。
- 提取 Circle 工作流区域，共 94 个非删除元素；未把同一画布中的翻译、生产排程等其他项目图混入本项目。
- 源图：[circle-workflow.excalidraw](architecture/circle-workflow.excalidraw)。
- 可读预览：[circle-workflow-preview.png](architecture/circle-workflow-preview.png)，已视觉检查。按源几何绘制，原始手绘笔触和字体以 Excalidraw 源文件为准。
- [来源记录与文件 SHA-256](architecture/source-metadata.json)。未保存房间密钥。
- “最新”指抓取时官方存储返回的最新已保存版本，未声称包含其他在线用户尚未保存的实时修改。

## 实现

图中的主链为：需求、架构 → Issue → Service → Agent 实现 → 委派 Review → 最终验收。原仓库已有左侧文档和 Issue 控制、任务分支，本次增加中间的中央 Service 与右侧审核验收控制。

新增程序：

- `circle/scripts/service.py`：发布任务、集中协调、HTTP 服务、运行状态、最终结果回写。
- `circle/scripts/service_client.py`：各机器的鉴权 JSON 客户端；令牌来自文件或环境变量，不通过命令行值传递。
- `circle/scripts/service_credentials.py`：为 worker/reviewer/admin 生成不同身份的令牌文件与服务器哈希配置。

Service 只使用 Python 标准库。HTTP、SQLite、租约、角色授权是实现选择，不是原图上写明的技术选型。采用主动领取任务的方式，未新增模型调用或自动启动 Agent 的功能。

已实现的约束：

1. 中央 SQLite 事务保证同一任务只有一个有效领取者；每次执行具有递增 attempt。
2. 依赖全部完成后才能领取；负责人非空时按身份匹配。
3. 租约可续期、过期可重新领取；旧执行者及旧 attempt 的更新会被拒绝。
4. 提交需携带完整 commit hash、证据引用和全部验收项；不会在服务器执行证据中的内容。
5. 指定独立 Reviewer 审核，可以通过或驳回；驳回后新执行必须重新提交证据。
6. 独立 Owner 最终验收，未经审核不能直接完成。
7. 发布幂等、追加式、不会覆盖已派发任务；上下文随任务冻结。
8. `sync` 回写前检查源 Issue 指纹、项目身份和提交是否合入 HEAD；按依赖顺序写入，重复运行不重复修改。
9. 服务凭据分角色，远程绑定须提供 TLS 证书；客户端拒绝远程明文 HTTP，不随重定向转发令牌。
10. 全库校验现在拒绝重复标题，以及验收未完成却标成 done 的任务。

本地独立模式仍可使用，与中央模式并存。旧版 `.circle` 生命周期不变；中央执行中的任务不能再混用本地完成命令，最终由 sync 将审核证据与完成状态写回。

## 验证证据

最终全量运行：**115 项测试通过，94.847 秒**，Windows + Python 3.10.11。相较上一轮 Windows 适配的 105 项，新增 10 项 Service 集成测试；此前用于复现两项事实库校验漏洞的用例已改为验证正确拒绝。旧模型测试夹具原先使用重复标题、未勾选的 done 状态，本次相应改为符合已声明约束的合法夹具。`git diff --check` 通过。

[全量测试日志](../evidence/architecture-development-tests.log) 是最终测试结果的依据。[真实 Git + HTTP 流程记录](../evidence/service-e2e.json) 包含本次临时测试仓库的真实提交哈希、完成任务快照、下游解除依赖和事件序列；这是测试夹具实现，不是生产项目交付证据。

测试覆盖：

- 既有控制器、Windows 文件锁、中文及 emoji 路径、Git 分支与双克隆合并。
- 两个真实 HTTP 客户端同时领取同一任务，一个成功、一个收到 409。
- 过期租约重新领取、旧 attempt 拒绝、续期、越权拒绝、未指定 Reviewer 拒绝、旧 version 拒绝。
- 验收项或证据不全时拒绝提交，Review 驳回后的重新领取与提交。
- 真实创建任务分支、提交代码、HTTP 提交审核验收；未合入时 sync 拒绝，合入后 sync 成功，第二次 sync 无修改。
- 完成任务的下游解除阻塞；重新打开 SQLite 协调器保留任务状态。
- 独立子进程运行凭据生成器、publish、serve、client、status；无 TLS 的远程绑定拒绝。

旧报告 `WINDOWS_COLLABORATION_REPORT.md` 记录了开发前的缺口，其中两个全库校验漏洞已修复；本地模式仍不提供跨克隆互斥，跨机器应使用本次中央 Service。

## 未完成与未验证

- 未接入微信、QQ、企业微信、飞书、Notion、Slack。源图对此仍包含问号与方案备选。
- 不自动启动或终止 Codex，不自动执行测试、核实证据内容或做人工审核决定。
- 不自动 push/pull、建 PR 或合并代码。中央状态 done 不替代工作机器同步代码的义务。
- 失去租约会阻止服务接受旧结果，但无法阻止失联机器继续运行或产生外部副作用。
- SQLite 是单机中央服务，不是多副本分布式集群；数据库不能放在同步盘或网络共享中。
- 已验证本机 HTTP、多线程竞争、真实 Git 与独立 CLI 子进程。尚未进行两台物理机联网、真实 TLS 证书部署、网络分区、故障注入或生产负载验收。
- 未安装为个人 Skill，未开启持续后台服务，未对公网发布，未提交或推送 Git。

可复现启动和完整使用步骤见 [Service 操作说明](../circle/references/SERVICE.md)。
