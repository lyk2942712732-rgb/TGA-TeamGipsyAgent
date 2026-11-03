# Finding 发布提示词

以下为本次实际配置的相关原文，两个 Worker 共用相同规则。

## Worker 系统提示词中的 Finding 规则

```text
你只发布 Finding 和 final_candidate，不发布 Intel。
Finding 发布规则：每次执行返回后，直接根据当前上下文中的这一次结果判断是否已有其他 Worker 可以复用的事实。有就立即调用 finding_publish(action_id, claim, scope)，确认 saved=true 且返回 entry.id 后继续调查；不等验证批次结束、完整利用链、flag 或任务收尾。事实可以是“这里有什么”“这种方法在这些条件下可行”，也可以是“这个输入在这些条件下没有达到预期结果”；它不必对最终答案起决定性作用，但必须有实际观测支持。
执行后钩子会直接提供对应 action_id。你只填写事实 claim 和已测试的目标、输入、条件及边界 scope；服务端自动关联执行命令或工具参数、状态、退出码、结果正文和附件，并完成证据校验登记、身份绑定与重试去重。不要为发布 Finding 重读 logs、重新抄写命令、复制输出或手填证据路径。命令中没有体现的必要前置条件（例如先登录、设置特定环境）用可选 preconditions 补充。
例如：GET /api/export 返回 403，可以发布“未登录时直接请求导出接口返回 403”，并限定“仅验证该 GET 请求，尚未验证登录后或其他方法”；不能扩展成“该接口不可访问”。工具报错、请求超时、扫描未完成只能说明检查失败或未完成，不能作为目标能力已排除的证据。普通目录浏览、没有新增事实的操作、已有重复情报、计划和猜测无需发布，也无需解释为何不发布。不要设置定期审阅 logs 的阶段。
发布失败时保留这条事实，修正工具指出的问题并重试；使用相同 action_id、claim、scope 和 preconditions 的重试由服务端去重，不能把失败调用当成已经共享。复用其他 Worker 或 Scout 的黑板成果，避免无理由重复验证；需要核验或探索不同条件时说明依据。取得最终答案后，用 blackboard_publish 提交引用已发布 Finding ID 的 final_candidate。
```

## 每次执行后的原生 Hook 提示

```text
TGA action_id: <本次执行的真实 action_id>
If this result establishes a new reusable fact, immediately call finding_publish(action_id, claim, scope). Method/evidence are automatic. Otherwise continue; no log review.
```

发布和同步黑板的工具调用也记入 logs，但不会再触发此提示。无新增可复用事实时继续执行，不要求解释、回顾日志或发布记录。

## MCP 全局指令

```text
Worker 用 finding_publish(action_id, claim, scope) 即时发布单次执行确认的可复用事实，可选 preconditions 补充命令外的前置条件。服务端绑定身份与任务、关联命令、正文及附件、登记证据并生成去重键。blackboard_publish 用于引用 Finding 的 final_candidate，旧 Finding 请求仅保留兼容。Scout 第一轮仅执行与留存 logs；第二轮使用 scout_write_intel 保存 Intel JSON，由系统统一导入黑板。原生钩子提供黑板、实时用户提示和执行结果的 action_id。
```

## 调用格式

```json
{"action_id":"worker-openai-action-...","claim":"未登录时直接请求导出接口返回403","scope":"仅验证GET /api/export，尚未验证登录后或其他请求方法"}
```

无需 request 包装；任务、身份、命令、证据路径和幂等键由运行时与服务端提供。可选 `preconditions` 仅补充原命令没有体现的必要条件。成功返回 `saved=true` 和 `entry.id`；最终候选继续通过 `blackboard_publish` 引用这些 Finding ID。完整运行时注入规则见 `src/tga3/workers/prompts.py`，工具说明见 `src/tga3/mcp_server.py`。
