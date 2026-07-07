# 开发者 A 指导书：Orchestrator / Evidence

## 你的目标

你负责 TGA 的大脑和可信核心：任务模型、调度器、证据库、flag gate、finding gate。第一周你不需要做复杂多智能体算法，只需要让任务能被拆成少量步骤，并保证所有结论都有证据。

## 本周交付

1. `TGATask` 任务模型。
2. `Intent` 调度模型。
3. SQLite 版 evidence store。
4. CTF flag gate。
5. 漏洞 finding evidence gate。
6. 至少 4 个核心单元测试。

## 你负责开发的文件

```text
tga/contracts.py
tga/core/task.py
tga/core/intents.py
tga/core/findings.py
tga/core/scope.py
tga/core/evidence_gate.py
tga/core/flag_gate.py
tga/orchestrator/planner.py
tga/orchestrator/scheduler.py
tga/evidence/store.py
tga/evidence/artifacts.py
tests/test_task_model.py
tests/test_scope_policy.py
tests/test_flag_gate.py
tests/test_finding_gate.py
```

## 对接注意事项

你是共享契约的 owner。Day 1 必须先把 `tga/contracts.py` 定下来，B 和 C 都从这里 import 模型，不允许各自复制一份。

必须统一并冻结这些内容：

- `TGATask`
- `Intent`
- `ArtifactRecord`
- `Finding`
- `WorkerResult`
- `IntentStatus`
- `FindingStatus`
- 错误码字符串

Day 2 之后，如果你要改字段名、枚举值、ID 格式，必须同步 B/C，并同时改 examples 和 tests。

EvidenceStore 对外只暴露接口，不让 B/C 直接写 SQL。最低接口：

```python
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

和 B 的对接边界：

- scheduler 只调用 `Worker.run(task, intent, workspace) -> WorkerResult`。
- B 返回的 finding 一律先按 candidate 入库。
- B 返回的 flags 必须经过 `flag_gate.py`，不能直接入 `flags` 表。
- B 返回的 artifacts 必须先 `add_artifact`，再进入 gate。

和 C 的对接边界：

- C 的 config loader 输出必须是 `TGATask`。
- C 的 report 只能读 `task_snapshot(task_id)` 和 ArtifactStore，不要直接查 SQLite 表。
- 你需要保证 snapshot 字段稳定：`task`、`intents`、`artifacts`、`findings`、`flags`、`events`。

## 核心模型建议

`TGATask`：

```python
class TGATask(BaseModel):
    id: str
    name: str
    mode: Literal["ctf", "web_audit", "code_audit", "binary_ctf"]
    target: str
    scope: list[str]
    intensity: Literal["passive", "normal", "active"] = "normal"
    allow_active_scan: bool = False
    goal: str
    flag_format: str | None = None
```

`Intent`：

```python
class Intent(BaseModel):
    id: str
    kind: Literal["recon", "verify", "exploit_ctf", "code_scan", "report"]
    target: str
    goal: str
    required_tools: list[str] = []
    risk: Literal["passive", "active", "destructive"] = "passive"
```

`Finding`：

```python
class Finding(BaseModel):
    id: str
    title: str
    target: str
    severity: Literal["info", "low", "medium", "high", "critical"]
    status: Literal["candidate", "confirmed", "rejected"]
    evidence_artifact_id: str | None = None
    evidence_excerpt: str | None = None
    reproduction_steps: list[str] = []
    remediation: str | None = None
```

## Evidence store MVP

第一周用 SQLite 足够：

```text
tasks(id, name, mode, target, scope_json, created_at)
intents(id, task_id, kind, target, goal, status, created_at, updated_at)
artifacts(id, task_id, intent_id, path, sha256, kind, created_at)
events(id, task_id, intent_id, type, payload_json, created_at)
findings(id, task_id, title, target, severity, status, evidence_artifact_id, payload_json)
flags(id, task_id, value, evidence_artifact_id, created_at)
```

关键点：不要让模型直接写 confirmed。模型只能提出 candidate，gate 通过后再升级。

## Gate 规则

CTF flag gate：

- 必须匹配 `flag_format`。
- 不能是 placeholder，如 `flag{...}`、`flag{your_flag}`。
- 必须出现在 worker stdout/stderr 或 artifact 内容中。

Finding gate：

- target 必须在 scope 内。
- confirmed finding 必须有 artifact。
- artifact 必须包含 evidence excerpt 或可解析证据。
- 只有模型自然语言、没有工具输出的 finding 只能是 candidate。

## 每日安排

Day 1：

- 定义 task/intent/finding 模型。
- 写 scope 校验函数。

Day 2：

- 实现 SQLite evidence store 和 artifact store。
- 实现 flag gate。

Day 3：

- 实现 finding gate。
- 和开发者 B 联调工具输出 artifact。

Day 4：

- 实现 planner 的简单规则：CTF -> recon/exploit_ctf/report；web audit -> recon/verify/report；code audit -> code_scan/report。
- 和开发者 C 联调报告数据读取。

Day 5：

- 补测试、修 bug、冻结 MVP 接口。

## 验收标准

- 没有 artifact 的 finding 不能 confirmed。
- out-of-scope target 不能生成 confirmed finding。
- placeholder flag 被拒绝。
- 真实 artifact 中的 flag/finding 能通过 gate。
- evidence store 能完整回放一个 task 的事件和 artifact。
