# TGA - Multi-Solver Task Runtime

TGA 是面向 CTF 与授权安全分析的本地、多 Solver、证据驱动 Runtime。
每个 Task 由唯一 `TaskOrchestrator` 管理，Supervisor 维护 Intent DAG，
Worker、Reviewer 与 Reporter 使用独立身份、Transcript、预算和工具策略。

正式应用只接受 schema v6。schema v5 数据会被应用、API 和 Runtime 明确拒绝，
只能通过显式、离线、备份优先的迁移命令读取和转换。SQLite 是当前唯一协调存储，
最大并发边界为两个活动 Worker。

## Quick Start

安装后，Windows 与 Linux 都只需要一条命令：

```powershell
tga up
```

`tga up` 会依次检查运行环境、完成首次初始化、启动容器引擎与 `tga-sandboxd`、
启动 API 与前端、等待 readiness 通过，然后打开界面。重复执行是幂等的：
第二次只会报告"已在运行"，不会重复创建服务。

统一命令：

```powershell
tga up          # 启动并打开界面
tga down        # 停止服务，保留全部任务数据
tga status      # 查看当前运行状态
tga doctor      # 逐项诊断并给出修复建议
tga logs        # 查看组件日志
```

公网部署使用 `tga up --public`（仅 Linux 服务器），它绑定所有网卡并且不打开浏览器；
反向代理与访问控制由部署方提供。Provider API Key 只写不读，API 不回显已保存凭据。

用户不需要执行 `wsl --install`、`systemctl`、Docker 命令或任何部署脚本。
Windows 上 `tga.exe` 自动管理 TGA 专用的 WSL2 发行版（`TGA-Runtime`），
并把命令转发给其中统一的 Linux Runtime；Linux 上 `tga` 直接调用同一套 Runtime。

### 开发者构建

从源码工作时需要 Python 3.11+、Node.js 和 npm：

```powershell
python -m pip install -e ".[dev]"
cd apps\web; npm install; npm run build; cd ..\..
tga up
```

正式部署不会在启动时执行 `npm run build`；前端必须提前构建并通过
`TGA_WEB_DIST` 指向已构建的产物。

### 启动可用性分级

`tga up` 按能力分级报告，而不是只看进程是否监听：

- `ready` —— 核心可用，且沙箱隔离已强制执行。
- `degraded` —— 核心可用可服务，但工具执行未被隔离（例如 `sandbox.runtime`
  为 `disabled`，或 Profile 镜像尚未 digest 固定）。启动仍算成功，
  `tga doctor` 会明确指出缺哪一项及对应错误码。
- `failed` —— 核心不可用（API 或存储不可写），启动失败。

判定依据是 `GET /api/v2/system/readiness`，而不是 `/api/health`：
后者只能证明有进程在监听，不能证明任务可运行或工具执行已被隔离。

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

打包安装使用固定布局，Windows 的 WSL2 发行版与 Linux 服务器完全一致：

```text
/opt/tga/{app,web,bin}    代码、预构建前端产物、tga-internal
/etc/tga/                 sandbox.json、tga.env
/var/lib/tga/runs/        任务数据（唯一 TGA_RUN_ROOT）
/var/log/tga/             组件日志
```

所有任务、数据库、Artifact 与沙箱生命周期统一读取 `TGA_RUN_ROOT`，
任何模块都不得写死 `runs`——否则会出现任务写入一个根、
而沙箱回收扫描另一个根的分裂状态。Windows 本地版禁止把任务数据放在 `/mnt/c/...`。

每个任务目录结构：

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
cd ..\..\launcher
go test ./...
```

架构和运维说明位于 `docs/architecture/`，发布门禁记录在
`CUTOVER_CHECKLIST.md`。
