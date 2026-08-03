---
name: host-forensics
modes: [incident_response]
capabilities: [input.read, artifact.inspect]
tags: [host, forensics]
version: 2.0.0
---
# 主机取证

## 目标
分析主机文件、进程、账户、持久化、执行痕迹和系统配置。

## 执行流程
1. 建立主机基线。
2. 检查执行、持久化、横向和数据访问痕迹。
3. 将发现映射到时间线与 IOC。

## 输出契约
- 主机发现。
- Artifact/Claim 引用。
- 受影响范围。

## 边界与证据规则
- 优先离线只读分析。
- 不得在样本主机上直接清理。
