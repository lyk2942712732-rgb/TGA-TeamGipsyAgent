# TGA3

TGA3 是新的双 Worker 黑板运行时，不兼容旧数据库或旧任务。`apps/web` 直接使用 TGA3 API；两个成熟 Agent SDK 负责执行，主服务只负责控制面、共享黑板、对话流、主持建议、最终报告和容器生命周期。

## 拓扑

```text
用户 / apps/web
       │ HTTP + SSE
       ▼
┌──────────────────────── Ubuntu 主服务 ────────────────────────┐
│ TaskCoordinator：创建/暂停/恢复/停止，不做解题规划              │
│ SolverDialogue：真实状态、动作摘要、进度、Supervisor 提问        │
│ Supervisor：只读黑板/按名读 Skill，只写建议                     │
│ Reporter：final_candidate 后固定快照，生成 Markdown              │
│ Blackboard MCP：sync / publish / artifact_register / skills      │
│ DockerContainerRuntime：每任务动态创建与回收两个 Worker           │
└──────────────┬───────────────────────────┬──────────────────────┘
               │ JSON-RPC 2.0 / WebSocket │
       ┌───────▼────────┐          ┌───────▼────────┐
       │ OpenAI Worker  │          │ Claude Worker  │
       │ Agents SDK     │          │ Agent SDK      │
       │ /workspace 独立│          │ /workspace 独立│
       └───────┬────────┘          └───────┬────────┘
               └──────── Blackboard MCP ──┘
                              │
                         PostgreSQL 容器
```

没有 Observer、LangChain 或 LangGraph。Worker 不互相等待，也不直接对话；它们收到 `blackboard.changed(latest_seq)` 后自行增量读取。工作周期上限、兜底同步频率等均从配置读取。

## 数据边界

黑板公开条目为 `user_prompt`、`user_file`、`supervisor_advice`、`finding`、`qa` 和 `final_candidate`。场景提示使用 `user_prompt` 条目、`topic=scene`，在用户描述之前写入。

- Worker 先把持久证据写到 `/artifacts`，再调用 `artifact_register`。
- `finding` 写入事务验证 Artifact 存在、属于同一任务且仍可用；公开黑板只保留 Finding，关联存入 `finding_artifact_links`。
- Worker 的问题作为运行事件交给 Supervisor。只有 Supervisor 在对话流提问并标明来源；回答后问题与答案合并为一个 `qa` 条目。
- `final_candidate` 必须引用已有 Finding。宽限期后 Reporter 固定 `snapshot_seq`，之后的内容不会混入报告。

PostgreSQL 用每任务 advisory transaction lock 分配黑板和对话序号，允许两个 Worker 并发写入；所有写入均带幂等键。

## 配置唯一数据源

- `config/models.json`：Provider、模型、Base URL 和密钥。
- `config/agents.json`：四个 Agent 的身份、SDK、模型绑定、轮次和完整系统提示词。
- `config/scenes.json`：八种任务场景及进入黑板的场景提示词。
- `config/runtime.json`：Docker 资源、镜像、目录、通信地址、运行节奏、MCP 指令和 Worker 周期提示。
- `config/skills/<name>/SKILL.md`：不分角色与场景的通用 Skill；Agent 先列名称再按需读取。

生产代码不从环境变量覆盖 Provider、模型、密钥或 Agent 提示词。环境变量只用于把上述已解析配置传给动态 Worker 容器。运行前填写 `models.json` 并限制权限：

```bash
chmod 600 config/models.json config/agents.json config/scenes.json config/runtime.json
```

## Ubuntu 部署

Windows 仓库只负责开发和提交；镜像在 Ubuntu 测试机拉取代码后构建：

```bash
cd tga3
chmod +x scripts/*.sh
./scripts/ubuntu-bootstrap.sh
```

脚本通过 Compose 启动长期 PostgreSQL，构建共享 Kali 基础镜像和两个 SDK 增量镜像，安装主服务，并构建 `apps/web/dist`。Worker 不在 Compose 中常驻；创建任务时由 Docker SDK 动态启动，停止任务时回收。

共享基础镜像使用官方 `kalilinux/kali-rolling` 和无桌面的 `kali-linux-headless` meta package。两个 Worker 共享 Docker layer，不复制两份桌面镜像。

首次数据库由 PostgreSQL 容器执行 `schema.sql`。项目不提供迁移；schema 变化后删除测试数据卷并重建：

```bash
docker compose down -v
docker compose up -d --wait postgres
```

主服务可直接启动：

```bash
.venv/bin/tga3 --config-dir config serve
```

生产运行可采用 `deploy/tga3.service` 和 `deploy/nginx-tga3.conf`。Worker 容器仍由任务生命周期管理，无需手工启动。

## 前端 API

- `GET /api/v3/scenes`：返回 `scenes.json` 中的真实场景目录。
- `GET /api/v3/models`：返回无密钥、无系统提示词的 Agent 默认模型绑定与模型目录。
- `GET /api/v3/tasks`：任务列表。
- `POST /api/v3/tasks`：multipart 创建任务；字段为 `title`、`scene_id`、`prompt` 和零到多个 `files`，写完初始黑板后启动 Worker。
- `POST /api/v3/tasks/{id}/files`、`POST /api/v3/tasks/{id}/prompts`：后续多模态文件与提示。
- `GET /api/v3/tasks/{id}/blackboard`：黑板增量。
- `GET /api/v3/tasks/{id}/dialogue` 与 `/dialogue/stream`：历史和 SSE 对话流。
- `POST /api/v3/questions/{id}/answer`：回答 Supervisor 的问题并写入 Q&A。
- `POST /api/v3/tasks/{id}/agents/{agent}/pause|resume|model`：单独控制容器 Worker。
- `GET /api/v3/tasks/{id}/writeup/download`：下载最终 Markdown writeup。
- `WS /internal/agents/{task}/{agent}`：Worker 主动建立的 JSON-RPC 通道。
- `/mcp`：两个 Worker 共用的黑板、Artifact 和 Skill 执行器。

## 开发验证

```bash
python -m pip install -e ".[dev]"
pytest
ruff check src tests

cd ../apps/web
npm test
npm run build
npm run test:e2e
```
