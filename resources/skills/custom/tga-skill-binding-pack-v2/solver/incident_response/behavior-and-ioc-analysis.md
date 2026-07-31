---
name: behavior-and-ioc-analysis
modes: [incident_response]
capabilities: [workspace.read, artifact.inspect]
tags: [malware, behavior, ioc]
version: 2.0.0
---
# 行为与 IOC 分析

## 目标
在授权沙箱中关联行为轨迹与可操作 IOC。

## 执行流程
1. 确认执行权限和隔离配置。
2. 记录文件、进程、网络、注册表或系统调用。
3. 将观察与静态假设交叉验证。

## 输出契约
- 行为时间线。
- IOC。
- 静态/动态一致性。

## 边界与证据规则
- 无执行权限时返回静态替代方案。
- 不得连接未授权外部目标。
