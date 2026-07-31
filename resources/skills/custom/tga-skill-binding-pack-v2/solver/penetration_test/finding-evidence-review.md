---
name: finding-evidence-review
modes: [penetration_test, vulnerability_research]
capabilities: [artifact.inspect]
tags: [review, finding, evidence]
version: 2.0.0
---
# Finding 证据审核

## 目标
检查 Finding 是否由有效 Claim、Artifact、定位和复现支持。

## 执行流程
1. 核对每个关键主张的证据。
2. 验证复现步骤和环境一致。
3. 把不足的 Finding 降级或退回补证。

## 输出契约
- 审核结论。
- 缺失证据。
- 状态建议。

## 边界与证据规则
- Reviewer 不自行扩大测试。
- 外部资料不能替代任务证据。
