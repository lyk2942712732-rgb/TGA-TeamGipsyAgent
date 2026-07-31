---
name: evidence-preservation
modes: [incident_response]
capabilities: []
tags: [incident, evidence, preservation]
version: 2.0.0
---
# 事件响应证据保全

## 目标
保持日志、PCAP、内存、磁盘和样本的原始性、来源和哈希链。

## 执行流程
1. 登记输入来源、采集时间、媒体类型和 SHA-256。
2. 所有解析与转换都从只读原件生成派生 Artifact。
3. 记录解析失败、缺失范围和时区信息。

## 输出契约
- 证据清单。
- 哈希与来源。
- 派生 Artifact 关系。

## 边界与证据规则
- 不得修改原始证据。
- 不确定的时间、主机和身份必须显式标注。
