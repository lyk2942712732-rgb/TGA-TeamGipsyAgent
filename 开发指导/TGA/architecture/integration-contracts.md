# TGA Week 1 统一接口契约

这份文件是三个人本周共同遵守的对接契约。任何字段名、状态值、返回结构、路径约定，如果要修改，必须三个人同步确认。不要各自在自己的模块里定义一套相似但不完全相同的结构。

## 统一原则

1. 共享模型只定义一处：`tga/contracts.py`。
2. 所有模块都 import `tga.contracts`，不要复制粘贴模型。
3. 所有跨模块返回值都用 Pydantic model 或明确的 JSON dict。
4. 所有时间字段使用 UTC ISO 8601 字符串，例如 `2026-07-07T12:00:00Z`。
5. 所有路径字段存相对 task workspace 的路径，只有最外层 runner 解析成绝对路径。
6. 所有 confirmed 结论必须引用 `artifact_id`。
7. worker、tool、report 不直接绕过 evidence gate 写 confirmed finding。

## 必须统一的文件

Week 1 请把跨模块模型集中放在：

```text
tga/contracts.py
```

建议内容：

```python
from typing import Literal
from pydantic import BaseModel, Field

TaskMode = Literal["ctf", "web_audit", "code_audit", "binary_ctf"]
Intensity = Literal["passive", "normal", "active"]
IntentKind = Literal["recon", "verify", "exploit_ctf", "code_scan", "report"]
IntentStatus = Literal["pending", "running", "done", "failed", "blocked"]
FindingStatus = Literal["candidate", "confirmed", "rejected"]
Severity = Literal["info", "low", "medium", "high", "critical"]

class TGATask(BaseModel):
    id: str
    name: str
    mode: TaskMode
    target: str
    scope: list[str]
    intensity: Intensity = "normal"
    allow_active_scan: bool = False
    goal: str
    flag_format: str | None = None

class Intent(BaseModel):
    id: str
    task_id: str
    kind: IntentKind
    target: str
    goal: str
    required_tools: list[str] = Field(default_factory=list)
    risk: Literal["passive", "active", "destructive"] = "passive"
    status: IntentStatus = "pending"

class ArtifactRecord(BaseModel):
    id: str
    task_id: str
    intent_id: str | None = None
    kind: Literal["stdout", "stderr", "tool_output", "http_response", "file", "report"]
    path: str
    sha256: str
    tool: str | None = None
    target: str | None = None
    created_at: str

class Finding(BaseModel):
    id: str
    task_id: str
    title: str
    target: str
    severity: Severity
    status: FindingStatus = "candidate"
    evidence_artifact_id: str | None = None
    evidence_excerpt: str | None = None
    reproduction_steps: list[str] = Field(default_factory=list)
    remediation: str | None = None
    tool: str | None = None

class WorkerResult(BaseModel):
    task_id: str
    intent_id: str
    status: Literal["ok", "failed", "blocked"]
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    leads: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
```

## ID 规范

统一 ID 前缀：

```text
task_<8-12 chars>
intent_<8-12 chars>
artifact_<sha256 first 12>
finding_<8-12 chars>
event_<monotonic integer or uuid>
```

不要在各模块里用不同风格的 ID，例如一边用 UUID，一边用数据库自增，一边用文件名。

## Workspace 目录约定

每个 task 的运行目录：

```text
runs/
└── task_xxxxxxxx/
    ├── task.json
    ├── evidence.db
    ├── artifacts/
    │   ├── artifact_aaaa1111.txt
    │   └── artifact_bbbb2222.json
    ├── inputs/
    ├── work/
    │   └── intent_xxxxxxxx/
    └── reports/
        └── report.md
```

约定：

- B 的 worker/tool 只写 `work/` 和通过 ArtifactStore 写 `artifacts/`。
- A 的 EvidenceStore 写 `evidence.db`。
- C 的 report 只写 `reports/`。
- 任何模块不要直接写别人的目录。

## EvidenceStore 接口

A 提供，B/C 只调用接口，不直接写 SQL。

