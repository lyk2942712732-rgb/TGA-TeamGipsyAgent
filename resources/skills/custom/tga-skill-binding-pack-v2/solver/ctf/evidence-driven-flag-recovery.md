---
name: evidence-driven-flag-recovery
modes: [ctf]
capabilities: [artifact.inspect]
tags: [ctf, flag, evidence]
version: 2.0.0
---
# 证据驱动的 Flag 恢复

## 目标
把 Flag 恢复视为 Artifact 支持的完成门禁，而不是文本答案。

## 执行流程
1. 要求专业 Solver 将候选 Flag 所在输出发布为任务 Artifact。
2. 调用 Flag Verifier 检查格式、Artifact 内容和可选平台验证。
3. 仅在验证通过后提出 Task completion。

## 输出契约
- 候选 Flag。
- 支持 Artifact 与定位。
- 验证结果。

## 边界与证据规则
- 模型看到 Flag 不等于完成。
- 占位符、示例 Flag 和无 Artifact Flag 必须拒绝。
