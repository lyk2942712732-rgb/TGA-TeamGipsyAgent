---
name: structured-worker-result
modes: [ctf, penetration_test, incident_response, vulnerability_research, reverse_engineering]
capabilities: [artifact.inspect]
tags: [worker-result, handoff, orchestration]
version: 2.0.0
---
# 结构化 WorkerResult

## 目标
向 Supervisor 提交可合并、可审核、可追溯的 WorkerResult。

## 执行流程
1. 汇总完成的 Intent、采取的动作和关键 Artifact。
2. 将结论分为 confirmed、candidate、rejected 与 unknown。
3. 记录失败路线、未覆盖范围、建议后续 Intent 和资源需求。

## 输出契约
- Intent 状态。
- Artifact/Evidence 引用。
- 结构化发现、限制与建议。

## 边界与证据规则
- 不得直接宣告 Task 完成。
- 不得把外部 reference 升级为任务事实。
- 结果必须绑定当前 Solver 与 Intent。
