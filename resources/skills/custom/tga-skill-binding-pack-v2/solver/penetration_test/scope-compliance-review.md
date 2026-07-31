---
name: scope-compliance-review
modes: [penetration_test]
capabilities: [artifact.inspect]
tags: [scope, review, compliance]
version: 2.0.0
---
# 范围合规审核

## 目标
确认测试动作、Artifact 和 Finding 均位于约定范围和规则内。

## 执行流程
1. 核对目标、时间、身份和技术。
2. 检查越界拒绝与审批记录。
3. 标记需删除或隔离的无关数据。

## 输出契约
- 范围审核。
- 例外与违规风险。
- 交付建议。

## 边界与证据规则
- 不得通过报告措辞掩盖越界。
