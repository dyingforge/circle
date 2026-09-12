# 08: 显式生成 DAG 并完成端到端验收

**What to build:** 用户可以按需从事实源重建 Mermaid DAG，并通过一套可重复执行的验收流程证明 Circle v1 的成功路径和关键保护措施均有效。

**Blocked by:** 07: 安全维护 Issue 依赖关系

**Status:** ready-for-agent

- [ ] `/render` 显式生成 Mermaid DAG；其他 Issue 修改不会隐式改写该派生文件。
- [ ] 三 Issue 完整流程覆盖 Preview、Commit、状态推进、新增 Issue、依赖维护、DAG 和项目完成。
- [ ] 失败测试至少覆盖依赖环、stale revision、重新打开或修改 `done` Issue，并验证无部分写入。
- [ ] 无第三方依赖实现通过测试、项目校验、Skill 结构校验和一次独立调用验收。
