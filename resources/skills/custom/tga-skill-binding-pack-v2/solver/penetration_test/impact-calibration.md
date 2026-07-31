---
name: impact-calibration
modes: [penetration_test, vulnerability_research]
capabilities: [artifact.inspect]
tags: [impact, severity, review]
version: 2.0.0
---
# 影响校准

## 目标
把已证实能力映射到合理影响和严重性，避免最大化推断。

## 执行流程
1. 区分观察、可控性、权限和业务影响。
2. 考虑前置条件、用户交互、范围和缓解措施。
3. 记录支持与反对严重性因素。

## 输出契约
- 严重性建议。
- 影响边界。
- 未证实升级路径。

## 边界与证据规则
- 潜在链条未验证时不能计入已证实影响。
