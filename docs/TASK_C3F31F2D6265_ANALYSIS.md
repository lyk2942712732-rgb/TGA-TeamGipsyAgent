# task_c3f31f2d6265 真实执行过程分析

> 取证日期：2026-07-19（Asia/Shanghai）  
> 任务页面：`http://127.0.0.1:5173/tasks/task_c3f31f2d6265/runtime`  
> 任务目录：`runs/task_c3f31f2d6265/`  
> 本报告只做事后取证和设计分析，不改变任务、数据库、配置或运行状态。

## 0. 结论摘要

这个任务并不是“文章完全没有进入模型”，而是“文章以弱结构进入，正文读取低效，之后又被工具会话缺陷和模型试错放大”。

核心事实如下：

1. 任务共运行 **42 个模型回合、48 个工具动作、187 条 AgentEvent**，生成 **44 个唯一 Artifact**；48 个动作中 42 个成功、6 个失败。Session 从 `2026-07-19T13:22:40.806915Z` 运行到 `13:35:02.253325Z`，约 **12 分 21 秒**，停止原因为 `flag_observed`。（证据：`evidence.db:sessions`、事件 seq 4—187）
2. 用户提示首先作为 `goal` 中的 URL 和一条 `kind=hint` 的 Memory 保存；文章正文没有预先成为 Artifact、Ideas、Hypotheses、next tests 或 constraints。初始 Solver 消息又把 `goal` 和 `hints` 一并发送，因此同一 URL 实际出现两次。（证据：`evidence.db:tasks`、`memory_entries:mem_fcf8265950be`、`messages.json` 消息 1；代码 `tga/runtime/agent_session.py:252-270`）
3. 唯一 Solver 在第 1 回合就读取文章；但 CSDN 响应是 **169,309 字节原始 HTML**，正文在持久化 `body_excerpt` 约第 29,901 字符才开始，而每个新 Artifact 首次只回填前 16,000 字节。因此初次结果主要是站点外壳，不是解题正文。（证据：action `act_22d07305ab3b`、artifact `artifact_db264b93652d`；代码 `tga/runtime/agent_session.py:477-482`）
4. Solver 到第 12 回合、事件 seq 63 才明确说“Now I have the full exploit from the blog”。此前用了 4 次 `artifact.inspect`，并夹杂目标探测、下载源码和 4 次失败的 workspace 下载/解压尝试。（证据：seq 13—63；actions 3、9—17）
5. 文章给出的正确主路径是：构造 263 个 `alter` 的字符逃逸 payload，得到 `10-0`，再以 `admin/1` 登录并访问 update 页面。Solver 在 action 36 首次得到 `10-0`，但 action 37 登录报“密码错误”。关键原因是当前 `http.request` **每次 action 都重新创建 `CookieJar`**，不能跨工具调用保留 PHP session；文章第一步写入的 `$_SESSION['token']='admin'` 没有带到下一次登录。（证据：artifacts `artifact_9c2be113a9cc`、`artifact_87c80cf5fb5a`；源码 `tga/capabilities/http.py:30-50`，尤其 41—42 行）
6. Solver 没有把该失败首先归因为 Cookie 会话不连续，而是误判“文章只执行 SELECT、没有修改密码”，继而尝试 UPDATE 数据库密码。action 42 的确执行了 UPDATE，action 43 随后登录成功，但仍因 Cookie 不连续无法跟随登录态获取 Flag。最后 action 48 改成在**同一个请求**中调用 `User->login()`，同请求内设置 `$_SESSION['login']=1` 并拿到 Flag。（证据：seq 139—185；artifacts `artifact_57da2d4aa1aa`、`artifact_1f88472cf1b1`、`artifact_ff713a9a3e39`）
7. 该任务走的是单 Solver 原生 AgentSession，不走 legacy Planner/Scheduler/Observer 路径。DB 中 `hypotheses=0`、`intents=0`、`subagent_requests=0`；事件流中没有 `MANAGER_DECISION`、`OBSERVER_*`、`HYPOTHESIS_*`。因此文章没有被任何 Manager/Observer 结构化、纠偏或去重。（证据：SQLite 表计数和事件类型；代码 `tga/runtime/manager.py:104-134`）
8. Native AgentSession 不做代码侧上下文裁剪或摘要。最终 `messages.json` 有 92 条消息、文件 503,017 字节；发送第 42 回合前约有 475,050 个序列化字符。仅文章相关的 5 条 tool message 就有 143,779 个 content 字符。**推断：**这类原始 HTML 和重复 Artifact 回填显著增加模型延迟和推理噪声，但没有 provider token 使用量，无法证明具体发生了 provider 侧截断。（证据：本地 transcript 统计；代码 `tga/models/openai_compatible.py:26-47` 原样发送全部 `messages`）

最终 Flag 为 `0ctfshow{c2371aa9-5c6a-4ebf-a283-8ac2adaa092c}`，直接出现在 `artifact_ff713a9a3e39` 的 HTTP 响应中；事件 seq 185 记录 `FLAG_FOUND`，seq 187 记录 Session 完成。

---

## 1. 取证范围、来源与限制

### 1.1 使用的真实数据

| 数据源 | 取证结果 | 用途 |
|---|---:|---|
| `runs/task_c3f31f2d6265/evidence.db` | 1 task、1 session、1 solver、48 actions/results、187 agent_events、44 artifacts、1 flag、1 memory；0 hypotheses/findings/intents/subagents | 任务配置、动作、事件、Flag、角色与状态的主证据 |
| `session/checkpoint.json` | `turn_count=42/48`、`status=completed`、`stop_reason=flag_observed`、proof=`artifact_ff713a9a3e39` | 终态与 checkpoint 交叉验证 |
| `board/snapshot.json` | 0 hypotheses；唯一 Memory 是文章 URL 提示 | 文章是否结构化的证据 |
| `solvers/solver_4e380761ab10/session/messages.json` | 1 system、1 user、42 assistant、48 tool，共 92 条 | Solver 可见上下文与逐回合 reasoning/tool call |
| `artifacts/*.json` | 44 个唯一文件 | HTTP/body、shell/stdout、Flag provenance |
| `solvers/.../workspace/` | 下载得到的 `lib.php/index.php/login.php/update.php` 与生成脚本/payload | 真实漏洞逻辑和模型构造过程 |
| README、运行时文档、API、Manager/Solver/Observer/Planner/Scheduler/Capability/Evidence/测试 | 已只读检查 | 区分真实路径与 legacy/测试路径 |

