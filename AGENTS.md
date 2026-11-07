# 项目内技能

发布 VPN Traffic 版本时，使用 [traffic-monitor-release](.agents/skills/traffic-monitor-release/SKILL.md)。该技能随仓库提交。

用户未指定版本号时，先列出本次更新点，再给出候选版本号并等待用户确认；已明确指定时不重复询问。发布不额外安排测试或验收，现有 CI 按工作流自动执行。

普通修改、普通提交及技能编辑不自动触发应用发布。
