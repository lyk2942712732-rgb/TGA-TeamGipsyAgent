---
name: artifact-and-evidence-discipline
modes: [ctf, penetration_test, incident_response, vulnerability_research, reverse_engineering]
capabilities: [artifact.inspect]
tags: [evidence, artifact, governance]
version: 2.0.0
---
# Artifact 与证据纪律

## 目标
确保所有关键结论都可追溯到本任务的不可变 Artifact 与精确定位信息。

## 执行流程
1. 区分外部参考、候选知识、任务 Artifact、EvidenceClaim 与已确认 Finding。
2. 对关键事实记录 Artifact ID、定位器、产生工具、时间与所属 Task。
3. 发现证据缺口时明确提出下一步取证动作，而不是补全或猜测。

## 输出契约
- 证据引用清单。
- 已确认、候选、未知三类结论。
- 缺失证据与下一步取证建议。

## 边界与证据规则
- 检索命中本身不是验证。
- 不得伪造 Artifact、Flag、漏洞、IOC 或工具输出。
- 外部资料只能作为 reference。
