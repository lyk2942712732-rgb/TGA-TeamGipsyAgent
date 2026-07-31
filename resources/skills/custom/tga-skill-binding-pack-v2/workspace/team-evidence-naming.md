---
name: team-evidence-naming
modes: [ctf, penetration_test, incident_response, vulnerability_research, reverse_engineering]
capabilities: []
tags: [team, evidence, naming]
version: 2.0.0
---
# 团队证据命名规范

## 目标
为跨任务复用建立稳定的 Artifact、Claim、Finding 和报告命名方式。

## 执行流程
1. 使用 mode、task、intent、solver、timestamp 等可审计字段。
2. 对原始材料、解析结果、脚本、输出和报告使用不同后缀。
3. 在元数据中保留原始来源和 SHA-256。

## 输出契约
- 命名规则。
- 目录规则。
- 元数据最小字段。

## 边界与证据规则
- 不得修改原始输入。
- 同名对象不得静默覆盖。
