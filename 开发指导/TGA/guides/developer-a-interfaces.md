# 开发者 A 接口文档：已提供与仍需对接

本文档描述开发者 A（Orchestrator / Evidence）当前向团队提供的接口，以及 A 需要开发者 B/C 和运行环境提供的接口。跨模块模型以 `tga/contracts.py` 为准。

## A 已提供的接口

### 1. 共享契约模型

位置：`tga/contracts.py`

已提供模型：

- `TGATask`：任务配置模型。
- `Intent`：调度意图模型。
- `ArtifactRecord`：证据 artifact 元数据。
- `Finding`：漏洞发现模型。
- `WorkerResult`：worker 返回模型。
- `TGAError`：跨模块错误结构。

关键约束：

- `web_audit` 任务必须提供非空 `scope`。
- confirmed finding 必须引用 `evidence_artifact_id`。
- worker 声明的 confirmed finding 进入系统后仍按 candidate 处理。

### 2. Scope 校验

位置：`tga/core/scope.py`

接口：

```python
is_in_scope(target: str, scope: list[str]) -> bool
require_in_scope(target: str, scope: list[str]) -> None
```

支持：

- host:port 精确匹配。
- HTTP/HTTPS 默认端口推断。
- CIDR 网段。
- `*.example.com` 通配子域。

### 3. Gate 接口

位置：

- `tga/core/flag_gate.py`
- `tga/core/evidence_gate.py`

接口：

```python
flag_ok(
    flag: str,
    *,
    flag_format: str,
    raw_output: str = "",
    artifact_texts: list[str] | None = None,
) -> bool

finding_ok(
    finding: Finding,
    *,
    task: TGATask,
    artifact_text: str | None,
) -> bool
```

规则：

- Flag 必须匹配 `flag_format`。
- Placeholder flag 会被拒绝，例如 `flag{...}`、`flag{your_flag}`。
- Flag 必须出现在真实 stdout/stderr 或 artifact 内容中。
- Finding 的 target 必须在 scope 内。
- Finding 必须有 artifact。
- 如果 Finding 带 `evidence_excerpt`，artifact 内容必须包含该片段。

### 4. EvidenceStore

位置：`tga/evidence/store.py`

接口：

```python
EvidenceStore(db_path)

create_task(task)
add_intent(intent)
update_intent_status(intent_id, status)
add_artifact(artifact)
add_event(task_id, type, payload, intent_id=None)
add_candidate_finding(finding)
confirm_finding(finding_id, evidence_artifact_id)
add_flag(task_id, value, evidence_artifact_id)
task_snapshot(task_id)
```

对 B/C 的约定：

- B/C 不直接写 SQLite。
- C 生成报告时读取 `task_snapshot(task_id)`。
- `task_snapshot` 稳定返回 `task`、`intents`、`artifacts`、`findings`、`flags`、`events`。

### 5. ArtifactStore

位置：`tga/evidence/artifacts.py`

接口：

```python
ArtifactStore(root)

save_text(
    *,
    task_id,
    intent_id,
    kind,
    text,
    tool=None,
    target=None,
    suffix=".txt",
) -> ArtifactRecord

read_text(artifact_id) -> str
```

约定：

- Artifact 文件路径保存为相对 task workspace 的路径或 artifact 文件名。
- 工具原始输出必须优先保存为 artifact，再进入 gate。

### 6. Planner / Scheduler

位置：

- `tga/orchestrator/planner.py`
- `tga/orchestrator/scheduler.py`
- `tga/orchestrator/run_loop.py`

接口：

```python
plan_initial_intents(task: TGATask) -> list[Intent]

Scheduler(
    *,
    store: EvidenceStore,
    worker: Worker,
    run_root: str,
)

Scheduler.run_intent(task: TGATask, intent: Intent) -> WorkerResult

run_task(
    *,
    task: TGATask,
    store: EvidenceStore,
    worker: Worker,
    run_root: str,
) -> None
```