```python
class EvidenceStore:
    def create_task(self, task: TGATask) -> None: ...
    def add_intent(self, intent: Intent) -> None: ...
    def update_intent_status(self, intent_id: str, status: IntentStatus) -> None: ...
    def add_artifact(self, artifact: ArtifactRecord) -> None: ...
    def add_event(self, task_id: str, type: str, payload: dict, intent_id: str | None = None) -> None: ...
    def add_candidate_finding(self, finding: Finding) -> None: ...
    def confirm_finding(self, finding_id: str, evidence_artifact_id: str) -> None: ...
    def add_flag(self, task_id: str, value: str, evidence_artifact_id: str) -> None: ...
    def task_snapshot(self, task_id: str) -> dict: ...
```

## ArtifactStore 接口

A 提供接口，B 主要调用，C 读取。

```python
class ArtifactStore:
    def save_text(
        self,
        *,
        task_id: str,
        intent_id: str | None,
        kind: str,
        text: str,
        tool: str | None = None,
        target: str | None = None,
        suffix: str = ".txt",
    ) -> ArtifactRecord: ...

    def read_text(self, artifact_id: str) -> str: ...
```

## Worker 接口

B 提供，A 的 scheduler 调用。

```python
class Worker:
    def run(self, *, task: TGATask, intent: Intent, workspace: str) -> WorkerResult: ...
```

要求：

- Worker 不直接确认 finding。
- Worker 可以返回 candidate finding、facts、leads、flags。
- Worker 产生的 stdout/stderr 必须保存为 artifact。
- Worker 失败时返回 `status="failed"` 和 `errors`，不要抛出未处理异常让整个 task 崩溃。

## ToolRunner 接口

B 提供。

```python
class ToolRunner:
    def run_tool(
        self,
        *,
        task: TGATask,
        intent: Intent,
        tool: str,
        target: str,
        args: dict,
    ) -> ArtifactRecord: ...
```

要求：

- 运行前必须调用 `tool_policy.is_allowed(...)`。
- 工具原始输出必须保存 artifact。
- MCP 调用失败也保存 tool error artifact，方便报告解释。

## Marker 协议

如果使用 CLI agent 或 shell worker，stdout 中统一使用以下 marker：

```text
VERIFIED_FACT=<plain text>
UNVERIFIED_LEAD=<plain text>
DEADEND=<plain text>
FOUND_FLAG=<flag text>
ARTIFACT=<artifact_id>
TOOL_ERROR=<tool>|<reason>
CONFIRMED_FINDING_JSON=<json object>
```

注意：

- `CONFIRMED_FINDING_JSON` 只是 worker 的声明，进入系统后先按 candidate 处理。
- 只有 evidence gate 通过后，status 才能变成 confirmed。
- JSON 必须是单行，字段名使用 `Finding` 模型字段。

## Error 结构

跨模块错误统一：

```json
{
  "code": "OUT_OF_SCOPE",
  "message": "target is not in task scope",
  "retryable": false
}
```

建议错误码：

```text
OUT_OF_SCOPE
ACTIVE_SCAN_NOT_ALLOWED
TOOL_NOT_AVAILABLE
TOOL_TIMEOUT
MCP_UNAVAILABLE
ARTIFACT_NOT_FOUND
GATE_REJECTED
INVALID_TASK_CONFIG
```

## 三人联调检查点

Day 1 晚上：

- C 的 `task.json` 能被 A 的 `TGATask` 解析。
- A 的 `Intent` 能被 B 的 worker 接收。

Day 2 晚上：

- B 的 worker 能返回 `WorkerResult`。
- A 能把 `WorkerResult.artifacts` 写入 EvidenceStore。

Day 3 晚上：

- B 的工具输出 artifact 能被 A 的 finding gate 读取。
- C 能从 `task_snapshot()` 生成 report 草稿。

Day 4 晚上：

- web CTF、web audit、code audit 至少各跑一遍。
- 三个人一起看一份 `evidence.db` 和一份 `report.md`，确认字段齐全。