### 1.2 服务可用性

- `GET http://127.0.0.1:5173/tasks/task_c3f31f2d6265/runtime` 返回 200，但只有 556 字节 Vite 页面壳。
- `GET http://127.0.0.1:8000/api/v2/tasks/task_c3f31f2d6265/session` 和 `/events` 均连接失败，所以页面当时无法提供后端快照。
- 因此本报告以任务目录中的 SQLite、checkpoint、transcript 和 Artifact 为主证据。
- 没有调用 `GET /report`，因为该 GET 实际会执行 `write_report()` 并写入 `runs/<task>/reports/report.md`（`apps/api/routes_v2.py:214-218`、`tga/runtime/service.py:133-138`），不符合本次只读约束。
- `runs/task_c3f31f2d6265/reports/` 为空，说明该任务没有现成 report 可读。

### 1.3 事实与推断标记

- **事实**：能够由上述 DB 行、事件 seq、action/artifact、transcript 或明确代码位置直接验证。
- **推断**：由事实组合得到但缺少 provider 服务器日志、token usage 或网络层抓包；均显式标注“推断”。

---

## 2. 为什么给了文章思路，仍执行了许多步才拿到 Flag

### 2.1 文章以什么形式进入系统

**事实：它以两个弱结构入口进入，而不是结构化策略。**

1. `tasks.payload_json.goal`：`先阅读<文章 URL>,读懂之后解出<目标 URL>`。
2. `memory_entries:mem_fcf8265950be`：`kind=hint`、`source=user`、`artifact_ids=[]`，内容是“必须先阅读 URL，根据这篇文章的思路解题”。事件 seq 1 为 `USER_HINT`，seq 2 为 `MEMORY_UPSERTED`，seq 3 为唯一 `BOARD_SNAPSHOT`。
3. AgentSession 的 `_initial_prompt()` 把 task goal 和所有 hint Memory 原样拼入一个 JSON user message（`tga/runtime/agent_session.py:252-270`）。所以唯一 Solver 从第 1 回合起看到了 URL。
4. 文章正文直到 action `act_22d07305ab3b` 执行后才成为 `artifact_db264b93652d`；该 Artifact 没有写回 Board Memory，也没有生成 Hypothesis、next test、约束或文章摘要。任务结束时 `hypotheses` 仍为空，Memory 仍只有最初 URL。

因此，不存在“文章正文作为任务描述直接注入”或“系统已把文章变成计划”的机制。实际机制只是：把 URL 告诉模型，让模型自己用工具读取原始网页。

### 2.2 哪些角色看到了文章

| 角色 | 是否实际看到 | 证据与说明 |
|---|---|---|
| Manager | **只负责持久化/转交，没有模型式阅读** | `start_session` 保存 hint，`run_session` 选择 AgentToolSession（`manager.py:141-164,104-134`）；没有 `MANAGER_DECISION` 事件。 |
| Main Solver | **是，并持续位于同一 transcript** | 唯一 Solver `solver_4e380761ab10`；消息 1 同时含 goal 和 hint；第 1 回合 reasoning 明确复述“First read the blog post”。 |
| 其他 Solver | **不存在** | `solvers` 表只有一行，`subagent_requests=0`。不存在并发 Solver 信息不共享问题。 |
| Observer | **没有在本任务运行** | 事件中无 `OBSERVER_*`；native 分支直接进入 AgentToolSession，Observer 的每 6 回合 sidecar 逻辑只在 legacy `_run()`（`manager.py:343-599`）。 |
| Planner/Scheduler | **没有在本任务运行** | `intents=0`，无 `PLAN_CREATED/DECISION_TRACE`；`tga/orchestrator/*` 是另一条 legacy Week 1 链。 |

### 2.3 文章何时被引用、何时失效

文章没有被“遗忘”，而是经历了三个失效阶段。

#### 阶段 A：正文被网页外壳淹没（回合 1—11）

- seq 8—9：action 1 获取文章，HTTP 200；Artifact 是 169,309 字节 CSDN HTML。
- AgentSession 首次只回填 Artifact 文件前 16,000 字节（`agent_session.py:477-482`）；取证提取显示文章正文约从 `body_excerpt` 第 29,901 字符开始，初次回填看不到核心 payload。
- Solver 随后用 actions 3、10、12、17 多次 `artifact.inspect`。inspect 本身最多把 12,000 字符 lead 回填，同时 AgentSession 又附带源 Artifact 前 16,000 字节，造成单条 inspect tool message 约 31k 字符（`runtime.py:409-430` 与 `agent_session.py:404-421,477-482`）。
- action 9 尝试从 workspace Python 再抓 CSDN，因网络连接失败；actions 13—16 连续因 Linux 命令用于 PowerShell、Python 网络不可达、文件未下载等失败。
- 直到 seq 59—61 的 action 17 才返回包含完整字符逃逸和 263 个 `alter` payload 的正文片段；seq 63 才宣布完整理解。

**判断：**文章确实被利用，但网页正文抽取和 Artifact 表达不适合“把一篇外部文章转成策略”。约 11 个回合不是必要安全验证，而是可避免的读取/环境试错。

#### 阶段 B：文章已理解，但没有绑定成可执行检查表（回合 12—30）

- seq 63 已准确复述文章路径和 263 个 `alter`。
- action 18 的 body 就是 263 个 `alter` + 263 字符注入串，但没有设置 `Content-Type: application/x-www-form-urlencoded`。Capability 对字符串 body 默认使用 `text/plain; charset=utf-8`（`tga/capabilities/http.py:160-173`），PHP 没有按表单填充 `$_POST`，所以没有 `10-0`。
- action 24 设置了正确 Content-Type，却只有 262 个 `alter`，仍没有触发。
- actions 23—35 反复写/改/运行 payload 辅助脚本，其中三次覆盖 `build_payload.py`，action 30 因 PowerShell 引号失败。直到 action 36 才同时满足：263 个 `alter`、正确注入串、正确表单 Content-Type；Artifact `artifact_9c2be113a9cc` 返回“你还没有登陆呢！10-0”。

