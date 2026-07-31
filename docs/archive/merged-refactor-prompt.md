# 仓库架构重构执行提示词

你现在是这个仓库的主要重构执行者。请直接完成架构重构、前端迁移、测试和验证，不要只输出设计方案。

仓库目标：彻底删除旧 Agent 执行架构，将产品统一到单一、受治理、可恢复、可观测的 ReAct 执行链路，并解决当前存在的重复执行器、生命周期多写入者、事务缺失、God Object、SSE 全量查询、Board 重复、API 与 Store 职责过重等问题。

请先完整阅读相关代码、测试、数据库结构、前端事件 reducer、运行配置和架构文档，建立真实调用关系后再实施删除和重构。不要仅凭文件名判断是否删除。工作区可能存在用户修改，必须保留并与其兼容，不得回滚、覆盖或格式化无关文件。

本次重构不保留旧产品语义、旧架构回退、Legacy Feature Flag、双架构分支或新旧模型双写逻辑。不要在完成计划后停下；除非遇到会破坏且无法恢复的用户数据、无法判断的外部协议或不可恢复的迁移风险，否则持续实施到验证完成。

# 一、最终目标架构

最终只允许存在以下产品执行主路径：

User / API
→ TaskRuntimeService
→ Manager
→ SessionCoordinator
→ AgentSessionRunner
→ ModelClient.chat_tools
→ ToolDispatcher
→ CapabilityHandler / InputHandler / MCPHandler
→ ActionResult + Artifact + AgentEvent
→ tool message 回传 LLM
→ 下一轮 ReAct
→ CompletionService
→ completed / blocked / paused / cancelled / failed
→ RuntimeReadModel / SSE / Artifact / Report
→ 前端任务执行面板
→ User

必须满足：

1. LLM 只产生公开的 assistant content、决策和 function calling，不直接执行工具。
2. 所有工具调用统一进入 ToolDispatcher。
3. 执行结果必须先持久化为 Artifact、ActionResult 和 AgentEvent，再作为 tool message 回传 LLM。
4. Session 生命周期只有 SessionCoordinator 可以修改。
5. 一个业务命令产生的状态、事件及相关记录必须在同一事务中提交。
6. 完成状态只能在 CompletionService 完成校验通过后，由 SessionCoordinator 写入。
7. SSE 使用增量事件查询，不得周期性构建完整 Snapshot。
8. 新运行时只保留 StrategyCard、StrategyStep 和 EvidenceMemory。
9. 不保留旧 Agent 执行回退、兼容开关、双架构分支或确定性 Solver fallback。
10. 不得为了兼容旧测试而保留生产 Legacy 代码；测试应改为注入 Fake ModelClient、Fake Transport 或受控 Handler。
11. 模型未配置或 Provider 请求失败时必须返回明确、可观测的错误，不得切换到旧架构。
12. 所有确认结论都必须通过 Completion Gate，并关联 task-owned Artifact provenance。

# 二、删除旧架构

彻底删除以下 Legacy 产品路径及其配置、测试依赖、兼容层和失去调用方的抽象。

## 1. 删除旧 Agent 执行路径

当前存在：

- 新路径：Manager → AgentToolSession
- 旧路径：Manager._run → Solver → Hypothesis → ActionSpec

要求：

- 删除 Manager 中的 `_run` Legacy 分支。
- 删除 Legacy Solver fallback。
- 删除通过注入旧 Solver 改变生产执行架构的机制。
- 删除旧 Planner / Solver 相关生产执行代码。
- 删除旧 Hypothesis 驱动的执行循环。
- 删除旧多角色伪 Solver、abandoned role-fanout 和相关兼容逻辑。
- 删除旧执行路径相关环境变量、Feature Flag、配置、文档、测试和死代码。
- 如果 `tga/runtime/solver.py`、旧 orchestrator 模块或其他文件不再有真实调用者，直接删除。
- Manager 在模型未配置或 Provider 不可用时必须返回明确错误，不能回退到旧执行架构。
- 测试模型行为时注入 Fake ModelClient，而不是旧 Solver。

