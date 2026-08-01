---
name: cryptanalytic-hypothesis-testing
modes: [ctf]
capabilities: [workspace.read, artifact.inspect]
tags: [crypto, analysis]
version: 2.0.0
---
# 密码分析假设检验

## 目标
用小规模验证和数学不变量筛选密码攻击路线。

## 执行流程
1. 为每个攻击写明前置条件。
2. 实现最小验证脚本。
3. 比较输出、复杂度和失败原因。

## 输出契约
- 验证脚本。
- 假设状态。
- 恢复出的中间量。

## 边界与证据规则
- 不得凭公式名称宣告成功。
- 大整数计算需保存参数与输出。
