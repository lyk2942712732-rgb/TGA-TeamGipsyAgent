# 模式与完成验证架构

## 权威模式注册

后端以 `tga/modes.py` 为唯一模式注册边界；前端以 `apps/web/src/modes.ts` 为 UI 解析边界。任务与 API 只使用以下值：

| 模式 | 方法与证据重点 | 完成重点 |
| --- | --- | --- |
| `ctf` | 根据题面动态选择 Web、Pwn、Reverse、Crypto、Misc 工具 | Supervisor 提交 `propose_task_completion`，且 Flag 通过远端验证器或本地格式、占位符、Artifact 归属和内容验证 |
| `penetration_test` | 授权范围、攻击面、假设验证、影响和覆盖 | 有真实证据、覆盖和限制；允许“未发现漏洞” |
| `incident_response` | 非破坏性保全、时间线、IOC、根因、影响和处置 | 调查结论、覆盖和逐条证据引用 |
| `vulnerability_research` | 静态/动态分析、最小化复现、根因和前提 | 漏洞声明必须有复现证据；阴性结果必须有覆盖和限制 |
| `reverse_engineering` | 文件识别、静态/动态分析、逻辑和数据恢复 | 恢复结果必须引用分析输出、脚本或等价 Artifact |

已移除的旧模式值（`web_audit`、`code_audit`、`binary_ctf`）不再被映射，一律直接拒绝。

## 完成状态机

`propose_task_completion` 是 Supervisor 的整项任务完成提案，不是回合结束动作。公共字段为 `summary`、`evidence_artifact_ids`、结构化 `claims`、`coverage` 和 `limitations`；只有 CTF Schema 暴露 `flag`。Schema 和嵌套 claim 均禁止额外字段。

1. 普通工具调用：结果和 Artifact 回填原会话，继续下一轮。
2. 完成提案被拒绝：写入 `FINISH_REJECTED`，结构化 `missing` 作为 tool result 回填，Task 保持 `running`。
3. 完成提案被 Host 接受：由 `TaskOrchestrator` 唯一写入 `TASK_COMPLETION_ACCEPTED`，随后 Task 才进入 `completed`，并写入 `SESSION_STOPPED`。
4. 无 tool call 的自然结束：写入 `AGENT_TURN_ENDED` 和 `CONTINUATION_TRIGGERED`，继续原会话；连续无进展只触发 Observer 纠偏。
5. `max_turns`、暂停、取消和模型失败继续使用各自硬停止状态，不会伪装成完成。

完成规则实现于 `tga/runtime/completion_validators.py`，Agent 循环只通过模式注册表选择验证器。所有引用 Artifact 都必须真实存在并属于当前任务。审计事件只保存模式、验证代码、缺失条件、Artifact ID、回合和 terminal 标志，不保存未经脱敏的工具参数。
