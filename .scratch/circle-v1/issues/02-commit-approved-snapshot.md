# 02: 从已批准快照原子创建事实库

**What to build:** 用户确认 Preview 后，可以使用同一个快照及哈希创建项目事实库；系统不会重新解析原始输入，也不会在快照不可用时留下部分结果。

**Blocked by:** 01: 生成只读的项目导入 Preview

**Status:** ready-for-agent

- [ ] Commit 仅接受 Preview 返回的快照哈希，并由该快照生成项目与独立 Issue 文件。
- [ ] 快照缺失、损坏、哈希不一致或目标事实库已存在时，操作失败且不产生部分写入。
- [ ] `PROJECT.md` 与 `issues/*.md` 成为事实源，每个 Issue 的初始 revision 为 1。
