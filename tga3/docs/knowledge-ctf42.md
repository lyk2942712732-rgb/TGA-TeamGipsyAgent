# ctf42 在线知识库接入

上游：`https://ctf42.online/mcp`。Streamable HTTP，无需 API Key。仅四个 Worker 配置名为 `ctf42` 的原生 MCP，Scout/Reporter 不配置。

## 请求路径

```text
Codex / Claude Code 的原生 HTTP MCP 客户端
  -> 现有控制服务 /knowledge-mcp/
  -> ctf42.online/mcp
```

部署时实测 Ubuntu 主机可访问，而当前 Docker 默认 bridge 直连同一地址超时，因此由现有后端转发固定配置的上游。没有新进程、新容器、本地索引或模型调用；后端复用一个 HTTP 客户端。只转发 MCP 协议头，不转发内部任务头、模型凭证、Authorization 或 Cookie，也不接受请求参数指定任意转发地址。保留上游 JSON/SSE、状态码和 MCP 会话头。

当前上游返回有限 JSON，GET 返回 405，不保持 GET 事件流。原生客户端会并发发起发现请求；转发端使用单条复用连接排队，避免当前网络路径下反复新建连接的间歇超时。若未来上游改成长连接事件流，需要重新评估此连接数配置。外部网络故障仍可能导致 unavailable，成功握手不代表每次检索都能成功。

`runtime.ctf_knowledge_mcp_url` 配置上游，默认上述地址，空字符串禁用。容器 URL 由现有控制服务地址生成，保持 bridge 网络及目标访问设置。旧 `ctf_knowledge_root` 为兼容历史配置而接受，但不再启用挂载和 stdio 服务。旧资料目录不作破坏性删除。

## 工具与使用

| 工具 | 用途 |
|---|---|
| `list_techniques(query)` | 查技法标签 |
| `list_technologies(query)` | 查框架/库及版本标签 |
| `search_challenges(...)` | 按技法、技术栈和分类查历史题目；建议 `has_writeup=true,page_size=3` |
| `get_challenge(id)` | 读取选定历史题的详情、附件和 WP 链接 |
| `find_similar_challenges(id)` | 根据 ctf42 整数题目 ID 查相似历史题，不是当前平台题号 |
| `search_ctfs(query)` / `get_ctf(slug)` | 定位已知赛事及其届次 |

检索使用当前遇到的具体技术障碍，不发送任务凭证、内部目标地址、完整私有题面或附件。WP 多为外链，命中链接不等于已读正文；再按需用 WebFetch/curl 读取公开页面，核对前提。资料仅作参考，当前任务的 Finding 仍应基于实际执行证据。

原生初始化超时 15 秒；Codex 设置 `required=false`、单次工具超时 30 秒；Claude 使用 15 秒 MCP 初始化上限。上游连接/读取受后端超时约束，失败返回 unavailable。提示词要求连续两次知识库不可用/调用失败后停止本次尝试的重试，继续使用现有上下文、任务经验卡和本地工具；这不是代码强制的调用次数限制。

## 离线资料

本次没有安装新离线库或承诺自动切换。推荐 CTF Wiki 作为覆盖多个 CTF 方向的本地正文资料；Web 题较多时可补充 PayloadsAllTheThings。先采用本地文件/章节检索即可，不需要为兜底先部署向量数据库。引用的外链、赛题附件和姊妹仓库需要分别准备，不能把只克隆文档仓库等同于收齐全部外部资料。