**判断：**这不是文章信息不足，而是文章没有变成机器可校验的结构：`padding_count=263`、`content_type=form`、`success_marker=10-0`、`next=login(admin,1)`。因此模型每次都在自然语言里重新计算并重复试错。

#### 阶段 C：文章路径与 HTTP 工具会话模型不兼容（回合 31—42）

- action 36 的 POP 链通过 `dbCtrl->login()` 设置目标 PHP session 的 `token=admin`；文章下一步依赖同一 session 进行 login。
- 当前 `execute_http()` 在**每次 action**内部新建 `CookieJar()` 和 opener，action 结束后即丢弃（`tga/capabilities/http.py:30-50`）。所以 action 37 没有携带 action 36 的 PHP session cookie，返回“密码错误！”。
- Solver 的 seq 143 reasoning 错误归因成“SELECT 只返回常量，没有真正修改密码”，没有首先识别工具不保留 Cookie；随后 actions 38—42 尝试通过 UPDATE 修改 admin 密码。Artifact `artifact_57da2d4aa1aa` 返回“用户不存在!0-0”，但 UPDATE 在报错前已执行；action 43 新会话登录 `admin/1` 成功，证明目标数据库状态已被改变。
- 因 cookie 再次不连续，登录成功仍不能让下一请求进入已登录页面。seq 167 时 Solver 终于明确指出“tool doesn't maintain sessions across requests”，转而构造同请求 POP 链。
- actions 44—47 生成并检查同请求 payload；action 48 在一个 POST 中同时带 `username=admin&password=1`，POP 链调用 `User->login()`，同请求内把 `$_SESSION['login']=1`，随后 `update.php` 输出 Flag。

**判断：**action 37 是遵照文章的合理验证；actions 38—43 是工具缺陷触发、但由错误归因扩大且有不必要持久状态修改的偏航；actions 44—48 是在无 Cookie 会话能力下的合理适配，但 4 个本地辅助动作仍可压缩。

### 2.4 紧凑时间线

| 阶段/seq | 决策角色 | 输入与已知文章信息 | 实际动作 | 结果 | 判断（必要/可避免/不确定） |
|---|---|---|---|---|---|
| seq 1—5，13:22:40 | Manager/运行时 | goal 含 URL；hint Memory 含 URL，无正文/假设 | 保存 hint、创建 Session、启动唯一 Main Solver | 0 hypothesis、1 memory | 初始化必要；缺少结构化提示可改进 |
| seq 6—11，回合 1，actions 1—2 | Solver | 明确知道必须先读文章 | 并行 GET 文章和目标首页 | 文章 169,309B；目标是登录页 | 两个 baseline 动作合理 |
| seq 12—27，回合 2—3，actions 3—8 | Solver | 已识别 PHP 字符逃逸、safe/Info 概念 | inspect 文章；探测 login/register/update/lib | 获得部分文章线索和页面行为；register 404 | 一次 inspect/目标确认合理；admin/admin、register 可避免 |
| seq 28—41，回合 4—6，actions 9—12 | Solver | 知道文章核心但缺完整 payload | Python 抓文失败；offset inspect；GET www.zip；继续 inspect | 找到源码备份和更多正文 | 文章/源码验证合理；二次抓文失败可避免 |
| seq 42—61，回合 7—11，actions 13—17 | Solver | 已知 www.zip 与字符逃逸 | 4 次下载/解压失败，继续 inspect | action 17 终于返回完整 payload | 1 次环境探测可理解；4 次失败高度可避免 |
| seq 62—65，回合 12，action 18 | Solver | 完整文章：263 alter、注入串、10-0 | POST 正确 payload，但未设 form Content-Type | 只返回“未登录”，无 10-0 | 可避免：执行前未检查传输前提 |
| seq 66—81，回合 13—16，actions 19—22 | Solver | 误以为需先拿 session；怀疑 payload/源码差异 | GET 登录页、重复下载 zip、在 PowerShell 成功解压并读源码 | 验证实际源码与文章基本一致 | 源码审计有价值；重复下载和“先登录”误判可避免 |
| seq 82—89，回合 17—18，actions 23—24 | Solver | 有源码和文章 payload | 写 8.7k 脚本；POST 262 个 alter | 仍无 10-0 | 可避免：count 错 1，缺少自动断言 |
| seq 90—137，回合 19—30，actions 25—36 | Solver | 已知正确 marker 和 payload 结构 | 多轮计算、生成、验证、覆盖脚本；最终 POST 263 个 alter | `artifact_9c2be113a9cc` 出现 `10-0` | 至少一次本地校验合理；其余大多冗余 |
| seq 138—141，回合 31，action 37 | Solver | 文章明确下一步 admin/1 | POST login | `artifact_87c80cf5fb5a`：“密码错误！” | 合理验证；失败由跨 action Cookie 丢失导致 |
| seq 142—165，回合 32—37，actions 38—43 | Solver | POP 已成立，但未识别 cookie 根因 | 构造 UPDATE payload、修改数据库密码、再次 login | UPDATE 后 action 43 登录成功 | 大多可避免；模型误归因，且引入目标持久状态变更 |
| seq 166—184，回合 38—42，actions 44—48 | Solver | 已明确 HTTP 工具不保持 session | 构造同请求 `User->login()` POP 链并 POST | `artifact_ff713a9a3e39` 返回 Flag | 适配思路必要；本地 4 个辅助动作可压缩 |
| seq 185—187，13:35:02 | Capability/AgentSession | HTTP raw 中出现符合 regex 的 Flag | 自动记录 Flag、设 challenge solved、停止 | `flag_observed`、42/48 turns | 证据识别和停止必要；gate 实现需加强 |

### 2.5 动作性质拆分

#### 应保留的安全/证据动作

- 读取文章本身，并把文章 Artifact 与后续策略建立 provenance。
- 至少一次目标首页/源码核验，确认文章对应的题目版本，而不是盲用外部 payload。
- 在发送主动 payload 前做序列化长度、Content-Type、预期 marker 的本地校验。
- 观察 `10-0` 作为 POP 成功证据，再进行登录/Flag 获取。
- Flag 必须来自目标响应 Artifact；最终 `artifact_ff713a9a3e39` 满足这一点。
- Capability schema、超时、输出上限、workspace 边界、Artifact 持久化和 append-only 事件应保留。

