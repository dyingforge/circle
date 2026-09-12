# 04: 查看 Issue 与项目可执行状态

**What to build:** 用户可以查看项目状态、Issue 列表和单个 Issue，并从事实源得到准确的 blocked、unblocked、actionable 及项目完成情况。

**Blocked by:** 03: 安全导入带依赖关系的多 Issue 项目

**Status:** ready-for-agent

- [ ] `/status` 汇总生命周期状态并列出当前 actionable Issue。
- [ ] `/issue list` 和 `/issue show` 返回 Issue 的事实字段及计算得出的阻塞信息。
- [ ] 阻塞状态不写入事实文件；被取消的 blocker 仍阻塞下游。
- [ ] 所有读取命令能识别并报告损坏或不一致的事实库。
