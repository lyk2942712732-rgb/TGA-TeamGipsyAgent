---
name: ctf-task-contract
modes: [ctf]
capabilities: []
tags: [ctf, task-contract]
version: 2.0.0
---
# CTF 任务共同契约

## 目标
统一所有 CTF Solver 对 subtype、Artifact、Flag 和完成门禁的理解。

## 执行流程
1. 遵循已确认 subtype 与分配的 Intent。
2. 所有候选 Flag 必须进入本任务 Artifact。
3. 只有 Supervisor 可提出任务完成。

## 输出契约
- 当前 Intent 范围。
- Artifact 支持的结果。
- 明确的阻塞或下一步。

## 边界与证据规则
- 专业 Solver 不得越过 subtype 无目的扩张。
- 外部 Writeup 只能作参考。