#### 可由更好决策避免的冗余

- actions 4—5 的弱相关 admin/admin 和 register.php 探测。
- actions 9、13—16、30 共 6 个失败动作中的大部分；其中 4 个是没有先识别 Windows/PowerShell 环境。
- 重复 GET `www.zip`（actions 11、20）和四次读取同一文章 Artifact。
- actions 23—35 中重复的 payload 脚本写入、覆盖和引号修复。
- action 18 缺 form Content-Type、action 24 少一个 `alter`。
- actions 38—43 的 UPDATE 密码旁路；它是错误归因后的高副作用偏航。
- actions 44—47 可合并为一次“生成 + 断言 + 执行”受控 helper。

#### 因当前架构/代码约束发生的动作

- 外部文章 URL 不会自动读取或结构化，Solver 必须自己调用工具。
- 原始 Artifact 首次只给 16k 头部，正文靠后就必须 inspect。
- `http.request` 没有跨 action CookieJar，文章的两阶段 session 路径必然失败。
- 每轮都把完整 transcript 发给模型，没有代码侧摘要/裁剪；工具结果越大，后续请求越重。
- Native AgentSession 没有 Observer、Manager plan ledger、Hypothesis/Memory 更新或语义重复 gate。

#### 主要由模型推理质量造成的动作

- 已有源码仍把“未登录提示”误认为 update 逻辑完全没有执行；源码实际无论登录与否都会调用 `$users->update()`（工作区 `update.php:8-15`）。
- 在正确文章 payload 上先漏 Content-Type、后错 padding count。
- action 37 失败后没有第一时间把 action 间 session 丢失作为首要假设，转而修改数据库。
- 多次使用不适配 PowerShell 的命令和易出错的内联引号。

### 2.6 上下文、记忆、并发、Observer 与调度问题

| 检查项 | 本任务事实 | 结论 |
|---|---|---|
| 上下文窗口/裁剪 | Native client 每回合把全部 `messages` 原样发送；最终发送前约 475,050 序列化字符 | 没有代码侧“遗忘/摘要截断”；**推断：**可能接近 provider 上下文边界并造成噪声/高延迟 |
| 记忆压缩 | Board 只有 1 条 URL hint，未触发 20 条 Memory 压缩；文章结论不进入 Memory | 不是压缩丢失，而是根本未结构化写入 |
| 大工具输出 | 文章相关 tool content 合计 143,779 字符；inspect 同时返回 lead 和源 Artifact 头部 | 明显噪声放大 |
| 并发 Solver | 只有 1 个 Main Solver | 不存在本任务内跨 Solver 信息不共享 |
| Observer | 完全未触发 | 无法发现重复、偏航、错误归因或危险 UPDATE |
| Planner/Scheduler | legacy 路径未进入 | 没有 Manager 分解、优先级、去重或文章策略约束 |
| 工具状态 | HTTP Cookie 仅 action 内保存 | 与文章两请求路径直接冲突 |
| 时间占比 | 42 个 `MESSAGE_START→END` 区间累计约 717.45 秒；48 个 action 的 `created→updated` 累计约 21.61 秒 | 事实表明主要耗时在模型/provider 回合，不在工具执行；上下文噪声是可能因素但非唯一可证明因素 |

---

## 3. 当前执行流程中哪些动作是“写死的”，Agent 无法决定

### 3.1 本任务的真实主链

本任务实际链路是：

`POST task → start_session/hint memory → 后台 runner → Manager 选择 AgentToolSession → 单 Main Solver 原生 tool loop → Capability executor → Artifact + tool result 回填同一 transcript → HTTP raw regex 找到 Flag → AgentSession 直接标 solved 并 stop`。

`tga/orchestrator/planner.py`、`scheduler.py` 以及 `Manager._run()` 中的 Hypothesis/Observer/多 Solver 流程没有参与本任务。README 和迁移文档也明确普通产品 Session 使用持久 native AgentSession（`README.md:3,46-58`；`docs/REFACTOR_PLAN.md:17-23,42-43,80-81`）。

### 3.2 节点分类（真实关键节点不少于 15 个）

