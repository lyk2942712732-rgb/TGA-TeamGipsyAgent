目前项目在入口层已经明确拒绝 schema v5，但内部实际状态更像：

schema v6 领域模型
        +
旧版基础数据库 schema
        +
运行时自动补列
        +
两套 Repository / Evidence / Skill 模型
        +
旧单 Agent Session 生命周期

所以现在不能认为“v5 已经完全隔离”。最需要优先清理的是下面几处。

P0：会直接阻碍新版本稳定运行
1. schema v6 仍建立在旧 schema.sql 上

这是最严重的残余。

数据库每次正常打开时依次执行：

检查 Task 是否 schema 6
→ 执行 tga/evidence/schema.sql
→ 执行 _migrate_schema() 自动补列
→ 执行 schema_v6.sql

也就是说，正常运行路径本身就是一个隐式迁移器，而不是单纯加载完整的 v6 schema。

旧 tga/evidence/schema.sql 仍负责创建：

tasks
sessions
agent_events
artifacts
findings
flags
artifact_indexes
challenge_contracts

其中 sessions.schema_version 和 agent_events.schema_version 默认值甚至还是 2。

而所谓 schema_v6.sql 并不是一个可以单独创建全新数据库的完整 schema，它依赖旧文件先创建的 tasks、intents、artifacts 等基础表。

风险
新数据库和迁移数据库可能有不同的真实 DDL。
每次启动可能修改数据库结构。
缺列、旧索引、错误默认值可能被静默接受。
schema_metadata 更新 hash，但不拒绝结构漂移。
测试通过不代表用户现有数据库结构与测试数据库一致。
应改成
新建数据库
    → 只执行一份完整 schema_v6.sql
    → 校验 schema hash
    → 不允许正常启动自动 ALTER TABLE

_migrate_schema() 应从 Database.__init__() 中彻底移除。所有结构变更只能走显式升级命令。

2. 同一数据库仍有两套 Repository 写入路径

现在 EvidenceStore 通过多重继承继续组合旧 Repository：

EventRepository
ArtifactRepository
SessionRepository
TaskRepository

同时新的 PersistenceBundle 又创建：

SqliteTaskRepository
SqlitePlanRepository
SqliteSolverRepository
SqliteEvidenceRepository
SqliteEventRepository
...

当前 Runtime 中两种调用都存在：

store.append_agent_event(...)
store.add_artifact(...)

PersistenceBundle(store).events.append_agent_event(...)
PersistenceBundle(store).evidence.add_artifact(...)

旧 ArtifactRepository 甚至还保留：

if schema_version == 6:
    # immutable insert
else:
    INSERT OR REPLACE

这说明旧写入语义仍然留在正式 Repository 内，不只是迁移代码。

风险
同一实体可能经过不同校验。
事务边界不一致。
Event payload 正规化规则不同。
Artifact 不可变规则可能因入口不同而不同。
后续修改一个 Repository，另一个入口没有同步修改。
目标状态

EvidenceStore 只负责：

SQLite connection
transaction
close

所有领域读写统一经过：

PersistenceBundle

旧 tga/evidence/repositories.py 中的领域 Repository 应删除或移入迁移专用目录。

3. Skill 系统仍然是两套模型，且会静默丢 Skill

目前任务创建使用旧的：

tga/skills/models.py
tga/skills/selection.py

旧选择器允许最多选择 3 个 Skill。

新的 schema v6 模型则规定，新 Task Common Skill 最多只能有 2 个；3 个只允许用于 legacy_import=True。

两套模型之间的适配器直接执行：

for skill in bundle.skills[:2]

也就是静默截断第三个 Skill。

实际结果可能是：

前端预览：选择了 3 个
Preflight：3 个全部通过
创建任务：成功
运行时快照：只剩前 2 个

这是确定存在的新版本功能错误。

应立即统一

假设新架构决定上限为 2：

API selectedSkills 最大长度改为 2。
MAX_SELECTED_SKILLS 改为 2。
选择器直接输出 TaskCommonSkillSnapshot。
删除 tga/skills/models.py 中间模型。
删除 current_skill_bundle_to_task_common() 适配层。
第三个 Skill 必须明确报错，不能截断。
P1：新架构尚未完全接管
4. 多 Solver 运行时仍由旧单 Agent Session 投影控制

SessionRecord 仍有：

active_solver_id
turn_count
max_turns

