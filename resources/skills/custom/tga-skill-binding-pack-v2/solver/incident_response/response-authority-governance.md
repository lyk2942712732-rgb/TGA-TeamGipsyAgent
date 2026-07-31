---
name: response-authority-governance
modes: [incident_response]
capabilities: []
tags: [incident, containment, authority]
version: 2.0.0
---
# 响应权限治理

## 目标
根据 analysis_only、containment_with_approval 或 authorized_containment 控制处置建议与执行。

## 执行流程
1. 读取 response_authority。
2. 区分建议、需审批动作和已授权执行。
3. 为所有处置动作记录影响、可逆性和验证步骤。

## 输出契约
- 权限判定。
- 处置建议或审批请求。
- 执行后验证计划。

## 边界与证据规则
- analysis_only 禁止执行处置。
- 高影响动作必须经过治理网关。
