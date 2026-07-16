# TGA LLM 配置

TGA 使用 OpenAI-compatible 模型接口驱动持久化 Agent Session。普通 API 任务在
模型未配置时不会退回旧的规则 planner。

## 环境变量

```bash
TGA_LLM_BASE_URL=https://你的模型网关/v1
TGA_LLM_API_KEY=你的密钥
TGA_LLM_MODEL=你的模型名
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
- `finish_session` 用于明确结束；发现 flag 时也可以直接完成。
- `target` 是 Session 的目标契约。产品入口不再要求独立 scope、执行强度、
  主动探测或 TLS 例外开关。

旧任务记录中的这些字段仅为兼容读取，不参与正常 Agent Session 的执行决策。