| 流程节点 | 当前实际行为 | 分类 | 控制它的模块/函数/配置 | Agent 能否影响 | 设计目的 | 对本任务的影响 |
|---|---|---|---|---|---|---|
| 1. Task schema 校验 | task 必含 id/name/mode/target/goal；空 scope 从 target origin 派生 | 完全写死 | `tga/contracts.py:42-96` | 否 | 形成合法任务合同 | scope 固定为 challenge origin |
| 2. Task 创建 | API 先写 task，再调用 `start_session` | 完全写死 | `routes_v2.py:181-193`；`service.py:37-46` | 否 | 创建与启动对 UI 原子化 | 产生 task 与 session 持久状态 |
| 3. 固定目录/checkpoint | 创建 session/board/solvers/artifacts/reports 目录和 SessionRecord | 完全写死 | `runtime/session.py:23-34` | 否 | 崩溃恢复 | 本任务有 checkpoint/board，但 reports 空 |
| 4. Hint 写入 | 最多 800 字符，作为 `kind=hint` Memory，追加 3 个事件/快照 | 完全写死 | `manager.py:329-341`；API `routes_v2.py:446-449` | 只能提供文本，不能选择结构 | 统一用户干预入口 | 文章只成普通 URL 文本，无 Artifact/假设 |
| 5. 后台调度 | 每 task 最多一个进程内 runner thread | 完全写死 | `routes_v2.py:464-483` | 否 | HTTP 请求不被长循环占用 | 任务异步启动 |
| 6. 运行时分支 | 有模型配置且未注入测试 Solver 时强制走 `AgentToolSession`；legacy planner 路径被跳过 | 完全写死 | `manager.py:104-134` | 否 | 使用 BreachWeave 式原生工具回合 | 本任务无 Planner/Observer/Manager 决策 |
| 7. 默认 Solver 数量 | 创建/复用一个 role=`main` Solver；清理旧伪 Solver | 完全写死 | `agent_session.py:215-243` | 否 | 单持久执行主体 | 实际只有 `solver_4e380761ab10` |
| 8. 初始 system prompt | 固定要求持续使用工具、无需重新询问 scope/risk、用 `finish_session` 完成 | 完全写死 | `agent_session.py:245-250` | 否 | 约束 native tool loop | 取消了前端强度/主动探测的执行意义 |
| 9. 初始 user prompt | 固定拼 session/mode/target/goal/theme/description/flag_format/hints | 完全写死 | `agent_session.py:252-270` | 仅能影响字段值 | 给 Solver 初始上下文 | 文章 URL 同时在 goal/hints，正文未注入 |
| 10. Hint 同步 | 每回合检查新 hint ID；未出现过才追加 user message | 受约束可选 | `agent_session.py:109,272-283` | 用户可追加；Solver不能控制同步 | 运行中可干预 | 本任务只有初始 hint，无后续 hint |
| 11. 工具注册 | 默认注册 HTTP、MCP generic invoke、workspace read/write/python/shell、artifact.inspect | 完全写死 | `capabilities/registry.py:55-65` | 只能从注册集选择 | Schema/risk/capability 边界 | 本任务用了 6 种 capability，未用 MCP |
| 12. 工具暴露 | 任务 mode 允许的 registry 项全部变为 `tga_*` tool，再加 `finish_session` | 完全写死 | `agent_session.py:285-326` | 只能选择暴露项 | 原生 function calling | generic `tool.invoke` 即使无精确 MCP 方法也会暴露 |
| 13. 回合上限 | 默认 48，可由 `TGA_MAX_SESSION_TURNS` 在 1—512 内覆盖；达到即 blocked | 受约束可选 | `manager.py:31-62`；`agent_session.py:100-107` | Agent不能提高；环境可配 | 防无限运行 | 本任务用 42/48，余量很小 |
| 14. 每回合模型参数 | 把完整 transcript 和工具表发送；temperature=0.2，tool_choice=auto | 完全写死 | `agent_session.py:117-122`；`openai_compatible.py:26-47` | Agent不能更改 | 保持连续工具会话 | 无裁剪，后期上下文膨胀 |
| 15. 无 tool call fallback | 连续两次纯文本且无 Flag/finish 就 blocked；第一次注入“继续具体工具调用” | 完全写死 | `agent_session.py:164-181` | 可通过调用工具避免 | 防模型空转 | 本任务每轮都有 tool call，未触发 |
| 16. 动作目标/理由模板 | 每个 native action 的 target 固定为 task target，hypothesis 固定 `session_<solver>`，rationale 固定 `native AgentSession tool call` | 完全写死 | `agent_session.py:365-380` | 只影响 arguments/capability | 兼容 ActionSpec/Evidence | UI/DB 看不到真实决策理由，48 条 rationale 全相同 |
| 17. 工具选择与参数 | Solver 自主选择何时读文章、探测、写脚本、发送何种 payload | Agent 自主决定 | provider function call | 是 | 让模型解决开放任务 | 产生本文大多数合理动作与冗余动作 |
| 18. Schema/risk 校验 | 参数需通过 Pydantic；非 GET HTTP 被标 active；capability/mode/risk/hypothesis 校验 | 受约束可选 | `agent_session.py:328-368`；`runtime.py:130-166` | 可提议，不能绕过 schema | 防无效/越权执行 | body/Header 合法但语义错误不会被拦截 |
| 19. Native scope/强度改写 | 执行前把 scope 改为 `['*']`、intensity=`active`、allow_active_scan=`True` | 完全写死 | `agent_session.py:435-449` | 否 | 将 target 视作 Session 授权合同 | 原 task 的 `allow_active_scan=false` 不约束实际执行；文章域也被允许 |
| 20. 默认预算 | 产品 executor 使用 `ExecutionBudget(unrestricted=True)`；action/重复/rate limit 只记数不拒绝 | 完全写死 | `manager.py:944-957`；`runtime.py:72-105` | 否 | BreachWeave 式长会话 | 重复动作没有执行层早停；48 个动作均未被 budget 拦截 |
| 21. MCP 执行 | 仅 `tool.invoke` 且 tool runner、server、method 均存在时执行；并发默认 2（非 unrestricted 才可能 gate） | 受约束可选 | `runtime.py:249-308`；`tools/bootstrap.py:47-58` | Solver可选择，但目录/依赖/目录表决定可用性 | 受控调用 MCP | 本任务 action capability 中没有 `tool.invoke`，所以 0 个 MCP 服务被调用 |
| 22. HTTP 范围/重定向 | 解析 URL、最多 6 次 redirect、检查 scope；native 传入 `*` | 受约束可选 | `http.py:30-65,136-145` | 可选 URL/GET/POST，不能控制 redirect gate | 限制网络行为 | 本任务文章与目标均可请求 |
| 23. HTTP Cookie 生命周期 | 每次 `execute_http` 新建 CookieJar；只在该 action 的 redirect 链内复用 | 完全写死 | `http.py:41-50` | 否 | 简单隔离请求状态 | 直接破坏文章的跨请求 PHP session 路径 |
| 24. HTTP timeout/output | timeout schema 1—60s；executor 上限 120s；响应最多 262,144B | 受约束可选 | `schemas.py:12-25`；`runtime.py:37-68,191-220` | 可请求更短，不能超上限 | 防挂起/巨量输出 | CSDN 169k 未截断，但仍很噪声 |
| 25. Artifact 持久化 | 每次工具输出保存内容寻址 Artifact；inspect 复用源 Artifact ID | 完全写死 | `artifacts.py:12-77`；`runtime.py:409-430` | 否 | 可追溯证据 | 48 动作只产生 44 唯一 Artifact |
| 26. Tool result 回填 | result facts/leads/flag/Artifact 前 16k 原样加入同一 transcript | 完全写死 | `agent_session.py:388-433,477-482` | 否 | 原生连续工具回合 | 文章 raw HTML 和重复头部造成上下文噪声 |
| 27. Transcript 持久化 | 每次 assistant/tool message 原子替换 `messages.json`；恢复时整份加载 | 完全写死 | `agent_session.py:541-553` | 否 | 会话恢复 | 没有摘要/分层记忆；最终文件 503kB |
| 28. Hypothesis/Board | native 路径不创建 Hypothesis，也不把结果 facts 写 Memory | 完全写死（当前分支） | 分支点 `manager.py:119-134`；legacy 写入在 `manager.py:389-402,601+` | Agent无可调用的 board patch 工具 | 简化原生架构 | 文章没有变成可追踪策略，Board 一直空白 |
| 29. Observer 触发 | native 路径不创建 ObserverSidecar；legacy 才每 6 turn 请求 review | 完全写死（当前分支） | native 分支 `manager.py:119-134`；legacy `manager.py:408-411,585-597` | 否 | legacy 纠偏隔离 | 本任务重复/偏航无人纠正 |
| 30. Observer 权限（若走 legacy） | 只能 patch Memory/Hypothesis/steer，不能执行动作或 verify | 受约束可选 | `observer.py:45-88` | Observer只能建议/board patch | 防 Observer 越权 | 本任务未运行，影响为 0 |
| 31. Planner/Scheduler（legacy） | CTF 固定 recon→exploit→report，顺序执行并过 safety/evidence gate | 完全写死/受约束可选 | `orchestrator/planner.py:11-30`；`scheduler.py:21-91` | legacy LLM只能 reorder/refine | 旧可复现流程 | `intents=0`，本任务完全未参与 |
| 32. Flag 候选提取 | HTTP raw 按 task flag regex 自动提取 | 完全写死 | `runtime.py:221-242`；`http.py:264-270` | 只能通过目标输出影响 | 自动发现 Flag | final HTTP 直接产出 candidate |
| 33. Native Flag 接受 | 对每个 candidate 用最后 Artifact 调 `_accept_flag`，直接写 flags/challenge；不调用 `CompletionGate/flag_ok` | 完全写死 | `agent_session.py:411-433,484-508` | 否 | 立即完成 CTF | 本任务 Artifact 确实含 Flag，结果正确；但实现比 legacy provenance gate 弱 |
| 34. 自动停止 | 任一 tool result 有 candidate_flags 就立刻 `completed/flag_observed` | 完全写死 | `agent_session.py:430-433,524-539` | 否 | 早停 | seq 185 发现、seq 187 停止，无多余报告回合 |
| 35. checkpoint | 每批 tool calls 后及退出时写 session/board checkpoint | 完全写死 | `agent_session.py:203-212`；`session.py:36-66` | 否 | 恢复和 UI replay | 终态可完整取证 |
| 36. 报告生成 | 完成后不自动生成；只有显式 GET/report 或脚本调用才写 report | 受约束可选 | `service.py:133-138`；`routes_v2.py:214-218` | AgentSession不能主动决定默认 report | 避免循环内报告开销 | 本任务 reports 为空 |

