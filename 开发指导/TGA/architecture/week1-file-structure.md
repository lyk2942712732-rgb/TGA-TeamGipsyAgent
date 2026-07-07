# TGA 本周要开发的文件结构

这是一个独立新项目的 Week 1 结构建议。第一周不要把目录铺得过大，先保证核心闭环。

## Week 1 目标结构

```text
TGA/
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── .mcp.tga.example.json
├── docs/
│   ├── TGA_MVP.md
│   ├── TGA_SECURITY_MODEL.md
│   ├── TGA_RUNBOOK.md
│   └── TGA_REPORT_SCHEMA.md
├── tga/
│   ├── __init__.py
│   ├── contracts.py
│   ├── cli/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   └── config_loader.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── task.py
│   │   ├── intents.py
│   │   ├── findings.py
│   │   ├── scope.py
│   │   ├── flag_gate.py
│   │   └── evidence_gate.py
│   ├── orchestrator/
│   │   ├── __init__.py
│   │   ├── planner.py
│   │   ├── scheduler.py
│   │   └── run_loop.py
│   ├── workers/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── subprocess_worker.py
│   │   ├── cli_agent_worker.py
│   │   └── output_parser.py
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── mcp_catalog.py
│   │   ├── mcp_client.py
│   │   ├── mcp_healthcheck.py
│   │   ├── tool_policy.py
│   │   ├── tool_runner.py
│   │   └── rate_limit.py
│   ├── evidence/
│   │   ├── __init__.py
│   │   ├── store.py
│   │   ├── artifacts.py
│   │   └── schema.sql
│   └── reporting/
│       ├── __init__.py
│       ├── report_model.py
│       ├── markdown_report.py
│       └── evidence_renderer.py
├── examples/
│   ├── web_ctf/
│   │   └── task.json
│   ├── web_audit/
│   │   └── task.json
│   └── code_audit/
│       └── task.json
├── scripts/
│   ├── tga_mcp_healthcheck.py
│   ├── tga_run_demo.py
│   └── tga_generate_report.py
└── tests/
    ├── test_task_model.py
    ├── test_scope_policy.py
    ├── test_flag_gate.py
    ├── test_finding_gate.py
    ├── test_tool_policy.py
    ├── test_output_parser.py
    ├── test_config_loader.py
    └── test_markdown_report.py
```

## MVP 必须先开发的文件

优先级 P0：

```text
tga/core/task.py
tga/contracts.py
tga/core/scope.py
tga/core/flag_gate.py
tga/core/evidence_gate.py
tga/evidence/store.py
tga/evidence/artifacts.py
tga/workers/subprocess_worker.py
tga/workers/output_parser.py
tga/tools/tool_policy.py
tga/reporting/markdown_report.py
scripts/tga_run_demo.py
```

优先级 P1：

```text
tga/tools/mcp_catalog.py
tga/tools/mcp_client.py
tga/tools/mcp_healthcheck.py
tga/orchestrator/planner.py
tga/orchestrator/scheduler.py
tga/cli/main.py
examples/*/task.json
docs/TGA_RUNBOOK.md
```

优先级 P2：

```text
tga/workers/cli_agent_worker.py
tga/tools/rate_limit.py
tga/reporting/evidence_renderer.py
docs/TGA_SECURITY_MODEL.md
```

## 文件职责简表

| 文件 | 职责 |
| --- | --- |
| `task.py` | 定义任务输入模型 |
| `scope.py` | 授权范围校验 |
| `flag_gate.py` | CTF flag 证据门 |
| `evidence_gate.py` | 漏洞 finding 证据门 |
| `store.py` | SQLite evidence store |
| `artifacts.py` | 保存和读取工具输出 |
| `planner.py` | 把任务拆成 recon/verify/report intent |
| `scheduler.py` | 分配 intent 给 worker |
| `subprocess_worker.py` | 执行本地命令或脚本 |
| `cli_agent_worker.py` | 可选：调用 Claude/Codex 等 CLI agent |
| `output_parser.py` | 解析 worker marker |
| `mcp_catalog.py` | 管理 MVP MCP 工具清单 |
| `tool_policy.py` | scope/intensity 工具准入 |
| `markdown_report.py` | 生成 Markdown 报告 |

## 第一周不建议开发

- 复杂 Web dashboard。
- 分布式 worker。
- 自动提交平台 flag。
- 长期知识库和 RAG。
- 全量 MCP 工具 marketplace。
- 多租户权限系统。
