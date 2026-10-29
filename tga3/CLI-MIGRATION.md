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
