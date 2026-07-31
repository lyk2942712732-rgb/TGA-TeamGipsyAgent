---
name: approval-aware-response-actions
modes: [incident_response]
capabilities: []
tags: [approval, containment, governance]
version: 2.0.0
---
# 审批感知的响应动作

## 目标
把响应动作映射到 analysis_only、需审批或已授权执行。

## 执行流程
1. 读取 response_authority。
2. 为需审批动作准备具体影响与替代方案。
3. 执行后保存动作结果和验证 Artifact。

## 输出契约
- 权限状态。
- 审批请求或执行记录。
- 失败回滚。

## 边界与证据规则
- 不得把紧急性当作越权理由。
