# CLI 执行层

两个 Worker 使用 `codex_cli`（OpenAI Responses）和 `claude_cli`（Anthropic Messages）。
Reporter 使用 `codex_cli`，`runtime.worker_images.reporter` 必须与 Codex Worker 指向相同镜像。
Supervisor 定义暂时保留，`runtime.supervisor_enabled=false`，不发起推理。

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
`supervisor` 可选依赖仅供预留的旧顾问实现使用，不包含在生产 `control` 安装中。

## 认证与协议

模型、服务根地址、密钥仍在 `config/models.json`，模型绑定及提示词在 `config/agents.json`。
控制层启动容器时注入已解析配置，Codex 通过 `CODEX_API_KEY`，Claude 通过 `ANTHROPIC_API_KEY`
进行非交互认证。镜像、Git 和日志不得包含真实密钥。

Codex 仅支持 Responses：仅有 Chat Completions 的模型供应商不能直接绑定 Codex Worker / Reporter。
Claude 需要支持工具调用的 Anthropic Messages 端点。自定义供应商可以分别添加，根地址可包含路径前缀；
例如 Responses 地址 `/compatible-mode/v1` 对应根地址 `/compatible-mode`，Anthropic 地址
`/apps/anthropic` 原样作为该独立供应商根地址。

迁移已有部署时先备份配置，保留原有供应商、密钥、Skills 和数据；将三个执行角色 runtime 分别改为
`codex_cli`、`claude_cli`、`codex_cli`，配置正确的协议及供应商，再增加 Reporter 镜像映射。
Supervisor 配置不需要重写，但保持停用。旧 SDK 的私有会话不能直接导入 CLI；已有公共黑板和证据保留。

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
