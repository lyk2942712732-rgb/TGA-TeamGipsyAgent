---
name: instrumentation-analysis
modes: [reverse_engineering]
capabilities: [input.read, kali.exec, artifact.inspect]
tags: [instrumentation, reverse]
version: 2.0.0
---
# 插桩分析

## 目标
在 allow_instrumentation 为真时进行 Hook、覆盖或系统调用级观测。

## 执行流程
1. 定义要验证的函数或行为。
2. 选择最小插桩点和数据采集。
3. 校验插桩是否改变目标行为。

## 输出契约
- 插桩脚本。
- 轨迹 Artifact。
- 扰动评估。

## 边界与证据规则
- 插桩权限需单独检查。
