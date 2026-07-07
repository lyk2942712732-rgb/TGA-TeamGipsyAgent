# TGA 独立项目本周开发指导

TGA 是你们团队新建的独立项目，目标是做一个面向授权漏洞审查和 CTF 解题的渗透型 agent。它不是 Muteki 的 fork，也不依赖 Muteki 的代码。第一周只做 MVP：能接收任务、调度 worker、调用安全工具、记录证据、生成报告。

## 本周 MVP 目标

到本周结束，TGA 应该能跑通三条闭环：

1. Web CTF：给定题目描述、目标、附件和 flag 格式，agent 能调用真实命令或 MCP 工具，找到 flag，并证明 flag 来自真实输出。
2. Web 漏洞审查：给定授权目标和 scope，agent 能做有限 recon、工具扫描、人工式验证，并输出 confirmed findings。
3. 代码审计：给定本地代码目录，agent 能调用 semgrep/gitleaks 等工具，输出带文件路径、规则、证据片段和修复建议的报告。

MVP 的成功标准不是“模型说完成了”，而是“证据链可复现”。所有 confirmed finding 都必须引用真实 artifact。

## 架构原则

- Worker 可以是 CLI agent 或普通 Python runner，但工具执行必须产生可保存的 stdout/stderr/artifact。
- 所有发现先进入 evidence store，再由 evidence gate 决定能否成为 confirmed。
- CTF flag 必须同时满足格式匹配和真实输出可追溯。
- 漏洞 finding 必须有工具输出、HTTP 响应、源码位置或复现脚本输出支撑。
- MCP 工具是能力层，不是事实层；MCP 结果必须落 artifact。
- 所有主动扫描都必须受 scope allowlist 和 intensity policy 限制。

## 三人本周分工

| 成员 | 角色 | 主责 |
| --- | --- | --- |
| 开发者 A | Orchestrator / Evidence | 任务模型、调度器、证据库、flag/finding 证据门 |
| 开发者 B | Workers / MCP Tools | worker 执行器、mcp-security-hub 接入、工具策略、artifact 捕获 |
| 开发者 C | Product / Reports / Evaluation | CLI/Web 入口、报告生成、demo 靶场、验收流程 |

## 本目录内容

- `guides/developer-a-orchestrator-evidence.md`：开发者 A 的开发指导书。
- `guides/developer-b-workers-mcp-tools.md`：开发者 B 的开发指导书。
- `guides/developer-c-product-reports-eval.md`：开发者 C 的开发指导书。
- `architecture/integration-contracts.md`：三个人必须统一的模型、接口、marker、错误码和目录契约。
- `architecture/week1-file-structure.md`：本周要开发的项目文件结构。
- `week1/sprint-plan.md`：周一到周五执行计划。
- `acceptance-checklist.md`：本周验收清单。

## 本周不要做

- 不做互联网泛扫平台。
- 不做未授权目标测试。
- 不做全自动横向移动和持久化能力。
- 不一次接入 mcp-security-hub 的全部工具。
- 不先做复杂 UI，优先保证任务闭环、证据闭环和报告闭环。
