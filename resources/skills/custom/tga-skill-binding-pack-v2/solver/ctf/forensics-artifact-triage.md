---
name: forensics-artifact-triage
modes: [ctf, incident_response]
capabilities: [input.read, artifact.inspect]
tags: [forensics, triage]
version: 2.0.0
---
# 取证 Artifact 分诊

## 目标
识别 PCAP、磁盘、内存、日志、媒体和文档中的可分析结构。

## 执行流程
1. 记录 Hash、媒体类型和时间信息。
2. 选择对应解析工具和只读流程。
3. 标记异常文件、流、进程或元数据。

## 输出契约
- 输入分类。
- 异常线索。
- 下一步取证路线。

## 边界与证据规则
- 不修改原件。
- 解析失败必须保留状态。
