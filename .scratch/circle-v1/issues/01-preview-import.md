# 01: 生成只读的项目导入 Preview

**What to build:** 用户显式调用 `$circle /init` 后，可以把项目 Markdown 或纯文本归一化为项目与 Issue 数据，为 Issue 分配稳定 CIR ID，查看推断、警告和仓库外的带哈希暂存快照，同时目标项目保持不变。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Skill 只响应显式 `$circle` 调用，并提供中文导入模板与宽松输入说明。
- [ ] Preview 展示项目、正式 CIR ID、标准化字段、推断、警告、原始输入摘要和快照哈希。
- [ ] Preview 生成后目标工作区中不存在新建的 `.circle` 事实库。
- [ ] 暂存快照保存在目标仓库之外，且同一份规范化输入只解析一次。
