---
name: timeline-reconstruction
modes: [incident_response]
capabilities: [workspace.read, artifact.inspect]
tags: [timeline, incident]
version: 2.0.0
---
# 时间线重建

## 目标
把多源事件规范化为带来源、时区和置信度的结构化时间线。

## 执行流程
1. 统一时间格式并保留原时区。
2. 关联主机、用户、进程、网络和云事件。
3. 标记推断关系与缺口。

## 输出契约
- CSV/JSON 时间线。
- 来源与 Artifact 引用。
- 关键阶段和缺口。

## 边界与证据规则
- 不得静默修正不确定时间。
- 关联不等于因果。