删除前使用 `rg` 和静态调用关系确认所有引用；删除后确保仓库中不存在产品代码对 Legacy Solver 路径的引用。

## 2. 删除旧能力执行器和旧 MCP 栈

只保留一套受治理的能力分发机制：

- CapabilityDispatcher 或统一 Handler 注册器
- MCPManager
- MCPGateway
- MCPTransport
- MCPPolicy

删除：

- Legacy CapabilityExecutor
- ToolRunner
- MCPClient
- legacy `tool.invoke`
- mcp-security-hub 旧接入
- `TGA_ENABLE_LEGACY_MCP_HUB` 等旧开关
- 旧 bootstrap、注册、配置和相关测试
- 所有重复的 MCP 调用协议

所有能力必须通过统一 Handler 注册机制执行。不要错误合并 MCPGateway 与 MCPManager，也不要合并 MCPTransport 与 MCPPolicy；它们职责不同，必须保留边界。

## 3. 删除旧 Hypothesis Board

当前同时存在：

- Legacy BoardStore / Hypothesis
- Native StrategyBoard / StrategyCard

要求：

- 新运行时只保留 StrategyCard、StrategyStep、EvidenceMemory。
- 删除运行时对 Hypothesis Board 的写入和读取。
- 删除 BoardStore 的 Hypothesis 写入、hypothesis status 分组和 legacy ideas。
- 删除旧 AttackFlow 语义。
- 删除 API 中为 Native Session 主动清空 Hypothesis 的兼容代码。
- 删除前端 “Solver / Hypothesis / Board” 旧文案、旧分组和旧展示逻辑。
- 前端策略面板直接展示 StrategyCard 和 StrategyStep。
- 如果不需要兼容历史运行数据，删除只读 Adapter，不保留无实际用途的历史兼容层。
- 清理 SQLite 中不再需要的 Hypothesis 表、Repository 和 Snapshot 字段。
- 使用明确的单向 schema migration；如果无法安全迁移，则提升 schema version 并明确拒绝旧库。
- 不允许运行时继续双写新旧模型，也不得静默删除用户文件。

# 三、拆分 AgentToolSession

删除 `AgentToolSession` God Object，将职责拆分为清晰模块。可以根据仓库命名规范调整名称和目录，但职责必须等价，且不得只是机械搬迁大文件。

## AgentSessionRunner

只负责：

- ReAct 回合控制
- 构建本轮模型请求
- 调用 ModelClient
- 接收 assistant message 和 tool calls
- 调用 ToolDispatcher
- 将 tool message 追加到 Transcript
- 返回 SessionOutcome

它不得：

- 直接执行工具
- 直接修改生命周期
- 包含 MCP Transport 细节
- 直接读写 Artifact 文件格式
- 直接处理输入物化细节
- 直接读写 `messages.json`

## ToolDefinitionBuilder

负责：

- Capability 工具定义
- Input 工具定义
- MCP 工具定义
- `finish_session` 定义
- Provider 工具名映射
- 工具名称冲突检测
- 每轮 MCP Catalog 快照

## ToolDispatcher

负责：

- 解析 function name
- 解析 arguments
- 解析 `_tga` 治理元数据
- 路由到对应 Handler
- 统一处理 unknown tool 和参数错误
- 返回标准 ToolExecutionResponse

## CapabilityToolHandler

负责：

- HTTP
- Workspace read/write
- Python
- Shell
- Artifact inspect
- Policy、scope、risk、budget 和 semantic repeat 校验
- 调用统一 CapabilityDispatcher

## InputToolHandler

负责：

- input_list
- input_get
- input_read
- input_search
- input_view
- input_materialize
- 输入归属、哈希、不可变性和路径边界校验

## MCPToolHandler

负责：

