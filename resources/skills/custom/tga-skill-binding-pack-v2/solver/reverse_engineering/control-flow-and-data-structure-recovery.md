---
name: control-flow-and-data-structure-recovery
modes: [reverse_engineering]
capabilities: [input.read, artifact.inspect]
tags: [control-flow, data-structure, reverse]
version: 2.0.0
---
# 控制流与数据结构恢复

## 目标
恢复状态机、对象布局、协议结构和关键数据生命周期。

## 执行流程
1. 建立 CFG 或状态转换。
2. 从访问偏移、分配和调用恢复结构。
3. 用多个函数或动态迹象交叉验证。

## 输出契约
- 状态机。
- 结构定义。
- 验证依据。

## 边界与证据规则
- 命名和类型需标注推断。
