# 开发者 B 指导书：Workers / MCP Tools

## 你的目标

你负责 TGA 的执行能力：worker 运行、MCP 工具接入、工具安全策略、stdout/stderr 捕获、artifact 保存。第一周重点不是工具数量，而是工具调用稳定、输出可追溯。

## 本周交付

1. Python subprocess worker。
2. 可选 CLI-agent worker 适配层。
3. mcp-security-hub MVP 工具接入。
4. 工具调用 policy：scope + intensity + rate limit。
5. 工具输出 artifact 捕获。
6. `tga_mcp_healthcheck.py`。

## 你负责开发的文件

```text
tga/contracts.py  # 只 import，不要私自改模型
tga/workers/base.py
tga/workers/subprocess_worker.py
tga/workers/cli_agent_worker.py
tga/workers/output_parser.py
tga/tools/mcp_catalog.py
tga/tools/mcp_client.py
tga/tools/mcp_healthcheck.py
tga/tools/tool_policy.py
tga/tools/tool_runner.py
tga/tools/rate_limit.py
scripts/tga_mcp_healthcheck.py
tests/test_tool_policy.py
tests/test_output_parser.py
tests/test_mcp_catalog.py
```

## 对接注意事项

你不定义新的 task/intent/finding 数据结构，统一从 `tga/contracts.py` import：

```python
from tga.contracts import TGATask, Intent, ArtifactRecord, WorkerResult, Finding
```

Worker 对外只暴露一个稳定接口：

```python
class Worker:
    def run(self, *, task: TGATask, intent: Intent, workspace: str) -> WorkerResult:
        ...
```

返回约定：

- 成功但没发现问题：`status="ok"`，`findings=[]`，可以有 `facts` 或 `dead ends` 事件。
- 工具失败：`status="failed"`，填 `errors`，同时保存 tool error artifact。
- 缺少外部条件：`status="blocked"`，填 `errors`，不要无限重试。
- 所有 stdout/stderr/tool output 都通过 ArtifactStore 保存，并返回 `ArtifactRecord`。

你不要直接写 EvidenceStore 的 SQL 表。工具输出交给 A 的 EvidenceStore 和 gate 处理。

和 A 的对接边界：

- `ToolRunner.run_tool(...)` 运行前必须调用 `tool_policy.is_allowed(...)`。
- out-of-scope 或 active scan forbidden 时，返回结构化错误，不执行工具。
- `CONFIRMED_FINDING_JSON` 解析出来后仍然返回 `Finding(status="candidate")`，不能自己标 confirmed。
- `FOUND_FLAG` 只返回给 A，不能自己判定 solved。

和 C 的对接边界：

- healthcheck 输出 JSON，字段固定为 `tool`、`status`、`detail`。
- artifact 的 `tool`、`target`、`kind` 字段必须填好，C 的报告会直接使用。
- 工具失败也要留下 artifact 或 event，否则报告无法解释为什么没结果。

统一 marker 协议以 `architecture/integration-contracts.md` 为准。不要新增临时 marker，除非三个人一起改 parser 和 report。

## MVP 工具范围

只接入少量高价值工具：

```text
recon:
  nmap
  whatweb

web:
  nuclei
  ffuf
  sqlmap

code:
  semgrep
  gitleaks

binary/forensics:
  binwalk 或 radare2 二选一
```

不要第一周接入全部 security-hub 工具。工具越多，越难限制行为和复现结果。

## Worker 输出协议

worker 或 CLI agent 输出统一 marker：

```text
VERIFIED_FACT=<fact text>
UNVERIFIED_LEAD=<lead text>
CONFIRMED_FINDING=<json>
DEADEND=<reason>
FOUND_FLAG=<flag>
ARTIFACT=<artifact_id>
TOOL_ERROR=<tool>|<reason>
```

你的 `output_parser.py` 负责解析这些 marker，但不要直接信任它们。解析结果交给开发者 A 的 gate。

## Tool policy

工具运行前必须检查：

```python
allowed = tool_policy.is_allowed(
    tool="nuclei",
    target="http://127.0.0.1:8080",
    scope=task.scope,
    intensity=task.intensity,
    allow_active_scan=task.allow_active_scan,
)
```

强度建议：

```text
passive:
  允许 whatweb、semgrep、gitleaks
  禁止 sqlmap、ffuf、nuclei 主动模板

normal:
  允许 nmap 少量端口、nuclei safe templates、ffuf 小字典
  sqlmap 只允许轻量检测

active:
  允许更完整扫描，但必须限速、记录授权、严格 scope
```

## Artifact 捕获

每次工具调用都保存：

- task_id
- intent_id
- tool
- target
- command 或 MCP method
- started_at / finished_at
- exit code 或 MCP status
- stdout/stderr/raw result
- sha256

artifact 保存后返回 `artifact_id`，给 gate 和 report 使用。

## 每日安排

Day 1：

- 跑起 mcp-security-hub。
- 写 `mcp_catalog.py` 和 healthcheck 设计。

Day 2：

- 实现 subprocess worker 和 output parser。
- 完成 `tga_mcp_healthcheck.py`。

Day 3：

- 实现 tool policy 和 rate limit。
- 工具输出接入 artifact store。

Day 4：

- 跑通 nmap/whatweb、nuclei、semgrep/gitleaks 三类 demo。
- 和开发者 A 联调 gate。

Day 5：

- 补异常处理：工具不存在、超时、out-of-scope、MCP 连接失败。

## 验收标准

- healthcheck 能显示 MVP 工具可用性。
- out-of-scope 工具调用被拒绝。
- 工具失败不会导致整个 task 崩溃。
- 至少三种工具输出能保存 artifact。
- worker 输出 marker 能被 parser 稳定解析。