- MCP Catalog status/list/search/describe
- MCP route 解析
- MCP Policy 授权
- MCP 工具调用
- Catalog snapshot
- 结果截断、图片投影和 Artifact 保存

## ActionRecorder

负责：

- 创建 ActionSpec
- 写入 Action、ActionResult 和 AgentEvent
- 更新 StrategyStep
- 关联 Artifact 引用
- 通过 UnitOfWork 事务性提交

## ArtifactService

负责：

- Artifact 保存
- 临时文件与原子 rename
- 索引
- 有界检索和有界预览
- 截断和大结果 spill
- 敏感值脱敏
- provenance

不得创建无意义的 artifact-of-artifact 链。

## ObserverCoordinator

负责：

- Observer trigger
- Observer context
- Strategy / EvidenceMemory 建议

Observer 不得执行工具、修改 Session 生命周期、确认完成或伪造已验证结论。

## CompletionService

负责：

- 解析 FinishSubmission
- 调用模式对应的 CompletionValidator
- 校验 task-owned Artifact provenance
- 返回 accepted / rejected / missing
- accepted 后请求 SessionCoordinator 完成 Session
- rejected 后生成结构化 continuation，让 ReAct 继续

## TranscriptStore

负责：

- Transcript 读取
- 原子追加和原子保存
- 进程重启恢复
- 敏感内容处理

拆分后必须满足：

- 每个模块具有清晰接口。
- 避免反向依赖和循环依赖。
- Tool Handler 不得直接更新 Session 生命周期。
- Runner 不得包含具体能力实现、MCP transport、Artifact 文件格式或输入物化细节。
- 单文件控制在可维护范围内，不要把原 God Object 原样拆成若干相互强耦合的大文件。

# 四、引入唯一生命周期写入者

增加 `SessionCoordinator`，作为唯一生命周期写入者。

它负责：

- create
- start
- pause
- resume
- cancel
- complete
- block
- fail

要求：

1. Manager、Runner、Handler、Observer、CompletionValidator 均不得直接调用 `update_session`，也不得直接改变 Solver / Challenge 生命周期。
2. 所有状态转换必须经过显式状态机校验。
3. 非法转换返回稳定错误码。
4. Session、Solver、Challenge 和对应生命周期 AgentEvent 必须在同一事务中提交。
5. `AgentSessionRunner.run()` 返回 `SessionOutcome`，不直接写入最终生命周期。
6. SessionOutcome 至少包含：
   - status
   - stop_reason
   - turn_count
   - summary
   - evidence_artifact_ids
   - error
7. Coordinator 统一关闭 HTTP Session、MCP Session 和其他运行资源。
8. pause 后不得开始下一轮；resume 必须从 Transcript 和最后事件序号继续，不得重复执行已完成 Action。

# 五、增加 UnitOfWork 和事务边界

增加 `UnitOfWork` 或等价的 `EvidenceStore.transaction()`。

要求：

1. 支持显式 transaction context。
2. Repository 方法在事务内不得自行 commit。
3. 一个工具执行命令必须原子提交：
   - Action
   - ActionResult
   - Artifact metadata
   - StrategyStep 更新
   - AgentEvent
4. 完成命令必须原子提交：
   - Completion validation result
   - Session 状态
   - Solver 状态
   - Challenge 状态
   - FINISH_ACCEPTED
   - AGENT_FINISHED
   - SESSION_STOPPED
5. 回滚后不得出现“状态已完成但事件缺失”“事件显示成功但 Artifact 未登记”或其他部分写入。
6. 增加事务回滚和故障注入测试。
7. Artifact 文件无法参与数据库事务时，采用可证明一致的安全顺序：
   - 先写临时文件
   - 在数据库事务中登记 metadata
   - 原子 rename
   - 失败时回滚并清理临时文件
8. 不允许出现数据库记录成功但 Artifact 文件缺失的最终状态。

# 六、拆分 EvidenceStore

EvidenceStore 不应同时承担数据库连接、迁移、全部 Repository 和 Snapshot Builder。

