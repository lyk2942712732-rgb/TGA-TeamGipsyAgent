# TGA 本周要开发的文件结构

这是 TGA 独立新项目的 Week 1 文件结构。第一周不要把目录铺得过大，先保证核心闭环：

用户任务配置 -> 生成 intent -> worker 执行工具 -> 保存 artifact -> evidence gate 判定 -> 生成 report。

下面的目录树右侧已经加了说明。开发时请按职责边界放文件，不要把调度、工具执行、证据判断、报告生成混在一个文件里。

## Week 1 目标结构

```text
TGA/                                      # 项目根目录.
|-- README.md                             # 项目说明、快速启动、Week 1 原则.
|-- pyproject.toml                        # Python 项目配置、依赖、pytest 配置.
|-- .env.example                          # 环境变量示例，不放真实密钥.
|-- .gitignore                            # Git 忽略规则.
|-- .mcp.tga.example.json                 # MCP 配置示例，用于接 mcp-security-hub.
|
|-- docs/                                 # 项目文档，给开发者和使用者看.
|   |-- TGA_MVP.md                        # MVP 范围：本周做什么、不做什么.
|   |-- TGA_SECURITY_MODEL.md             # 安全边界：scope、主动扫描、证据门.
|   |-- TGA_RUNBOOK.md                    # 启动、配置、跑 demo 的操作手册.
|   `-- TGA_REPORT_SCHEMA.md              # 报告字段和 snapshot 数据格式.
|
|-- tga/                                  # Python 主包，核心代码都在这里.
|   |-- __init__.py                       # 包初始化和版本号.
|   |-- contracts.py                      # 三个人共用的数据模型和接口契约，优先开发.
|   |
|   |-- cli/                              # 命令行入口，主要由开发者 C 负责.
|   |   |-- __init__.py                   # cli 子包初始化.
|   |   |-- main.py                       # tga 命令入口：读取配置、启动 run、写报告.
|   |   `-- config_loader.py              # 读取 task.json 并转成 TGATask.
|   |
|   |-- core/                             # 核心安全和任务逻辑，主要由开发者 A 负责.
|   |   |-- __init__.py                   # core 子包初始化.
|   |   |-- task.py                       # 任务辅助函数，例如生成 task_id、规范化 scope.
|   |   |-- intents.py                    # intent 辅助函数，例如生成 recon/verify/report intent.
|   |   |-- findings.py                   # finding 辅助函数，例如生成 finding_id.
|   |   |-- scope.py                      # 授权范围校验：URL、host、port、CIDR.
|   |   |-- flag_gate.py                  # CTF flag 证据门：格式 + placeholder + provenance.
|   |   `-- evidence_gate.py              # 漏洞 finding 证据门：artifact + scope + excerpt.
|   |
|   |-- orchestrator/                     # 调度层，连接 task、intent、worker、store.
|   |   |-- __init__.py                   # orchestrator 子包初始化.
|   |   |-- planner.py                    # 简单规则 planner：按 mode 生成 intent.
|   |   |-- scheduler.py                  # 顺序执行 intent，调用 worker，写 EvidenceStore.
|   |   `-- run_loop.py                   # 一次完整 task run 的主流程.
|   |
|   |-- workers/                          # worker 执行层，主要由开发者 B 负责.
|   |   |-- __init__.py                   # workers 子包初始化.
|   |   |-- base.py                       # Worker Protocol，统一 run(task, intent, workspace).
|   |   |-- subprocess_worker.py          # 本地 subprocess worker，Week 1 默认可跑.
|   |   |-- cli_agent_worker.py           # 可选后端模型执行适配器；不是 UI，不影响自研界面.
|   |   `-- output_parser.py              # 解析 stdout marker：FOUND_FLAG、UNVERIFIED_LEAD 等.
|   |
|   |-- tools/                            # 工具和 MCP 接入层，主要由开发者 B 负责.
|   |   |-- __init__.py                   # tools 子包初始化.
|   |   |-- mcp_catalog.py                # MVP 工具清单：nmap、whatweb、nuclei、semgrep 等.
|   |   |-- mcp_client.py                 # MCP 客户端封装，后续接 mcp-security-hub.
|   |   |-- mcp_healthcheck.py            # 检查本地/MCP 工具是否可用.
|   |   |-- tool_policy.py                # 工具准入策略：scope + intensity + allow_active_scan.
|   |   |-- tool_runner.py                # 统一运行工具，并保存输出 artifact.
|   |   `-- rate_limit.py                 # 简单限速，防止主动扫描过猛.
|   |
|   |-- evidence/                         # 证据和 artifact 存储，A 负责接口，B/C 调用.
|   |   |-- __init__.py                   # evidence 子包初始化.
|   |   |-- store.py                      # SQLite EvidenceStore：task、intent、finding、flag、event.
|   |   |-- artifacts.py                  # ArtifactStore：保存/读取 stdout、工具输出、报告.
|   |   `-- schema.sql                    # SQLite 表结构.
|   |
|   `-- reporting/                        # 报告生成，主要由开发者 C 负责.
|       |-- __init__.py                   # reporting 子包初始化.
|       |-- report_model.py               # 报告辅助模型，例如 tools_used.
|       |-- markdown_report.py            # 从 task_snapshot 生成 Markdown 报告.
|       `-- evidence_renderer.py          # 证据片段渲染，例如截断 excerpt.
|
|-- apps/                                 # TGA 自己的产品界面层，主要由开发者 C 负责.
|   |-- api/                              # Web UI 调用的后端 API，不直接写核心逻辑.
|   |   |-- main.py                       # FastAPI/Flask 入口，暴露 task、run、report API.
|   |   |-- schemas.py                    # API request/response schema，映射到 tga/contracts.py.
|   |   `-- routes.py                     # 路由：创建任务、查看状态、下载报告.
|   `-- web/                              # TGA 独立前端 UI，不依赖 Claude/Codex 的界面.
|       |-- package.json                  # 前端依赖，可选 Next/Vite/React.
|       |-- src/                          # 前端源码.
|       |   |-- App.tsx                    # 主页面：任务创建、运行状态、报告查看.
|       |   |-- api.ts                     # 调用 apps/api 的 HTTP/SSE 客户端.
|       |   `-- pages/                    # 页面：New Task、Run Detail、Report.
|       `-- README.md                     # 前端启动说明.
|
|-- examples/                             # 示例任务配置，C 负责维护.
|   |-- web_ctf/                          # Web CTF demo.
|   |   `-- task.json                     # CTF 任务配置，包含 flag_format.
|   |-- web_audit/                        # Web 漏洞审查 demo.
|   |   `-- task.json                     # Web audit 配置，必须有 scope.
|   `-- code_audit/                       # 本地代码审计 demo.
|       `-- task.json                     # Code audit 配置，target 指向本地代码目录.
|
|-- scripts/                              # 开发和演示脚本，C/B 共同维护.
|   |-- tga_mcp_healthcheck.py            # 输出 MVP 工具健康状态.
|   |-- tga_run_demo.py                   # 用 task.json 跑一次 demo.
|   `-- tga_generate_report.py            # 从 evidence.db 重新生成报告.
|
`-- tests/                                # 单元测试，三个人都要补自己模块的测试.
    |-- test_task_model.py                # TGATask 基础解析测试.
    |-- test_scope_policy.py              # scope allowlist 测试.
    |-- test_flag_gate.py                 # flag gate 测试.
    |-- test_finding_gate.py              # finding evidence gate 测试.
    |-- test_tool_policy.py               # 工具准入策略测试.
    |-- test_output_parser.py             # worker marker 解析测试.
    |-- test_config_loader.py             # task.json 加载测试.
    `-- test_markdown_report.py           # Markdown 报告渲染测试.
```

