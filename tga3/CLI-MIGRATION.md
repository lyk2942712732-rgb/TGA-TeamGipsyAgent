> 当前版本已改为 Worker 写 Finding/最终候选文件、系统导入黑板；旧版写入 MCP 已移除。以 [FILE-PUBLICATIONS.md](FILE-PUBLICATIONS.md) 为当前规范，下文保留早期迁移记录。

# CLI 执行层

两个 Worker 使用 `codex_cli`（OpenAI Responses）和 `claude_cli`（Anthropic Messages）。
Reporter 使用 `codex_cli`，`runtime.worker_images.reporter` 必须与 Codex Worker 指向相同镜像。
运行角色固定为这三个执行智能体，不再加载 Supervisor。

## 构建与启动

```bash
cd tga3
bash scripts/build-images.sh
python3 -m venv .venv
.venv/bin/pip install '.[control]'
docker compose up -d --wait postgres
.venv/bin/tga3 --config-dir config serve
```

构建安装固定版本的官方 npm CLI 包，不复制宿主账号登录信息。
基础系统和工具已切换到 Debian Bookworm / StrikeAgent 评测基线，详见 [工具基线](docker/TOOL-BASELINE.md)。Dockerfile 的 `CLI_VERSION` build arg 可用于经过验证的版本升级。
主服务与 Worker 不再依赖 `openai-agents`、`claude-agent-sdk` 或项目维护的 SQLiteSession。

## 认证与协议

模型、服务根地址、密钥仍在 `config/models.json`，模型绑定及提示词在 `config/agents.json`。
控制层启动容器时注入已解析配置，Codex 通过 `CODEX_API_KEY`，Claude 通过 `ANTHROPIC_API_KEY`
进行非交互认证。镜像、Git 和日志不得包含真实密钥。

Codex 使用 Responses：仅有 Chat Completions 端点的模型供应商不能直接绑定 Codex Worker / Reporter。
Claude 需要支持工具调用的 Anthropic Messages 端点。同一供应商在 `endpoints` 下分别配置协议端点，
共享密钥，每个端点独立维护 `base_url` 和 `models`。至少配置一个端点，地址保留完整网关前缀及版本，
例如 Responses 填写 `https://gateway/compatible-mode/v1`，Anthropic 填写
`https://gateway/apps/anthropic`。新配置不再根据供应商品牌自动改写地址。

旧版 `protocols/base_url/models` 配置加载时自动转换成端点结构，保留原来的实际调用地址及模型绑定；
保存 Models 配置后写入新结构。每个端点模型有独立的 `enabled` 开关。首次发现的模型默认关闭，
刷新保留已有开关；消失的模型保留记录并标记不可访问、关闭，不自动替换 Solver 绑定。
Solver、新任务和运行中切换模型的选项只包含对应协议端点中已启用且可访问的模型，
界面显示“供应商 / 模型”，内部仍保留协议用于路由。模型发现只证明列表可读取，不代表 CLI 推理兼容性已验证。
模型列表将已启用项置顶。Anthropic 模型列表接口返回 404、405 或 501 时，复用同一供应商已读取的
OpenAI 端点模型列表；尚未读取时，先从配置的 OpenAI 端点获取。仅复用模型名称，
Anthropic 的实际推理地址及各端点的启用开关仍独立保存。认证失败或限流等错误正常报告。

迁移已有部署时先备份配置，保留原有供应商、密钥、Skills 和数据；将三个执行角色 runtime 分别改为
`codex_cli`、`claude_cli`、`codex_cli`，配置正确的协议及供应商，再增加 Reporter 镜像映射。
旧任务已清理，不支持旧 Supervisor 或问答记录；配置只接受三个执行角色。

## 会话与运行

每个任务/Worker 有独立持久化 `/workspace`。Codex 的 `CODEX_HOME=/workspace/.codex`，
Claude 的 `CLAUDE_CONFIG_DIR=/workspace/.claude`；`.tga3/` 仅存 CLI 会话指针及无密钥的 MCP 配置。
恢复相同工作目录时按 CLI session ID 续接。PG `agent_runs.metadata.cli_session_id` 记录 CLI 标识，
与 `agent_runs.session_id` 控制通道标识分开，现有 metadata 列不需数据库迁移。

`runtime.cli_timeout_seconds` 限制一次 CLI 周期的墙钟时间。两个 Worker 均不设置固定轮数，
CLI 自然结束一段工作后立即恢复同一会话继续，无 90 秒等待。暂停/取消/超时清理整个 CLI 进程组。
外层 Docker 管理权限和资源边界，CLI 不再嵌套启用系统 sandbox。

出现最终候选后，项目等待宽限期、停止并回收两个 Worker，然后固定黑板快照，启动一次性 Reporter。
Reporter 的快照、证据、输入和 Skills 只读挂载，不连接可写黑板 MCP；使用全新 Codex 会话，
输出非空 Markdown 后由控制层保存文件及哈希。成功、失败、超时或取消均回收 Reporter 容器。

## 目录与工具边界

