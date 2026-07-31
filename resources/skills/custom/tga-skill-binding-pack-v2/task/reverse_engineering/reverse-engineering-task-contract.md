---
name: reverse-engineering-task-contract
modes: [reverse_engineering]
capabilities: []
tags: [reverse, task-contract]
version: 2.0.0
---
# 逆向分析共同契约

## 目标
统一所有 Reverse Solver 对执行权限、目标输出和证据引用的要求。

## 执行流程
1. 围绕用户要求的逻辑、配置、协议或算法工作。
2. 动态与插桩按配置门禁。
3. 关键解释必须绑定代码位置或轨迹。

## 输出契约
- 目标恢复结果。
- Artifact 和定位。
- 版本与未覆盖路径。

## 边界与证据规则
- 未知二进制不得在宿主执行。
- 反编译输出不是源码事实。
