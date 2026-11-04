# TGA3 CLI 黑板运行时

两个 Worker 在独立 Kali 容器中并行运行 Codex CLI 和 Claude Code CLI。
出现最终候选后，控制层等待收尾宽限期、停止并回收 Worker，固定黑板快照，
再用与 Codex Worker 相同的镜像启动一次性 Reporter 容器。
Scout 分三轮工作内容，复用同一个容器、CLI 会话和上下文：第一轮执行并留存 logs；第二轮覆盖全部 action，保存原始 Intel JSON；第三轮基于全部原始 Intel 统一合并，另存最终 JSON。控制层只导入合并版本，完整覆盖后才启动两个 Worker。

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
- Scout 第一轮默认 300 秒，可在 Solver 配置中设置为 60–600 秒；结束或超时后在原会话继续第二轮。第二、三轮不设总时间预算，分别以覆盖全部 action、覆盖全部原始 Intel 为结束条件。旧配置 `scout_extract_timeout_seconds` 仅兼容读取，不再生效。CLI 无进展恢复仍复用原会话，Token 统计归于 Scout。
- CLI 在自己的 `/workspace/` 执行，生成文件和附件留在工作区。每个智能体只维护一份 `/logs/<agent-id>-actions.json`，其中 `actions` 数组按动作记录命令、参数、状态、时间与 `artifact`。`action_id` 带智能体前缀且全局唯一，CLI 可重复的原生编号另存为 `tool_call_id`。开始、结束更新同一条记录；重新建立 CLI 调用后仍汇总到原文件。文件锁与原子替换避免并发写入丢记录或暴露半份 JSON。
- 每条 action 的 `artifact` 包含 `output_path` 和 `attachments`，路径均相对于该智能体的 `/workspace/`。工具返回正文保存在 `result/<agent-id>-action-<唯一编号>.txt` 或 `.json`；正文为空仍保存空文件，中断但未收到返回时保存明确的状态说明。附件是执行期间新增或修改的工作区文件，无附件时为 `[]`。CLI 自身截断的正文无法恢复；命令重定向生成的文件通过附件路径读取。日志不再重复保存 `result` 正文，也不使用 `result_path` 或 `output_paths`。
- `result/` 与 CLI 的隐藏会话目录是运行时保留目录，不计入附件。Scout 第二、三轮暂停记录工具调用，在原会话逐条概括汇总文件中的 action，通过 `source_action_ids` 标注来源；系统按动作 ID 核对覆盖率，遗漏时继续提取。第二轮调用 `scout_write_intel` 保存 `/intels/raw/scout-intels.json`；第三轮调用 `scout_merge_intel` 保存 `/intels/merged/scout-intels.json`，原始 JSON 保留不改写。合并请求只填 `merged_from_ids`（单条原样复制），多条组再由 AI 提供正文和主题，服务端自动求动作 ID、证据路径及引用的并集，拒绝未知来源和重复归组。每个原始 ID 必须且只能归入一条最终 Intel；无法合并的内容单独保留。合并保留条件、独立事实、失败边界和后续修正，不能新增推论。
- 任务顶部显示累计 Token，点击向右展开 Scout、两个 Worker 和 Reporter 各自用量。控制层增量读取各任务的 Codex/Claude 原生 JSONL，用量及去重检查点持久化在 Agent metadata；重新打开历史任务会回填现存日志。总数为输入加输出（包含缓存输入，缓存命中不重复相加），没有原生用量记录时显示“未上报”，不估算。
- 共享黑板支持实时用户提示；Scout 和两个 Worker 的原生钩子在下一个工具调用前注入这些提示，绕过普通同步间隔。运行中的工具会先完成；收尾和终态任务不再接收新提示。 任务顶部、运行图和 Agent 面板显示 Scout 当前处于“第一轮·侦察执行中”、“第二轮·Intel 提取中”或“第三轮·Intel 合并中”。
- PostgreSQL 保存任务、共享黑板、只读执行事件和证据/报告索引；Intel 与 Finding 的 `source_action_ids` 持久化在黑板条目。既有数据库连接时自动添加向后兼容的来源字段。
- `/inputs` 只读；`/workspace` 私有；`/logs/<agent-id>-actions.json` 是各智能体自己的动作汇总。Reporter 通过只读 `/evidence/<agent-id>` 和 `/evidence-files/<agent-id>` 查看记录和产物，自身记录仍写入自己的 `/logs`。已移除 `/recon`。
- Worker 在单次执行确认可复用事实后立即用 `finding_publish(action_id, claim, scope)` 发布，可选 `preconditions` 补充命令外的前置条件。执行后原生 Hook 在同一次工具返回中提供 action ID，不要求模型审阅 logs。服务端从绑定连接获取身份和任务，自动关联该 action 的方法、状态、结果正文及附件，登记证据并生成重试去重键。Finding 的 detail 包含适用条件和复现方法，其他 Worker 通过黑板直接使用。失败提交保存在私有 `.tga3/finding-outbox/`，相同参数重试返回同一条 Finding。`blackboard_publish` 只用于最终候选；删除未使用的 `blackboard_sync` 工具与旧 Finding 发布分支，黑板由原生 Hook 注入。Finding 不保存或返回 `topic`，界面需要主题时使用 `claim` 前 200 字。相关实际提示词见 [Finding 提示词](FINDING-PROMPTS.md)。Scout 的 `scout_write_intel` 只需 action_id 和内容，服务端绑定任务并关联 source_action_ids 与正文、附件路径，原始 Intel 只校验路径，不登记证据、不保存 `artifact_refs`；合并阶段自动求来源与路径的并集，最终导入黑板时才登记证据并写入关联。取消或提取、合并失败时保留文件，不导入不完整的交接、不启动 Worker。Skills 使用 CLI 原生工具读取。
- CLI 周期在安全点消费合并后的黑板更新；暂停、停止和超时会回收 CLI 的工具进程组。

