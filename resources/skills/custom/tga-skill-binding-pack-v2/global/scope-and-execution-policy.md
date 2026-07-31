---
name: scope-and-execution-policy
modes: [ctf, penetration_test, incident_response, vulnerability_research, reverse_engineering]
capabilities: []
tags: [scope, policy, authorization]
version: 2.0.0
---
# 范围与执行策略

## 目标
在任何工具动作前核对任务范围、授权强度、审批要求和执行策略。

## 执行流程
1. 读取 TaskSpec、scope、exclusions、ExecutionPolicy 与模式专用权限字段。
2. 把拟执行动作映射到被动、主动、高影响或处置类别。
3. 超出范围或权限不足时返回结构化阻断原因与低风险替代方案。

## 输出契约
- 允许动作边界。
- 被拒绝动作与原因。
- 需要审批的具体动作。

## 边界与证据规则
- Skill 不得扩大授权。
- 可读资源不等于可执行权限。
- 审批只适用于已明确授权的目标。
