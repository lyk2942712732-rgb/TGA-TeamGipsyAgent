# TGA Week 1 验收清单

## 项目基础

- [ ] TGA 是独立仓库或独立目录，不依赖其他项目代码。
- [ ] `pytest` 可以运行。
- [ ] `.env.example` 不包含真实密钥。
- [ ] `.mcp.tga.example.json` 不包含真实 token。
- [ ] README 能说明如何启动 MVP。

## 安全边界

- [ ] web audit 模式没有 scope 时拒绝启动。
- [ ] out-of-scope target 被拒绝。
- [ ] passive intensity 不允许主动扫描。
- [ ] active scan 必须显式开启 `allow_active_scan`。
- [ ] 所有工具调用记录 target、tool、time、artifact。

## CTF 能力

- [ ] 能跑通一个本地 Web CTF demo。
- [ ] flag 匹配 `flag_format`。
- [ ] flag 出现在真实 stdout/stderr/artifact 中。
- [ ] placeholder flag 被拒绝。
- [ ] 报告能显示 flag provenance。

## 漏洞审查能力

- [ ] Web audit demo 至少产生一个 confirmed finding。
- [ ] Code audit demo 至少产生一个 confirmed finding。
- [ ] 没有 artifact 的 finding 不能 confirmed。
- [ ] confirmed finding 必须包含 evidence artifact id。
- [ ] unverified leads 不会被写成 confirmed findings。

## MCP / 工具能力

- [ ] mcp-security-hub 能启动。
- [ ] healthcheck 能显示 MVP 工具状态。
- [ ] 至少 nmap/whatweb、nuclei 或 ffuf、semgrep 或 gitleaks 三类工具有一类可跑通。
- [ ] 工具失败不会导致整个任务崩溃。
- [ ] 工具输出保存为 artifact。

## 报告

- [ ] 可以生成 Markdown 报告。
- [ ] 报告包含 task、mode、target、scope、intensity。
- [ ] 报告包含 tools used。
- [ ] 报告区分 confirmed findings、unverified leads、dead ends。
- [ ] 每个 confirmed finding 有复现步骤或证据片段。
- [ ] 报告包含 limitations。

## 测试

- [ ] `test_scope_policy.py`
- [ ] `test_flag_gate.py`
- [ ] `test_finding_gate.py`
- [ ] `test_tool_policy.py`
- [ ] `test_markdown_report.py`

