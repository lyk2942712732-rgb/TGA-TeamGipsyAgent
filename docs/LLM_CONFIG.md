# TGA 模型配置

TGA 通过通用的 OpenAI-compatible 工具调用接口驱动持久化 Agent Session。生产执行链只依赖 `ModelClient` 协议，不包含任何特定模型供应商模块或默认地址。

## 环境变量

```bash
# 密钥只通过进程环境或密钥管理服务注入，不要写入仓库。
TGA_LLM_API_KEY=...
TGA_LLM_BASE_URL=https://provider.example/v1
TGA_LLM_MODEL=provider-model-id
TGA_LLM_TIMEOUT_S=60
TGA_LLM_MAX_OUTPUT_TOKENS=512
TGA_LLM_TEMPERATURE=0.2
TGA_LLM_SUPPORTS_VISION=false
```

模型可以在 Web 的“Provider 与模型”页面完整配置，包括 API Key、Provider Base URL、模型 ID 和视觉输入能力。API Key 是只写字段：浏览器提交后会立即清空，任何 GET/POST 响应都不会回传密钥内容。Windows 上密钥使用当前用户的 DPAPI 加密后写入 `~/.tga/llm-settings.json`；其他平台将配置文件限制为当前用户可读写。可使用 `TGA_LLM_CONFIG_PATH` 指定其他本地路径。

部署环境也可以设置 `TGA_LLM_API_KEY`、`TGA_LLM_BASE_URL` 和 `TGA_LLM_MODEL`。环境变量的优先级高于浏览器配置，适合容器、服务器和集中式秘密管理。公网部署必须把模型设置接口作为管理面保护，不能向不受信任的访问者开放凭据写入权限。

运行连接与工具协议检查：

```bash
python scripts/tga_llm_healthcheck.py
```

`LLM_NOT_CONFIGURED` 表示当前没有可执行模型。系统会返回明确错误，不会回退到旧执行架构或确定性执行器。

## Agent Session 行为

- 每轮向已配置的模型发送当前可用工具的 function schema。
- assistant `tool_calls` 与对应 tool result 保存在同一 Transcript。
- 所有工具调用先经过治理、执行和持久化，再将结构化结果回传模型。
- `finish_session` 必须通过模式对应的 Completion Gate，并引用 task-owned Artifact provenance。
- 模型或 Provider 请求失败时保留可观测错误，不切换到其他执行架构。

单元和集成测试通过依赖注入使用 Fake `ModelClient`、Fake Transport 或受控 Handler。Fake 只存在于测试边界，不进入生产模型配置或执行路径。
