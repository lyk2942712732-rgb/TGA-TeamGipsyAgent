# TGA LLM 配置

TGA 使用 OpenAI-compatible 模型接口驱动持久化 Agent Session。普通 API 任务在
模型未配置时不会退回旧的规则 planner。

## 环境变量

```bash
TGA_LLM_BASE_URL=https://你的模型网关/v1
TGA_LLM_API_KEY=你的密钥
TGA_LLM_MODEL=你的模型名
TGA_LLM_SUPPORTS_VISION=true  # text-only 模型设为 false；留空表示由 provider 决定
```

也可以在 Web 设置页保存等价配置。运行连通性检查：

```bash
python scripts/tga_llm_healthcheck.py
```

`LLM_NOT_CONFIGURED` 表示当前没有可执行模型，需要先填写配置再启动或恢复任务。

## Agent Session 行为

- 模型直接收到当前可用工具的 function schema。
- assistant 的 `tool_calls` 与对应 tool result 保存在同一会话记录中。
- 每次工具结果会回到模型上下文，模型可继续调用下一项工具。
- `finish_session` 用于明确结束；CTF flag 仍需通过任务格式、占位符、Artifact 内容和任务归属门禁。
- 产品入口可从 `target` 派生精确 scope，但不会扩大为通配范围，也不会隐式开启主动探测或 TLS 例外。
- Hint 先进入 StrategyCard；Agent 动作由 Manager 绑定策略步骤、预期结果、重试理由和副作用分析。

旧任务记录仍可兼容读取；原有明确 scope 和安全字段继续参与 Agent Session 的执行决策。