## 三个人该重点看的目录

开发者 A：Orchestrator / Evidence.

```text
tga/contracts.py                          # 共享模型，A 负责先定接口.
tga/core/                                 # scope、flag gate、finding gate.
tga/evidence/                             # EvidenceStore、ArtifactStore.
tga/orchestrator/                         # planner、scheduler、run_loop.
tests/test_scope_policy.py                # A 的安全边界测试.
tests/test_flag_gate.py                   # A 的 flag gate 测试.
tests/test_finding_gate.py                # A 的 finding gate 测试.
```

开发者 B：Workers / MCP Tools.

```text
tga/contracts.py                          # 只 import，不要自己重定义模型.
tga/workers/                              # worker 接口、subprocess worker、output parser.
tga/tools/                                # MCP、工具清单、tool policy、tool runner.
tga/evidence/artifacts.py                 # 保存工具输出 artifact.
scripts/tga_mcp_healthcheck.py            # 工具健康检查入口.
tests/test_tool_policy.py                 # B 的工具准入测试.
tests/test_output_parser.py               # B 的 marker 解析测试.
```

开发者 C：Product / Reports / Evaluation.

```text
tga/contracts.py                          # 读取统一 TGATask，不另写字段名.
tga/cli/                                  # CLI 入口和 config loader.
tga/reporting/                            # Markdown 报告生成.
apps/api/                                 # TGA 自己的后端 API，给 UI 调用.
apps/web/                                 # TGA 自己的独立 Web UI.
examples/                                 # 三个 demo task.json.
scripts/tga_run_demo.py                   # demo 启动脚本.
scripts/tga_generate_report.py            # 报告重生成脚本.
docs/                                     # runbook 和报告 schema.
tests/test_config_loader.py               # C 的配置加载测试.
tests/test_markdown_report.py             # C 的报告测试.
```

