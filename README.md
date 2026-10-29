# TGA — Team Gipsy Agent

TGA3 使用独立 Kali 容器中的 Codex CLI 和 Claude Code CLI 并行执行任务，
通过共享黑板协作。两个 Worker 停止后，再启动 Codex CLI Reporter 生成报告。
项目控制层管理任务、PostgreSQL、文件、MCP 和容器；执行角色仅包含两个 Worker 和 Reporter。

详细架构与运行方式见 [tga3/README.md](tga3/README.md)，
迁移及认证见 [tga3/CLI-MIGRATION.md](tga3/CLI-MIGRATION.md)。

## 目录

- `apps/web`：React/Vite 控制台。
- `tga3`：FastAPI 控制面、Agent 运行时、PostgreSQL schema、Worker 镜像与部署文件。
- `tga3/config/models.json`：Provider、兼容协议、自动发现的模型、Base URL 和密钥。
- `tga3/config/agents.json`：各 Agent 的身份、执行后端、模型与实际调用协议绑定、轮次和系统提示词。
- `tga3/config/scenes.json`：任务场景及场景提示词。
- `tga3/config/runtime.json`：容器、存储路径、运行节奏和周期提示。
- `tga3/config/skills/<name>/SKILL.md`：所有 Agent 按名称读取的通用 Skills。

前端各配置页面读写各自的配置文件。Models 页面填写供应商根地址和密钥后自动发现模型；Solver 页面再为每个 Agent 选择模型及其实际调用协议，不需要在 Linux 服务器上手工编辑 JSON。

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

Linux 部署的唯一入口位于仓库内的 [`部署手册`](部署手册/README.md)；通信拓扑、数据边界和 API 说明见 [`tga3/README.md`](tga3/README.md)。
