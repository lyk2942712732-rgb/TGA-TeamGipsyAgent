---
name: logic-and-config-recovery
modes: [reverse_engineering]
capabilities: [workspace.read, artifact.inspect]
tags: [logic, config, reverse]
version: 2.0.0
---
# 逻辑与配置恢复

## 目标
提取用户要求的算法、配置、密钥派生、C2 或行为开关。

## 执行流程
1. 定位配置来源和解析路径。
2. 恢复字段、默认值、加密和使用点。
3. 用样本或脚本验证。

## 输出契约
- 结构化配置。
- 算法说明。
- 证据与脚本。

## 边界与证据规则
- 未验证字符串不得标成活动 C2。