### 3.3 “看似 Agent 决定、其实是模板/fallback”的路径

1. action 的 `target`、`risk` 基线、`hypothesis_id` 和 `rationale` 并非模型完整决定；host 固定 target、生成 synthetic session hypothesis，并把真实 rationale 抹成统一文本（`agent_session.py:365-380`）。
2. task 的 `intensity=normal`、`allow_active_scan=false` 看似是用户控制，native `_execution_task()` 却统一改成 active/true/scope=*（`agent_session.py:435-449`）。
3. `MAX_ACTIONS_PER_SOLVER=32` 和 semantic retry 配置看似预算；native 产品 executor 实际 `unrestricted=True`，本任务做了 48 actions 仍不拦截（`manager.py:944-957`）。
4. Observer 每 6 回合纠偏看似全局机制，实际只在 legacy `_run()`；本任务 native AgentSession 完全绕过。
5. `CompletionGate`/`flag_ok` 看似统一 Flag gate，native 候选实际走 `_accept_flag()` 直接落库；本任务事件是 `FLAG_FOUND` 而不是 `FLAG_CONFIRMED`。
6. `finish_session(flag=...)` 会把 Flag 绑定到“最后一个 Artifact”，并不验证该 Artifact 包含 Flag（`agent_session.py:339-352,484-508`）。本任务没有使用 finish tool，而是 HTTP raw 自动候选，因此最终 provenance 仍真实。

---

## 4. 可增强 Agent 能力的改进措施（只建议，不实施）

