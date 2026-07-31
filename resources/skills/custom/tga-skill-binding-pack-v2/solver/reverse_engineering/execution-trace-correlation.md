---
name: execution-trace-correlation
modes: [reverse_engineering]
capabilities: [workspace.read, workspace.write, workspace.python, workspace.shell, artifact.inspect]
tags: [trace, correlation, reverse]
version: 2.0.0
---
# 执行轨迹关联

## 目标
把动态轨迹与静态函数、数据结构和用户目标关联。

## 执行流程
1. 规范地址、模块和线程。
2. 将事件映射到函数与数据流。
3. 标记未覆盖路径和竞态。

## 输出契约
- 静态/动态映射。
- 关键路径。
- 覆盖限制。

## 边界与证据规则
- 单次轨迹不能代表所有行为。
