---
name: team-tooling-conventions
modes: [ctf, penetration_test, incident_response, vulnerability_research, reverse_engineering]
capabilities: []
tags: [team, tooling, conventions]
version: 2.0.0
---
# 团队工具约定

## 目标
统一团队对工具选择、命令记录、脚本保存和结果命名的约定。

## 执行流程
1. 优先使用团队批准的工具和固定版本。
2. 保存可复现脚本、参数、依赖和执行环境。
3. 按团队命名规范发布 Artifact 与输出文件。

## 输出契约
- 工具与版本清单。
- 复现命令或脚本。
- 团队标准路径与文件名。

## 边界与证据规则
- 此模板需由 Workspace 管理员补充具体工具。
- 团队约定不得绕过 Task ExecutionPolicy。