| 优先级 | 改进措施 | 解决的根因 | 预期收益 | 实现范围 | 风险/安全影响 | 验证指标 |
|---|---|---|---|---|---|---|
| P0 | 建立“Hint/文章摄取器”：URL 先做 readability/正文抽取，再产出带 provenance 的 `StrategyCard`（claims、preconditions、exact steps、expected markers、next tests、constraints） | hint 只是 URL；原始 HTML 淹没正文；没有结构化策略 | 第 1—2 回合直接得到 263 alter、form Content-Type、10-0、admin/1 路径 | API/Manager、Artifact parser、Board schema、UI | 外部文章是不可信输入；必须标注 source、禁止把文章文本当系统指令，且主动步骤仍过授权 gate | hint→首个结构化假设回合数；正文有效字符率；hint claim coverage；从 hint 到首个命中 marker 的动作数 |
| P0 | 给每个 Solver 提供显式、隔离、可清除的 HTTP session profile/cookie jar；tool call 用 `session_id` 选择，默认同 Solver 持续 | 每 action 新 CookieJar 使文章原路径必败 | 本任务 action 36 后可直接 login，再 GET update；避免 UPDATE 数据库和同请求复杂 POP | HTTP Capability、Artifact redaction、checkpoint secret storage、UI session telemetry | Cookie 是敏感授权状态；不得写明文事件/报告，按 origin 隔离，跨 scope 禁止，任务结束销毁 | 跨请求登录成功率；因 Cookie 丢失导致的重试数；跨 origin cookie 泄漏=0；本题路径压缩到 3 个 exploit HTTP actions |
| P0 | 引入 Manager “证据—计划账本”：每一步必须绑定 hint claim/hypothesis、expected observation、failure boundary；相同前提失败后先诊断环境而非换 payload | native 路径无 Hypothesis/去重/错误归因；rationale 被写死 | 发现 action 18 Content-Type、action 24 count、action 37 session 三个清晰失败边界并早停重复 | AgentSession host、ActionSpec 扩展、Board、UI | 不应把计划账本变成阻塞探索的硬 gate；允许显式 override 并记录原因 | 计划覆盖率；重复 semantic action 比例；无新证据动作数；错误归因修正所需回合 |
| P1 | 为大 Artifact 建立分层返回：工具只回摘要、结构化 page facts 和命中片段；原文按 section/offset 懒加载，不再同时附源 Artifact 头部 | 5 条文章 tool message 143,779 字符，inspect 还重复前 16k | 显著减小上下文、模型时延和噪声 | Capability result schema、artifact.inspect、AgentSession result renderer | 摘要可能漏细节；必须保留 raw Artifact 和可追溯 offset/hash | 每回合输入字符/token；有效片段占比；inspect 重复率；原文可追溯率=100% |
| P1 | 原生 Session 增加分层记忆和可验证压缩：固定保留 system/user hint、StrategyCard、最近动作、失败边界和关键 Artifact 引用；旧 raw tool results 移出 prompt | 完整 transcript 到 47.5 万字符，无代码侧裁剪 | 降低延迟/上下文溢出风险，同时不丢 hint | AgentSession context builder、checkpoint、provider adapter | 压缩错误会丢关键信息；需 provenance 与可展开原文，提示不可被低优先级摘要覆盖 | hint retention=100%；压缩后关键 claim recall；P95 turn latency；上下文字符/token |
| P1 | Observer 改为原生路径可用、事件驱动触发：连续失败、相同 endpoint 重试、目标状态变更、上下文超阈值时审查；正常进展不打扰 | 本任务 Observer 为 0，无法纠偏；固定“每 6 回合”仅存在于 legacy | 在 action 13—16、23—35、38—43 前后及时提示环境/cookie/重复问题 | AgentToolSession、ObserverSidecar、patch schema | Observer 不得执行动作/确认 Flag；设置 cooldown 和只在高信号触发，避免打断 | Observer precision；被采纳纠偏率；无效打扰率；重复动作下降幅度 |
| P1 | 为已知解题路径提供“最小验证优先”：文章与题目 title/source hash 对齐后，先执行最小、可回滚验证，不先做通用探测 | article 已精确对应同题，仍探测 register/admin/admin、重复 zip | 缩短从 hint 到 10-0 的路径 | Manager strategy、Solver prompt、source matcher | 防止文章过期/恶意；仍需一次版本核验和 scope gate | 已知路径优先率；source-match 后非路径动作数；错误文章误用率 |
| P1 | HTTP/form 语义预检：字符串 body 含 `a=b&...` 且 POST 时提示/要求明确 Content-Type；payload builder 提供 length/count/assertions | action 18 传 text/plain；action 24 少 1 个 alter | 消除低级传输和长度错误 | HTTP schema、preflight validator、workspace helper | 只做提示或安全拒绝无歧义错误；不能擅自改 payload | form Content-Type 错误率；长度断言失败在出网前发现率；payload 重试数 |
| P1 | 目标状态变更保护：检测 UPDATE/DELETE/持久凭据修改意图，要求策略解释、较低副作用替代比较和专门事件 | action 42 修改 admin 密码，偏离文章且改变目标状态 | 减少不必要持久副作用 | Solver tool schema、risk classifier、Manager approval/telemetry | 可提高安全但不能把合法 CTF 完全卡死；CTF 可允许但必须审计 | 持久变更动作数；无替代分析的高副作用动作=0；安全事件可追溯率 |
| P2 | 恢复真实 action rationale：保存模型 reasoning 的短摘要、关联 StrategyCard step，而不是统一 `native AgentSession tool call` | DB/UI 无法看出为什么调用工具 | 人类可快速判断文章是否被用、何时偏航 | AgentSession、ActionSpec、UI drawer | reasoning 可能含敏感信息；只存模型生成的短 rationale 并脱敏 | action rationale 非模板率；人工定位偏航耗时；rationale—evidence linkage |
| P2 | UI 增加 Hint 利用面板：显示“已读取/已结构化/当前绑定步骤/最后引用回合/偏离原因”，并标记重复 endpoint、失败串和 Cookie profile | 当前只能看 tool timeline，无法发现 hint 未结构化或重复 | 汇报/人工干预更及时 | Web event reducer、runtime API projection、观测指标 | 不展示原始 Cookie、secret、完整 exploit body；按角色控制 | 人类发现偏航时间；hint 未利用告警 precision；重复动作可见率 |
| P2 | 统一 native 与 legacy Flag provenance gate：候选必须 regex fullmatch、非 placeholder、Artifact 实含 Flag；`finish_session` 也不能借最后 Artifact | native `_accept_flag` 绕过 `CompletionGate/flag_ok` | 防模型口头 Flag 或错误 Artifact 被标 solved | AgentSession、CompletionGate、事件命名、tests | 增强安全；注意二进制/编码 Artifact 的验证兼容 | false positive flag=0；所有 solved 均有内容匹配 proof；`FLAG_FOUND→CONFIRMED` 审计完整 |
| P2 | 报告投影只读化：GET report 不应写文件；将生成改为纯响应或显式 POST/export | 当前 GET `/report` 有写副作用，妨碍严格只读取证 | API 语义清晰、取证安全 | API/service/reporting | 低风险；保留显式导出权限 | GET 幂等且零文件变化；只读取证测试覆盖 |
| P2 | 建立任务级路径效率评测集，保存 provider usage 和策略事件 | 当前只有 turn/action/event，没有 hint 利用率与 token/latency 因果证据 | 可持续量化改进 | Evals、telemetry、report schema、CI | provider usage 可能敏感；只保留聚合值，不记录 key/raw secrets | 见下方指标体系 |

### 4.1 建议的评测指标

1. **提示利用率（Hint Utilization）**
   - `被 StrategyCard 引用的 hint claims / 提取出的有效 claims`。
   - `首次读取 hint → 首次创建可执行假设` 的回合数/秒数。
   - `当前 plan step 有 hint/evidence 绑定的动作数 / 总动作数`。
2. **路径效率（Hint-to-Flag Efficiency）**
   - `hint 注入 seq → FLAG_CONFIRMED seq` 的 turn、tool call、wall time、provider input token。
   - 与已知最短安全路径相比的 `excess actions`。
3. **冗余步骤**
   - 相同 capability+endpoint+语义参数、但无新增事实/Artifact 的重复率。
   - 连续失败后未新增 failure hypothesis 的动作数。
   - 只改脚本文本但未增加断言/证据的 workspace.write 次数。
4. **工具调用质量**
   - HTTP Content-Type/会话 profile/预期 marker 声明完整率。
   - 失败归因准确率；环境错误（shell/platform/network）复发率。
