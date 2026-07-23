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

MCP 使用显式 `config/mcp.json` allowlist。TGA 不扫描本机镜像，也不依赖
`mcp-security-hub` 源码目录；已构建的 Docker 镜像可直接写入配置并通过
标准 `initialize` / `tools/list` 动态发现能力。

“能力与 MCP”页面是 MCP 的唯一管理入口，通过四步向导管理 Docker STDIO 与
MCP Streamable HTTP 服务。STDIO 可拖入 `docker save` 镜像归档或选择已有本地
镜像；源码压缩包和 Dockerfile 不会被执行。新建 Session 页面不选择、授权或
测试 MCP。上传方不能传入任意 Docker 参数、挂载或环境变量。

## 核心运行方式

- 新建 Session 的任务信息只来自任务文件、可选 Hint 文本和 Hint 附件；URL、仓库地址、账号或题面文字应写入 Hint 或文件。
- 文件先流式上传为临时 asset，创建成功后归档到 `runs/<session>/workspace/inputs/task` 或 `inputs/hints`。后端生成存储名、检测 MIME、限制数量/大小并记录 SHA-256。
- 网络、文件系统、进程、速率、并发、状态变更与处置边界仍由 `executionPolicy` 独立控制，Hint 附件不会扩大授权。
- 全局已配置、启用且已发现或可达的 MCP 自动进入新 Session 的能力快照，不存在任务级 MCP 复选框或 ACL。新增 MCP 只对之后创建的 Session 可见；全局禁用会立即阻止已有 Session 的后续调用。
- Hint 会先转成带来源的候选 StrategyCard；工具动作需关联策略步骤、预期证据和风险信息。
- Solver 使用原生 function calling 连续调用 HTTP、workspace 与 MCP 工具。
- assistant `tool_calls` 与匹配的 tool result 保存在同一持久 transcript 中，支持恢复。
- Runtime 通过顺序事件区分模型计划、真实工具执行、Manager 拒绝、Observer 建议和最终确认。

## 项目结构

- `tga/runtime/agent_session.py`：产品使用的持久 Agent 工具循环。
- `tga/runtime/manager.py`：Session 生命周期控制与兼容入口。
- `tga/capabilities/`：HTTP、workspace 与 MCP 工具适配。
- `apps/api/`：FastAPI v2 Runtime API。
- `apps/web/`：React Runtime 控制台。
- `config/mcp.json`、`tga/tools/mcp_*`：显式 MCP allowlist、动态发现、策略与 transport。
- `runs/`：本地任务状态、Solver transcript、Session workspace、artifact 与报告（运行时生成）。

Schema-v4 Session 使用一套持久 workspace：

```text
runs/<session-id>/workspace/
  inputs/task/       # 不可变任务文件
  inputs/hints/      # 不可变 Hint 附件
  artifacts/         # 派生结果
  evidence/
  tool-results/
  state/             # 输入清单与弃用字段审计
```

Agent 和本地 Docker MCP 看到相同的 `/workspace/...` 路径。远程 HTTP MCP 不会
被标记为已挂载本地目录，只有协议显式传输内容时才能接收文件。支持视觉的模型
在首轮收到真实 `image_url` content block；文本模型收到可审计路径和图像分析指引。

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

## MCP 工具配置

复制 `config/mcp.example.json`，仅声明允许 TGA 启动的本地进程或 Docker
镜像，然后设置配置路径：

```powershell
$env:TGA_MCP_CONFIG_PATH = 'C:\path\to\mcp.json'
python scripts\tga_mcp_catalog.py --config $env:TGA_MCP_CONFIG_PATH
python scripts\tga_mcp_healthcheck.py --config $env:TGA_MCP_CONFIG_PATH
python scripts\tga_mcp_smoke.py --config $env:TGA_MCP_CONFIG_PATH --server nmap --tool quick_scan --arguments '{"target":"127.0.0.1"}'
```

发现到的每个 method 会作为 `mcp__<server>__<method>` 原生 function tool
进入 AgentSession。宿主负责 schema、可见性、风险和资源策略；小结果保留
原始 content blocks，大结果写入 Artifact 并由 `artifact.inspect` 分段读取。
完整字段、安全默认值和刷新行为见 [docs/MCP_CONFIGURATION.md](docs/MCP_CONFIGURATION.md)。

## Runtime API 概览

- `POST /api/v2/tasks`：创建任务、初始化 v2 Session，并异步调度 Runtime。
- `POST /api/v2/input-uploads`、`DELETE /api/v2/input-uploads/{asset_id}`：流式暂存或删除待归属文件。
- `GET /api/v2/tasks/{task_id}/session`：读取 Session 快照。
- `GET /api/v2/tasks/{task_id}/events/stream`：按 `seq` 增量读取 SSE 事件。
- `POST /api/v2/tasks/{task_id}/control`、`/hints`：控制 Session 或追加上下文。
- `GET /api/v2/tasks/{task_id}/report`：只读生成并下载 Markdown 报告，不落盘。
- `POST /api/v2/tasks/{task_id}/report/export`：显式、可审计地导出报告文件。

前端不直接执行工具；页面展示服务端 Session、transcript 投影和工具事件。

新建请求的核心结构为：

```json
{
  "name": "Session name",
  "mode": "reverse_engineering",
  "goal": "Analyze the supplied sample",
  "modeOptions": {"mode": "reverse_engineering"},
  "input": {
    "taskFileIds": ["asset_..."],
    "hintText": "optional",
    "hintFileIds": []
  },
  "executionPolicy": {}
}
```

`targetUrls`、`references`、`mcpResources`、`mcpTools` 及 MCP grant 字段不会影响
新 Session；若旧客户端额外发送这些字段，服务端忽略并写入弃用审计。历史
Session 的旧 target/reference/MCP 数据仍可只读打开。

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
