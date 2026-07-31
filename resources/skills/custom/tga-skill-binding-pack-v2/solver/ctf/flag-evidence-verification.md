---
name: flag-evidence-verification
modes: [ctf]
capabilities: [artifact.inspect]
tags: [ctf, verifier, deterministic]
version: 2.0.0
---
# Flag 证据验证

## 目标
确定性或半确定性检查候选 Flag 是否满足格式并真实存在于本任务 Artifact。

## 执行流程
1. 验证完整正则匹配并排除占位符。
2. 读取 Artifact，确认字节内容、Task 所有权和定位。
3. 配置平台验证器时执行受控验证并记录结果。

## 输出契约
- verified/rejected。
- Flag、Artifact ID、定位。
- 平台验证状态。

## 边界与证据规则
- 只验证，不探索。
- 无 Artifact 必须拒绝。
- 不得接受其他 Task 的 Artifact。
