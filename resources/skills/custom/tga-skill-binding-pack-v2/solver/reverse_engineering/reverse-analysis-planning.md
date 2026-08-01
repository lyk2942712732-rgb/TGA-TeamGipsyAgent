---
name: reverse-analysis-planning
modes: [reverse_engineering]
capabilities: []
tags: [reverse, planning, binary]
version: 2.0.0
---
# 逆向分析规划

## 目标
围绕用户真正需要恢复的逻辑、配置、协议或算法组织逆向流程。

## 执行流程
1. 先进行 Binary Triage。
2. 根据目标选择 Static、Dynamic 和 Logic/Config Recovery Intent。
3. 把大量反编译输出压缩为可回答用户问题的证据链。

## 输出契约
- 分析路线。
- 按需 Solver 激活。
- 目标输出定义。

## 边界与证据规则
- 静态输出数量不是完成标准。
- 缺乏证据时不猜测行为。
