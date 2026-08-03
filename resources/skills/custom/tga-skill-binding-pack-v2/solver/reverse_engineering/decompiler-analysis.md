---
name: decompiler-analysis
modes: [reverse_engineering]
capabilities: [input.read, artifact.inspect]
tags: [decompiler, reverse]
version: 2.0.0
---
# 反编译分析

## 目标
使用反编译结果加速理解并持续与机器码校验。

## 执行流程
1. 恢复类型、变量和函数签名。
2. 识别反编译伪影。
3. 对关键条件和算术回到指令验证。

## 输出契约
- 高层伪代码。
- 类型假设。
- 已校验关键点。

## 边界与证据规则
- 反编译输出不是源码事实。
