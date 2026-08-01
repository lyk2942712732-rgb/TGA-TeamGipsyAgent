---
name: cloud-audit-analysis
modes: [incident_response]
capabilities: [workspace.read, artifact.inspect]
tags: [cloud, audit, incident]
version: 2.0.0
---
# 云审计分析

## 目标
分析云控制面、身份、资源和数据访问审计事件。

## 执行流程
1. 规范账号、角色、资源和 API。
2. 识别异常认证、权限变更和数据操作。
3. 关联来源 IP、设备与会话。

## 输出契约
- 云时间线。
- 身份与资源影响。
- 审计覆盖缺口。

## 边界与证据规则
- 租户与账号范围必须明确。