拆分为清晰组件，例如：

- Database / UnitOfWork
- TaskRepository
- SessionRepository
- ActionRepository
- ArtifactRepository
- EventRepository
- StrategyRepository
- MemoryRepository
- ContextMetricRepository
- RuntimeReadModel

要求：

- Repository 只处理各自聚合的数据访问。
- RuntimeReadModel 负责构建 API 所需投影。
- 写模型不能依赖完整 `task_snapshot`。
- 删除只有转发作用且没有附加语义的 EventStore；如果保留，则必须赋予明确的事件序列或事务职责。
- 不要为了“抽象”增加无行为的薄包装。

# 七、统一持久化真相来源与恢复路径

最终权威来源：

- SQLite：Session、Action、Strategy、Memory、Event
- Artifact 文件系统：不可变大对象
- TranscriptStore：模型交互审计 Transcript

删除：

- 没有恢复读取路径的 `checkpoint.json`
- 重复的 `board/snapshot.json`
- 重复保存完整 Board 的事件
- 写了但从不读取的恢复代码

要求：

1. 事件只保存变化或必要审计信息，不重复保存完整 Board 状态。
2. 恢复流程必须有测试，证明进程重启后能从 SQLite 与 Transcript 继续未结束 Session。
3. 恢复后不得重复执行已完成 Action。
4. 不保留没有消费者的 checkpoint 写入逻辑。

# 八、修复 SSE 和查询路径

当前 SSE 不得周期性调用完整 `_snapshot()`。

修改为：

1. 初次页面加载调用一次 RuntimeReadModel Snapshot。
2. SSE 只调用：
   - `list_agent_events(task_id, after_seq, limit)`
3. Heartbeat 只调用：
   - `latest_agent_event_seq(task_id)`
4. `/tasks/{id}/events` 直接使用增量 EventRepository。
5. 确保 `(task_id, seq)` 索引存在。
6. 支持稳定游标分页和稳定顺序。
7. 大量事件查询不得加载 Artifact、Action、Board、Metrics 或完整 Snapshot。
8. 前端使用事件 reducer 增量更新回合、Action、Artifact、Strategy 和 Session 状态。
9. 只有无法通过事件增量还原的数据才允许触发一次显式刷新。
10. 断线后从最后 seq 恢复；重复事件必须幂等处理。
11. heartbeat 不得触发完整页面状态重建。
12. 未知新事件必须安全降级显示，不得导致页面崩溃。

# 九、拆分 routes_v2.py

`apps/api/routes_v2.py` 不应继续混合任务、上传、SSE、Artifact、LLM、Skill、MCP、Docker 和后台线程职责。

按领域拆分路由，例如：

- tasks
- sessions
- events
- artifacts
- reports
- inputs
- llm_settings
- skills
- mcp
- capabilities

要求：

- 路由只负责 HTTP 参数、鉴权边界、状态码和 DTO 转换。
- 路由不得直接修改 SQLite。
- 业务操作进入 Application Service。
- 后台 Runner 调度进入独立 RuntimeScheduler。
- 保持已有公共 API URL，除非旧 URL 只属于已删除 Legacy 架构。
- 为公共 API 增加契约测试。

# 十、需要保留的职责边界

以下模块职责不同，不要错误合并：

- MCPGateway 与 MCPManager
- MCPTransport 与 MCPPolicy
- ArtifactStore 与数据库 ArtifactRepository
- Mode CompletionValidator 与 CTF Flag provenance CompletionGate
- SessionContextBuilder 与完整 TranscriptStore
- API adapter 与 Runtime Application Service
- Observer 与 AgentSessionRunner
- Write Model 与 RuntimeReadModel

可以调整命名和目录，但这些职责边界必须继续存在。

# 十一、前端任务执行面板同步重构

前端任务执行面板不能只修改字段名，必须完全采用 ReAct 执行语义，且所有数据来自真实 Runtime API 和 AgentEvent，不使用占位数据或旧架构截图。

