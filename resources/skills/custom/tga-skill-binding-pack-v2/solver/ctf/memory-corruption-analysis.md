---
name: memory-corruption-analysis
modes: [ctf, vulnerability_research]
capabilities: []
tags: [pwn, memory-corruption]
version: 2.0.0
---
# 内存破坏分析

## 目标
分析栈、堆、格式化字符串、整数和生命周期错误的触发与可控性。

## 执行流程
1. 定位崩溃点和输入传播。
2. 识别可控寄存器、指针、长度或内存区域。
3. 评估保护机制与可利用路径。

## 输出契约
- 根因位置。
- 可控性分析。
- 利用假设与反证。

## 边界与证据规则
- Crash 不等于代码执行。
- 版本和 libc 假设必须验证。
