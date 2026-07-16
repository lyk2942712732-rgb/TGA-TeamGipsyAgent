# TGA — Agent Session Runtime

TGA 是面向 CTF 与安全分析任务的本地 Agent Runtime。产品架构采用 BreachWeave 式持久 Solver Session：模型直接接收工具定义，工具结果回填同一会话，持续运行到完成、暂停、取消或达到回合上限。

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

公网直接运行时可使用（前端默认自动使用访问页面的公网地址）：

```powershell
tga web --host 0.0.0.0
```

若前端和 API 分别部署，才需要在**构建前**指定 API 地址。PowerShell 写法为：

```powershell
$env:VITE_TGA_API_BASE = "https://api.example.com"
tga web --host 0.0.0.0
```

项目内的 `mcp-security-hub/` 会被自动发现，但 Docker 镜像**不会**由 `tga go` 自动构建。只有需要实际调用 MCP 工具时，才安装 Docker Desktop 并执行：

```powershell
git clone https://github.com/FuzzingLabs/mcp-security-hub.git
docker compose -f .\mcp-security-hub\docker-compose.yml build
```

## 核心运行方式

- 新建 Session 时只需目标、任务目标和可选 Hint/Flag 格式。
- `target` 是该 Session 的目标契约，不再单独配置 scope、执行强度或主动探测开关。
- Solver 使用原生 function calling 连续调用 HTTP、workspace 与 MCP 工具。
- assistant `tool_calls` 与匹配的 tool result 保存在同一持久 transcript 中，支持恢复。
- Runtime 通过顺序事件展示模型消息、工具开始/结束、错误和最终结果。

## 项目结构

- `tga/runtime/agent_session.py`：产品使用的持久 Agent 工具循环。
- `tga/runtime/manager.py`：Session 生命周期控制与兼容入口。
- `tga/capabilities/`：HTTP、workspace 与 MCP 工具适配。
- `apps/api/`：FastAPI v2 Runtime API。
- `apps/web/`：React Runtime 控制台。
- `mcp-security-hub/`：随项目放置的 MCP 工具目录。
- `runs/`：本地任务状态、Solver transcript、workspace、artifact 与报告（运行时生成）。

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

MCP 工具只有在目录中被发现且本机运行依赖可用时才会出现在 Agent 工具目录中。工具失败会作为匹配的 tool result 回到当前 Solver Session。

## Runtime API 概览

- `POST /api/v2/tasks`：创建任务、初始化 v2 Session，并异步调度 Runtime。
- `GET /api/v2/tasks/{task_id}/session`：读取 Session 快照。
- `GET /api/v2/tasks/{task_id}/events/stream`：按 `seq` 增量读取 SSE 事件。
- `POST /api/v2/tasks/{task_id}/control`、`/hints`：控制 Session 或追加上下文。
- `GET /api/v2/tasks/{task_id}/report`：生成并下载 Markdown 报告。

前端不直接执行工具；页面展示服务端 Session、transcript 投影和工具事件。

## 统一 CLI

CLI、Web 与 API 使用同一个 Agent Session 服务和顺序事件协议：

```powershell
tga create examples\web_ctf\task.json
tga start task_web_ctf_demo
tga status task_web_ctf_demo
tga observe task_web_ctf_demo --follow
tga cancel task_web_ctf_demo
tga resume task_web_ctf_demo
```

`tga run <task.json>` 是“创建 + 运行 + 生成报告”的快捷方式。迁移细节、持久目录与旧调度器删除条件见 `docs/V2_MIGRATION.md`；目标架构与验收见 `docs/REFACTOR_PLAN.md`。

Runtime 控制台围绕 Solver、消息、工具调用、Artifact 和结果展示；架构映射见 `docs/REFACTOR_PLAN.md`。

## 本地评测

```powershell
python evals\run_eval.py
```

评测覆盖 Agent 工具循环、HTTP/POST、workspace、恢复、事件顺序与最终结果。