在多 Solver 架构中，一个任务可能同时有多个 Worker，因此 Task Session 不应该再有唯一的 active_solver_id。真正权威的执行状态应该来自：

TaskOrchestratorState
SolverInstance
SolverRun

但当前 SessionCoordinator 仍会切换 active_solver_id，并生成旧事件：

AGENT_STARTED
FINISH_ACCEPTED
AGENT_FINISHED
SESSION_STOPPED

与此同时，新事件体系已有：

SOLVER_STARTED
SOLVER_COMPLETED
TASK_COMPLETION_PROPOSED
TASK_COMPLETION_ACCEPTED
影响

一次完成过程可能同时存在：

TASK_COMPLETION_ACCEPTED
FINISH_ACCEPTED
AGENT_FINISHED
SESSION_STOPPED

前端、统计、恢复逻辑很容易重复判断或者依赖错误事件。

建议

Task Session 只保存任务级生命周期：

created
running
paused
awaiting_approval
blocked
completed
failed
cancelled

删除 active_solver_id。Solver 状态完全从 solver_runs 读取。

持久化事件只保留一套：

TASK_*
SOLVER_*
INTENT_*
TOOL_*
EVIDENCE_*

旧 AGENT_*、FINISH_* 事件若前端仍需要，应在查询层临时映射，而不是继续写入数据库。

5. Evidence 仍有旧模型和新模型并存

旧模型：

ArtifactRecord
CandidateFindingRecord

其中 Candidate Finding 直接持有 evidence_artifact_id。

新模型：

Artifact
EvidenceClaim
Finding

新 Finding 要求 confirmed Finding 必须至少关联一个 confirmed EvidenceClaim。

但 tga/domain/evidence/__init__.py 仍同时公开两套类型。

更关键的是，新的执行后端产物注册服务仍然创建 ArtifactRecord，然后通过旧 EvidenceStore.add_artifact() 写入。

SessionCoordinator 在任务完成时也会直接创建 CandidateFindingRecord，绕开新的 EvidenceClaim → Finding 审核链。

应统一为
原始结果
  → Artifact
  → EvidenceClaim(candidate)
  → Reviewer 确认
  → Finding(candidate / confirmed)

应删除正式运行路径中的：

ArtifactRecord
CandidateFindingRecord
add_candidate_finding()
confirm_finding(finding_id, artifact_id)

迁移旧数据时才允许把旧 Candidate Finding 转换为带 legacy_import provenance 的新 Finding。

6. CLI 仍可绕过新任务创建和 Preflight

Web/API 创建任务使用 TaskCreationService，会：

验证模型；
冻结模型快照；
选择 Skill；
验证 Policy；
生成 Preflight fingerprint。

但 CLI 的 tga run 和 tga create 仍然：

读取 task.json
→ TGATask.model_validate()
→ TaskRuntimeService.create_task()

没有经过统一的 TaskCreationService。

而 TGATask 又会自动补全缺失的：

mode_config
execution_policy
schema_version = 6

因此一个旧格式但恰好能通过验证的 task.json，可能在未明确迁移的情况下被当作 schema v6。

此外，model_snapshot 还是可选字段，Manager 遇到没有 snapshot 的任务会继续使用当前模型配置。这破坏了 v6 所强调的可重放和配置冻结。

应修改
schema_version: Literal[6]
mode_config: ModeConfig
execution_policy: ExecutionPolicy
model_snapshot: ModelSnapshot

不要在持久化模型的 validator 中补默认值。默认值应该只在创建任务表单或 CreateTaskCommand 中生成。

CLI 必须调用与 Web 相同的：

preflight
→ fingerprint
→ create
→ schedule
7. TaskSpec 还没有真正成为任务权威来源

新架构声称 TaskSpec 保存：

objective
instructions
constraints
success criteria
resources

但当前创建任务时虽然建立了 TaskSpec，却始终保存：

resources=[]

并标记：

"session_resources_projected": False

后续 Initial Intent 又直接从 task.session_input.files 读取资源，而不是从 TaskSpec 的 ResourceRef 获取。

这意味着：

TGATask.session_input

仍然是事实上的任务输入权威来源，TaskSpec 只是附加记录。

应完成迁移

创建任务时把每个 SessionFile 转换为 TaskSpec ResourceRef：

input file
→ ResourceRef(role=target)
→ TaskSpec.resources
→ Intent.allowed_resource_ids
→ SolverAssignment.allowed_resources

运行时禁止再从 TGATask.session_input.files 推导授权资源。