## MVP 必须先开发的文件

P0：没有这些文件，三个人无法联调.

```text
tga/contracts.py                          # 统一模型：TGATask、Intent、ArtifactRecord、Finding、WorkerResult.
tga/core/task.py                          # task 基础工具.
tga/core/scope.py                         # scope 判断.
tga/core/flag_gate.py                     # flag 证据门.
tga/core/evidence_gate.py                 # finding 证据门.
tga/evidence/store.py                     # SQLite evidence store.
tga/evidence/artifacts.py                 # artifact 存取.
tga/workers/subprocess_worker.py          # 最小可运行 worker.
tga/workers/output_parser.py              # marker 解析.
tga/tools/tool_policy.py                  # 工具是否允许运行.
tga/reporting/markdown_report.py          # 最小报告生成.
scripts/tga_run_demo.py                   # 最小 demo 入口.
```

P1：有了这些文件，MVP 开始接近可演示.

```text
tga/tools/mcp_catalog.py                  # MVP 工具清单.
tga/tools/mcp_client.py                   # MCP 客户端封装.
tga/tools/mcp_healthcheck.py              # 工具健康检查.
tga/orchestrator/planner.py               # 生成 intent.
tga/orchestrator/scheduler.py             # 分配 intent 给 worker.
tga/cli/main.py                           # tga 命令入口.
examples/*/task.json                      # 三个 demo 配置.
docs/TGA_RUNBOOK.md                       # 操作手册.
```

P2：有余力再做.

```text
tga/workers/cli_agent_worker.py           # 可选：后端调用模型命令行工具；不是用户界面.
apps/api/main.py                          # 自研 UI 的后端 API 入口.
apps/api/routes.py                        # 创建任务、查询状态、获取报告的 API.
apps/web/src/App.tsx                      # 自研 Web UI 主页面.
apps/web/src/api.ts                       # 前端调用后端 API 的封装.
tga/tools/rate_limit.py                   # 更细限速.
tga/reporting/evidence_renderer.py        # 更好看的证据片段渲染.
docs/TGA_SECURITY_MODEL.md                # 更完整安全模型.
```

## 文件职责简表

| 文件 | 谁负责 | 职责 |
| --- | --- | --- |
| `tga/contracts.py` | A 主导，B/C 共同确认 | 统一所有跨模块模型和枚举 |
| `tga/core/task.py` | A | 任务辅助函数 |
| `tga/core/scope.py` | A | 授权范围校验 |
| `tga/core/flag_gate.py` | A | CTF flag 证据门 |
| `tga/core/evidence_gate.py` | A | 漏洞 finding 证据门 |
| `tga/evidence/store.py` | A | SQLite evidence store |
| `tga/evidence/artifacts.py` | A/B | 保存和读取工具输出 |
| `tga/orchestrator/planner.py` | A | 把任务拆成 intent |
| `tga/orchestrator/scheduler.py` | A | 分配 intent 给 worker |
| `tga/workers/subprocess_worker.py` | B | 执行本地命令或脚本 |
| `tga/workers/cli_agent_worker.py` | B | 可选：后端模型命令行适配器，不是用户界面 |
| `tga/workers/output_parser.py` | B | 解析 worker marker |
| `tga/tools/mcp_catalog.py` | B | 管理 MVP MCP 工具清单 |
| `tga/tools/tool_policy.py` | B | scope/intensity 工具准入 |
| `tga/tools/tool_runner.py` | B | 统一工具运行和 artifact 捕获 |
| `tga/cli/config_loader.py` | C | 读取 task.json |
| `tga/cli/main.py` | C | CLI 入口 |
| `tga/reporting/markdown_report.py` | C | 生成 Markdown 报告 |
| `apps/api/main.py` | C | TGA 自研 UI 的后端 API 入口 |
| `apps/api/routes.py` | C | 任务创建、状态查询、报告下载接口 |
| `apps/web/src/App.tsx` | C | TGA 自研 Web UI 主页面 |
| `apps/web/src/api.ts` | C | 前端 API 调用封装 |
| `examples/*/task.json` | C | demo 任务配置 |

## 第一周不建议开发

- 复杂 Web dashboard。Week 1 可以做最小 UI：创建任务、查看状态、打开报告。
- 分布式 worker。
- 自动提交平台 flag。
- 长期知识库和 RAG。
- 全量 MCP 工具 marketplace。
- 多租户权限系统。
