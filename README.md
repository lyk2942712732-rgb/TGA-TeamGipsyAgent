# TGA — Trusted Goal Agent

TGA 是面向已授权 CTF 靶机和安全评审任务的本地 Runtime。当前项目只保留 v2 Runtime：任务、会话、事件、控制、证据、报告均通过 `/api/v2` 工作，不存在旧聊天 UI 或 v1 API 兼容路径。

## Quick Start

首次使用请在 Windows CMD 或 PowerShell 中依次执行：

```powershell

cd TGA-TeamGipsyAgent

# 需要 Python 3.11+；此命令会安装 tga、FastAPI 和桌面窗口依赖
python -m pip install -e ".[dev]"

# 需要已安装 Node.js/npm；构建前端并打开桌面窗口
tga go
```

若 `tga` 不是内部或外部命令，请重新打开终端、激活安装时使用的虚拟环境，或将该 Python 的 `Scripts` 目录加入 PATH。只想在浏览器中打开时使用 `tga web`，默认地址为 `http://127.0.0.1:5173`。

项目内的 `mcp-security-hub/` 会被自动发现，但 Docker 镜像**不会**由 `tga go` 自动构建。只有需要实际调用 MCP 工具时，才安装 Docker Desktop 并执行：

```powershell
docker compose -f .\mcp-security-hub\docker-compose.yml build
```

## 核心约束

- 只对明确授权且在 `scope` 内的目标执行动作。
- 任何 Flag 必须匹配任务的 `flag_format`、出现在真实 artifact 中，并通过服务端 flag gate。
- Flag 一旦确认即视为任务完成：Runtime 停止后续攻击动作并生成报告。
- TGA 不会向靶机自动提交 Flag；报告和证据面板会保留可人工核验的 Flag 与 artifact。
- 所有工具结果、策略拒绝、超时和失败均保留为可回放事件或 artifact。

## 项目结构

- `tga/runtime/`：Manager、Solver、策略板、事件与持久会话。
- `tga/capabilities/`：受 scope、风险和预算约束的执行能力。
- `apps/api/`：FastAPI v2 Runtime API。
- `apps/web/`：React Runtime 控制台。
- `mcp-security-hub/`：随项目放置的 MCP 工具目录。
- `runs/`：本地任务的 SQLite 证据库、artifact、检查点与报告（运行时生成）。

## 安装

在项目根目录执行：

```powershell
python -m pip install -e ".[dev]"
```

桌面/浏览器界面构建还需要 Node.js 和 npm。运行测试：

```powershell
pytest -q
cd apps\web
npm test
npm run build
```

## 启动界面

两个命令都会在同一端口同时启动 FastAPI 后端和 React 前端；无需额外打开后端终端。

```powershell
# 原生桌面窗口，默认 http://127.0.0.1:8123
tga go

# 默认浏览器，默认 http://127.0.0.1:5173；Ctrl+C 停止
tga web
```

端口被占用时可自行指定，例如 `tga web --port 5174`。`tga go` 关闭窗口会停止本地服务。

## MCP 工具目录

默认使用项目内的相对路径：

```text
TGA-TeamGipsyAgent/
└── mcp-security-hub/
```

无需在代码或环境变量中填写 `C:\Users\...` 等机器相关绝对路径。仅当需要使用另一份 Hub 时才设置覆盖变量：

```powershell
$env:TGA_MCP_SECURITY_HUB_ROOT = 'D:\another\mcp-security-hub'
```

查看当前项目内的 MCP 目录和镜像可用性：

```powershell
python scripts\tga_mcp_catalog.py --hub-root .\mcp-security-hub --summary
python scripts\tga_mcp_healthcheck.py --hub-root .\mcp-security-hub
```

MCP 的“已注册”“本机镜像可用”“被当前任务策略允许”是三个不同状态。即使镜像存在，执行仍受授权范围、`allow_active_scan`、风险分级、限速与 artifact 规则约束。

## Runtime API 概览

- `POST /api/v2/tasks`：创建任务、初始化 v2 Session，并异步调度 Runtime。
- `GET /api/v2/tasks/{task_id}/session`：读取可信快照。
- `GET /api/v2/tasks/{task_id}/events/stream`：按 `seq` 增量读取 SSE 事件。
- `POST /api/v2/tasks/{task_id}/control`、`/hints`：仅向 Manager 请求控制或提示。
- `GET /api/v2/tasks/{task_id}/report`：生成并下载 Markdown 报告。

前端不直接执行工具、不写证据库，也不自行推断 Flag 成功；页面只展示服务端快照与事件。

## 本地评测

```powershell
python evals\run_eval.py
```

评测驱动真实 Manager 和受控执行器，覆盖本地隐藏路径、POST 表单、scope 拒绝与 workspace/binary 场景，并输出成功率、动作数、重复动作、空计划、scope 拒绝与耗时等指标。
