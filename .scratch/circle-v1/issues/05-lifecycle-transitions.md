# 05: 通过受约束的生命周期推进 Issue

**What to build:** 用户可以让 Issue 沿受支持的生命周期推进、取消或恢复，同时系统始终维护 blocker 和已完成 Issue 的不变量。

**Blocked by:** 04: 查看 Issue 与项目可执行状态

**Status:** ready-for-agent

- [ ] 支持 `draft → ready → in_progress → review → done`，非终态可取消，`cancelled` 可恢复为 `draft`。
- [ ] 进入 `in_progress` 或 `done` 前，所有 blocker 必须为 `done`。
- [ ] `done` 不可重新打开，成功转换递增 revision 并可保存备注。
- [ ] stale revision 或非法转换失败时不修改任何事实文件。