## 1. 顶部 Session 状态区

显示：

- 任务名称与模式
- Session 状态
- 当前回合 / 最大回合
- 模型名称
- 已用 Token
- 已运行时间
- stop_reason
- pause / resume / cancel 控制

状态文案统一为中文：

- 已创建
- 运行中
- 已暂停
- 已完成
- 已阻塞
- 已取消
- 执行失败

不得继续显示旧 Solver Pool、伪多 Solver 或旧 Planner 状态。

## 2. 中央 ReAct 回合时间线

按 `turn` 分组显示每轮真实执行过程：

1. 上下文已构建
2. 模型请求已发送
3. 模型返回决策
4. 提出工具调用
5. 治理层批准或拒绝
6. 工具开始执行
7. 工具执行结束
8. Artifact 已保存
9. Observer 建议
10. 完成校验尝试
11. 本轮结束或继续下一轮

每个回合支持展开，显示：

- Context 字符数和 Token
- StrategyCard / StrategyStep
- 模型耗时和 Token usage
- tool name
- 参数脱敏摘要
- rationale
- expected_outcome
- risk
- authorization
- execution location
- duration
- status
- error code
- Artifact 引用
- finish validation missing 条件

不得在 UI、日志、事件或 Artifact 中显示模型的隐藏思维过程。只显示可公开的 assistant content、工具决策、治理元数据和执行事实。

## 3. 执行位置必须明确

工具卡片必须显示实际执行位置：

- TGA 进程
- Session Workspace
- Docker MCP 容器
- Remote MCP 服务
- 授权 HTTP 目标
- Input Store
- Artifact Store

用户必须能看出：

- 谁提出调用
- 谁批准
- 谁执行
- 在哪里执行
- 执行结果保存在哪里
- 什么内容作为 tool message 回传模型

## 4. 策略面板

只展示：

- StrategyCard
- StrategyStep
- 当前步骤
- pending / testing / succeeded / failed / blocked
- success marker
- 最近 Action
- Artifact 引用
- 下一步建议

删除：

- Hypothesis 分组
- testing / pending / verified 的旧假设语义
- legacy ideas
- Solver owner
- AttackFlow 旧模型

## 5. 证据面板

显示：

- Artifact 类型
- tool
- target
- created_at
- sha256
- provenance
- input_id
- 截断状态
- Artifact 索引片段
- 预览、检索和下载入口

确认结论只展示通过 Completion Gate 且带 task-owned Artifact provenance 的结果。

## 6. 完成和退出面板

必须区分：

- `finish_session` 被拒：不是任务失败，显示缺失条件并继续
- `completed`：完成校验已通过
- `blocked`：轮次上限、Provider 错误或不可恢复条件
- `paused`：可恢复
- `cancelled`：用户主动终止
- `failed`：运行时不可恢复错误

最终结果面板只在 `FINISH_ACCEPTED + AGENT_FINISHED` 后显示为“已确认最终结果”。

显示：

- summary
- coverage
- limitations
- evidence Artifact
- CTF flag 或对应模式的结构化结论
- stop_reason
- 完成时间

不得把 assistant 自然语言直接标记为已确认结果。

## 7. SSE 和增量状态

前端必须：

- 初次加载一次 Snapshot
- 后续通过 `after_seq` SSE 增量更新
- 使用事件 reducer 更新回合、Action、Artifact、Strategy 和状态
- 断线后从最后 seq 恢复
- 幂等处理重复事件
- 不因 heartbeat 重建完整页面状态
- 不轮询完整 Snapshot
- 对未知新事件安全降级显示
- 完整呈现 disconnected、reconnecting 和 error 状态

## 8. 视觉与响应式

保持现有产品设计语言，不创建营销式页面。

要求：

- 桌面端适合长时间观测。
- 移动端不发生文字、卡片和面板重叠。
- 时间线、策略、证据可通过 Tab 或可调整布局访问。
- 错误、拒绝、执行中和完成状态视觉区分明确。
- 中文文案为主，代码标识保留英文。
- 使用现有图标库，不手画重复图标。
- 补充 loading、empty、disconnected、reconnecting 和 error 状态。

