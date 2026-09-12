# 06: 新增和编辑独立 Issue

**What to build:** 用户可以新增修正 Issue 或编辑现有 Issue 的执行信息，并通过 revision 防止当前工作树中的陈旧覆盖。

**Blocked by:** 05: 通过受约束的生命周期推进 Issue

**Status:** ready-for-agent

- [ ] `/issue add` 创建不可重复使用的新 CIR ID，并支持正文、负责人、预估、依赖和初始状态。
- [ ] `/issue edit` 只修改允许的字段，成功后递增 revision。
- [ ] `done` Issue 的核心内容和依赖不可修改，但非核心负责人信息可按规则维护。
- [ ] stale revision、未知 blocker 或无效数据导致整笔修改被拒绝。
