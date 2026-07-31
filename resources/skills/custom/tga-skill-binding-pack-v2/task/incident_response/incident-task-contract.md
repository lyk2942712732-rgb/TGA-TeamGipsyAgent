---
name: incident-task-contract
modes: [incident_response]
capabilities: []
tags: [incident, task-contract]
version: 2.0.0
---
# 应急响应共同契约

## 目标
统一所有 Incident Solver 对证据保全、时间不确定性和响应权限的要求。

## 执行流程
1. 原始证据保持只读。
2. 派生结果保留 Hash 与来源。
3. 任何处置遵循 response_authority。

## 输出契约
- 可追溯分析结果。
- 时间、IOC 与范围置信度。
- 处置权限状态。

## 边界与证据规则
- 不得无证据归因。
- analysis_only 不得执行处置。
