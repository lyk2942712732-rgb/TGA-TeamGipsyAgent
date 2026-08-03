---
name: disassembly-analysis
modes: [reverse_engineering]
capabilities: [input.read, artifact.inspect]
tags: [disassembly, reverse]
version: 2.0.0
---
# 反汇编分析

## 目标
从指令、调用约定、控制流和数据引用恢复关键行为。

## 执行流程
1. 确认架构和基址。
2. 标记函数、基本块、调用和关键数据。
3. 用交叉引用验证语义。

## 输出契约
- 关键函数。
- 控制流与数据引用。
- 证据位置。

## 边界与证据规则
- 避免逐指令无目标抄录。
