# TGA - Team Gipsy Agent

TGA 是一个面向 CTF、代码审计与授权安全分析的比赛型 Agent 项目。前端保留完整的产品展示规模；后端使用 LangChain/LangGraph 执行，并把任务、证据和报告作为自己的业务真相。

## 目录边界

```text
apps/
├── api/          唯一 FastAPI 后端与 /api/v2 协议
└── web/          React/Vite 前端
tga2/
├── agent/        LangGraph 拓扑、LangChain Agent、工具和中间件
├── core/         Task、Evidence、Policy、SQLite、Workspace、Report
└── integrations/ 模型和 MCP 的官方适配器
tests/            当前架构的后端纵向测试
examples/         可用于演示的任务输入
```

`tga2` 中没有 FastAPI 路由；`apps/api` 中没有另一套 Runtime。API、CLI 和测试都从 `tga2.bootstrap.Container` 取得同一个配置、Skill、MCP 与任务服务。

## 运行

需要 Python 3.11+ 和 Node.js：

```powershell
python -m pip install -e ".[dev]"
cd apps\web
npm install
npm run build
cd ..\..
uvicorn apps.api.main:app --reload
```

打开 <http://127.0.0.1:8000>。开发前端时可另开终端运行 `npm run dev`。

未配置模型密钥时，任务走确定性的离线 Agent，但仍经过相同的 LangGraph、SQLite、Artifact、EvidenceClaim、Finding 和报告链路。配置真实模型后，Supervisor、Worker、Reviewer、Reporter 改由 LangChain `create_agent` 执行。

## 框架与自有职责

LangChain/LangGraph 接管 Agent 循环、结构化输出、重试、工具调用、HITL interrupt/resume、checkpoint，以及 Shell/Docker 执行策略。TGA 保留任务授权、工具 allowlist、风险与脱敏、Artifact hash、EvidenceClaim/Finding 约束、业务事件和报告可信性。

MCP 使用 `langchain-mcp-adapters`。前端保存 MCP、Prompt、Skill、Solver 能力后，修改的是 Runtime 实际读取的同一份配置，不存在仅供页面展示的第二套状态。

## 验证

```powershell
ruff check tga2 apps/api tests
pytest -q
cd apps\web
npm test -- --run
npm run build
```

更多内部说明见 [tga2/ARCHITECTURE.md](tga2/ARCHITECTURE.md)。
