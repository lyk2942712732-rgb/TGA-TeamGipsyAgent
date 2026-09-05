# TGA3

TGA3 是新的双 Worker 黑板运行时，不兼容旧数据库或旧任务。`apps/web` 直接使用 TGA3 API；两个成熟 Agent SDK 负责执行，主服务只负责控制面、共享黑板、对话流、主持建议、最终报告和容器生命周期。

## 拓扑

```text
用户 / apps/web
       │ HTTP + SSE
       ▼
┌──────────────────────── Linux 主服务 ─────────────────────────┐
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

黑板公开条目为 `user_prompt`、`user_file`、`supervisor_advice`、`intel`、`finding`、`qa` 和 `final_candidate`。场景提示使用 `user_prompt` 条目、`topic=scene`，在用户描述之前写入。

- Worker 先把持久证据写到 `/artifacts`，再调用 `artifact_register`。
- `intel` 与 `finding` 共用严格的 `claim/detail` 正文：Intel 表示会影响其他 Agent 下一步行动的已验证中间情报，Artifact 可选；Finding 表示关键突破或重要结果，Artifact 必填。
- 非空 Artifact 引用在写入事务中验证其存在、属于同一任务且仍可用；公开黑板不暴露关联，统一存入 `blackboard_artifact_links`。
- Worker 的问题作为运行事件交给 Supervisor。只有 Supervisor 在对话流提问并标明来源；回答后问题与答案合并为一个 `qa` 条目。
- `final_candidate` 必须引用已有 Finding。宽限期后 Reporter 固定 `snapshot_seq`，之后的内容不会混入报告。
- Worker 对黑板变更按最新序号合并，在当前工具/SDK 周期结束后的安全点增量读取；90 秒周期同步负责漏事件兜底。
- Supervisor 对 Intel 使用 2 秒静默合并和 10 秒批次上限进行审阅；由 Intel 触发的建议至少间隔 60 秒，相同或近似建议直接抑制。用户提示和 Finding 绕过时间限制，90 秒无共享进展时执行一次沉默检查；没有建议时只把审阅进度写入任务对话。

PostgreSQL 用每任务 advisory transaction lock 分配黑板和对话序号，允许两个 Worker 并发写入；所有写入均带幂等键。

共享状态与 SDK 私有会话彼此独立：PostgreSQL/黑板保存所有 Agent 可见的任务事实，OpenAI Worker 的
`/workspace/.tga3-openai-session.db` 只保存该 Worker 自己的 Agents SDK 对话记忆，不作为公共情报源。

## 配置唯一数据源

- `config/models.json`：Provider、支持的兼容协议、自动发现的模型、Base URL 和密钥。
- `config/agents.json`：四个 Agent 的身份、SDK、模型与实际调用协议绑定、轮次和完整系统提示词。
- `config/scenes.json`：八种任务场景及进入黑板的场景提示词。
- `config/runtime.json`：Docker 资源、镜像、目录、通信地址、运行节奏、MCP 指令和 Worker 周期提示。
- `config/skills/<name>/`：不分角色与场景的通用 Skill 包；Agent 先列包名，选中后只读 `SKILL.md`，再依据其中的指引按需读取包内其他 Markdown。

生产代码不从环境变量覆盖 Provider、模型、密钥或 Agent 提示词。环境变量只用于把上述已解析配置传给动态 Worker 容器。前端各配置页面通过 `/api/v3/config` 读写自己的 JSON；Skills 页面直接管理 `config/skills`。Models 页面按供应商预设补全模型发现路径，Solver 页面按 Agent SDK 选择实际推理协议；同一供应商可同时为不同 Agent 暴露多种协议。保存时后端先执行完整的跨文件校验，校验失败不会替换现有配置。

新任务立即使用保存后的 Provider、模型、Agent 提示词、场景和容器参数。运行中的任务保留 PostgreSQL 中的运行状态；监听地址和 PostgreSQL DSN 保存后需要重启主服务。`GET /api/v3/models` 仍是供普通任务界面使用的无密钥目录，只有配置中心接口返回可编辑的完整配置。

## Linux 部署

完整部署支持通用 Linux，唯一部署编排维护在仓库内的 [`部署手册/deploy.py`](../部署手册/deploy.py)。从仓库根目录执行：

```bash
cd 部署手册
chmod +x deploy.sh
./deploy.sh
```

`scripts/ubuntu-bootstrap.sh` 仅为旧命令兼容入口，会转交给上述部署手册，不再包含独立部署逻辑。镜像构建仍复用 `scripts/build-images.sh` 这一项目构建组件。

部署完成后，按 [`部署手册/生产环境部署.md`](../部署手册/生产环境部署.md) 配置运行账户、systemd 或发行版对应的服务管理器，以及 Nginx。启动控制面后，在前端“配置中心”填写供应商、模型和密钥。

Worker 不在 Compose 中常驻；创建任务时由 Docker SDK 动态启动，停止任务时回收。首次数据库由 PostgreSQL 容器执行 `schema.sql`，项目不提供自动迁移。数据库重建、生产备份、UID/GID、网络和故障排除均以部署手册为准。

## 前端 API

- `GET /api/v3/scenes`：返回 `scenes.json` 中的真实场景目录。
- `GET /api/v3/models`：返回无密钥、无系统提示词的 Agent 默认模型绑定与模型目录。
- `GET|PUT /api/v3/config`：读取、校验并原子写回完整的 models、agents、scenes 和 runtime 配置。
- `GET|POST|PUT|DELETE /api/v3/skills/{name}`：以 `config/skills/<name>/` 目录包为单位管理其中全部 Markdown；Agent 默认先读 `SKILL.md`，再按需读取包内具体文档。
- `POST /api/v3/skills/import`：上传 ZIP Skill 包；安全校验后按顶层目录名（或 ZIP 文件名）创建目录并保留其中的 Markdown 布局。
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
