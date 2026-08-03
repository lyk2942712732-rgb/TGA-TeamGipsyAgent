---
name: log-correlation
modes: [incident_response]
capabilities: [input.read, artifact.inspect]
tags: [logs, correlation, incident]
version: 2.0.0
---
# 日志关联

## 目标
跨身份、主机、应用和网络日志关联同一活动链。

## 执行流程
1. 规范字段和实体标识。
2. 按时间、会话、进程、请求和账号建立关联。
3. 验证关键跳转并标记缺失日志。

## 输出契约
- 关联事件链。
- 支持证据。
- 日志覆盖缺口。

## 边界与证据规则
- 避免只凭时间邻近做强因果结论。
