---
name: ctf-task-planning
modes: [ctf]
capabilities: []
tags: [ctf, planning, supervisor]
version: 2.0.0
---
# CTF 任务规划

## 目标
由 CTF Supervisor 建立最小 Intent DAG，并只激活与 subtype 匹配的主力 Solver。

## 执行流程
1. 先创建分类 Intent。
2. 分类完成后创建一个主力解题 Intent，必要时创建验证 Intent。
3. 只有证据不足或 subtype 改判时才增加辅助 Intent。

## 输出契约
- Intent DAG。
- Solver 分配。
- 完成条件与失败边界。

## 边界与证据规则
- 默认只运行一个专业主力 Solver。
- Flag Verifier 不参与探索。
- 不得让多个专业 Solver 无目的并行。
