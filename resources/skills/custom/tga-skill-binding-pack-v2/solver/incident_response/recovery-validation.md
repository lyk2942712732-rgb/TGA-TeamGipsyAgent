---
name: recovery-validation
modes: [incident_response]
capabilities: []
tags: [recovery, validation, incident]
version: 2.0.0
---
# 恢复验证

## 目标
验证遏制和恢复是否生效且未破坏关键业务。

## 执行流程
1. 定义预期状态和监控指标。
2. 检查恶意活动停止、服务健康和证据保留。
3. 记录复发风险与后续监控。

## 输出契约
- 恢复检查表。
- 验证 Artifact。
- 剩余风险。

## 边界与证据规则
- 不得删除完成复盘所需证据。
