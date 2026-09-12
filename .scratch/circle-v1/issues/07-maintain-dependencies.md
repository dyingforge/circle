# 07: 安全维护 Issue 依赖关系

**What to build:** 用户可以独立添加或删除 Issue 之间的阻塞边，并立即看到由此产生的 actionable 变化，而非法图修改不会部分落盘。

**Blocked by:** 06: 新增和编辑独立 Issue

**Status:** ready-for-agent

- [ ] `/dependency add` 与 `/dependency remove` 使用 expected revision 并在成功后递增 revision。
- [ ] 未知 ID、自引用、重复边、缺失边和依赖环均被拒绝。
- [ ] 对 `done` Issue 的依赖不可修改，且任何失败都保持原图不变。
- [ ] 成功修改后报告新 revision 和新变为 actionable 的 Issue。
