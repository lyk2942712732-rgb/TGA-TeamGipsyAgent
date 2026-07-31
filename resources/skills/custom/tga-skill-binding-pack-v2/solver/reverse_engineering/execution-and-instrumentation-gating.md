---
name: execution-and-instrumentation-gating
modes: [reverse_engineering]
capabilities: []
tags: [reverse, dynamic, policy]
version: 2.0.0
---
# 动态执行与插桩门禁

## 目标
根据 allow_dynamic_execution 和 allow_instrumentation 控制动态分析与插桩。

## 执行流程
1. 读取模式配置。
2. 无动态执行权限时只创建静态分析。
3. 插桩动作单独核对权限、沙箱和输入输出边界。

## 输出契约
- 权限判定。
- 允许的动态手段。
- 被拒绝动作与静态替代方案。

## 边界与证据规则
- 不得在宿主机直接运行未知二进制。
- 插桩权限不等于网络或高影响权限。