P2：不会立即崩溃，但会持续制造混乱
8. schema_version 同时代表至少四种不同版本

现在存在：

Task schema_version        = 6
Runtime API 路由           = /v2
Runtime protocol schema    = 2
Event envelope schema      = 6
Event payload schema       = 1
Dashboard/Catalog schema   = 1

其中 tga/runtime/protocol.py 仍明确写着：

RUNTIME_SCHEMA_VERSION = 2

API 路由也继续使用 /v2。

这不一定意味着旧代码有错，但所有地方都使用同一个字段名 schema_version，非常容易把 API 版本、数据库版本、领域模型版本混为一谈。

建议明确改名：

api_version
task_schema_version
database_schema_version
event_envelope_version
event_payload_version
9. Compatibility DTO 仍是公开 Application API

application/projections/models.py 明确保留了：

# Compatibility names retained for earlier application callers.
TaskSummaryProjection
SessionProjection
EvidenceProjection
TimelineProjection

这些旧 DTO 仍从 projections/__init__.py 公开导出。

并且 TaskProjectionQueries 仍然实际实现这些旧查询，直接读取 sessions.active_solver_id。

当前 API 已经使用新的 RuntimeQueries 和 v6 DTO，因此这套兼容查询大概率可以删除。

10. 当前查询仍会主动读取旧表

任务摘要中，活跃 Solver 数量优先读取：

solver_instances

不存在时则退回：

solvers

Artifact Index 查询也把旧 artifact_indexes 和新 Retrieval Index Projection 合并成一个 legacy_indexes 结构，再返回给前端。

schema v6 数据库不应该再在普通查询路径中检测旧表。迁移完成后，应保证旧表已经不存在；如果存在，就拒绝打开，而不是运行时 fallback。

哪些旧版本支持可以合理保留

以下部分不应该简单删除：

tga migrate 离线迁移入口；
v5 原始数据库备份；
旧 Task JSON 备份；
旧 Runtime 表归档；
provenance.migrated_from_schema = 5；
schema 版本不匹配时明确拒绝运行。

当前迁移器会备份、归档并删除旧 Runtime 表，这个方向是正确的。

迁移专用 Skill 转换也已经放在 tga/migrations/skill_bundles.py，这是合理的隔离方式。

但迁移器当前仍采用：

复制旧数据库
→ 把 Task 标成 v6
→ 用正常 PersistenceBundle 打开
→ 依赖正常启动路径自动补表补列

应改为：

创建全新空白 v6 数据库
→ 从旧数据库只读提取
→ 显式转换
→ 写入新数据库
→ 完整校验
→ 原子替换

这样迁移代码和正常 Runtime 才真正隔离。

推荐清理顺序
第一批：必须先做
重写完整、独立的 schema_v6.sql。
删除正常启动中的 _migrate_schema()。
修复 Skill 3 → 2 静默截断。
禁止 CLI 绕过 TaskCreationService。
TGATask.schema_version 改为 Literal[6]。
第二批：统一运行时
只保留 PersistenceBundle Repository。
删除旧 EvidenceStore 领域 Repository 继承。
统一 Artifact / EvidenceClaim / Finding。
删除 active_solver_id。
删除旧 AGENT_* 和 FINISH_* 持久化事件。
第三批：删除死兼容层
删除 TaskProjectionQueries。
删除旧 Compatibility DTO。
删除查询中的 solvers 表 fallback。
删除旧 artifact_indexes 合并逻辑。
把 legacy_import 从领域特殊校验改成普通 provenance。
最终验收标准

完成清理后应满足：

打开一个 v6 数据库不会执行任何 DDL
新建数据库只执行一份 schema_v6.sql
迁移后的数据库 schema hash 与全新数据库完全一致
Runtime 代码不查询 solvers、memory_entries、actions 等旧表
正式运行代码不导入 tga.migrations
只有一个 Event writer、一个 Artifact writer、一个 Finding pipeline
选择 3 个 Skill 会明确报错，而不是静默丢失
一次任务完成只产生一套 canonical completion events
CLI、Web、API 使用完全相同的 Preflight 和创建流程

所以我的判断是：外部入口已经基本切到 schema v6，但数据库底座、Evidence、Skill、Session/Event 和部分查询层仍没有彻底迁移。 当前最危险的不是 v5 任务还能不能运行，而是新任务本身仍在依赖旧版基础设施。此次结论来自 main 分支静态代码审计，尚未通过实际测试运行验证所有触发路径。
