# 🔌 角色 2：平台对接与 MCP 工程师 (Platform Integration & MCP Dev)

## 🎯 核心目标
你的任务是打造 Agent 的“沟通能力”。你需要确保 Agent 能够顺利连接到比赛官方的后台平台，能够稳定地获取题目列表、动态启动靶机容器，并最终成功提交 Flag 得分。

## 📁 负责目录
`my-ctf-agent/mcp_client/`

## 🏗️ 架构设计与实现指导

### 1. MCP 客户端连接管理 (connection.py)
* **背景**：平台基于 MCP (Model Context Protocol) 协议通过 SSE (Server-Sent Events) 提供工具。
* **设计指导**：
  * 使用 `langchain-mcp-adapters` 的 `MultiServerMCPClient` 与平台建立连接。
  * **认证处理**：确保从环境变量读取 `CTF_TOKEN` 等信息，并在连接初始化时正确传递（通常在 Header 中）。
  * **连接韧性**：SSE 连接在长时间运行（如网络抖动）时可能会断开。你需要设计自动重连机制，确保 Agent 运行半小时后不会因为断网而崩溃。

### 2. 平台工具封装 (platform_tools.py)
* **痛点**：平台通过 MCP 返回的工具（如 `start_challenge`）如果报错，原样抛出给 Agent 可能会导致 Agent 不知所措。
* **设计指导**：
  * 深入了解平台提供的核心工具链，通常包括：`get_challenges`（获取题目信息）、`start_challenge`（启动靶机并获取靶机 IP/URL）、`submit_flag`（提交 Flag）。
  * 在获取到 MCP 工具后，可以考虑对这些工具进行二次包装，拦截底层 HTTP/网络错误，并转换为友好的自然语言提示模型。例如：“启动靶机超时，平台当前负载较高，请等待 1 分钟后重试。”

### 3. 生命周期管理
* **设计指导**：
  * Agent 启动时，必须确保 MCP 连接就绪。
  * 配合角色 1，在 Agent 完成任务或报错退出时，优雅地关闭（Graceful Shutdown）与 MCP Server 的连接。

## 🤝 协作指南
* 为 **角色 1** 提供稳定可靠的平台工具列表。
* 与 **角色 4** 合作，确保在本地测试时，你的客户端可以连接到一个 Mock（模拟）的 MCP 服务器，以便在没有网络的情况下开发。
