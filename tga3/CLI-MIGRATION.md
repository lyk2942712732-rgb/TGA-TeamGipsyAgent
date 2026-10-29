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
Kali 工具基础镜像保持不变。Dockerfile 的 `CLI_VERSION` build arg 可用于经过验证的版本升级。
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

`runtime.cli_timeout_seconds` 限制一次 CLI 周期的墙钟时间。Claude 使用 `max_turns_per_cycle`，
Codex 的工具循环由 CLI 自己管理，不再模拟 SDK max_turns。暂停/取消/超时清理整个 CLI 进程组。
外层 Docker 管理权限和资源边界，CLI 不再嵌套启用系统 sandbox。

出现最终候选后，项目等待宽限期、停止并回收两个 Worker，然后固定黑板快照，启动一次性 Reporter。
Reporter 的快照、证据、输入和 Skills 只读挂载，不连接可写黑板 MCP；使用全新 Codex 会话，
输出非空 Markdown 后由控制层保存文件及哈希。成功、失败、超时或取消均回收 Reporter 容器。

## 目录与工具边界

CLI 使用控制层传入的工作目录（当前均为容器内 `/workspace`），不会自动创建容器间隔离。
宿主机为每个任务维护以下目录；两个 Worker 的相同容器内路径指向不同宿主目录：

```text
workspaces/<task-id>/worker-openai/   → Codex /workspace（读写）
workspaces/<task-id>/worker-claude/   → Claude /workspace（读写）
workspaces/<task-id>/reporter/        → Reporter /workspace（读写）
artifacts/<task-id>/worker-openai/    → Codex /artifacts（读写）
artifacts/<task-id>/worker-claude/    → Claude /artifacts（读写）
artifacts/<task-id>/                  → Reporter /artifacts（只读）
inputs/<task-id>/                    → 全部 /inputs（只读）
skills/                             → 全部 /skills（只读）
```

Worker 无法通过挂载访问对方工作目录或证据目录；双方通过公共黑板共享已验证结论。
Reporter 从 `/artifacts/<worker-id>/` 汇总证据，只向自己的工作目录写入报告。

文件读写、搜索、Shell 与 Skills 文档读取直接使用 CLI 原生能力，删除重复的 Skills MCP 工具。
MCP 只保留 `blackboard_sync`、`blackboard_publish`、`artifact_register`；证据登记按发布 Worker
定位私有目录并拒绝目录穿越。Skills 配置中心仍负责管理文档包。
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

部署时还需验证真实镜像版本、Kali 工具、数据库记录、CLI 会话恢复、Worker → Reporter 串行收尾与清理。

## Scout 前置侦察

任务按 Scout → 两个 Worker 并行 → Reporter 串行推进。Scout 复用 Codex Worker 镜像，
使用独立目录和全新 CLI 单轮执行。仅 Scout 可发布 Intel，Worker 仅发布 Finding 和最终候选。
`runtime.scout_timeout_seconds` 默认 300，允许 60–600 秒；控制层限制包含容器启动的总时间，
超时先终止并回收 Scout 再启动 Worker，已有 Intel 与证据保留。正常结束但没有 Intel 时
提示 Worker 从原始材料开始；执行错误则任务失败，不悄悄绕过错误。
Scout 的 /artifacts 独立保存在 artifacts/<task-id>/scout；Worker 将其只读挂载到 /recon。
Reporter 只读查看 Scout 与两个 Worker 的证据。只有 Intel 作为交接，不生成额外侦察简报。