# 十二、测试要求

重构过程中同步修改测试。不得通过保留 Legacy 产品代码让旧测试继续通过，也不得声称执行了实际未运行的测试。

至少覆盖：

## 1. 正常 ReAct 流程

- 构建上下文
- FakeModelClient 返回 tool call
- Handler 真实执行受控工具
- Artifact、ActionResult 和 AgentEvent 原子写入
- tool result 回传
- 下一轮调用 finish_session
- Completion 校验通过
- Session completed

## 2. Finish 被拒

- 返回 missing 条件
- Session 保持 running
- continuation 进入下一轮
- 补充工具调用和证据
- 再次 finish_session 后完成

## 3. Policy 拒绝

- 工具不执行
- ActionResult 为 blocked
- 审计事件包含脱敏后的拒绝原因
- LLM 收到结构化拒绝结果
- 前端显示“治理拒绝”，不能显示为执行成功或普通执行失败

## 4. 工具失败和超时

- Artifact、错误、ActionResult 和事件保持一致
- Session 根据策略继续或 block
- 不留下部分成功记录

## 5. 生命周期

- pause / resume / cancel
- 非法状态转换
- 达到 max_turns
- Provider request failure
- resume 后不重复执行已完成 Action

## 6. 事务与故障注入

- ActionResult 写入失败时整体回滚
- 完成事件写入失败时 Session 不得变为 completed
- Artifact 文件写入或 rename 失败时不留下成功 metadata
- StrategyStep 或 Event 写入失败时命令整体回滚

## 7. MCP

- Catalog snapshot
- Policy authorize
- Docker / Remote transport 使用 Fake Transport
- MCP 结果投影、截断和 Artifact 保存
- MCP 结果正确作为 tool message 回传

## 8. SSE

- after_seq 增量读取
- heartbeat 只查询 latest seq
- 游标稳定分页
- 重复事件幂等
- 大量事件下不调用完整 Snapshot，也不加载 Artifact 和 Action

## 9. 重启恢复

- 从 SQLite + Transcript 恢复
- 不依赖旧 checkpoint.json
- 从最后事件 seq 继续
- 不重复执行已完成 Action

## 10. 前端

- StrategyCard / StrategyStep 展示
- ReAct Turn 时间线
- 事件 reducer 增量更新与幂等
- 未知事件兼容
- SSE 断线重连
- 工具执行位置和 Artifact 链接
- 不再依赖 Hypothesis Board
- 完成结果只显示带 Artifact provenance 的结论
- 桌面和移动视口无重叠、无空白、无旧语义

## 11. 端到端前端验证

启动后端和前端，使用 Playwright 验证至少一个完整受控任务：

- 桌面视口
- 移动视口
- Session 状态
- ReAct 回合时间线
- 工具执行位置
- Artifact 链接
- finish rejected 或 finish accepted
- 最终结果
- SSE 断线重连
- 页面无重叠、无空白、无旧 Hypothesis / Solver 语义

保留必要截图作为测试输出。测试、截图和日志不得包含凭据、Authorization、Cookie、Token 或其他敏感值。

# 十三、删除验证

完成后执行全仓库搜索，确保以下内容不存在于生产路径中：

- `Manager._run` Legacy Agent 路径
- Legacy Solver fallback
- CapabilityExecutor
- ToolRunner
- MCPClient
- legacy `tool.invoke`
- `TGA_ENABLE_LEGACY_MCP_HUB`
- Hypothesis Board 写入
- `legacy ideas`
- 旧 Solver Pool 前端语义
- 无读取方的 checkpoint 写入
- SSE 中完整 `_snapshot()` 轮询
- Handler、Runner 或 Validator 直接更新 Session 状态
- Legacy Feature Flag、兼容文件或注释形式保留的旧执行代码

