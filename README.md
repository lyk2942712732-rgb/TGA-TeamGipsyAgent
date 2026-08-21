# TGA3 — Team Gipsy Agent

TGA3 是一个面向安全竞赛与授权安全任务的双 Worker 黑板运行时。Supervisor 只提供建议，OpenAI Agents SDK Worker 与 Claude Agent SDK Worker 在独立容器中并行执行，Reporter 在最终候选出现后生成 Markdown writeup。

```text
apps/web  ── /api/v3 + SSE ──>  tga3 control plane
                                      │
                         PostgreSQL shared blackboard
                             │                  │
                    OpenAI Worker        Claude Worker
                         container           container
```

## 目录

- `apps/web`：React/Vite 控制台。
- `tga3`：FastAPI 控制面、Agent 运行时、PostgreSQL schema、Worker 镜像与部署文件。
- `tga3/config/models.json`：Provider、兼容协议、自动发现的模型、Base URL 和密钥。
- `tga3/config/agents.json`：四个 Agent 的身份、SDK、模型与实际调用协议绑定、轮次和系统提示词。
- `tga3/config/scenes.json`：任务场景及场景提示词。
- `tga3/config/runtime.json`：容器、存储路径、运行节奏和周期提示。
- `tga3/config/skills/<name>/SKILL.md`：所有 Agent 按名称读取的通用 Skills。

前端各配置页面读写各自的配置文件。Models 页面填写供应商根地址和密钥后自动发现模型；Solver 页面再为每个 Agent 选择模型及其实际调用协议，不需要在 Ubuntu 上手工编辑 JSON。

## 验证

```bash
cd tga3
python -m pip install -e ".[dev]"
pytest
ruff check src tests

cd ../apps/web
npm ci
npm test
npm run build
```

Ubuntu 部署、通信拓扑、数据边界和 API 说明见 [`tga3/README.md`](tga3/README.md)。
