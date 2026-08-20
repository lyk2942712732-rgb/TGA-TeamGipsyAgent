# TGA3

TGA3 是与旧 `tga2` 并列的新后端，不适配旧数据库、旧任务或现有前端。它把“如何执行”交给两个成熟 Agent SDK，把主服务缩成控制面、黑板、对话流和容器生命周期管理。

## 最终拓扑

```text
用户 / 后续前端
       │ HTTP + SSE
       ▼
┌──────────────────────── Ubuntu 主服务 ────────────────────────┐
│ TaskCoordinator：创建/暂停/恢复/停止，不做规划                 │
│ SolverDialogue：状态、动作摘要、进度、Supervisor 提问           │
│ Supervisor：只读黑板/按名读 Skill，只写建议                    │
│ Reporter：final_candidate 后固定快照，生成 Markdown             │
│ Blackboard MCP：sync / publish / artifact_register / skills     │
│ DockerContainerRuntime：每任务动态创建与回收两个 Worker          │
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

没有 Observer、LangChain 或 LangGraph。Worker 不互相等待，也不直接对话；它们收到 `blackboard.changed(latest_seq)` 后自行增量读取。默认每个 SDK 工作周期最多 3 个模型轮次，并在黑板变化、追加提示或 90 秒兜底计时到达时进入下一周期。

## 数据边界

黑板只有六种公开条目：`user_prompt`、`user_file`、`supervisor_advice`、`finding`、`qa`、`final_candidate`。不存在 `worker_summary`、`worker_finding`、`worker_blocker` 或 `worker_question`。

- Worker 先把证据文件写到 `/artifacts`，再调用 `artifact_register`。
- `finding` 写入事务会验证 Artifact 存在、属于同一任务且仍可用。
- 黑板中的 Finding 只保留精简结论；对应关系在 `finding_artifact_links` 隐藏保存。
- Worker 的提问是运行事件。只有 Supervisor 在对话流里向用户提问并标注来源；回答后，完整问题和答案以一个 `qa` 条目进入黑板。
- `final_candidate` 必须引用已存在的 Finding。等待宽限期后 Reporter 固定 `snapshot_seq`，后续内容不会悄悄混入报告。

PostgreSQL 用每任务 advisory transaction lock 分配黑板和对话序号，因此两个 Worker 可以并发写入，不依赖“先读后写”的应用层竞争判断。所有写入带幂等键。

## 配置

- `config/models.json`：Provider、模型和密钥放在一起。
- `config/agents-models.json`：角色到 Provider/模型的默认绑定。
- `config/runtime.json`：Docker 资源、目录、通信地址和节奏。
- `config/skills/<name>/SKILL.md`：不分角色和场景，Agent 先列名字，再按需读取。

运行前填写 `models.json` 中的密钥，并在 Ubuntu 上限制文件权限：

```bash
chmod 600 config/models.json
```

## Ubuntu 部署

Windows 仓库只负责开发与提交；镜像应在 Ubuntu 测试机拉取代码后构建：

```bash
cd tga3
chmod +x scripts/*.sh
./scripts/ubuntu-bootstrap.sh
```

脚本只通过 Compose 启动长期 PostgreSQL，并构建共同 Kali 基础镜像及两个 SDK 增量镜像。Worker 不在 Compose 中；任务创建时由 Docker SDK 动态启动，任务停止时回收。

共同基础镜像使用官方 `kalilinux/kali-rolling` 和无桌面的 `kali-linux-headless` meta package。它仍然是较大的安全工具镜像，但不再复制两份 26GB 桌面镜像：Docker layer 会被两个 Worker 共享。若以后确认工具利用率很低，再把 meta package 换成精确包列表即可。

首次数据库由官方 PostgreSQL 容器自动执行 `schema.sql`。本项目没有迁移；修改 schema 后直接删除测试数据卷再重建：

```bash
docker compose down -v
docker compose up -d --wait postgres
```

主服务可直接启动：

```bash
.venv/bin/tga3 --config-dir config serve
```

也可复制 `deploy/tga3.service`，按实际仓库路径调整后交给 systemd。该 unit 的 `ExecStartPre` 会确保 PostgreSQL 已启动；用户不需要每次手工 `docker compose up`。

## 主要接口（供后续前端对接）

- `POST /tasks`：创建任务并启动两个 Worker 容器。
- `POST /tasks/{id}/files`、`POST /tasks/{id}/prompts`：多模态文件与后续提示。
- `GET /tasks/{id}/blackboard`：精简黑板增量。
- `GET /tasks/{id}/dialogue` 与 `/dialogue/stream`：历史及 SSE 对话流。
- `POST /questions/{id}/answer`：回答 Supervisor 的问题并写入 Q&A。
- `POST /tasks/{id}/agents/{agent}/pause|resume|model`：单独控制 Worker。
- `WS /internal/agents/{task}/{agent}`：Worker 主动建立的 JSON-RPC 通道。
- `/mcp`：两个 Worker 共用的黑板/Skill 执行器。

## 开发验证

```bash
python -m pip install -e ".[dev]"
pytest
ruff check src tests
```
