---
name: evidence-triage
modes: [incident_response]
capabilities: [workspace.read, artifact.inspect]
tags: [incident, triage]
version: 2.0.0
---
# 证据初筛

## 目标
快速识别输入证据类型、可读性、时间范围和分析优先级。

## 执行流程
1. 登记文件、Hash、大小和媒体类型。
2. 识别日志、PCAP、内存、磁盘、云审计和样本。
3. 推荐对应分析 Solver。

## 输出契约
- 证据清单。
- 类型与优先级。
- 解析风险和缺口。

## 边界与证据规则
- 保持原件只读。
- 不从文件名推断内容。