如果某个关键词仍存在，逐项说明它为什么是合理保留项；无合理原因则删除。

同时扫描源码、配置、日志、Artifact、Transcript、测试输出和 Git diff，确认不存在凭据、Authorization、API Key、Cookie、Token 或其他敏感值泄漏。

# 十四、执行顺序

按以下顺序实施，每阶段完成后运行相关测试：

1. 检查工作区状态，建立真实调用关系和 Legacy 删除清单。
2. 删除旧 Agent 执行路径及相关测试依赖。
3. 删除旧能力执行器和旧 MCP 栈。
4. 引入 UnitOfWork 和 SessionCoordinator。
5. 拆分 AgentToolSession。
6. 删除 Hypothesis Board，统一 Strategy 模型。
7. 拆分 EvidenceStore，建立 RuntimeReadModel。
8. 修复后端 SSE 增量查询。
9. 重构前端任务执行面板和事件 reducer。
10. 拆分 routes_v2.py。
11. 清理 checkpoint、旧数据库字段、死代码和旧文档。
12. 运行普通后端测试和故障注入测试。
13. 运行前端测试、类型检查、Lint 和构建。
14. 启动前后端并运行 Playwright 桌面 / 移动验证。
15. 执行最终 Legacy 和敏感信息泄漏扫描。

不要为了追求一次性大改而跳过中间验证。遇到已有测试与目标架构冲突时，修改测试以验证新架构，而不是恢复 Legacy 分支。

# 十五、验收标准

最终必须满足：

- 产品中只有一条 Agent 执行路径。
- 模型未配置或 Provider 不可用时明确失败，不发生架构回退。
- 只有一个能力分发机制和一套 MCP 栈。
- 只有 SessionCoordinator 能修改生命周期。
- 每个业务命令的状态、事件及相关记录原子提交。
- AgentSessionRunner 不执行具体工具。
- Tool Handler 不修改 Session 状态。
- CompletionValidator 不直接结束 Session。
- 新运行时不存在 Hypothesis Board。
- API 路由、应用服务、运行时和 Repository 边界明确。
- SQLite、Artifact 文件系统和 TranscriptStore 的权威职责清晰。
- 进程重启后能恢复未结束 Session，且不重复执行已完成 Action。
- SSE 不构建完整 Snapshot。
- 前端完全使用 StrategyCard / StrategyStep / ReAct Turn 语义。
- 前端能显示工具由谁决策、谁批准、在哪里执行、执行结果和证据位置。
- 最终确认结果必须具有 task-owned Artifact provenance。
- 后端测试通过。
- 前端测试、类型检查、Lint 和构建通过。
- Playwright 桌面和移动验证通过。
- 删除的 Legacy 代码没有通过兼容文件、Feature Flag、注释代码或死代码保留下来。
- 源码、Git、日志、Transcript、Artifact、截图和报告中不存在敏感信息泄漏。

# 十六、最终交付报告

完成后提供：

1. 最终架构和 ReAct 执行链路概述。
2. 删除的 Legacy 文件、类、配置、环境变量和前端语义清单。
3. 新增模块及各自职责。
4. 生命周期状态机与非法转换处理。
5. UnitOfWork 和事务边界说明。
6. 数据库 schema migration 与持久化权威来源说明。
7. API 与前端任务执行面板变化。
8. SSE、EventRepository 和 RuntimeReadModel 变化。
9. 执行过的后端测试、前端测试、类型检查、Lint、构建和 Playwright 验证及结果。
10. 重启恢复、故障注入和增量事件验证结果。
11. Legacy 删除扫描和敏感信息泄漏扫描结果。
12. 尚存风险和未完成项。
13. `git diff --stat` 和关键文件列表。

不要声称完成或运行了未实际执行的验证。除非遇到会破坏用户现有数据、无法判断的外部协议或不可恢复的迁移风险，否则不要停留在提问或计划阶段，直接完成实现、验证和清理。
