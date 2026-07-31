# TGA - Multi-Solver Task Runtime

TGA 是面向 CTF 与授权安全分析的本地、多 Solver、证据驱动 Runtime。
每个 Task 由唯一 `TaskOrchestrator` 管理，Supervisor 维护 Intent DAG，
Worker、Reviewer 与 Reporter 使用独立身份、Transcript、预算和工具策略。

正式应用只接受 schema v6。schema v5 数据会被应用、API 和 Runtime 明确拒绝，
只能通过显式、离线、备份优先的迁移命令读取和转换。SQLite 是当前唯一协调存储，
最大并发边界为两个活动 Worker。

## Quick Start

需要 Python 3.11+、Node.js 和 npm：

```powershell
python -m pip install -e ".[dev]"
cd apps\web
npm install
npm run build
cd ..\..
tga go
```

`tga go` 打开桌面界面，`tga web` 打开浏览器界面。公网监听必须显式使用
`tga web --host 0.0.0.0`。Provider API Key 只写不读，API 不回显已保存凭据。

## Runtime Contract

- `TaskSpec` 保存 objective、instructions、constraints、success criteria 和 resources。
- `GlobalPlan` 由 Supervisor 独占写入，`LocalPlan` 属于 Solver 与 Intent。
- `KnowledgeItem` 有明确作用域和验证状态；Hint、Skill、RAG 不能扩大授权。
- Artifact 是不可变原始材料；EvidenceClaim 通过 locator 定位；Finding 需要已确认 Claim。
- Worker 使用 `submit_worker_result`，Supervisor 使用 `propose_task_completion`。
- Host 执行确定性完成校验；所有可执行工具调用经过 `ToolGovernanceGateway`。

模型只提交非权威 `ModelToolIntent`。Host 注入 Task、Solver、Intent 和 Policy 身份，
统一处理授权、审批、预算、幂等和资源锁，再将调用交给执行适配器。

## Data Layout

```text
runs/<task-id>/
  evidence.db
  solvers/<solver-id>/
    session/messages.json
    workspace/scratch/
    workspace/outputs/
  workspace/inputs/files/
  workspace/artifacts/
  reports/
```

## Offline Migration

迁移必须在应用停止访问目标数据库时执行：

```powershell
tga migrate --db runs\<task-id>\evidence.db --backup --dry-run
tga migrate --db runs\<task-id>\evidence.db --apply
tga migrate --db runs\<task-id>\evidence.db --verify
```

Dry run 不修改数据库。Apply 保留逐字节数据库备份、原始 v5 Task JSON、旧 Runtime
归档和审计报告，先在副本完成迁移与校验，再发布 schema v6。无法证明的 Evidence、
Finding 和 Knowledge 不会被提升为 confirmed 或 verified。

## Product Routes

产品只注册 Dashboard、Task 生命周期、全局审批/资源/报告/知识库、六个配置页面与
系统状态页面。旧 `/sessions/*`、聚合 Settings 别名和旧 Runtime 页面不提供重定向。

## Verification

```powershell
python -m pytest -q
cd apps\web
npm test
npm run build
npm run test:e2e
```

架构和运维说明位于 `docs/architecture/`，发布门禁记录在
`CUTOVER_CHECKLIST.md`。
