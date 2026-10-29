# TGA3 CLI 黑板运行时

两个 Worker 在独立 Kali 容器中并行运行 Codex CLI 和 Claude Code CLI。
出现最终候选后，控制层等待收尾宽限期、停止并回收 Worker，固定黑板快照，
再用与 Codex Worker 相同的镜像启动一次性 Reporter 容器。
Supervisor 暂时不参与任务，原有定义保留供后续改造。

```text
Web UI → FastAPI 控制层 → PostgreSQL / 共享黑板 / MCP
                    ├─ Kali + Codex CLI Worker ──────┐
                    └─ Kali + Claude Code Worker ────┤ 并行
                                                    ↓ 停止两者
                                    固定快照 + 只读证据 / Skills
                                                    ↓
                                       Codex CLI Reporter
                                                    ↓
                                         保存报告、回收容器
```

CLI 管理私有会话、工具执行和上下文压缩；不再使用 Agents SDK Worker、Claude Agent SDK
或项目自己的 SQLiteSession。控制层只通过薄适配器管理进程和统一事件，不复刻 CLI 的对话记录。

- Codex 会话：每个 Worker 工作目录的 `.codex/`。
- Claude 会话：每个 Worker 工作目录的 `.claude/`。
- `.tga3/*-session.json` 只保存 CLI 会话指针；PG `agent_runs.metadata.cli_session_id` 保存对应标识。
- `agent_runs.session_id` 仍是控制通道标识，不用于 CLI resume。
- PostgreSQL 保存任务、共享黑板、对话事件、用户问答和证据/报告索引。
- `/inputs` 只读、Worker `/artifacts` 可写、Reporter `/artifacts` 与快照只读。
- Worker 通过 HTTP MCP 访问黑板、注册证据和读取 Skills。发布协议维持 Intel / Finding / final_candidate。
- CLI 周期在安全点消费合并后的黑板更新；暂停、停止和超时会回收 CLI 的工具进程组。

详细构建、认证及旧配置迁移说明见 [CLI 迁移说明](CLI-MIGRATION.md)。

## 配置唯一数据源

- `config/models.json`：Provider、各协议端点的独立地址与模型启用选择，以及供应商共享的密钥。
- `config/agents.json`：四个 Agent 的身份、CLI 后端、模型与实际调用协议绑定、轮次和完整系统提示词。
- `config/scenes.json`：八种任务场景及进入黑板的场景提示词。
- `config/runtime.json`：Docker 资源、镜像、目录、通信地址、运行节奏、MCP 指令和 Worker 周期提示。
- `config/skills/<name>/`：不分角色与场景的通用 Skill 包；Agent 先列包名，选中后只读 `SKILL.md`，再依据其中的指引按需读取包内其他 Markdown。

生产代码不从环境变量覆盖 Provider、模型、密钥或 Agent 提示词。环境变量只用于把上述已解析配置传给动态执行容器。前端各配置页面通过 `/api/v3/config` 读写自己的 JSON；Skills 页面直接管理 `config/skills`。Models 页面分别配置协议端点，模型列表可折叠、搜索、开启或关闭。Solver 页面只显示当前 CLI 所需端点的已启用模型，协议由执行后端确定。保存时后端先执行完整的跨文件校验，校验失败不会替换现有配置。

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