5. **Observer/Manager 效果**
   - 纠偏 precision、Solver 采纳率、采纳后减少的动作数。
   - 正常路径被 Observer 打断的比例（防打扰指标）。
6. **证据与安全**
   - 每个 solved Flag 的 Artifact 内容匹配率必须为 100%。
   - 跨 origin Cookie 泄漏、越 scope 动作、未审计持久状态变更均必须为 0。

---

## 5. 最值得优先做的 3 项改进

1. **结构化摄取文章/Hints**：把文章从“URL + 16k 原始头部”变成带 provenance 的 StrategyCard，并自动生成 `263 alter / form POST / 10-0 / admin:1 / session prerequisite` 的可执行检查表。这直接消除前 11 回合和后续多次重新计算。
2. **每 Solver 持久、隔离的 HTTP Cookie session**：这是本任务文章路径失败的最直接代码根因。若 action 36 的 session 能延续到 action 37，再跟随 update，理论上无需 UPDATE 密码和同请求复杂 POP。
3. **原生 AgentSession 的证据—计划账本 + 事件触发 Observer/去重早停**：让每个 action 明确预期、结果和失败边界；在 Content-Type 错、padding 错、连续 shell 失败、Cookie 丢失和数据库修改前进行纠偏。

## 6. 对该任务理论上可减少的步骤类型

**推断：**在文章正文抽取正确、HTTP session 持久且保留一次源码版本核验的前提下，48 个工具动作可保守压缩到约 **6—10 个**：

1. 读取并结构化文章（1）；
2. 获取目标首页并核验标题/版本（1）；
3. 获取或检查 `www.zip` 源码（1—2）；
4. 生成并本地断言文章 payload（1）；
5. form POST payload，观察 `10-0`（1）；
6. 同 Cookie session 登录 admin/1（1）；
7. 跟随/GET update，获取并验证 Flag（1）。

可减少的主要类型为：重复文章 inspect、错误环境命令、重复 zip 下载、无断言的 payload 文件改写、错误 Content-Type/count 重试、Cookie 丢失后的 UPDATE 数据库偏航、同请求替代链的多次脚本构建。

这不是承诺固定 6 步：文章可能过期、题目版本可能变化、源码核验可能需要额外动作。合理目标是把“有新证据的动作”与“纯重复/修语法动作”分开，而不是机械追求最少 call。

## 7. 即使优化后仍应保留的步骤

1. **授权和 scope 合同校验**：外部文章域与 challenge 域必须分别有明确读取授权；当前 native `scope=['*']` 应收紧，而不是删除 gate。
2. **Capability 注册、参数 schema、workspace 边界、timeout、输出上限和敏感头脱敏**。
3. **文章与真实目标版本的一次证据核验**：不能仅凭第三方文章直接执行主动 payload。
4. **主动 payload 前的本地结构/长度/Content-Type 断言**。
5. **成功 marker（本题为 `10-0`）的响应 Artifact**，用于证明 POP 链确实执行。
6. **Flag 的格式、placeholder、Artifact 内容和 task ownership gate**。
7. **append-only 事件、action/result、Artifact hash、Session checkpoint**，用于恢复和审计。
8. **Cookie 的 origin 隔离、生命周期和销毁**；增强会话能力不能以泄漏敏感状态为代价。

## 8. 置信度与缺失数据

### 8.1 置信度

- **高（约 0.97）**：42 turns、48 actions、187 events、44 Artifacts、角色数量、文章存储形式、文章首次/完整引用时间、每次 action 输入/结果、最终 Flag 与 proof Artifact、CookieJar 每 action 新建、Observer/Planner 未运行。这些由 SQLite、transcript、Artifact 和代码直接交叉验证。
- **高（约 0.93）**：文章主路径在 action 36→37 之间因 Cookie 不连续而失败。文章正文、目标 PHP 源码、action 响应和 `http.py` Cookie 生命周期形成闭环证据。
- **中高（约 0.85）**：上下文膨胀导致模型高延迟/推理质量下降。输入规模和事件时间是事实，但缺少 provider token usage、queue time 和服务端 tracing，故因果只作推断。
- **中（约 0.80）**：理论路径可压缩到 6—10 actions。它基于文章和当前源码，但未在本次只读任务中重新执行验证。

### 8.2 缺失数据

1. FastAPI 后端当时不可访问，无法把当前持久化快照与在线 API 响应再比对；前端只返回静态壳。
2. 没有 provider 服务端请求日志、input/output token、context-limit 告警、排队时间或缓存命中信息。
3. HTTP `Set-Cookie` 被正确脱敏，未保留可读取明文，因此不能逐字展示 session ID；但每 action 新 CookieJar 的代码和行为差异足以证明不连续。
4. 任务没有 report；为遵守只读约束，没有触发会写文件的 GET report。
5. 没有 Observer/Manager/Planner 的实际决策输出可分析，因为该任务的 native 分支根本没有生成这些记录；这本身是结论而非取证遗漏。
6. 无法只读确认任务运行当时所有环境变量的瞬时值；不过持久化 Session 为 48 turns，默认 executor 代码为 unrestricted，且实际 48 actions 未被预算拒绝。

---

## 9. 最终回答

文章没有消失：唯一 Solver 从第 1 回合就看见 URL，第 12 回合拿到完整 exploit，第 31 回合执行正确文章 payload 并得到 `10-0`。它之所以仍走了 48 个动作，是三个问题叠加：

1. **摄取问题**：文章仅作为普通 URL hint，正文藏在 16.9 万字节 CSDN HTML 中，没有结构化成可追踪策略；
2. **工具问题**：HTTP 每 action 丢弃 CookieJar，文章要求的跨请求 session 路径无法工作；
3. **决策问题**：native AgentSession 没有 Manager plan/Hypothesis/Observer/去重，模型又在 Content-Type、padding、PowerShell 引号和 Cookie 失败归因上多次试错，甚至偏航到修改数据库密码。

最终拿到 Flag 的不是“文章原样两步路径”，而是 Solver 在识别 Cookie 不连续后构造的同请求 POP 链；Flag 出现在 action `act_a41c78a027ff` 的 `artifact_ff713a9a3e39`，由事件 seq 185 记录并在 seq 187 自动停止。
