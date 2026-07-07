# 开发者 C 指导书：Product / Reports / Evaluation

## 你的目标

你负责让 TGA 变成团队可以使用、演示和验收的产品。第一周优先做 CLI 入口、配置样例、Markdown 报告和 demo 靶场。Web UI 可以做，但不要让 UI 占用核心闭环时间。

## 本周交付

1. `tga run` 或 `scripts/tga_run_demo.py` 入口。
2. 示例任务配置：web CTF、web audit、code audit。
3. Markdown 报告生成器。
4. Demo runbook。
5. 验收脚本或手动验收流程。

## 你负责开发的文件

```text
tga/contracts.py  # 只 import，不要私自改模型
tga/cli/main.py
tga/cli/config_loader.py
tga/reporting/report_model.py
tga/reporting/markdown_report.py
tga/reporting/evidence_renderer.py
examples/web_ctf/task.json
examples/web_audit/task.json
examples/code_audit/task.json
scripts/tga_run_demo.py
scripts/tga_generate_report.py
docs/TGA_RUNBOOK.md
docs/TGA_REPORT_SCHEMA.md
tests/test_markdown_report.py
tests/test_config_loader.py
```

## 对接注意事项

你负责把用户输入变成统一 `TGATask`，不要在 CLI 或 examples 里使用另一套字段名。配置文件必须能被下面的代码直接解析：

```python
from tga.contracts import TGATask

task = TGATask.model_validate(config_json)
```

和 A 的对接边界：

- config loader 只产出 `TGATask`。
- report 只读取 `EvidenceStore.task_snapshot(task_id)` 和 ArtifactStore，不直接查 SQLite。
- 报告字段以 snapshot 为准，不在 reporting 层重新推断 confirmed/candidate。

和 B 的对接边界：

- examples 里的 target/scope 必须能通过 B 的 tool policy。
- demo 文档里写清楚需要哪些 MCP 工具可用。
- report 的 `Tools Used` 直接读取 artifact 的 `tool` 字段，所以你要和 B 确认工具名枚举。

报告输入 snapshot 约定：

```json
{
  "task": {},
  "intents": [],
  "artifacts": [],
  "findings": [],
  "flags": [],
  "events": []
}
```

报告不要把 `unverified leads` 写入 `confirmed findings`。如果 snapshot 中 finding status 不是 `confirmed`，只能放到 Unverified Leads 或 Candidate Findings。

## 任务配置格式

```json
{
  "name": "local-web-audit-demo",
  "mode": "web_audit",
  "target": "http://127.0.0.1:8080",
  "scope": ["127.0.0.1:8080"],
  "intensity": "normal",
  "allow_active_scan": true,
  "goal": "Find and prove common web vulnerabilities in scope.",
  "flag_format": null
}
```

CTF 示例：

```json
{
  "name": "web-ctf-demo",
  "mode": "ctf",
  "target": "http://127.0.0.1:8081",
  "scope": ["127.0.0.1:8081"],
  "intensity": "normal",
  "allow_active_scan": true,
  "goal": "Solve the challenge and recover the flag.",
  "flag_format": "flag\\{[^}]+\\}"
}
```

## 报告结构

Markdown 报告第一周就够：

```text
# TGA Report

## Summary
- Task
- Mode
- Target
- Scope
- Started / Finished
- Intensity
- Tools Used

## Confirmed Findings
- Title
- Severity
- Target
- Evidence Artifact
- Evidence Excerpt
- Reproduction Steps
- Remediation

## CTF Flags
- Flag
- Evidence Artifact
- Provenance

## Unverified Leads

## Dead Ends

## Artifacts

## Limitations
```

## Demo 建议

Web CTF：

- 本地可启动的小题。
- flag 格式固定。
- 最终报告能显示 flag 和 artifact。

Web audit：

- 本地 DVWA、Juice Shop 或自建 vulnerable app。
- 至少一个 confirmed finding。
- 至少一个 dead end 或 unverified lead。

Code audit：

- 自建一个含 hardcoded secret 和简单注入风险的 repo。
- semgrep/gitleaks 输出必须进入 artifact。

## 每日安排

Day 1：

- 写 task JSON schema 和 config loader。
- 确定三个 demo 目标。

Day 2：

- 写 CLI 入口：读取 config -> 创建 task -> 调 orchestrator。
- 写报告模板。

Day 3：

- 对接 evidence store，生成第一版 report。
- 准备 examples。

Day 4：

- 跑通三个 demo。
- 写 `docs/TGA_RUNBOOK.md`。

Day 5：

- 按验收清单跑全流程。
- 整理报告样例和已知限制。

## 验收标准

- 新成员按 runbook 可以跑起至少一个 demo。
- 至少生成一份 CTF 报告和一份 audit 报告。
- 报告中的 confirmed finding 都有 artifact id。
- 报告明确标出 scope、工具、限制和未验证线索。