当前行为：

- `Scheduler` 顺序执行 intent。
- worker 返回的 artifacts 先写入 `EvidenceStore`。
- worker 返回的 findings 先按 candidate 入库。
- worker 返回的 flags 必须通过 `flag_gate` 后才写入 `flags`。
- worker 返回的 findings 必须通过 `finding_gate` 后才升级为 confirmed。
- Gate 通过会写入 `FLAG_CONFIRMED` 或 `FINDING_CONFIRMED` event。
- Gate 拒绝会写入 `GATE_REJECTED` event。

## A 需要 B 提供的接口

### 1. Worker

位置建议：`tga/workers/base.py` 的协议实现。

接口：

```python
class Worker:
    def run(
        self,
        *,
        task: TGATask,
        intent: Intent,
        workspace: str,
    ) -> WorkerResult:
        ...
```

要求：

- 不直接写 confirmed finding。
- 不直接写 flags 表。
- 所有 stdout/stderr、MCP 输出、HTTP 响应、扫描结果必须保存为 artifact。
- 返回的 `WorkerResult.artifacts` 必须包含本轮产生的 artifact。
- 返回的 `WorkerResult.flags` 只放候选 flag 字符串。
- 返回的 `WorkerResult.findings` 只放 candidate finding。
- 失败时返回 `status="failed"` 和 `errors`，不要让未处理异常终止整个 task。

### 2. ToolRunner / MCP 工具

位置建议：`tga/tools/tool_runner.py`、`tga/tools/mcp_client.py`

A 需要 B 保证：

- 工具执行前调用 `tool_policy.is_allowed(...)`。
- 工具原始输出保存为 artifact。
- artifact 中保留可验证证据片段。
- artifact metadata 包含 `tool`、`target`、`kind`。
- MCP 调用失败也要保存 error artifact，方便 report 解释。

## A 需要 C 提供的接口

### 1. Task Config Loader

位置：`tga/cli/config_loader.py`

A 需要 C 保证：

- `task.json` 加载后返回 `TGATask`。
- web audit 示例必须提供非空 `scope`。
- `flag_format` 使用 Python regex 字符串。
- `target` 和 `scope` 的写法能被 `scope.py` 解析。

### 2. Report 读取

位置：`tga/reporting/markdown_report.py`

A 提供 `task_snapshot(task_id)`，C 只依赖该快照生成报告。

C 不应：

- 直接查 SQLite 表。
- 把 candidate finding 展示成 confirmed。
- 展示没有 artifact provenance 的 flag。

## A 需要运行环境提供的内容

- Python 3.11+。
- `pydantic`。
- 开发测试环境安装 `pytest`。
- 可写 run workspace，例如 `runs/task_xxxxxxxx/`。
- SQLite 可用。

## 当前 A 部分验收状态

已覆盖：

- `TGATask` 可以解析任务配置。
- `web_audit` 空 scope 会被拒绝。
- scope policy 能拒绝 out-of-scope target。
- placeholder flag 会被拒绝。
- 不在真实输出/artifact 中的 flag 会被拒绝。
- scheduler 会把真实 artifact 中的 flag 写入 `flags`。
- scheduler 会拒绝 placeholder flag，并写入 `GATE_REJECTED` event。
- scheduler 会将有 artifact 证据的 finding 升级为 confirmed。
- scheduler 会拒绝 out-of-scope finding，并保留 candidate 状态。

仍需和 B/C 联调验证：

- 真实 subprocess/MCP 工具输出 artifact 能被 gate 读取。
- Web CTF demo 能生成带 provenance 的 flag。
- Web audit demo 能生成 confirmed finding。
- Code audit demo 能生成 confirmed finding。
- Markdown report 能正确展示 `FLAG_CONFIRMED`、`FINDING_CONFIRMED` 和 `GATE_REJECTED` 对应结果。
