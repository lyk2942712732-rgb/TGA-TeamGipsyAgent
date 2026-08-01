---
name: dynamic-analysis
modes: [reverse_engineering]
capabilities: [workspace.read, workspace.write, workspace.python, workspace.shell, artifact.inspect]
tags: [dynamic, reverse]
version: 2.0.0
---
# 动态分析

## 目标
在授权沙箱中观察执行、输入输出、断点和状态变化。

## 执行流程
1. 确认 allow_dynamic_execution。
2. 设置最小输入、断点和观察点。
3. 保存轨迹、寄存器、内存和输出。

## 输出契约
- 执行轨迹。
- 关键状态。
- 静态交叉验证。

## 边界与证据规则
- 无权限时不得执行。
- 不得连接未授权网络。
