# 03: 安全导入带依赖关系的多 Issue 项目

**What to build:** 用户可以一次导入多个相互依赖的 Issue；临时 key 在 Preview 中解析为正式 CIR ID，并且任何不确定或非法的依赖都不会进入事实库。

**Blocked by:** 02: 从已批准快照原子创建事实库

**Status:** ready-for-agent

- [ ] 两阶段导入先为全部 Issue 分配 CIR ID，再把 `blocked_by` 临时 key 转换为正式 ID。
- [ ] 正式 Issue 文件仅保存 CIR ID，不保存临时 key。
- [ ] 重复 key、重复 Issue、未知引用、自引用和依赖环均在写入前被拒绝。
- [ ] 无法唯一确定的依赖由 Skill 要求用户修正，而不是猜测后提交。
