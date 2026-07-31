---
name: evidence-hashing-and-provenance
modes: [incident_response]
capabilities: [workspace.read, artifact.inspect]
tags: [incident, hash, provenance]
version: 2.0.0
---
# 证据哈希与来源

## 目标
建立原始证据与派生 Artifact 的完整哈希和来源关系。

## 执行流程
1. 计算并核对 SHA-256。
2. 记录采集者、时间、主机和传输路径。
3. 为解析产物添加 parent Artifact。

## 输出契约
- 哈希清单。
- 来源链。
- 完整性异常。

## 边界与证据规则
- 哈希不匹配必须停止并报告。
