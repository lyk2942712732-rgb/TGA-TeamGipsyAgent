---
name: team-writeup-promotion
modes: [ctf, penetration_test, incident_response, vulnerability_research, reverse_engineering]
capabilities: []
tags: [team, writeup, knowledge]
version: 2.0.0
---
# 团队 Writeup 提升流程

## 目标
把已完成任务中的已验证经验整理为 Workspace reference，而不是直接复用 Transcript。

## 执行流程
1. 从已确认 EvidenceClaim 与 Finding 中提取可泛化方法。
2. 去除 Flag、凭证、目标特定秘密和未经验证猜测。
3. 记录适用版本、前置条件、失败方法和原 Task 溯源。

## 输出契约
- 结构化 Writeup。
- 适用条件与版本。
- 来源 Task 与证据引用。

## 边界与证据规则
- 未经审核不得进入团队长期知识库。
- Transcript 不能整段自动提升。
