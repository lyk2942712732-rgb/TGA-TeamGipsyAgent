---
name: incident-reporting
modes: [incident_response]
capabilities: [artifact.inspect]
tags: [report, incident]
version: 2.0.0
---
# 事件响应报告

## 目标
汇总事件范围、时间线、IOC、根因、影响、处置和未确定事项。

## 执行流程
1. 从已验证时间线和 Claim 生成叙述。
2. 区分事实、分析判断和未知。
3. 提供遏制、恢复与长期改进建议。

## 输出契约
- 事件报告。
- 证据与 IOC 附录。
- 限制和后续行动。

## 边界与证据规则
- 不得无证据归因。
- 敏感信息按交付范围脱敏。
