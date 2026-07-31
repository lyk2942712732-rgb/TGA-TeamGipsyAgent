---
name: network-forensics
modes: [incident_response]
capabilities: [workspace.read, artifact.inspect]
tags: [network, forensics, pcap]
version: 2.0.0
---
# 网络取证

## 目标
分析 PCAP、流量日志和网络元数据以识别会话、协议和异常通信。

## 执行流程
1. 重建会话和关键协议字段。
2. 提取端点、域名、证书和传输对象。
3. 关联主机与时间线。

## 输出契约
- 会话摘要。
- 网络 IOC。
- 传输 Artifact。

## 边界与证据规则
- 加密流量限制必须说明。
- 不把异常流量直接归因。