CLI 使用控制层传入的工作目录（当前为容器内 `/workspace`），不会自动创建容器间隔离。
宿主机为每个任务维护以下目录；两个 Worker 的相同容器内路径指向不同宿主目录：

```text
tasks/<task-id>/workspaces/worker-openai/   → Codex /workspace（读写）
tasks/<task-id>/workspaces/worker-claude/   → Claude /workspace（读写）
tasks/<task-id>/workspaces/reporter/        → Reporter /workspace（读写）
tasks/<task-id>/logs/worker-openai/    → Codex /logs（读写）
tasks/<task-id>/logs/worker-claude/    → Claude /logs（读写）
tasks/<task-id>/logs/            → Reporter /evidence（只读）
inputs/<task-id>/                    → 全部 /inputs（只读）
skills/                             → 全部 /skills（只读）
```

Worker 无法通过挂载访问对方工作目录或证据目录；双方通过公共黑板共享已验证结论。
Reporter 从 `/evidence/<agent-id>/` 读取执行记录、从 `/evidence-files/<agent-id>/` 读取生成文件；报告写入 `/workspace/result/writeup.md`。

文件读写、搜索、Shell 与 Skills 文档读取直接使用 CLI 原生能力，删除重复的 Skills MCP 工具。
Worker MCP 仅提供 `finding_publish` 和提交最终候选的 `blackboard_publish`。Worker 用 action_id、claim、scope 即时发布 Finding，服务端自动关联结果和附件、校验路径。删除未使用的 `blackboard_sync` 和旧 Finding 发布分支；原生 Hook 提供黑板同步。Finding 不保存或返回 topic，界面按 claim 前 200 字派生。Skills 配置中心仍负责管理文档包。
执行页面仅保留状态、活动和控制，不提供 Worker/Reporter 聊天或问答转发。
PostgreSQL 使用 task_events 和 task_runs.event_seq 保存只读执行事件，不提供对话写入接口。

## 验证

```bash
cd tga3
python -m pip install -e '.[dev,control]'
pytest
ruff check src tests
cd ../apps/web
npm ci
npm test
npm run build
```

部署时还需验证真实镜像版本、基线工具、数据库记录、CLI 会话恢复、Worker → Reporter 串行收尾与清理。

## Scout 前置侦察

任务按 Scout → 两个 Worker 并行 → Reporter 串行推进。Scout 复用 Codex Worker 镜像，
使用私有目录及一个 CLI 会话，在同一个容器中完成三轮工作内容，不重建会话或容器。
第一轮执行侦察，`runtime.scout_timeout_seconds` 默认 300，允许 60–600 秒。
第一轮完成、超时或执行出错后，从保存的 action 和证据继续第二轮提取。
第二轮无总时间预算，使用 `scout_write_intel` 将全部 action 覆盖到 `/intels/raw/scout-intels.json`，不登记原始证据。
第三轮无总时间预算，使用 `scout_merge_intel` 基于原始 JSON 统一合并到 `/intels/merged/scout-intels.json`；
原始文件完整保留，动作和证据路径由服务端自动求并集。两种 Intel ID 由服务端生成并带不同前缀，merged_from_ids 仅引用原始 ID。文件无 artifact_refs，最终导入时才登记证据。
第二、三轮的工具调用不写入 action 日志，避免递归提取。全部原始 Intel 各归组一次后，
系统只将合并版本导入黑板，再回收 Scout 容器并启动两个 Worker。
取消、提取失败、合并失败或来源覆盖不完整时保留文件，不导入不完整交接、不启动 Worker。
Scout 的 /logs/scout-actions.json 位于 tasks/<task-id>/logs/scout；各智能体生成文件保存在 tasks/<task-id>/workspaces/<agent-id>。已移除 /recon。
Reporter 只读查看 Scout 与两个 Worker 的证据。只有 Intel 作为交接，不生成额外侦察简报。

## Worker 连续执行与黑板同步

Solver 为每个 Worker 单独保存 blackboard_interval_seconds（默认 60 秒）。
Codex/Claude 的 UserPromptSubmit 与 PreToolUse 原生 Hook 检查服务端游标：首次完整交接，
之后只有黑板有更新且间隔已满时才取未读增量；不是闹钟中断，也不采用长工具返回后的独立触发。
提交 final_candidate 前忽略时间间隔检查更新，有新条目时先阻止本次提交并注入内容，
模型消费后重新提交。无更新不查询内容，不产生额外模型黑板工具调用。
增量交接确认后持久化各 Worker 游标，重启/恢复时重发未确认批次；间隔从实际交接时间起计算。
运行中的 Worker 同样使用 Solver 已保存的新间隔。

所有执行角色遇到失败、长期无进展、重复相同工具结果或重复相同回复时，
无进展阈值默认 300 秒，短预算角色取单次执行预算的三分之一作为较短阈值。
清理当前 CLI 进程后使用其原 session ID 恢复，保留 CLI 上下文与文件，并给出换路提示。
尚未建立会话时无法 resume；该错误正常报告。恢复采用 2–30 秒退避，不删除记忆。
Scout 总预算和 Reporter 外层超时仍有效，用户暂停/取消不触发自动恢复。
