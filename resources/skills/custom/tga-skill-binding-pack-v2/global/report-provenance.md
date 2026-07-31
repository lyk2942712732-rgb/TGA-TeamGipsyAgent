---
name: report-provenance
modes: [ctf, penetration_test, incident_response, vulnerability_research, reverse_engineering]
capabilities: [artifact.inspect]
tags: [reporting, provenance, limitations]
version: 2.0.0
---
# 报告溯源与限制

## 目标
保证报告中的每个关键结论都包含来源、证据强度和限制。

## 执行流程
1. 按 Task scope、coverage、findings、evidence、limitations 组织材料。
2. 明确区分已复现问题、扫描线索、未验证假设和阴性结果。
3. 对版本、环境、时间窗口和未覆盖组件进行说明。

## 输出契约
- 可审计报告结构。
- 证据到结论映射。
- 限制、未覆盖范围与复现条件。

## 边界与证据规则
- 不得隐藏失败或范围拒绝。
- 不得把 candidate 写成 confirmed。
- 不得在报告中泄露无关凭证或秘密。
