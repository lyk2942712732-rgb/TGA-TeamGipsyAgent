# 模式与完成验证架构

## 权威模式注册

后端以 `tga/modes.py` 为唯一模式注册和旧值迁移边界；前端以 `apps/web/src/modes.ts` 为 UI/旧快照解析边界。新建任务与 API 输出只使用以下值：

| 模式 | 方法与证据重点 | 完成重点 |
| --- | --- | --- |
| `ctf` | 根据题面动态选择 Web、Pwn、Reverse、Crypto、Misc 工具 | 显式 `finish_session`，且 Flag 通过远端验证器或本地格式、占位符、Artifact 归属和内容验证 |
| `penetration_test` | 授权范围、攻击面、假设验证、影响和覆盖 | 有真实证据、覆盖和限制；允许“未发现漏洞” |
| `incident_response` | 非破坏性保全、时间线、IOC、根因、影响和处置 | 调查结论、覆盖和逐条证据引用 |
| `vulnerability_research` | 静态/动态分析、最小化复现、根因和前提 | 漏洞声明必须有复现证据；阴性结果必须有覆盖和限制 |
| `reverse_engineering` | 文件识别、静态/动态分析、逻辑和数据恢复 | 恢复结果必须引用分析输出、脚本或等价 Artifact |

旧值只在兼容入口被映射；持久化旧任务读取后会以新值输出，非 CTF 的旧 `flag_format` 会被清除。

## 完成状态机

`finish_session` 是整项任务的完成声明，不是回合结束动作。公共字段为 `summary`、`evidence_artifact_ids`、结构化 `claims`、`coverage` 和 `limitations`；只有 CTF Schema 暴露 `flag`。Schema 和嵌套 claim 均禁止额外字段。

1. 普通工具调用：结果和 Artifact 回填原会话，继续下一轮。
2. `finish_session` 被拒绝：写入 `FINISH_REJECTED`，结构化 `missing` 作为 tool result 回填，Session 保持 `running`。
3. `finish_session` 被接受：写入 `FINISH_ACCEPTED`，随后 Session 才进入 `completed`。
4. 无 tool call 的自然结束：写入 `AGENT_TURN_ENDED` 和 `CONTINUATION_TRIGGERED`，继续原会话；连续无进展只触发 Observer 纠偏。
5. `max_turns`、暂停、取消和模型失败继续使用各自硬停止状态，不会伪装成完成。

完成规则实现于 `tga/runtime/completion_validators.py`，Agent 循环只通过模式注册表选择验证器。所有引用 Artifact 都必须真实存在并属于当前任务。审计事件只保存模式、验证代码、缺失条件、Artifact ID、回合和 terminal 标志，不保存未经脱敏的工具参数。
