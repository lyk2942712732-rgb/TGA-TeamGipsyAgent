# TGA — Multi-Solver Task Runtime

TGA 是面向 CTF 与授权安全分析的本地、多 Solver、证据驱动 Runtime。
每个 Task 由唯一 `TaskOrchestrator` 管理；Supervisor 维护 Intent DAG，Worker、
Reviewer 与 Reporter 拥有独立身份、Transcript、预算、工具策略和私有工作区。

当前默认是 schema v6。历史 schema v5 数据采用 **v5 read-only** Snapshot 与
Replay；普通读取不会原地升级数据库。运行协调存储仍是本地 SQLite，并发边界为
最多两个活动 Worker，不是分布式调度系统。

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

`tga go` 打开桌面界面；`tga web` 打开浏览器界面。公网监听需显式执行
`tga web --host 0.0.0.0`。模型 Provider 与只写 API Key 可在设置页配置；生产
部署必须限制设置接口访问，浏览器和 API 响应不会返回已保存的密钥。

## 核心语义

- `TaskSpec` 保存正式 objective、instruction、constraint、success criteria 与 resources。
- `TaskHint` 是未经验证的线索；运行中的输入使用类型化 `UserIntervention`。
- `GlobalPlan` 是 Supervisor 独占写的 Intent DAG；`LocalPlan` 属于 Solver + Intent。
- `KnowledgeItem` 有 solver/intent/task 作用域和 candidate/verified/rejected/superseded 状态。
- Transcript 是每个 Solver 的协议历史；它不是 Knowledge，也不是 Event Log。
- Artifact 是不可变原始材料；EvidenceClaim 用 locator 指向 Artifact 片段；Finding 只能由已确认 Claim 支撑。
- Task Common Skill 与 Solver Specialized Skill 是冻结方法指导，不能扩大 ToolPolicy。
- Retrieval 结果是带来源、带可信度标签的候选参考，不能自动成为 verified Knowledge。

模型只提交 `ModelToolIntent`。宿主注入 Task/Solver/Intent/Policy 身份后，由
`ToolGovernanceGateway` 统一处理授权、审批、预算、幂等和资源锁，再交给执行适配器。
Worker 看不到 Task completion capability；Reporter 不能确认 Finding。

## 数据与工作区

```text
runs/<task-id>/
  evidence.db                 # SQLite 权威状态与事件
  solvers/<solver-id>/
    session/messages.json     # Solver 独立审计 Transcript
    workspace/scratch/        # Solver 私有可写区
    workspace/outputs/        # Solver 私有输出区
  workspace/inputs/files/     # 不可变任务输入
  workspace/artifacts/        # 追加式共享 Artifact
  reports/                    # 显式导出的报告
```

本地 Docker MCP 在任务调用时只读挂载输入/共享区，并只写专用 Artifact 目录；
远程 HTTP MCP 从不获得本地文件系统挂载。详细配置见
[MCP_CONFIGURATION.md](docs/MCP_CONFIGURATION.md)。

## v5/v6 兼容与迁移

- 新任务只创建 schema v6 数据。
- v5 Snapshot/Event Replay 使用只读 SQLite URI；v6 Command 对 v5 返回明确冲突。
- `MemoryEntry`、`StrategyCard`、`active_solver_id` 等仅保留在兼容/迁移边界。
- 不需要迁移即可查看旧任务。

显式离线迁移默认是 dry-run：

```powershell
python scripts\migrate_schema_v5_to_v6.py --db runs\<task-id>\evidence.db
python scripts\migrate_schema_v5_to_v6.py --db runs\<task-id>\evidence.db --apply
```

dry-run 只写迁移报告。`--apply` 会先生成数据库和原始 Task JSON 备份，在临时
数据库上迁移与校验，再原子发布；重复执行为 no-op。不可判断的旧 Evidence/
Finding 始终保持 candidate/legacy，不会推断确认状态。

## API 与前端

主要接口包括 Task 创建、Snapshot、分页 Event、SSE、Task/Solver 控制、
Intervention、Approval decision、Intent retry 和报告导出，均位于 `/api/v2`。

默认前端是 Task 指挥台：Team Explorer、Intent Board、事件 Timeline、证据与资源、
Approval Center、Solver Inspector 和只读 Replay。前端只消费后端投影，不通过
Transcript 猜测业务状态。旧单 Agent 页面仅由 `VITE_RUNTIME_PAGE=legacy` 提供
临时兼容。

## 验证

```powershell
python -m pytest -q
cd apps\web
npm test
npm run build
npm run test:e2e
```

更多信息：

- [OPERATIONS.md](docs/architecture/OPERATIONS.md)
- [SECURITY_MODEL.md](docs/architecture/SECURITY_MODEL.md)
- [RECOVERY.md](docs/architecture/RECOVERY.md)
- [RAG.md](docs/architecture/RAG.md)
- [FRONTEND_WORKBENCH.md](docs/architecture/FRONTEND_WORKBENCH.md)
- [BASELINE.md](docs/performance/BASELINE.md)
- [RELEASE_NOTES_V6.md](docs/RELEASE_NOTES_V6.md)