任务文件统一按 `runtime.task_root`（默认 `data/tasks`）组织：

```text
data/tasks/<任务ID>/
├── inputs/
├── workspaces/<agent-id>/       附件、脚本、生成文件及 CLI 隐藏会话目录
│   └── result/                 每条 action 的正文文件
├── logs/<agent-id>/
│   └── <agent-id>-actions.json  该智能体全部 action 的汇总
├── intels/scout/
│   ├── raw/scout-intels.json    第二轮全部原始 Intel（保留）
│   └── merged/scout-intels.json 第三轮全部合并 Intel（仅这些导入黑板）
└── writeups/                   最终报告
```

旧的五个 `*_root` 配置仅供离线迁移读取，新运行统一使用 `task_root`。
先停止服务并确认没有进行中的任务，再运行：

```sh
python -m tga3.task_storage --config-dir /path/to/tga3/config --journal /backup/task-storage-migration.json
```

迁移同时支持旧的类型优先目录和任务内 `artifacts/` 到 `logs/` 的升级，以重命名保留全部历史内容，同时事务更新 PostgreSQL 中附件、证据、报告的路径。
既有目录发生冲突时拒绝覆盖；失败会回滚本轮移动，恢复日志保存原始路径。
历史文件内容及原生会话不改写，历史 logs 中已有的原始证据仍保留，避免破坏旧引用。

详细构建、认证及旧配置迁移说明见 [CLI 迁移说明](CLI-MIGRATION.md)。

## 配置唯一数据源

- `config/models.json`：Provider、各协议端点的独立地址与模型启用选择，以及供应商共享的密钥。
- `config/agents.json`：四个 Agent 的身份、CLI 后端、模型与实际调用协议绑定、轮次和完整系统提示词。
- `config/scenes.json`：Web渗透测试、Pwn、Re逆向、Misc安全杂项、Crypto密码学问题、AI安全六种任务场景及进入黑板的场景提示词。历史任务保留原场景记录。
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
- `POST /api/v3/tasks`：multipart 创建任务；字段为 `title`、`scene_id`、`prompt` 和零到多个 `files`，写完初始黑板后启动 Scout 三轮流程。
- `POST /api/v3/tasks/{id}/files`：追加共享只读输入文件。
- `GET /api/v3/tasks/{id}/blackboard`：黑板增量。
- `POST /api/v3/tasks/{id}/blackboard/prompts`：写入实时提示，JSON 为 `{text, idempotency_key}`；仅在侦察/运行阶段接收。
- `PUT /api/v3/config/scout-settings`：只更新 Scout 第一轮时长，JSON 为 `{timeout_seconds}`；在 Solver 页面保存，新任务生效。
- `GET /api/v3/tasks/{id}/events` 与 `/events/stream`：只读执行事件和 SSE。
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


Intel 使用两份 `schema_version: 4` 汇总 JSON。外层为 `task_id`、`actor`、`stage` 和 `intels` 数组。
每条包含 `id`、`topic`、`body`、`source_action_ids`、`evidence_paths`；仅合并条目增加 `merged_from_ids`。
ID 由服务端对规范化内容生成，分别使用 `scout-raw-intel-`、`scout-merged-intel-` 前缀，模型无需起名。
相同参数重试返回同一 ID，引用顺序和重复项不影响 ID。每次追加采用任务级锁与原子替换，
不会重写原始 Intel 为合并 Intel。历史 v1/v2/v3 单文件记录可读取，部署时备份后转换为汇总文件。
Intel 文件不保存 `artifact_refs`，仅在最终黑板导入时登记合并结果的证据；重复导入复用已保存条目。


确定性信息补齐：Scout 第二轮由运行时按未覆盖 action 提供原文材料；调用
`scout_write_intel(action_id, claim, detail?, topic?)` 时由连接绑定任务，自动关联正文与附件。
只有选择更窄的证据时才填写 `evidence_paths`。Scout 的合并工具也绑定任务。
`scout_merge_intel(merged_from_ids)` 对单条组原样复制正文；多条合并仍由 AI 提供文字。
Worker 用 `blackboard_publish(conclusion, rationale, finding_ids, answer_type?, topic?)` 提交最终候选，
不再填写任务、身份、kind 和去重键。新候选的原生 Hook 新鲜度检查保留。
后续可交给代码的索引与校验工作见 [分工审查](CODE-VS-MODEL-REVIEW.md)。
