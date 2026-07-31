---
name: bounded-investigation
modes: [ctf, penetration_test, incident_response, vulnerability_research, reverse_engineering]
capabilities: []
tags: [budget, stopping, planning]
version: 2.0.0
---
# 有界调查与停止条件

## 目标
让调查围绕明确假设、预算和停止条件推进，避免无限扩展攻击面。

## 执行流程
1. 为每个 Intent 定义目标、证据要求、允许动作和停止条件。
2. 连续无新证据时记录失败边界并切换路线或回报阻塞。
3. 完成所需问题后停止，不把可选探索当成必需工作。

## 输出契约
- 当前假设与状态。
- 消耗预算与剩余预算。
- 停止、转向或升级理由。

## 边界与证据规则
- 同一语义动作不得无界重试。
- 失败结果也要保留证据。
- 不以自然语言自信代替完成门禁。
