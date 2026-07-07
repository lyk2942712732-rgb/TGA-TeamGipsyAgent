# TGA Week 1 Sprint Plan

## Day 1：定接口和安全边界

全员：

- 确认 TGA 是独立项目，不依赖其他项目代码。
- 初始化仓库、Python 环境、测试框架。
- 确定授权范围策略：没有 scope 不允许 web audit。

开发者 A：

- 定义 `TGATask`、`Intent`、`Finding`。
- 写 `scope.py` 第一版。

开发者 B：

- 跑起 mcp-security-hub。
- 确认 MVP 工具清单。

开发者 C：

- 写 task JSON 示例。
- 确定三个 demo 场景。

## Day 2：跑通最小执行

开发者 A：

- 写 SQLite evidence store。
- 写 artifact store。
- 写 flag gate。

开发者 B：

- 写 subprocess worker。
- 写 output parser。
- 写 MCP healthcheck。

开发者 C：

- 写 CLI demo runner。
- 写 Markdown 报告模板。

## Day 3：接上工具和证据门

开发者 A：

- 写 finding evidence gate。
- 将 gate 结果写回 evidence store。

开发者 B：

- 实现 tool policy。
- 将 MCP/工具输出保存为 artifact。

开发者 C：

- 从 evidence store 读取数据生成报告。
- 准备 examples。

## Day 4：端到端 demo

全员：

- 跑 Web CTF demo。
- 跑 Web audit demo。
- 跑 code audit demo。

开发者 A：

- 修 gate 和 store 问题。

开发者 B：

- 修工具失败、超时、scope 拒绝问题。

开发者 C：

- 完成 `docs/TGA_RUNBOOK.md`。
- 输出报告样例。

## Day 5：验收和冻结 MVP

全员：

- 跑测试。
- 按 `acceptance-checklist.md` 验收。
- 记录已知限制。
- 冻结 Week 1 接口，避免继续扩 scope。

最终交付：

- 可以运行的本地 TGA MVP。
- 三个 demo config。
- 至少一份 CTF 报告和一份 audit 报告。
- 单元测试覆盖核心安全边界。

